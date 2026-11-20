import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen
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
