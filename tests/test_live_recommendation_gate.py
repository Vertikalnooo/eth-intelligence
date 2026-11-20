"""Safety regression tests: live market API never authorizes unverified mark price."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
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

    def test_http_market_response_blocks_unverified_price(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with app._market_lock:
                app._market_cache = None
                app._market_cache_until = 0.0
                app._rate_limit_until = 0.0
                app._next_upstream_attempt = 0.0
                app._consecutive_429 = 0
            sample = {"symbol": "ETH/USDC", "mark_price": "1700",
                      "exchange_timestamp": None, "exchange_freshness_verified": False}
            with patch.object(app, "eth_market", return_value=sample):
                with urlopen(f"http://127.0.0.1:{server.server_port}/market/eth") as response:
                    self.assertEqual(response.status, 200)
                    payload = json.load(response)
            self.assertFalse(payload["recommendation_eligible"])
            self.assertEqual(payload["recommendation_block_reason"], "missing_exchange_timestamp")
            self.assertEqual(payload["mark_price"], "1700")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            with app._market_lock:
                app._market_cache = None
                app._market_cache_until = 0.0
                app._rate_limit_until = 0.0
                app._next_upstream_attempt = 0.0
                app._consecutive_429 = 0


if __name__ == "__main__":
    unittest.main()
