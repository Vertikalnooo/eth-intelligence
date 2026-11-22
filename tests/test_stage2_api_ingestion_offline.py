"""Actual API payload -> research observation -> Stage 2 report, all offline."""
import io
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import app
from freshness import Observation, Consistency, ExchangeState, TransportState, WsSession
from integration_harness import (
    rest_observation_from_api_snapshot, new_clock_domain, revoke_clock_domain,
)
from stage2_acceptance import inspect_stage2_offline


class OfflineApiIngressTests(unittest.TestCase):
    def setUp(self):
        self.domain = new_clock_domain()
        self.addCleanup(revoke_clock_domain, self.domain)
        self.now_ns = 10_000_000_000
        self.utc = datetime(2026, 11, 22, tzinfo=timezone.utc)
        fake_response = json.dumps([
            {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
            [{"markPx": "40000"}, {"markPx": "3000.000"}],
        ]).encode()
        # app.eth_market receives only an in-memory context-manager fixture.
        # Neither this call nor the adapter makes any network request.
        self.snapshot = app.eth_market(
            fetch=lambda _request, timeout: io.BytesIO(fake_response))

    def map(self, **updates):
        snapshot = dict(self.snapshot)
        snapshot.update(updates)
        return rest_observation_from_api_snapshot(
            snapshot, "api-rest-1", self.now_ns,
            self.now_ns - 1_000_000_000,
            self.now_ns - 100_000_000,
            self.domain)

    def test_actual_api_payload_maps_to_receipt_only(self):
        result = self.map()
        self.assertIsInstance(result, Observation)
        self.assertEqual(result.mark_px, Decimal("3000.000"))
        self.assertEqual(result.market_id, "ETH-PERP")
        self.assertEqual(result.source_channel, "REST")
        self.assertIsNone(result.exchange_timestamp)
        self.assertIsNone(result.event_id)

    def test_api_ingress_composes_with_full_offline_report(self):
        rest = self.map()
        ws = Observation(
            "ws-1", "ETH-PERP", "mark", "WS",
            rest.mark_px, self.now_ns, self.domain.token,
            connection_epoch="ws-epoch-1")
        report = inspect_stage2_offline(
            rest, ws, self.now_ns, self.domain,
            WsSession("ws-epoch-1", True, self.domain.token), self.utc)
        self.assertIs(report.transport_state, TransportState.FRESH)
        self.assertIs(report.consistency, Consistency.CONSISTENT)
        self.assertIs(report.exchange_state, ExchangeState.UNVERIFIED)
        self.assertFalse(report.accepted)
        self.assertFalse(report.recommendation_eligible)
        self.assertIn("missing_exchange_provenance", report.reason_codes)

    def test_api_observed_at_never_becomes_exchange_timestamp(self):
        snapshot = dict(self.snapshot, observed_at="2099-01-01T00:00:00+00:00")
        result = rest_observation_from_api_snapshot(
            snapshot, "api", self.now_ns, self.now_ns-100, self.now_ns-10,
            self.domain)
        self.assertIsNone(result.exchange_timestamp)

    def test_forged_timestamp_and_eligibility_flags_are_rejected(self):
        for change in (
            {"exchange_timestamp": 1800000000000},
            {"exchange_freshness_verified": True},
            {"mark_price_freshness_verified": True},
            {"recommendation_eligible": True},
            {"freshness_status": "fresh"},
            {"caller_exchange_evidence": b"fake"},
        ):
            with self.subTest(change=tuple(change)):
                self.assertIsNone(self.map(**change))

    def test_wrong_market_source_and_type_are_rejected(self):
        for change in (
            {"source": "other"},
            {"symbol": "BTC/USDC"},
            {"market_type": "spot"},
            {"price_type": "trade"},
            {"symbol": None},
        ):
            with self.subTest(change=tuple(change)):
                self.assertIsNone(self.map(**change))

    def test_malformed_price_is_rejected_without_decimal_overflow(self):
        for price in (
            3000, "", "NaN", "Infinity", "0", "-1", "3e99999",
            "1" * 161, "1,000", " 3000 ",
        ):
            with self.subTest(price=str(price)[:30]):
                self.assertIsNone(self.map(mark_price=price))

    def test_invalid_receipt_clock_metadata_is_rejected(self):
        snapshot = dict(self.snapshot)
        for observation_id, received, start, end in (
            ("id", True, 10, 11),
            ("id", self.now_ns, 12, 11),
            ("id", self.now_ns, self.now_ns + 1, self.now_ns + 2),
            (" ", self.now_ns, 1, 2),
        ):
            with self.subTest(observation_id=observation_id, received=received):
                result = rest_observation_from_api_snapshot(
                    snapshot, observation_id, received, start, end, self.domain)
                self.assertIsNone(result)

    def test_revoked_or_copied_clock_domain_rejected(self):
        snapshot = dict(self.snapshot)
        clone = replace(self.domain)
        self.assertIsNone(rest_observation_from_api_snapshot(
            snapshot, "id", self.now_ns, 1, 2, clone))
        self.assertTrue(revoke_clock_domain(self.domain))
        self.assertIsNone(rest_observation_from_api_snapshot(
            snapshot, "id", self.now_ns, 1, 2, self.domain))


if __name__ == "__main__":
    unittest.main()
