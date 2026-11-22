"""SDK-shaped ETH activeAssetCtx fixture -> WS operational freshness, offline."""
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from freshness import Consistency, ExchangeState, TransportState, WsSession, assess_transport
from integration_harness import (
    new_clock_domain, revoke_clock_domain, ws_observation_from_active_asset_ctx,
    rest_observation_from_api_snapshot,
)
from stage2_acceptance import inspect_stage2_offline


class OfflineWsIngressTests(unittest.TestCase):
    def setUp(self):
        self.domain = new_clock_domain()
        self.addCleanup(revoke_clock_domain, self.domain)
        self.now = 10_000_000_000
        self.utc = datetime(2026, 11, 22, tzinfo=timezone.utc)
        self.session = WsSession("ws-epoch", True, self.domain.token)
        # The public Hyperliquid Python SDK types specify exactly this
        # ActiveAssetCtxMsg envelope; no trusted event timestamp field.
        self.message = {
            "channel": "activeAssetCtx",
            "data": {"coin": "ETH", "ctx": {
                "markPx": "3000.00", "oraclePx": "3000.10",
                "funding": "0.0001", "midPx": "3000.05"}},
        }

    def decode(self, **overrides):
        params = dict(message=self.message, observation_id="ws-1",
                      received_ns=self.now, session=self.session,
                      domain=self.domain)
        params.update(overrides)
        return ws_observation_from_active_asset_ctx(**params)

    def test_sdk_message_price_is_operational_only(self):
        observation = self.decode()
        self.assertEqual(observation.mark_px, Decimal("3000.00"))
        self.assertEqual(observation.source_channel, "WS")
        self.assertEqual(observation.connection_epoch, "ws-epoch")
        self.assertIsNone(observation.exchange_timestamp)
        self.assertIsNone(observation.event_id)
        self.assertIs(assess_transport(
            observation, self.now, self.domain.token, session=self.session
        ).exchange_mark_state, ExchangeState.UNVERIFIED)

    def test_ws_and_production_shape_rest_integrate_deny_only(self):
        rest_payload = dict(symbol="ETH/USDC", market_type="perpetual",
                            source="hyperliquid", price_type="mark",
                            mark_price="3000.0", observed_at="untrusted",
                            exchange_timestamp=None,
                            exchange_freshness_verified=False,
                            mark_price_freshness_verified=False,
                            recommendation_eligible=False,
                            freshness_status="unknown")
        rest = rest_observation_from_api_snapshot(
            rest_payload, "rest-1", self.now,
            self.now-1_000_000_000, self.now-100_000_000, self.domain)
        self.assertIsNotNone(rest)
        ws = self.decode()
        report = inspect_stage2_offline(
            rest, ws, self.now, self.domain, self.session, self.utc)
        self.assertIs(report.consistency, Consistency.CONSISTENT)
        self.assertIs(report.exchange_state, ExchangeState.UNVERIFIED)
        self.assertFalse(report.accepted)
        self.assertFalse(report.recommendation_eligible)

    def test_wrong_channel_and_asset_rejected(self):
        for change in (
            {"channel": "allMids"},
            {"channel": "activeSpotAssetCtx"},
            {"data": {"coin": "BTC", "ctx": {"markPx": "3000"}}},
            {"data": {"coin": "ETH", "ctx": {}}},
            {"extra": "untrusted"},
        ):
            with self.subTest(change=tuple(change)):
                message = dict(self.message)
                message.update(change)
                self.assertIsNone(self.decode(message=message))

    def test_disconnected_or_wrong_domain_session_rejected(self):
        for session in (
            replace(self.session, connected=False),
            replace(self.session, connected="true"),
            replace(self.session, epoch=""),
            replace(self.session, clock_domain_id="another"),
        ):
            with self.subTest(session=session):
                self.assertIsNone(self.decode(session=session))

    def test_reconnect_requires_fresh_epoch(self):
        old = self.decode()
        new_session = replace(self.session, epoch="ws-new")
        from freshness import assess_transport
        self.assertIs(assess_transport(
            old, self.now, self.domain.token, session=new_session
        ).transport_state, TransportState.INVALID)
        self.assertEqual(self.decode(session=new_session).connection_epoch, "ws-new")

    def test_mark_price_validation(self):
        for px in ("", "NaN", "Infinity", "-1", "0", "1e9999",
                   "2" * 161, 3000, " 3000"):
            with self.subTest(px=str(px)[:25]):
                m = dict(self.message)
                m["data"] = {"coin": "ETH", "ctx": {"markPx": px}}
                self.assertIsNone(self.decode(message=m))

    def test_claimed_time_inside_context_is_ignored(self):
        m = dict(self.message)
        m["data"] = {"coin": "ETH", "ctx": {
            "markPx": "3000", "time": 1900000000000,
            "exchange_timestamp": 1900000000000,
            "event_id": "forged",
        }}
        obs = self.decode(message=m)
        self.assertIsNone(obs.exchange_timestamp)
        self.assertIsNone(obs.event_id)

    def test_inactive_domain_or_future_receipt_fail_closed(self):
        self.assertTrue(revoke_clock_domain(self.domain))
        self.assertIsNone(self.decode())
        # A decoded sample from an active domain with future age is also
        # invalid when subsequently assessed, not exchange-verified.


if __name__ == "__main__":
    unittest.main()
