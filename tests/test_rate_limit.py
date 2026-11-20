import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import app

class RateLimitTests(unittest.TestCase):
    def setUp(self):
        app._market_cache = None
        app._market_cache_until = 0
        app._rate_limit_until = 0

    def test_cache_prevents_duplicate_upstream_requests(self):
        value = {"mark_price": "123", "recommendation_eligible": False}
        with patch.object(app, "eth_market", return_value=value) as fetch:
            self.assertEqual(app.cached_eth_market(), value)
            self.assertEqual(app.cached_eth_market(), value)
            fetch.assert_called_once()

    def test_429_enters_cooldown_and_does_not_retry(self):
        error = HTTPError(app.UPSTREAM, 429, "Too Many Requests", {"Retry-After": "120"}, None)
        with patch.object(app, "eth_market", side_effect=error) as fetch:
            with self.assertRaises(HTTPError):
                app.cached_eth_market()
            with self.assertRaises(app.MarketRateLimited):
                app.cached_eth_market()
            fetch.assert_called_once()
            self.assertGreater(app._rate_limit_until, 0)

    def test_expired_cache_is_not_served_on_failure(self):
        app._market_cache = {"mark_price": "999"}
        app._market_cache_until = 0
        with patch.object(app, "eth_market", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                app.cached_eth_market()

    def test_recovery_after_cooldown(self):
        app._rate_limit_until = 0
        with patch.object(app, "eth_market", return_value={"recommendation_eligible": False}):
            self.assertFalse(app.cached_eth_market()["recommendation_eligible"])

if __name__ == "__main__":
    unittest.main()
