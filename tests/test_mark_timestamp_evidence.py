"""Offline regression tests for mark-timestamp evidence validation."""
import unittest
from dataclasses import replace
from mark_timestamp_evidence import MarkEvidence, assess_mark_evidence

NOW = 1_795_180_000_000
BASE = MarkEvidence("hyperliquid", "ETH", "mark", "2000.5", NOW - 1000,
                    "exchange_mark_tick", True, True)

class EvidenceTests(unittest.TestCase):
    def check_blocked(self, evidence, reason, now=NOW):
        result = assess_mark_evidence(evidence, now_utc_ms=now)
        self.assertEqual(result["status"], reason)
        self.assertFalse(result["recommendation_eligible"])
        self.assertFalse(result["mark_price_freshness_verified"])

    def test_verified_fixture_only(self):
        result = assess_mark_evidence(BASE, now_utc_ms=NOW)
        self.assertTrue(result["mark_price_freshness_verified"])
        self.assertFalse(result["recommendation_eligible"])

    def test_local_receive_time_is_not_provenance(self):
        self.check_blocked(replace(BASE, timestamp_semantics="local_receive_time"),
                           "unverified_timestamp_provenance")

    def test_trade_timestamp_not_mark_timestamp(self):
        self.check_blocked(replace(BASE, timestamp_semantics="exchange_trade_time"),
                           "unverified_timestamp_provenance")

    def test_unbound_or_unverified(self):
        for value in (replace(BASE, timestamp_bound_to_mark=False),
                      replace(BASE, provenance_verified=False)):
            self.check_blocked(value, "unverified_timestamp_provenance")

    def test_missing_timestamp(self):
        self.check_blocked(replace(BASE, tick_timestamp_ms=None), "invalid_timestamp")

    def test_stale_timestamp(self):
        self.check_blocked(replace(BASE, tick_timestamp_ms=NOW-10001), "stale_mark_tick")

    def test_future_timestamp(self):
        self.check_blocked(replace(BASE, tick_timestamp_ms=NOW+2001), "future_timestamp")

    def test_wrong_asset(self):
        self.check_blocked(replace(BASE, asset="BTC"), "wrong_source_or_asset")

    def test_wrong_price_type(self):
        self.check_blocked(replace(BASE, price_type="mid"), "wrong_price_type")

    def test_invalid_price(self):
        for price in ("NaN", "0", "-1", "abc"):
            self.check_blocked(replace(BASE, mark_price=price), "invalid_mark_price")

    def test_boolean_timestamp(self):
        self.check_blocked(replace(BASE, tick_timestamp_ms=True), "invalid_timestamp")

if __name__ == "__main__":
    unittest.main()
