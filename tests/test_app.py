import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen, Request
from app import Handler, eth_market

class FakeResponse:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, *args): return self.data.read(*args)
    def __init__(self, payload): self.data = io.BytesIO(json.dumps(payload).encode())

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_health(self):
        with urlopen(self.base + "/health") as response:
            self.assertEqual(json.load(response)["status"], "ok")

    def test_root_health(self):
        with urlopen(self.base + "/") as response:
            self.assertEqual(json.load(response)["version"], "0.2.1")

    def test_head_health(self):
        with urlopen(Request(self.base + "/health", method="HEAD")) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")

    def test_freshness_not_claimed(self):
        payload = [{"universe": [{"name": "ETH"}]}, [{"markPx": "3000"}]]
        result = eth_market(fetch=lambda request, timeout: FakeResponse(payload))
        self.assertFalse(result["exchange_freshness_verified"])
        self.assertIsNone(result["exchange_timestamp"])

    def test_invalid_price_variants(self):
        for price in ("0", "-1", "Infinity", "bad", None):
            with self.subTest(price=price):
                payload = [{"universe": [{"name": "ETH"}]}, [{"markPx": price}]]
                with self.assertRaises(ValueError):
                    eth_market(fetch=lambda request, timeout: FakeResponse(payload))

    def test_mismatched_contexts(self):
        payload = [{"universe": [{"name": "ETH"}]}, []]
        with self.assertRaises(ValueError):
            eth_market(fetch=lambda request, timeout: FakeResponse(payload))

    def test_duplicate_eth(self):
        payload = [{"universe": [{"name": "ETH"}, {"name": "ETH"}]},
                   [{"markPx": "1"}, {"markPx": "2"}]]
        with self.assertRaises(ValueError):
            eth_market(fetch=lambda request, timeout: FakeResponse(payload))

    def test_http_error(self):
        with patch("app.eth_market", side_effect=HTTPError("url", 429, "rate limit", {}, None)):
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.base + "/market/eth")
            self.assertEqual(caught.exception.code, 503)

    def test_unknown_route(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/unknown")
        self.assertEqual(caught.exception.code, 404)

    def test_market_mapping(self):
        payload = [{"universe": [{"name": "BTC"}, {"name": "ETH"}]},
                   [{"markPx": "50000"}, {"markPx": "3000.25"}]]
        market = eth_market(fetch=lambda request, timeout: FakeResponse(payload))
        self.assertEqual(market["mark_price"], "3000.25")
        self.assertEqual(market["market_type"], "perpetual")

    def test_invalid_price(self):
        payload = [{"universe": [{"name": "ETH"}]}, [{"markPx": "NaN"}]]
        with self.assertRaises(ValueError):
            eth_market(fetch=lambda request, timeout: FakeResponse(payload))

    def test_upstream_failure(self):
        with patch("app.eth_market", side_effect=TimeoutError):
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.base + "/market/eth")
            self.assertEqual(caught.exception.code, 503)
            self.assertEqual(json.load(caught.exception)["error"], "market_data_unavailable")

if __name__ == "__main__":
    unittest.main()
