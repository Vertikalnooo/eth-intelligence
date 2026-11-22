"""Cross-module Stage 2 acceptance tests: deterministic, offline, deny-only."""
import os
import tempfile
import unittest
import threading
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import stage2_acceptance as acceptance
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import app
from freshness import Consistency, ExchangeState, Observation, TransportState, WsSession
from integration_harness import (
    new_clock_domain, revoke_clock_domain, rotate_clock_domain, valid_domain, replay_diagnostic)
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
        self.assertIs(result.exchange_state, ExchangeState.UNVERIFIED)
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
            report = self.run_report(store=store, binding=self.binding,
                                     allow_synthetic_replay_claim=True)
            self.assertEqual(report.replay_result, ClaimResult.UNAVAILABLE)
            self.assertTrue(report.binding_structurally_matches)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
    def test_16_replay_duplicate_still_deny(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            self.run_report(store=store, binding=self.binding)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.DUPLICATE)
    def test_17_replay_conflicting_digest_still_deny(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
            self.assertEqual(replay_diagnostic(store, replace(
                self.binding, mark_px=Decimal("3001"))), ClaimResult.CONFLICT)
            report = self.run_report(store=store, binding=replace(
                self.binding, mark_px=Decimal("3001")))
            self.assertFalse(report.binding_structurally_matches)
            self.assertEqual(report.replay_result, ClaimResult.UNAVAILABLE)
    def test_18_replay_removed_storage_still_deny(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            os.unlink(store.path)
            report = self.run_report(store=store, binding=self.binding,
                                     allow_synthetic_replay_claim=True)
            self.assertEqual(report.replay_result, ClaimResult.UNAVAILABLE)
            self.assertFalse(os.path.exists(store.path))
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.UNAVAILABLE)
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
            self.run_report(store=store, binding=self.binding)
            true_report = self.run_report(store=store, binding=self.binding,
                                          allow_synthetic_replay_claim=True)
            self.assertEqual(true_report.replay_result, ClaimResult.UNAVAILABLE)
            self.assertIn("synthetic_replay_disabled", true_report.reason_codes)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
    def test_23_stale_never_consumes_replay(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocked = self.run_report(store=store, binding=self.binding,
                                      now_ns=16_000_000_000,
                                      allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.transport_state, TransportState.STALE)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
    def test_24_mismatch_never_consumes_replay(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocked = self.run_report(store=store, binding=self.binding,
                                      ws=replace(self.ws, mark_px=Decimal("3200")),
                                      allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.consistency, Consistency.MISMATCH)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
    def test_25_invalid_metadata_never_consumes_replay(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            invalid = MarkUpdateClaim(self.rest, event_id=" ")
            blocked = self.run_report(store=store, binding=self.binding,
                                      claim=invalid, allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.trust_claim_state, ClaimState.INVALID)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
    def test_26_binding_observation_mismatch_never_consumes(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocked = self.run_report(
                store=store, binding=replace(self.binding, observation_id="ws"),
                allow_synthetic_replay_claim=True)
            self.assertFalse(blocked.binding_structurally_matches)
            self.assertIn("binding_structural_mismatch", blocked.reason_codes)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
    def test_27_binding_event_mismatch_never_consumes(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            claim = MarkUpdateClaim(self.rest, "different", UTC, "mark_update")
            blocked = self.run_report(
                store=store, binding=self.binding, claim=claim,
                allow_synthetic_replay_claim=True)
            self.assertFalse(blocked.binding_structurally_matches)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
    def test_28_binding_timestamp_mismatch_never_consumes(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            claim = MarkUpdateClaim(self.rest, "event-1",
                                    datetime(2026, 11, 21, tzinfo=timezone.utc), "mark_update")
            blocked = self.run_report(
                store=store, binding=self.binding, claim=claim,
                allow_synthetic_replay_claim=True)
            self.assertFalse(blocked.binding_structurally_matches)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
    def test_29_disconnected_ws_never_consumes_replay(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocked = self.run_report(
                store=store, binding=self.binding,
                session=replace(self.session, connected=False),
                allow_synthetic_replay_claim=True)
            self.assertEqual(blocked.consistency, Consistency.DISCONNECTED)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
    def test_30_even_true_opt_in_never_claims(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            for opt in (False, True, 1, "yes", None):
                blocked = self.run_report(
                    store=store, binding=self.binding,
                    allow_synthetic_replay_claim=opt)
                self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
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
            self.assertEqual(blocked.consistency, Consistency.INVALID_INPUT)
            self.assertEqual(blocked.replay_result, ClaimResult.UNAVAILABLE)
            self.assertEqual(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)
    def test_33_sqlite_write_lock_does_not_block_domain_revoke(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            blocker = sqlite3.connect(store.path, timeout=0.1)
            try:
                blocker.execute("BEGIN EXCLUSIVE")
                report = self.run_report(store=store, binding=self.binding,
                                         allow_synthetic_replay_claim=True)
                self.assertEqual(report.replay_result, ClaimResult.UNAVAILABLE)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(revoke_clock_domain, self.domain)
                    self.assertTrue(future.result(timeout=1))
            finally:
                blocker.rollback()
                blocker.close()

    def test_34_hostile_store_is_never_touched(self):
        class NeverTouch:
            def __getattribute__(self, name):
                raise AssertionError("unified harness touched external store")
        report = self.run_report(store=NeverTouch(), binding=self.binding,
                                 allow_synthetic_replay_claim=True)
        self.assertTrue(report.binding_structurally_matches)
        self.assertEqual(report.replay_result, ClaimResult.UNAVAILABLE)

    def test_35_structural_match_never_proves_exchange(self):
        report = self.run_report(binding=self.binding)
        self.assertTrue(report.binding_structurally_matches)
        self.assertEqual(report.exchange_state, ExchangeState.UNVERIFIED)
        self.assertFalse(report.accepted)

    def test_36_direct_replay_contention_is_standalone(self):
        with tempfile.TemporaryDirectory() as d:
            store = SQLiteReplayStore(os.path.join(d, "claims.db"))
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(
                    lambda _: replay_diagnostic(store, self.binding), range(12)))
            self.assertEqual(results.count(ClaimResult.CLAIMED), 1)
            self.assertEqual(results.count(ClaimResult.DUPLICATE), 11)

    def test_37_fail_closed_on_unexpected_acceptance(self):
        class DangerousAdmission:
            accepted = True
            recommendation_eligible = True
        with patch.object(acceptance, "accept_market_data",
                          return_value=DangerousAdmission()):
            with self.assertRaises(AssertionError):
                inspect_stage2_offline(
                    self.rest, self.ws, NOW, self.domain, self.session, UTC)

    def test_38_rotate_serialized_with_in_memory_snapshot(self):
        entered, release, rotate_started = (threading.Event() for _ in range(3))
        real_map = acceptance.to_market_observation
        def blocking_map(*args):
            entered.set()
            if not release.wait(4):
                raise TimeoutError("release timeout")
            return real_map(*args)
        def rotate_work():
            rotate_started.set()
            return rotate_clock_domain(self.domain)
        with ThreadPoolExecutor(max_workers=2) as pool:
            with patch.object(acceptance, "to_market_observation", side_effect=blocking_map):
                report_future = pool.submit(self.run_report)
                try:
                    self.assertTrue(entered.wait(3))
                    rotate_future = pool.submit(rotate_work)
                    self.assertTrue(rotate_started.wait(3))
                    self.assertFalse(rotate_future.done())
                finally:
                    release.set()
                report = report_future.result(4)
                successor = rotate_future.result(4)
        self.assertTrue(report.snapshot_consistent)
        self.assertTrue(report.domain_active_at_check)
        self.assertIsNotNone(successor)
        self.addCleanup(revoke_clock_domain, successor)
        self.assertFalse(valid_domain(self.domain))
        self.assertTrue(valid_domain(successor))

    def test_39_inactive_domain_never_claims_coherent_snapshot(self):
        self.assertTrue(revoke_clock_domain(self.domain))
        report = self.run_report()
        self.assertFalse(report.domain_active_at_check)
        self.assertFalse(report.snapshot_consistent)
        self.assertFalse(report.binding_structurally_matches)

    def test_swapped_channels_invalidate_the_receipt_adapter(self):
        report = self.run_report(rest=self.ws, ws=self.rest)
        self.assertIs(report.transport_state, TransportState.INVALID)
        self.assertIs(report.consistency, Consistency.INVALID_INPUT)
        self.assertFalse(report.adapter_valid)

    def test_claim_must_refer_to_the_observation_in_the_snapshot(self):
        other_observations = (
            replace(self.rest, observation_id="another-rest"),
            replace(self.ws, mark_px=Decimal("2999")),
        )
        for observation in other_observations:
            with self.subTest(channel=observation.source_channel):
                report = self.run_report(claim=MarkUpdateClaim(observation))
                self.assertIs(report.trust_claim_state, ClaimState.INVALID)
                self.assertIn("claim_observation_outside_snapshot", report.reason_codes)
                self.assertIs(report.consistency, Consistency.CONSISTENT)

    def test_claim_on_current_ws_observation_stays_unverified(self):
        claim = MarkUpdateClaim(self.ws, event_id="event-1")
        report = self.run_report(claim=claim)
        self.assertIs(report.trust_claim_state, ClaimState.UNVERIFIED)
        self.assertIn("caller_event_id_untrusted", report.reason_codes)

    def test_binding_must_match_assertions_on_the_rest_observation(self):
        for rest in (
            replace(self.rest, event_id="different-event"),
            replace(self.rest, exchange_timestamp=UTC.replace(day=21)),
        ):
            with self.subTest(rest=rest):
                report = self.run_report(rest=rest, binding=self.binding)
                self.assertFalse(report.binding_structurally_matches)
                self.assertIn("binding_structural_mismatch", report.reason_codes)

    def test_invalid_claim_fields_do_not_invoke_caller_methods(self):
        class InvalidField:
            def __eq__(self, other):
                raise AssertionError("invalid claim field was compared")

            @property
            def source_channel(self):
                raise AssertionError("unvalidated claim observation was inspected")

        claim = MarkUpdateClaim(InvalidField(), event_id=InvalidField())
        report = self.run_report(claim=claim, binding=self.binding)
        self.assertIs(report.trust_claim_state, ClaimState.INVALID)
        self.assertIn("invalid_claimed_event_id", report.reason_codes)
        self.assertFalse(report.binding_structurally_matches)

        claim = MarkUpdateClaim(self.rest, semantic_label=InvalidField())
        report = self.run_report(claim=claim, binding=self.binding)
        self.assertIn("invalid_timestamp_semantics", report.reason_codes)
        self.assertFalse(report.binding_structurally_matches)

    def test_invalid_binding_fields_do_not_invoke_caller_methods(self):
        class InvalidField:
            def __eq__(self, other):
                raise AssertionError("invalid binding field was compared")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteReplayStore(os.path.join(directory, "claims.db"))
            for field in ("observation_id", "venue", "symbol", "price_type",
                          "timestamp_semantics", "mark_px"):
                with self.subTest(field=field):
                    binding = replace(self.binding, **{field: InvalidField()})
                    report = self.run_report(binding=binding)
                    self.assertFalse(report.binding_structurally_matches)
                    self.assertIs(replay_diagnostic(store, binding), ClaimResult.UNAVAILABLE)
            self.assertIs(replay_diagnostic(store, self.binding), ClaimResult.CLAIMED)

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
