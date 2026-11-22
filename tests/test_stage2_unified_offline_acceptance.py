"""Cross-module Stage 2 acceptance tests: deterministic, offline, deny-only."""
import os
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import stage2_acceptance as acceptance
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import app
from freshness import Consistency, ExchangeState, Observation, TransportState, WsSession
from integration_harness import (
    new_clock_domain, revoke_clock_domain, rotate_clock_domain, valid_domain)
from provenance.models import VerifiedBinding
from provenance.replay import ClaimResult, SQLiteReplayStore
from stage2_acceptance import inspect_stage2_offline
from trust_research import ClaimState, MarkUpdateClaim

NOW = 10_000_000_000
UTC = datetime(2026, 11, 22, tzinfo=timezone.utc)


class Stage2UnifiedAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.domain = new_clock_domain()
        self.addCleanup(revoke_clock_domain, self.domain)
        self.rest = Observation(
            "rest", "ETH-PERP", "mark", "REST", Decimal("3000"), NOW,
            self.domain.token, request_start_monotonic_ns=9_000_000_000,
            request_end_monotonic_ns=9_900_000_000)
        self.ws = Observation(
            "ws", "ETH-PERP", "mark", "WS", Decimal("3000"), NOW,
            self.domain.token, connection_epoch="epoch-1")
        self.session = WsSession("epoch-1", True, self.domain.token)
        self.binding = VerifiedBinding(
            "rest", "Hyperliquid", "ETH", "mark", Decimal("3000"),
            "event-1", UTC, "mark_update")

    def run_report(self, **kw):
        args = dict(rest=self.rest, ws=self.ws, now_ns=NOW,
                    domain=self.domain, session=self.session, receipt_utc=UTC)
        args.update(kw)
        result = inspect_stage2_offline(**args)
        self.assertIs(result.accepted, False)
        self.assertIs(result.recommendation_eligible, False)
        self.assertIs(result.stage2_complete, False)
        self.assertIsNot(result.exchange_state, ExchangeState.VERIFIED_FRESH)
        self.assertNotEqual(result.trust_claim_state.value, "VERIFIED")
        return result

    def test_01_fresh_matching_pair_still_deny(self):
        r = self.run_report()
        self.assertEqual(r.consistency, Consistency.CONSISTENT)
        self.assertEqual(r.transport_state, TransportState.FRESH)
        self.assertEqual(r.trust_claim_state, ClaimState.UNVERIFIED)
        self.assertTrue(r.adapter_valid)

    def test_02_mismatched_price_still_deny(self):
        r = self.run_report(ws=replace(self.ws, mark_px=Decimal("3100")))
        self.assertEqual(r.consistency, Consistency.MISMATCH)
        self.assertIn("rest_ws_mismatch", r.reason_codes)

    def test_03_stale_pair_still_deny(self):
        r = self.run_report(now_ns=16_000_000_000)
        self.assertEqual(r.consistency, Consistency.STALE)
        self.assertEqual(r.transport_state, TransportState.STALE)

    def test_04_degraded_pair_not_comparable(self):
        r = self.run_report(now_ns=13_000_000_000)
        self.assertEqual(r.consistency, Consistency.NOT_COMPARABLE)

    def test_05_disconnected_ws_blocks_pair(self):
        r = self.run_report(session=replace(self.session, connected=False))
        self.assertEqual(r.consistency, Consistency.DISCONNECTED)
        self.assertIn("rest_ws_disconnected", r.reason_codes)

    def test_06_reconnect_old_epoch_invalid(self):
        r = self.run_report(session=replace(self.session, epoch="epoch-2"))
        self.assertEqual(r.consistency, Consistency.INVALID_INPUT)
        self.assertIn("rest_ws_ws_old_ws_epoch", r.reason_codes)

    def test_07_future_local_receipt_invalid(self):
        r = self.run_report(ws=replace(self.ws, received_monotonic_ns=NOW+1))
        self.assertEqual(r.consistency, Consistency.INVALID_INPUT)

    def test_08_invalid_price_invalid_adapter(self):
        r = self.run_report(rest=replace(self.rest, mark_px=Decimal("NaN")))
        self.assertFalse(r.adapter_valid)
        self.assertEqual(r.transport_state, TransportState.INVALID)
        self.assertIn("adapter_invalid", r.reason_codes)

    def test_09_bad_exchange_timestamp_never_promoted(self):
        fake = replace(self.rest, exchange_timestamp=datetime(2026, 11, 22))
        r = self.run_report(rest=fake)
        self.assertEqual(r.transport_state, TransportState.INVALID)

    def test_10_untrusted_mark_update_claim(self):
        r = self.run_report(claim=MarkUpdateClaim(
            self.rest, "event-1", UTC, "mark_update", b"fake-signature"))
        self.assertEqual(r.trust_claim_state, ClaimState.UNVERIFIED)
        self.assertIn("caller_evidence_untrusted", r.reason_codes)

    def test_11_invalid_metadata_over_stale(self):
        r = self.run_report(now_ns=16_000_000_000,
                            claim=MarkUpdateClaim(self.rest, event_id=" "))
        self.assertEqual(r.trust_claim_state, ClaimState.INVALID)
        self.assertIn("invalid_claimed_event_id", r.reason_codes)

    def test_12_untrusted_block_timestamp(self):
        r = self.run_report(claim=MarkUpdateClaim(
            self.rest, "event-1", UTC, "block"))
        self.assertIn("unrelated_timestamp_semantics", r.reason_codes)

    def test_13_revoke_domain_denies_all(self):
        self.assertTrue(revoke_clock_domain(self.domain))
        r = self.run_report()
        self.assertFalse(r.domain_active_at_check)
        self.assertFalse(r.adapter_valid)
        self.assertEqual(r.consistency, Consistency.INVALID_INPUT)

    def test_14_rotate_domain_invalidates_old_observation(self):
        successor = rotate_clock_domain(self.domain)
        self.addCleanup(revoke_clock_domain, successor)
        self.assertTrue(valid_domain(successor))
        r = self.run_report()
        self.assertEqual(r.reason_codes, ("inactive_clock_domain",))

    def test_15_replay_claim_still_deny(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            r = self.run_report(store=store, binding=self.binding,
                                allow_synthetic_replay_claim=True)
            self.assertEqual(r.replay_result, ClaimResult.CLAIMED)

    def test_16_replay_duplicate_still_deny(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            self.run_report(store=store, binding=self.binding,
                            allow_synthetic_replay_claim=True)
            r = self.run_report(store=store, binding=self.binding,
                                allow_synthetic_replay_claim=True)
            self.assertEqual(r.replay_result, ClaimResult.DUPLICATE)

    def test_17_replay_conflicting_digest_still_deny(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            self.run_report(store=store, binding=self.binding,
                            allow_synthetic_replay_claim=True)
            r = self.run_report(store=store,
                                binding=replace(self.binding, mark_px=Decimal("3001")),
                                allow_synthetic_replay_claim=True)
            self.assertEqual(r.replay_result, ClaimResult.UNAVAILABLE)
            self.assertIn("synthetic_replay_not_authorized", r.reason_codes)

    def test_18_replay_removed_storage_still_deny(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            os.unlink(store.path)
            r = self.run_report(store=store, binding=self.binding,
                                allow_synthetic_replay_claim=True)
            self.assertEqual(r.replay_result, ClaimResult.UNAVAILABLE)
            self.assertFalse(os.path.exists(store.path))

    def test_19_replay_absent_still_deny(self):
        r = self.run_report()
        self.assertEqual(r.replay_result, ClaimResult.UNAVAILABLE)

    def test_20_invalid_trust_claim_type_still_deny(self):
        r = self.run_report(claim=object())
        self.assertEqual(r.trust_claim_state, ClaimState.INVALID)
        self.assertIn("invalid_claim_type", r.reason_codes)

    def test_22_default_replay_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            report = self.run_report(store=store, binding=self.binding)
            self.assertEqual(report.replay_result, ClaimResult.UNAVAILABLE)
            claimed = self.run_report(store=store, binding=self.binding,
                                      allow_synthetic_replay_claim=True)
            self.assertEqual(claimed.replay_result, ClaimResult.CLAIMED)

    def test_23_stale_never_consumes_replay(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocked = self.run_report(store=store, binding=self.binding,
                                      now_ns=16_000_000_000,
                                      allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            fresh = self.run_report(store=store, binding=self.binding,
                                    allow_synthetic_replay_claim=True)
            self.assertEqual(fresh.replay_result, ClaimResult.CLAIMED)

    def test_24_mismatch_never_consumes_replay(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocked = self.run_report(store=store, binding=self.binding,
                                      ws=replace(self.ws, mark_px=Decimal("3200")),
                                      allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            fresh = self.run_report(store=store, binding=self.binding,
                                    allow_synthetic_replay_claim=True)
            self.assertEqual(fresh.replay_result, ClaimResult.CLAIMED)

    def test_25_invalid_metadata_never_consumes_replay(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            invalid = MarkUpdateClaim(self.rest, event_id=" ")
            blocked = self.run_report(store=store, binding=self.binding,
                                      claim=invalid,
                                      allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.trust_claim_state, ClaimState.INVALID)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(self.run_report(
                store=store, binding=self.binding,
                allow_synthetic_replay_claim=True).replay_result, ClaimResult.CLAIMED)

    def test_26_binding_observation_mismatch_never_consumes(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocked = self.run_report(
                store=store, binding=replace(self.binding, observation_id="ws"),
                allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(self.run_report(
                store=store, binding=self.binding,
                allow_synthetic_replay_claim=True).replay_result, ClaimResult.CLAIMED)

    def test_27_binding_event_mismatch_never_consumes(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            claim = MarkUpdateClaim(self.rest, "different", UTC, "mark_update")
            blocked = self.run_report(
                store=store, binding=self.binding, claim=claim,
                allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(self.run_report(
                store=store, binding=self.binding,
                allow_synthetic_replay_claim=True).replay_result, ClaimResult.CLAIMED)

    def test_28_binding_timestamp_mismatch_never_consumes(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            claim = MarkUpdateClaim(self.rest, "event-1",
                                    datetime(2026, 11, 21, tzinfo=timezone.utc), "mark_update")
            blocked = self.run_report(
                store=store, binding=self.binding, claim=claim,
                allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(self.run_report(
                store=store, binding=self.binding,
                allow_synthetic_replay_claim=True).replay_result, ClaimResult.CLAIMED)

    def test_29_disconnected_ws_never_consumes_replay(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocked = self.run_report(
                store=store, binding=self.binding,
                session=replace(self.session, connected=False),
                allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(self.run_report(
                store=store, binding=self.binding,
                allow_synthetic_replay_claim=True).replay_result, ClaimResult.CLAIMED)

    def test_30_non_bool_opt_in_never_claims(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocked = self.run_report(
                store=store, binding=self.binding,
                allow_synthetic_replay_claim=1)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(self.run_report(
                store=store, binding=self.binding,
                allow_synthetic_replay_claim=True).replay_result, ClaimResult.CLAIMED)

    def test_31_revoke_serialized_with_whole_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        real_map = acceptance.to_market_observation
        def blocking_map(*args):
            entered.set()
            if not release.wait(4):
                raise TimeoutError("release timeout")
            return real_map(*args)
        with ThreadPoolExecutor(max_workers=2) as pool:
            with patch.object(acceptance, "to_market_observation", side_effect=blocking_map):
                report_future = pool.submit(self.run_report)
                try:
                    self.assertTrue(entered.wait(3))
                    revoke_future = pool.submit(revoke_clock_domain, self.domain)
                    self.assertFalse(revoke_future.done())
                finally:
                    release.set()
                report = report_future.result(4)
                self.assertTrue(report.domain_active_at_check)
                self.assertTrue(report.snapshot_consistent)
                self.assertTrue(revoke_future.result(4))
        self.assertFalse(valid_domain(self.domain))
        self.assertEqual(self.run_report().reason_codes, ("inactive_clock_domain",))

    def test_32_old_epoch_never_consumes_replay(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocked = self.run_report(
                store=store, binding=self.binding,
                session=replace(self.session, epoch="other"),
                allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(self.run_report(
                store=store, binding=self.binding,
                allow_synthetic_replay_claim=True).replay_result, ClaimResult.CLAIMED)

    def test_21_production_gate_unchanged(self):
        live = app.assess_market_recommendation({
            "exchange_timestamp": 1800000000000,
            "exchange_freshness_verified": True,
            "mark_price_freshness_verified": True})
        self.assertFalse(live["recommendation_eligible"])
        self.assertEqual(live["recommendation_block_reason"],
                         "mark_price_provenance_not_implemented")


if __name__ == "__main__":
    unittest.main()
