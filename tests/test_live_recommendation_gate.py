"""Safety regression tests: live market API never authorizes unverified mark price."""
import unittest
from unittest.mock import patch
import app


class LiveRecommendationGateTests(unittest.TestCase):
    def test_missing_timestamp_blocks(self):
        result = app.assess_market_recommendation({"exchange_timestamp": None})
        self.assertFalse(result["recommendation_eligible"])
        self.assertEqual(result["recommendation_block_reason"], "missing_exchange_timestamp")

    def test_unverified_exchange_blocks(self):
        result = app.assess_market_recommendation({"exchange_timestamp": 1800000000000, "exchange_freshness_verified": False})
        self.assertFalse(result["recommendation_eligible"])

    def test_unverified_mark_blocks(self):
        result = app.assess_market_recommendation({"exchange_timestamp": 1800000000000, "exchange_freshness_verified": True})
        self.assertEqual(result["recommendation_block_reason"], "unverified_mark_price_freshness")

    def test_even_claimed_verified_cannot_bypass_missing_provenance(self):
        result = app.assess_market_recommendation({"exchange_timestamp": 1800000000000, "exchange_freshness_verified": True, "mark_price_freshness_verified": True})
        self.assertFalse(result["recommendation_eligible"])

    def test_live_cached_market_applies_gate(self):
        with app._market_lock:
            app._market_cache = None
            app._market_cache_until = 0.0
            app._rate_limit_until = 0.0
            app._next_upstream_attempt = 0.0
            app._consecutive_429 = 0
        with patch.object(app, "eth_market", return_value={"symbol": "ETH/USDC", "exchange_timestamp": None, "exchange_freshness_verified": False}):
            response = app.cached_eth_market()
        self.assertFalse(response["recommendation_eligible"])
        self.assertEqual(response["recommendation_block_reason"], "missing_exchange_timestamp")
        with app._market_lock:
            app._market_cache = None
            app._market_cache_until = 0.0
            app._rate_limit_until = 0.0
            app._next_upstream_attempt = 0.0
            app._consecutive_429 = 0


if __name__ == "__main__":
    unittest.main()
