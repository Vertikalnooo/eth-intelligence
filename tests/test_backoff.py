import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import legacy_market_fixture as app

class BackoffTests(unittest.TestCase):
    def setUp(self):
        with app._market_lock:
            app._market_cache = None
            app._market_cache_until = 0
            app._rate_limit_until = 0
            app._consecutive_429 = 0
            app._next_upstream_attempt = 0.0

    def tearDown(self):
        self.setUp()

    def test_backoff_and_recovery(self):
        now = [1000.0]
        calls = [0]
        def upstream():
            calls[0] += 1
            if calls[0] <= 3:
                raise HTTPError("url", 429, "limited", {}, None)
            return {"mark_price": "3000", "recommendation_eligible": False}
        with patch("legacy_market_fixture.time.monotonic", side_effect=lambda: now[0]), patch("legacy_market_fixture.random.uniform", return_value=0), patch("legacy_market_fixture.eth_market", side_effect=upstream):
            for count, delay in enumerate((60, 120, 240), 1):
                with self.assertRaises(HTTPError):
                    app.cached_eth_market()
                self.assertEqual(app.market_status()["retry_after_seconds"], delay)
                with self.assertRaises(app.MarketRateLimited):
                    app.cached_eth_market()
                self.assertEqual(calls[0], count)
                now[0] += delay
            self.assertEqual(app.cached_eth_market()["mark_price"], "3000")
            self.assertEqual(app._consecutive_429, 0)
            self.assertEqual(app.cached_eth_market()["mark_price"], "3000")
            self.assertEqual(calls[0], 4)

    def test_retry_after_and_diagnostics_are_local(self):
        now = [2000.0]
        error = HTTPError("url", 429, "limited", {"Retry-After": "310"}, None)
        with patch("legacy_market_fixture.time.monotonic", side_effect=lambda: now[0]), patch("legacy_market_fixture.random.uniform", return_value=0), patch("legacy_market_fixture.eth_market", side_effect=error) as upstream:
            with self.assertRaises(HTTPError):
                app.cached_eth_market()
            for _ in range(5):
                state = app.market_status()
                self.assertEqual(state["state"], "cooldown")
                self.assertEqual(state["retry_after_seconds"], 310)
                self.assertFalse(state["recommendation_eligible"])
            self.assertEqual(upstream.call_count, 1)
