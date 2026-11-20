"""Regression tests for failure isolation and recovery; no live exchange calls."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from app import Handler


class RecoveryTests(unittest.TestCase):
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

    def test_recovery_after_upstream_outage(self):
        sample = {
            "symbol": "ETH/USDC",
            "market_type": "perpetual",
            "source": "hyperliquid",
            "price_type": "mark",
            "mark_price": "3000",
            "observed_at": "2026-11-20T11:00:00+00:00",
            "exchange_timestamp": None,
            "exchange_freshness_verified": False,
        }
        with patch("app.eth_market", side_effect=[URLError("offline"), sample]):
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.base + "/market/eth")
            self.assertEqual(caught.exception.code, 503)
            self.assertEqual(json.load(caught.exception), {"error": "market_data_unavailable"})
            with urlopen(self.base + "/health") as response:
                self.assertEqual(response.status, 200)
            with urlopen(self.base + "/market/eth") as response:
                self.assertEqual(response.status, 200)
                payload = json.load(response)
                self.assertEqual(payload["mark_price"], "3000")
                self.assertFalse(payload["exchange_freshness_verified"])

    def test_rate_limit_does_not_leak_upstream_response(self):
        with patch("app.eth_market", side_effect=HTTPError("https://api.hyperliquid.xyz/info", 429, "secret detail", {}, None)):
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.base + "/market/eth")
            self.assertEqual(caught.exception.code, 503)
            self.assertEqual(json.load(caught.exception), {"error": "market_data_unavailable"})
            self.assertEqual(caught.exception.headers.get("Cache-Control"), "no-store")

    def test_repeated_timeout_does_not_break_health(self):
        with patch("app.eth_market", side_effect=TimeoutError("upstream timeout")):
            for _ in range(3):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(self.base + "/market/eth")
                self.assertEqual(caught.exception.code, 503)
            with urlopen(self.base + "/health") as response:
                self.assertEqual(json.load(response)["status"], "ok")


if __name__ == "__main__":
    unittest.main()
