"""Deterministic cache and throttle diagnostics; no exchange requests."""
import unittest
from unittest.mock import patch
import app


class CacheDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        with app._market_lock:
            app._market_cache = None
            app._market_cache_until = 0.0
            app._rate_limit_until = 0.0
            app._consecutive_429 = 0
            app._next_upstream_attempt = 0.0

    def tearDown(self):
        self.setUp()

    def test_cache_survives_minimum_request_interval(self):
        sample = {"mark_price": "3000", "recommendation_eligible": False}
        with patch("app.time.monotonic", return_value=100.0), patch("app.eth_market", return_value=sample) as fetch:
            self.assertEqual(app.cached_eth_market(), sample)
        with patch("app.time.monotonic", return_value=116.0):
            self.assertEqual(app.cached_eth_market(), sample)
            status = app.market_status()
            self.assertTrue(status["cache_valid"])
            self.assertEqual(status["cache_remaining_seconds"], 14)
            self.assertTrue(status["upstream_request_allowed"])
        fetch.assert_called_once()

    def test_interval_is_distinct_from_rate_limit(self):
        with app._market_lock:
            app._next_upstream_attempt = 120.0
        with patch("app.time.monotonic", return_value=110.0):
            status = app.market_status()
            self.assertEqual(status["throttle_reason"], "request_interval")
            self.assertFalse(status["upstream_request_allowed"])
            self.assertEqual(status["retry_after_seconds"], 0)
            self.assertEqual(status["next_upstream_attempt_seconds"], 10)

    def test_rate_limit_takes_priority(self):
        with app._market_lock:
            app._rate_limit_until = 180.0
            app._next_upstream_attempt = 120.0
        with patch("app.time.monotonic", return_value=110.0):
            status = app.market_status()
            self.assertEqual(status["throttle_reason"], "rate_limit")
            self.assertEqual(status["retry_after_seconds"], 70)
            self.assertFalse(status["recommendation_eligible"])


if __name__ == "__main__":
    unittest.main()
