"""Offline integration checks for research-only REST/WS diagnostic API."""
import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import urlopen

import app
from observation_adapter import ETHObservationAdapter


class FakeResponse:
    def __init__(self, payload):
        self.data = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        return self.data.read(*args)


class ObservationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.adapter = ETHObservationAdapter(clock=lambda: self.now)

    def market(self, price="2000"):
        return {"symbol": "ETH/USDC", "price_type": "mark", "mark_price": price}

    def ws(self, price="2000", coin="ETH"):
        return {"channel": "activeAssetCtx", "data": {"coin": coin, "ctx": {"markPx": price}}}

    def test_rest_and_ws_independent(self):
        self.adapter.ingest_rest_market(self.market())
        self.assertEqual(self.adapter.snapshot()["ws_health"], "disconnected")
        gen = self.adapter.connect_ws()
        self.assertEqual(self.adapter.snapshot()["rest_health"], "unknown")
        self.adapter.ingest_rest_market(self.market())
        self.assertTrue(self.adapter.ingest_ws(self.ws(), gen))
        self.assertEqual(self.adapter.snapshot()["overall_health"], "healthy")
        self.assertFalse(self.adapter.snapshot()["recommendation_eligible"])

    def test_unrelated_ws_messages_ignored(self):
        gen = self.adapter.connect_ws()
        self.assertFalse(self.adapter.ingest_ws(self.ws(coin="BTC"), gen))
        self.assertFalse(self.adapter.ingest_ws({"channel": "trades", "data": {}}, gen))
        self.assertEqual(self.adapter.snapshot()["ws_health"], "unknown")

    def test_invalid_ws_price_fails_closed(self):
        gen = self.adapter.connect_ws()
        with self.assertRaises(ValueError):
            self.adapter.ingest_ws(self.ws("NaN"), gen)
        self.assertEqual(self.adapter.snapshot()["ws_health"], "degraded")
        self.assertFalse(self.adapter.snapshot()["recommendation_eligible"])

    def test_reconnect_rejects_stale_generation(self):
        first = self.adapter.connect_ws()
        second = self.adapter.connect_ws()
        self.assertFalse(self.adapter.ingest_ws(self.ws(), first))
        self.assertFalse(self.adapter.disconnect_ws(first))
        self.assertTrue(self.adapter.ingest_ws(self.ws(), second))
        self.assertTrue(self.adapter.disconnect_ws(second))
        self.assertEqual(self.adapter.snapshot()["overall_health"], "disconnected")

    def test_stale_and_recovery(self):
        gen = self.adapter.connect_ws()
        self.adapter.ingest_rest_market(self.market())
        self.adapter.ingest_ws(self.ws(), gen)
        self.now = 30
        self.assertEqual(self.adapter.snapshot()["overall_health"], "stale")
        self.adapter.ingest_rest_market(self.market())
        self.adapter.ingest_ws(self.ws(), gen)
        self.assertEqual(self.adapter.snapshot()["overall_health"], "healthy")

    def test_reject_wrong_rest_market(self):
        with self.assertRaises(ValueError):
            self.adapter.ingest_rest_market({"symbol": "BTC/USDC", "price_type": "mark", "mark_price": "1"})

    def test_diagnostic_disagreement_does_not_block_health(self):
        gen = self.adapter.connect_ws()
        self.adapter.ingest_rest_market(self.market("2100"))
        self.adapter.ingest_ws(self.ws("2000"), gen)
        result = self.adapter.snapshot()
        self.assertTrue(result["asynchronous_price_disagreement"])
        self.assertEqual(result["overall_health"], "healthy")
        self.assertFalse(result["recommendation_eligible"])


class ObservationEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%s" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        app._observation_adapter = ETHObservationAdapter()
        with app._market_lock:
            app._market_cache = None
            app._market_cache_until = 0
            app._rate_limit_until = 0
            app._next_upstream_attempt = 0
            app._consecutive_429 = 0

    def test_endpoint_is_local_and_fail_closed(self):
        with patch("app.eth_market", side_effect=AssertionError("must not fetch")):
            with urlopen(self.base + "/market/observation-health") as response:
                result = json.load(response)
        self.assertEqual(result["ws_health"], "disconnected")
        self.assertFalse(result["recommendation_eligible"])
        self.assertFalse(result["mark_price_freshness_verified"])
        self.assertIsNone(result["exchange_timestamp"])

    def test_cache_hit_does_not_create_new_rest_observation(self):
        upstream_market = {
            "symbol": "ETH/USDC", "price_type": "mark", "mark_price": "2000",
            "exchange_timestamp": None, "exchange_freshness_verified": False,
            "mark_price_freshness_verified": False, "recommendation_eligible": False,
        }
        with patch("app.eth_market", return_value=dict(upstream_market)) as upstream:
            first = app.cached_eth_market()
            before = app._observation_adapter.monitor.last_rest_seen
            second = app.cached_eth_market()
            after = app._observation_adapter.monitor.last_rest_seen
        self.assertEqual(upstream.call_count, 1)
        self.assertEqual(before, after)
        self.assertEqual(first["mark_price"], second["mark_price"])
        self.assertFalse(second["recommendation_eligible"])

    def test_existing_market_endpoint_preserves_block(self):
        payload = [{"universe": [{"name": "ETH"}]}, [{"markPx": "2000"}]]
        with patch("app.urlopen", return_value=FakeResponse(payload)):
            with urlopen(self.base + "/market/eth") as response:
                market = json.load(response)
        self.assertEqual(market["mark_price"], "2000")
        self.assertFalse(market["recommendation_eligible"])
        self.assertIsNone(market["exchange_timestamp"])


if __name__ == "__main__":
    unittest.main()
