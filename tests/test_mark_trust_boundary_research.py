"""Offline adversarial contract tests; never contact a price source."""
import unittest
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from freshness import Observation, WsSession
from trust_research import ClaimState, MarkUpdateClaim, inspect_mark_update_claim

REST = Observation("rest-1", "ETH-PERP", "mark", "REST", Decimal("3000"),
                   10_000_000_000, "research-domain",
                   request_start_monotonic_ns=9_000_000_000,
                   request_end_monotonic_ns=9_900_000_000)
WS = Observation("ws-1", "ETH-PERP", "mark", "WS", Decimal("3000"),
                 10_000_000_000, "research-domain", connection_epoch="epoch-1")
SESSION = WsSession("epoch-1", True, "research-domain")
NOW = 10_000_000_000
UTC = datetime(2026, 11, 22, tzinfo=timezone.utc)


class MarkClaimBoundaryTests(unittest.TestCase):
    def check_closed(self, result):
        self.assertFalse(result.exchange_provenance_verified)
        self.assertFalse(result.recommendation_eligible)
        self.assertNotIn("VERIFIED", ClaimState.__members__)

    def test_01_no_evidence(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST), NOW, "research-domain")
        self.assertEqual(r.state, ClaimState.UNVERIFIED)
        self.assertIn("mark_update_time_unproven", r.reason_codes)
        self.check_closed(r)

    def test_02_asserted_update_timestamp_not_proof(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST, "e1", UTC, "mark_update"),
                                      NOW, "research-domain")
        self.assertEqual(r.state, ClaimState.UNVERIFIED)
        self.assertIn("caller_timestamp_untrusted", r.reason_codes)
        self.check_closed(r)

    def test_03_forged_opaque_proof_not_trusted(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST, "e1", UTC,
                                                     "mark_update", b"fake-signature"),
                                      NOW, "research-domain")
        self.assertIn("caller_evidence_untrusted", r.reason_codes)
        self.check_closed(r)

    def test_04_block_timestamp_cannot_substitute(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST, "e1", UTC, "block"),
                                      NOW, "research-domain")
        self.assertIn("unrelated_timestamp_semantics", r.reason_codes)
        self.check_closed(r)

    def test_05_trade_timestamp_cannot_substitute(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST, "e1", UTC, "trade"),
                                      NOW, "research-domain")
        self.assertIn("unrelated_timestamp_semantics", r.reason_codes)
        self.check_closed(r)

    def test_06_receipt_timestamp_cannot_substitute(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST, "e1", UTC, "receipt"),
                                      NOW, "research-domain")
        self.assertIn("unrelated_timestamp_semantics", r.reason_codes)
        self.check_closed(r)

    def test_07_disconnected_ws_not_verified(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(WS), NOW, "research-domain",
                                      session=replace(SESSION, connected=False))
        self.assertEqual(r.state, ClaimState.UNVERIFIED)
        self.assertIn("ws_disconnected", r.reason_codes)
        self.check_closed(r)

    def test_08_old_ws_epoch_invalid(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(WS), NOW, "research-domain",
                                      session=replace(SESSION, epoch="epoch-2"))
        self.assertEqual(r.state, ClaimState.INVALID)
        self.assertIn("old_ws_epoch", r.reason_codes)
        self.check_closed(r)

    def test_09_wrong_clock_domain_invalid(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST), NOW, "another-domain")
        self.assertEqual(r.state, ClaimState.INVALID)
        self.check_closed(r)

    def test_10_stale_receipt_not_verified(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST), 16_000_000_000,
                                      "research-domain")
        self.assertIn("transport_stale", r.reason_codes)
        self.check_closed(r)

    def test_11_degraded_receipt_not_verified(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST), 13_000_000_000,
                                      "research-domain")
        self.assertIn("transport_degraded", r.reason_codes)
        self.check_closed(r)

    def test_12_missing_ws_session_invalid(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(WS), NOW, "research-domain")
        self.assertEqual(r.state, ClaimState.INVALID)
        self.check_closed(r)

    def test_13_invalid_event_id(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST, " "), NOW,
                                      "research-domain")
        self.assertEqual(r.state, ClaimState.INVALID)
        self.check_closed(r)

    def test_14_naive_utc_invalid(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST, asserted_at_utc=datetime(2026, 11, 22)),
                                      NOW, "research-domain")
        self.assertEqual(r.state, ClaimState.INVALID)
        self.check_closed(r)

    def test_15_arbitrary_claim_type_invalid(self):
        r = inspect_mark_update_claim(object(), NOW, "research-domain")
        self.assertEqual(r.state, ClaimState.INVALID)
        self.check_closed(r)

    def test_16_malformed_price_invalid(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(replace(REST, mark_px=Decimal("NaN"))),
                                      NOW, "research-domain")
        self.assertEqual(r.state, ClaimState.INVALID)
        self.check_closed(r)

    def test_17_fresh_ws_does_not_prove_exchange_time(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(WS, "e1", UTC, "mark_update"),
                                      NOW, "research-domain", session=SESSION)
        self.assertEqual(r.state, ClaimState.UNVERIFIED)
        self.check_closed(r)

    def test_18_synthetic_result_has_no_acceptance_field(self):
        r = inspect_mark_update_claim(MarkUpdateClaim(REST), NOW, "research-domain")
        self.assertFalse(hasattr(r, "accepted"))
        self.check_closed(r)


if __name__ == "__main__":
    unittest.main()
