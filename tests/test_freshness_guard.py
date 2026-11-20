"""Offline deterministic stale-data and fail-closed regression tests."""
import unittest
from dataclasses import replace
from freshness_guard import FreshnessGuard, MarketObservation

NOW_MS = 1795180000000
NOW_MONO = 1000.0
BASE = MarketObservation("1734.1", NOW_MS - 1000, NOW_MONO - 1, True)

class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.guard = FreshnessGuard()
        self.guard.connect()

    def check_blocked(self, observation, expected):
        self.guard.ingest(observation)
        result = self.guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)
        self.assertEqual(result["freshness_status"], expected)
        self.assertFalse(result["recommendation_eligible"])
        self.assertFalse(result["exchange_freshness_verified"])

    def test_unverified_source(self):
        self.check_blocked(replace(BASE, source_verified=False), "unverified_source")

    def test_missing_timestamp(self):
        self.check_blocked(replace(BASE, exchange_timestamp_ms=None), "missing_exchange_timestamp")

    def test_stale_exchange(self):
        self.check_blocked(replace(BASE, exchange_timestamp_ms=NOW_MS-10001), "stale_exchange")

    def test_stale_receive(self):
        self.check_blocked(replace(BASE, received_monotonic=NOW_MONO-5.01), "stale_receive")

    def test_future_timestamp(self):
        self.check_blocked(replace(BASE, exchange_timestamp_ms=NOW_MS+2001), "future_exchange_timestamp")

    def test_mid_price_not_mark(self):
        self.check_blocked(replace(BASE, price_type="mid"), "wrong_price_type")

    def test_invalid_price(self):
        for price in ("NaN", "Infinity", "-1", "0", "bad"):
            with self.subTest(price=price):
                self.check_blocked(replace(BASE, price=price), "invalid_price")

    def test_disconnect_and_reconnect_clears_data(self):
        self.guard.ingest(BASE)
        self.guard.disconnect()
        self.assertEqual(self.guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)["freshness_status"], "disconnected")
        self.guard.connect()
        self.assertEqual(self.guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)["freshness_status"], "no_data")

    def test_fresh_does_not_authorize_recommendation(self):
        self.guard.ingest(BASE)
        result = self.guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)
        self.assertEqual(result["freshness_status"], "fresh")
        self.assertTrue(result["freshness_gate_passed"])
        self.assertFalse(result["recommendation_eligible"])

    def test_stale_then_new_data(self):
        self.guard.ingest(replace(BASE, exchange_timestamp_ms=NOW_MS-11000))
        self.assertEqual(self.guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)["freshness_status"], "stale_exchange")
        self.guard.ingest(BASE)
        self.assertEqual(self.guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)["freshness_status"], "fresh")

    def test_no_data(self):
        self.assertEqual(self.guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)["freshness_status"], "no_data")

    def test_clock_rollback(self):
        self.guard.ingest(BASE)
        self.assertEqual(self.guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO-2)["freshness_status"], "stale_receive")

if __name__ == "__main__":
    unittest.main()
