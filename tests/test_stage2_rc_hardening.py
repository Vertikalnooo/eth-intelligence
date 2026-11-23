"""Stage 2 candidate hardening: local identity, refusal and contract invariants."""
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from datetime import datetime, timezone
import unittest

from freshness import ExchangeState, Observation, TransportState, Consistency, WsSession
from integration_harness import (
    LocalClockDomain, new_clock_domain, revoke_clock_domain, rotate_clock_domain,
    valid_domain, to_market_observation,
)
from provenance.replay import ClaimResult
from stage2_acceptance import Stage2OfflineReport, inspect_stage2_offline


class ReleaseCandidateHardeningTests(unittest.TestCase):
    def setUp(self):
        self.domain = new_clock_domain()
        self.addCleanup(revoke_clock_domain, self.domain)
        self.now = 10_000_000_000
        self.receipt_utc = datetime(2026, 11, 22, tzinfo=timezone.utc)
        self.rest = Observation(
            "r1", "ETH-PERP", "mark", "REST", Decimal("3000"), self.now,
            self.domain.token, request_start_monotonic_ns=9_000_000_000,
            request_end_monotonic_ns=9_900_000_000)
        self.ws = Observation(
            "w1", "ETH-PERP", "mark", "WS", Decimal("3000"), self.now,
            self.domain.token, connection_epoch="ws-1")
        self.session = WsSession("ws-1", True, self.domain.token)

    def report(self, **changes):
        params = dict(rest=self.rest, ws=self.ws, now_ns=self.now,
                      domain=self.domain, session=self.session,
                      receipt_utc=self.receipt_utc)
        params.update(changes)
        value = inspect_stage2_offline(**params)
        self.assertIs(value.accepted, False)
        self.assertIs(value.recommendation_eligible, False)
        self.assertIs(value.stage2_complete, False)
        self.assertIs(value.exchange_state, ExchangeState.UNVERIFIED)
        self.assertIs(value.replay_result, ClaimResult.UNAVAILABLE)
        return value

    def test_issued_domain_copy_has_no_authority(self):
        clone = LocalClockDomain(self.domain.token, self.domain.process_id)
        self.assertEqual(clone, self.domain)
        self.assertIsNot(clone, self.domain)
        self.assertFalse(valid_domain(clone))
        self.assertTrue(valid_domain(self.domain))
        self.assertFalse(revoke_clock_domain(clone))
        self.assertIsNone(rotate_clock_domain(clone))
        self.assertTrue(valid_domain(self.domain))

    def test_cloned_clock_domain_cannot_map_receipt(self):
        clone = replace(self.domain)
        self.assertIsNone(to_market_observation(self.rest, self.receipt_utc, clone))
        self.assertIsNotNone(to_market_observation(self.rest, self.receipt_utc, self.domain))

    def test_cloned_domain_is_rejected_by_unified_report(self):
        clone = replace(self.domain)
        result = self.report(domain=clone)
        self.assertIs(result.transport_state, TransportState.INVALID)
        self.assertIs(result.consistency, Consistency.INVALID_INPUT)
        self.assertEqual(result.reason_codes, ("inactive_clock_domain",))
        self.assertFalse(result.snapshot_consistent)

    def test_admission_flags_are_not_public_constructor_arguments(self):
        report = self.report()
        for name in ("accepted", "recommendation_eligible", "stage2_complete"):
            self.assertFalse(next(f for f in fields(Stage2OfflineReport) if f.name == name).init)
            with self.subTest(field=name):
                with self.assertRaises((ValueError, TypeError)):
                    replace(report, **{name: True})
                with self.assertRaises(FrozenInstanceError):
                    setattr(report, name, True)

    def test_stale_and_disconnected_are_local_not_verified(self):
        for overrides, expected in (
            ({"now_ns": 16_000_000_000}, Consistency.STALE),
            ({"session": replace(self.session, connected=False)}, Consistency.DISCONNECTED),
            ({"ws": replace(self.ws, mark_px=Decimal("3200"))}, Consistency.MISMATCH),
            ({"session": replace(self.session, epoch="another")}, Consistency.INVALID_INPUT),
            ({}, Consistency.CONSISTENT),
        ):
            with self.subTest(expected=expected):
                result = self.report(**overrides)
                self.assertIs(result.consistency, expected)
                self.assertIn("missing_exchange_provenance", result.reason_codes)

    def test_rotation_rejects_old_domain_and_preserves_deny(self):
        original = self.report()
        new = rotate_clock_domain(self.domain)
        self.assertIsNotNone(new)
        self.addCleanup(revoke_clock_domain, new)
        self.assertFalse(valid_domain(self.domain))
        self.assertTrue(valid_domain(new))
        later = self.report()
        self.assertFalse(later.domain_active_at_check)
        self.assertFalse(later.snapshot_consistent)
        self.assertFalse(original.accepted)
        self.assertFalse(later.accepted)


if __name__ == "__main__":
    unittest.main()
