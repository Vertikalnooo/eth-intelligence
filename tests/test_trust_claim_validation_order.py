"""P2 validation-order regressions; no exchange or network access."""
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from freshness import Observation, WsSession
from trust_research import ClaimState, MarkUpdateClaim, inspect_mark_update_claim


REST = Observation(
    "r", "ETH-PERP", "mark", "REST", Decimal("3000"),
    10_000_000_000, "domain",
    request_start_monotonic_ns=9_000_000_000,
    request_end_monotonic_ns=9_900_000_000,
)
WS = Observation(
    "w", "ETH-PERP", "mark", "WS", Decimal("3000"),
    10_000_000_000, "domain", connection_epoch="epoch",
)
DISCONNECTED = WsSession("epoch", False, "domain")
FRESH_SESSION = WsSession("epoch", True, "domain")
UTC = datetime(2026, 11, 22, tzinfo=timezone.utc)


class ValidationOrderTests(unittest.TestCase):
    def inspect(self, claim, now=10_000_000_000, session=None):
        result = inspect_mark_update_claim(claim, now, "domain", session=session)
        self.assertFalse(result.exchange_provenance_verified)
        self.assertFalse(result.recommendation_eligible)
        self.assertFalse(hasattr(result, "accepted"))
        return result

    def test_01_stale_invalid_id_rejected_before_transport(self):
        result = self.inspect(MarkUpdateClaim(REST, event_id=" "), 16_000_000_000)
        self.assertEqual(result.state, ClaimState.INVALID)
        self.assertEqual(result.reason_codes, ("invalid_claimed_event_id",))

    def test_02_stale_naive_utc_rejected(self):
        result = self.inspect(MarkUpdateClaim(REST, asserted_at_utc=datetime(2026, 11, 22)),
                              16_000_000_000)
        self.assertEqual(result.state, ClaimState.INVALID)
        self.assertIn("invalid_claimed_utc", result.reason_codes)

    def test_03_disconnected_ws_invalid_semantics_rejected(self):
        result = self.inspect(MarkUpdateClaim(WS, semantic_label="other"), session=DISCONNECTED)
        self.assertEqual(result.state, ClaimState.INVALID)
        self.assertIn("invalid_timestamp_semantics", result.reason_codes)

    def test_04_disconnected_ws_malformed_id_rejected(self):
        result = self.inspect(MarkUpdateClaim(WS, event_id=123), session=DISCONNECTED)
        self.assertEqual(result.state, ClaimState.INVALID)
        self.assertIn("invalid_claimed_event_id", result.reason_codes)

    def test_05_multiple_bad_metadata_fields_all_reported(self):
        result = self.inspect(MarkUpdateClaim(
            REST, event_id=" ", asserted_at_utc=datetime(2026, 11, 22),
            semantic_label="unknown"), 16_000_000_000)
        self.assertEqual(result.state, ClaimState.INVALID)
        self.assertEqual(result.reason_codes, (
            "invalid_claimed_event_id", "invalid_claimed_utc",
            "invalid_timestamp_semantics",
        ))

    def test_06_valid_stale_claim_keeps_unverified_and_reasons(self):
        result = self.inspect(MarkUpdateClaim(REST, "e1", UTC, "mark_update", b"fake"),
                              16_000_000_000)
        self.assertEqual(result.state, ClaimState.UNVERIFIED)
        for code in ("transport_stale", "mark_update_time_unproven",
                     "caller_timestamp_untrusted", "caller_evidence_untrusted"):
            self.assertIn(code, result.reason_codes)

    def test_07_valid_disconnected_claim_keeps_unverified_and_reasons(self):
        result = self.inspect(MarkUpdateClaim(WS, "e1", UTC, "mark_update", b"fake"),
                              session=DISCONNECTED)
        self.assertEqual(result.state, ClaimState.UNVERIFIED)
        self.assertIn("ws_disconnected", result.reason_codes)
        self.assertIn("caller_evidence_untrusted", result.reason_codes)

    def test_08_old_epoch_invalid_after_valid_metadata(self):
        result = self.inspect(MarkUpdateClaim(WS, "e1", UTC, "mark_update"),
                              session=replace(FRESH_SESSION, epoch="new"))
        self.assertEqual(result.state, ClaimState.INVALID)
        self.assertIn("old_ws_epoch", result.reason_codes)

    def test_09_degraded_receipt_still_unverified(self):
        result = self.inspect(MarkUpdateClaim(REST), 13_000_000_000)
        self.assertEqual(result.state, ClaimState.UNVERIFIED)
        self.assertIn("transport_degraded", result.reason_codes)
        self.assertIn("mark_update_time_unproven", result.reason_codes)

    def test_10_fresh_synthetic_evidence_never_authorizes(self):
        result = self.inspect(MarkUpdateClaim(WS, "e1", UTC, "mark_update", b"fake"),
                              session=FRESH_SESSION)
        self.assertEqual(result.state, ClaimState.UNVERIFIED)
        self.assertIn("caller_evidence_untrusted", result.reason_codes)


if __name__ == "__main__":
    unittest.main()
