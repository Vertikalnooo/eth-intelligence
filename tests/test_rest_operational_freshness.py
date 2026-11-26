"""No-network regression suite for Stage 2 REST operational-only telemetry."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import legacy_market_fixture as app
import rest_operational as rest

MARKET = {
    "symbol": "ETH/USDC",
    "market_type": "perpetual",
    "source": "hyperliquid",
    "price_type": "mark",
    "mark_price": "3000.25",
    "observed_at": "2099-01-01T00:00:00+00:00",  # Ignored.
    "exchange_timestamp": None,
    "exchange_freshness_verified": False,
    "mark_price_freshness_verified": False,
    "freshness_status": "unknown",
    "recommendation_eligible": False,
}


class RestOperationalFreshnessTests(unittest.TestCase):
    def setUp(self):
        with app._market_lock:
            app._market_cache = None
            app._market_cache_until = 0.0
            app._rate_limit_until = 0.0
            app._consecutive_429 = 0
            app._next_upstream_attempt = 0.0
            app._last_rest_receipt = None

    def tearDown(self):
        self.setUp()

    def test_01_no_data_does_not_fetch_or_authorize(self):
        with patch.object(app, "eth_market", side_effect=AssertionError("network forbidden")) as fetch:
            result = app.market_status()
        fetch.assert_not_called()
        diag = result["rest_operational_freshness"]
        self.assertEqual(diag["state"], "NO_DATA")
        self.assertIsNone(diag["age_ms"])
        self.assertIn("no_rest_receipt", diag["reason_codes"])
        self.assertFalse(result["recommendation_eligible"])
        self.assertFalse(result["stage2_complete"])
        self.assertFalse(diag["exchange_freshness_verified"])

    def test_02_monotonic_boundaries_and_stale_age(self):
        receipt = rest.capture(MARKET, 1_000_000_000, 2_000_000_000)
        self.assertIsNotNone(receipt.observation)
        for ns, state, age in (
            (4_000_000_000, "FRESH", 2000),
            (4_000_000_001, "DEGRADED", 2000),
            (7_000_000_000, "DEGRADED", 5000),
            (7_000_000_001, "STALE", 5000),
            (32_000_000_000, "STALE", 30000),
        ):
            with self.subTest(ns=ns):
                value = rest.inspect(receipt, ns, cache_valid=True)
                self.assertEqual(value["state"], state)
                self.assertEqual(value["age_ms"], age)
                self.assertFalse(value["recommendation_eligible"])

    def test_03_forged_source_fields_never_promote(self):
        edits = (
            {"exchange_timestamp": 1890000000000},
            {"exchange_freshness_verified": True},
            {"mark_price_freshness_verified": True},
            {"recommendation_eligible": True},
            {"mark_price": "NaN"},
            {"mark_price": "Infinity"},
            {"mark_price": "-10"},
            {"mark_price": "9" * 161},
            {"price_type": "mid"},
            {"source": "unknown"},
            {"symbol": "BTC/USDC"},
        )
        for edit in edits:
            with self.subTest(edit=edit):
                record = rest.capture({**MARKET, **edit}, 0, 1_000_000_000)
                state = rest.inspect(record, 1_100_000_000, cache_valid=True)
                self.assertEqual(state["state"], "INVALID")
                self.assertFalse(state["exchange_freshness_verified"])
                self.assertFalse(state["recommendation_eligible"])

    def test_04_time_reversal_or_fork_fail_closed(self):
        record = rest.capture(MARKET, 0, 1_000_000_000)
        reversed_clock = rest.inspect(record, 999_999_999, cache_valid=True)
        self.assertEqual(reversed_clock["state"], "INVALID")
        self.assertIn("invalid_clock_domain_or_age", reversed_clock["reason_codes"])
        with patch.object(rest.os, "getpid", return_value=rest._PROCESS_ID + 1):
            after_fork = rest.inspect(record, 1_500_000_000, cache_valid=True)
            self.assertEqual(after_fork["state"], "INVALID")
            self.assertIn("clock_domain_changed", after_fork["reason_codes"])

    def test_05_expired_cache_cannot_claim_fresh(self):
        record = rest.capture(MARKET, 0, 1_000_000_000)
        invalid = rest.inspect(record, 1_010_000_000, cache_valid=False)
        self.assertEqual(invalid["state"], "INVALID")
        self.assertIn("cache_unavailable", invalid["reason_codes"])
        stale = rest.inspect(record, 10_000_000_000, cache_valid=False)
        self.assertEqual(stale["state"], "STALE")
        self.assertIn("cache_unavailable", stale["reason_codes"])

    def test_06_cache_hit_never_refreshes_rest_receipt(self):
        with patch.object(app, "eth_market", return_value=dict(MARKET)) as fetch:
            first = app.cached_eth_market()
            with app._market_lock:
                recorded = app._last_rest_receipt
            second = app.cached_eth_market()
        fetch.assert_called_once()
        self.assertEqual(first, second)
        with app._market_lock:
            self.assertIs(app._last_rest_receipt, recorded)
        self.assertEqual(app.market_status()["rest_operational_freshness"]["state"], "FRESH")
        self.assertFalse(first["recommendation_eligible"])

    def test_07_rate_limit_revokes_receipt_and_disables_data(self):
        with patch.object(app, "eth_market", return_value=dict(MARKET)):
            app.cached_eth_market()
        with app._market_lock:
            app._market_cache_until = 0.0
            app._next_upstream_attempt = 0.0
        with patch.object(app, "eth_market", side_effect=HTTPError("url", 429, "limited", {}, None)):
            with self.assertRaises(HTTPError):
                app.cached_eth_market()
        status = app.market_status()
        self.assertEqual(status["state"], "cooldown")
        self.assertEqual(status["rest_operational_freshness"]["state"], "NO_DATA")
        self.assertIn("upstream_cooldown", status["rest_operational_freshness"]["reason_codes"])
        self.assertFalse(status["recommendation_eligible"])

    def test_08_transient_error_keeps_old_receipt_but_not_fresh(self):
        with patch.object(app, "eth_market", return_value=dict(MARKET)):
            app.cached_eth_market()
        with app._market_lock:
            receipt = app._last_rest_receipt
            app._market_cache_until = 0.0
            app._next_upstream_attempt = 0.0
        with patch.object(app, "eth_market", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                app.cached_eth_market()
        with app._market_lock:
            self.assertIs(app._last_rest_receipt, receipt)
        status = app.market_status()
        self.assertEqual(status["rest_operational_freshness"]["state"], "INVALID")
        self.assertFalse(status["recommendation_eligible"])

    def test_09_http_status_remains_additive_local_and_read_only(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(app, "eth_market", side_effect=AssertionError("network forbidden")) as fetch:
                with urlopen(f"http://127.0.0.1:{server.server_port}/market/status") as response:
                    payload = json.load(response)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get("Cache-Control"), "no-store")
                with urlopen(f"http://127.0.0.1:{server.server_port}/health") as response:
                    self.assertEqual(json.load(response)["version"], "0.2.1")
            fetch.assert_not_called()
            self.assertIn("cache_valid", payload)
            self.assertIn("rest_operational_freshness", payload)
            self.assertFalse(payload["exchange_freshness_verified"])
            self.assertFalse(payload["recommendation_eligible"])
            self.assertFalse(payload["stage2_complete"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_10_parallel_fetch_status_uses_same_lock(self):
        started = threading.Event()
        release = threading.Event()
        completed = []
        errors = []
        def delayed_market():
            started.set()
            if not release.wait(timeout=3):
                raise AssertionError("test timeout")
            return dict(MARKET)
        def fetch():
            try:
                app.cached_eth_market()
            except Exception as exc:
                errors.append(type(exc).__name__)
        with patch.object(app, "eth_market", side_effect=delayed_market):
            worker = threading.Thread(target=fetch)
            worker.start()
            self.assertTrue(started.wait(timeout=2))
            status_worker = threading.Thread(target=lambda: completed.append(app.market_status()))
            status_worker.start()
            try:
                self.assertEqual(completed, [])
            finally:
                release.set()
                worker.join(timeout=3)
                status_worker.join(timeout=3)
        self.assertEqual(errors, [])
        self.assertEqual(len(completed), 1)
        self.assertIn(completed[0]["rest_operational_freshness"]["state"],
                      ("FRESH", "DEGRADED"))
        self.assertFalse(completed[0]["recommendation_eligible"])

    def test_11_incomplete_mocked_snapshot_fails_closed(self):
        record = rest.capture({"mark_price": "3000"}, 1, 2)
        value = rest.inspect(record, 3, cache_valid=True)
        self.assertEqual(value["state"], "INVALID")
        self.assertIn("invalid_rest_snapshot", value["reason_codes"])

    def test_12_adversarial_objects_never_run_equality_or_value_methods(self):
        class Hostile:
            def __eq__(self, other):
                raise AssertionError("caller equality invoked")
            def __str__(self):
                raise AssertionError("caller coercion invoked")
        for name in ("symbol", "source", "market_type", "price_type"):
            with self.subTest(name=name):
                record = rest.capture({**MARKET, name: Hostile()}, 1, 2)
                self.assertEqual(record.error_code, "invalid_rest_snapshot")
                self.assertEqual(rest.inspect(record, 3, cache_valid=True)["state"], "INVALID")
        class HostileKey(str):
            pass
        sample = dict(MARKET)
        sample[HostileKey("unexpected")] = "value"
        self.assertEqual(rest.capture(sample, 1, 2).error_code,
                         "invalid_rest_snapshot")


if __name__ == "__main__":
    unittest.main()
