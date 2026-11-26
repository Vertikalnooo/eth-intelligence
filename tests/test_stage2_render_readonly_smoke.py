"""Two-endpoint Render smoke stays read-only, bounded and explicitly opt-in."""
import copy
import json
import os
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import stage2_render_readonly_smoke as smoke


class FakeResponse:
    def __init__(self, body, code=200):
        self.body = body
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def getcode(self):
        return self.code

    def read(self, n):
        return self.body[:n]


class RenderReadOnlySmokeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.health = {"status": "ok", "service": "eth-intelligence",
                       "version": "0.2.1"}
        self.status = {
            "source": "hyperliquid", "state": "ready_to_retry",
            "retry_after_seconds": 0, "consecutive_rate_limits": 0,
            "next_upstream_attempt_seconds": 0,
            "cache_valid": False, "cache_remaining_seconds": 0,
            "upstream_request_allowed": True, "throttle_reason": None,
            "exchange_freshness_verified": False,
            "recommendation_eligible": False, "stage2_complete": False,
            "rest_operational_freshness": {
                "state": "NO_DATA", "age_ms": None,
                "clock_basis": "process_local_monotonic", "source_channel": "REST",
                "reason_codes": ["no_rest_receipt", "missing_exchange_provenance"],
                "exchange_freshness_verified": False,
                "recommendation_eligible": False, "stage2_complete": False,
            },
        }

    def fetch(self, req, timeout):
        self.calls.append((req.full_url, req.get_method(), timeout))
        payload = self.health if req.full_url.endswith("/health") else self.status
        return FakeResponse(json.dumps(payload).encode())

    def test_01_readonly_routes_only(self):
        report = smoke.inspect(fetch=self.fetch)
        self.assertEqual(report["status"], "DIAGNOSTICS_PASS")
        self.assertEqual(self.calls, [
            (smoke.BASE + "/health", "GET", 5),
            (smoke.BASE + "/market/status", "GET", 5),
        ])
        self.assertFalse(report["exchange_mark_verified"])
        self.assertFalse(report["source_reliability_verified"])
        self.assertFalse(report["recommendation_eligible"])
        self.assertFalse(report["stage2_complete"])

    def test_02_no_redirects_allowed(self):
        self.assertIsNone(smoke.NoRedirect().redirect_request(
            None, None, 302, "moved", {}, "https://api.hyperliquid.xyz/info"))

    def test_03_disallows_foreign_urls(self):
        for url in ("http://eth-intelligence-api.onrender.com/health",
                    "https://api.hyperliquid.xyz/info",
                    "https://eth-intelligence-api.onrender.com/market/eth",
                    "https://eth-intelligence-api.onrender.com.evil/health",
                    "https://eth-intelligence-api.onrender.com/health?x=1",
                    "https://user:pass@eth-intelligence-api.onrender.com/health"):
            with self.subTest(url=url):
                self.assertFalse(smoke.validate_route_url(url))

    def test_04_cooldown_is_not_healthy_source_evidence(self):
        self.status["state"] = "cooldown"
        self.status["retry_after_seconds"] = 30
        self.status["consecutive_rate_limits"] = 1
        self.status["throttle_reason"] = "rate_limit"
        self.status["upstream_request_allowed"] = False
        self.status["rest_operational_freshness"]["reason_codes"].append("upstream_cooldown")
        report = smoke.inspect(fetch=self.fetch)
        self.assertEqual(report["status"], "DIAGNOSTICS_PASS")
        self.assertFalse(report["source_reliability_verified"])

    def test_05_forged_verification_flags_fail(self):
        for key in smoke.DENY_FLAGS:
            with self.subTest(key=key):
                self.status[key] = True
                self.calls.clear()
                report = smoke.inspect(fetch=self.fetch)
                self.assertEqual(report["status"], "FAILED")
                self.assertEqual([c[0] for c in self.calls],
                                 [smoke.BASE + "/health",
                                  smoke.BASE + "/market/status"])
                self.status[key] = False

    def test_06_non200_and_oversize_fail_closed(self):
        def error_fetch(req, timeout):
            return FakeResponse(b"{}", code=503)
        self.assertEqual(smoke.inspect(fetch=error_fetch)["status"], "FAILED")
        def too_large(req, timeout):
            return FakeResponse(b"x" * (smoke.MAX_BYTES + 1))
        self.assertEqual(smoke.inspect(fetch=too_large)["status"], "FAILED")

    def test_07_bad_schema_fail_closed(self):
        self.status["retry_after_seconds"] = -1
        self.assertEqual(smoke.inspect(fetch=self.fetch)["status"], "FAILED")

    def test_08_without_optin_no_network(self):
        with patch.dict(os.environ, {"STAGE2_LIVE_READONLY_APPROVED": ""}):
            with patch.object(sys, "argv", ["smoke", "--live-readonly"]):
                with patch.object(smoke, "inspect", side_effect=AssertionError("network called")):
                    self.assertEqual(smoke.main(), 2)

    def test_09_default_plan_does_not_call_network(self):
        with patch.object(sys, "argv", ["smoke"]):
            with patch.object(smoke, "inspect", side_effect=AssertionError("network called")):
                self.assertEqual(smoke.main(), 0)

    def test_10_all_nested_denial_flags_require_exact_false(self):
        for key in smoke.DENY_FLAGS:
            for forged in (True, 0, None, "false"):
                with self.subTest(key=key, forged=forged):
                    value = copy.deepcopy(self.status)
                    value["rest_operational_freshness"][key] = forged
                    self.assertFalse(smoke.verify_payload("/market/status", value))

    def test_11_every_contract_key_is_required(self):
        for nested in (False, True):
            keys = (self.status["rest_operational_freshness"] if nested
                    else self.status)
            for key in keys:
                with self.subTest(nested=nested, key=key):
                    value = copy.deepcopy(self.status)
                    target = value["rest_operational_freshness"] if nested else value
                    del target[key]
                    self.assertFalse(smoke.verify_payload("/market/status", value))

    def test_12_rejects_unknown_fields_and_invalid_types(self):
        for key, bad in (
            ("retry_after_seconds", False), ("cache_remaining_seconds", -1),
            ("consecutive_rate_limits", 1.5), ("cache_valid", 0),
            ("next_upstream_attempt_seconds", "0"),
            ("upstream_request_allowed", 1), ("stage2_complete", 0),
            ("throttle_reason", []), ("state", None),
            ("rest_operational_freshness", []), ("extra", True),
        ):
            with self.subTest(key=key, bad=bad):
                value = copy.deepcopy(self.status)
                value[key] = bad
                self.assertFalse(smoke.verify_payload("/market/status", value))
        for key, bad in (
            ("age_ms", False), ("age_ms", -1), ("age_ms", 1.5),
            ("clock_basis", "exchange_utc"), ("source_channel", "WS"),
            ("state", "VERIFIED_FRESH"), ("reason_codes", "missing_exchange_provenance"),
            ("reason_codes", ["no_rest_receipt"]),
            ("reason_codes", ["no_rest_receipt", "missing_exchange_provenance",
                              "missing_exchange_provenance"]),
            ("reason_codes", ["no_rest_receipt", "missing_exchange_provenance", "unknown"]),
            ("reason_codes", [["no_rest_receipt"]]), ("extra", True),
        ):
            with self.subTest(key=key, bad=bad):
                value = copy.deepcopy(self.status)
                value["rest_operational_freshness"][key] = bad
                self.assertFalse(smoke.verify_payload("/market/status", value))

    def test_13_accepts_all_actual_receipt_states_and_floor_boundaries(self):
        cases = (
            ("NO_DATA", None, False, ["no_rest_receipt"]),
            ("INVALID", None, True, ["invalid_rest_snapshot"]),
            ("INVALID", 10, False, ["cache_unavailable"]),
            ("FRESH", 0, True, []),
            ("FRESH", 2000, True, []),
            ("DEGRADED", 2000, True, ["transport_degraded"]),
            ("DEGRADED", 5000, True, ["transport_degraded"]),
            ("STALE", 5000, True, ["transport_stale"]),
            ("STALE", 30001, False, ["transport_stale", "cache_unavailable"]),
        )
        for state, age, cache, reasons in cases:
            with self.subTest(state=state, age=age, cache=cache):
                value = copy.deepcopy(self.status)
                value["cache_valid"] = cache
                value["cache_remaining_seconds"] = 1 if cache else 0
                value["rest_operational_freshness"].update(
                    state=state, age_ms=age,
                    reason_codes=reasons + ["missing_exchange_provenance"])
                self.assertTrue(smoke.verify_payload("/market/status", value))

    def test_14_rejects_inconsistent_diagnostics(self):
        for state, age, cache, reasons in (
            ("NO_DATA", 0, False, ["no_rest_receipt"]),
            ("NO_DATA", None, True, ["no_rest_receipt"]),
            ("FRESH", 2001, True, []),
            ("FRESH", 0, False, []),
            ("DEGRADED", 1999, True, ["transport_degraded"]),
            ("DEGRADED", 5001, True, ["transport_degraded"]),
            ("STALE", 4999, True, ["transport_stale"]),
            ("STALE", 6000, False, ["transport_stale"]),
            ("STALE", None, True, ["transport_stale"]),
            ("INVALID", None, True, []),
        ):
            with self.subTest(state=state, age=age, cache=cache):
                value = copy.deepcopy(self.status)
                value["cache_valid"] = cache
                value["cache_remaining_seconds"] = 1 if cache else 0
                value["rest_operational_freshness"].update(
                    state=state, age_ms=age,
                    reason_codes=reasons + ["missing_exchange_provenance"])
                self.assertFalse(smoke.verify_payload("/market/status", value))

    def test_15_rejects_inconsistent_throttle_fields(self):
        for edits in (
            {"state": "cooldown", "retry_after_seconds": 30},
            {"retry_after_seconds": 30},
            {"upstream_request_allowed": False},
            {"throttle_reason": "rate_limit"},
            {"throttle_reason": "request_interval"},
            {"next_upstream_attempt_seconds": 5},
            {"cache_remaining_seconds": 1},
        ):
            with self.subTest(edits=edits):
                self.assertFalse(smoke.verify_payload("/market/status",
                    {**self.status, **edits}))
        value = {**self.status, "upstream_request_allowed": False,
                 "throttle_reason": "request_interval",
                 "next_upstream_attempt_seconds": 5}
        self.assertTrue(smoke.verify_payload("/market/status", value))

    def test_16_rejects_ambiguous_or_malformed_json(self):
        good = json.dumps(self.status)
        nested_duplicate = good.replace('"age_ms": null',
                                        '"age_ms": null, "age_ms": null')
        for raw in (
            b'{"status":"ok","status":"ok"}',
            b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}',
            b'\xff', b'{', b'[' * 1500 + b']' * 1500,
            nested_duplicate.encode(),
            good.replace('"stage2_complete": false',
                         '"stage2_complete": true, "stage2_complete": false', 1).encode(),
        ):
            with self.subTest(raw=raw[:60]):
                def fetch(req, timeout):
                    if req.full_url.endswith("/health"):
                        return FakeResponse(json.dumps(self.health).encode())
                    return FakeResponse(raw)
                self.assertEqual(smoke.inspect(fetch=fetch)["status"], "FAILED")

    def test_17_exact_route_allowlist_rejects_malformed_urls_without_throwing(self):
        for url in (None, 1, "https://[invalid", smoke.BASE + ":bad/health",
                    smoke.BASE + ":443/health", smoke.BASE + "/health#",
                    smoke.BASE + "/health?", smoke.BASE + "/%68ealth",
                    " " + smoke.BASE + "/health"):
            self.assertFalse(smoke.validate_route_url(url))

    def test_18_real_app_status_producer_round_trip_offline(self):
        import legacy_market_fixture as app
        snapshot = {
            "symbol": "ETH/USDC", "source": "hyperliquid",
            "market_type": "perpetual", "price_type": "mark",
            "mark_price": "3000.25", "exchange_timestamp": None,
            "exchange_freshness_verified": False,
            "mark_price_freshness_verified": False,
            "recommendation_eligible": False,
        }
        with patch.multiple(app, _market_cache=None, _market_cache_until=0.0,
                            _rate_limit_until=0.0, _consecutive_429=0,
                            _next_upstream_attempt=0.0, _last_rest_receipt=None):
            with patch.object(app, "eth_market", return_value=snapshot) as fetch:
                self.assertTrue(smoke.verify_payload("/market/status", app.market_status()))
                fetch.assert_not_called()
                app.cached_eth_market()
                result = json.loads(json.dumps(app.market_status()))
                self.assertTrue(smoke.verify_payload("/market/status", result))
                fetch.assert_called_once_with()

    def test_19_nested_failure_propagates_to_smoke_report(self):
        self.status["rest_operational_freshness"]["stage2_complete"] = True
        result = smoke.inspect(fetch=self.fetch)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["route"], "/market/status")
        self.assertIs(result["recommendation_eligible"], False)
        self.assertIs(result["stage2_complete"], False)

    def test_20_invalid_receipt_cache_reason_matrix(self):
        cases = (
            # Legitimate early failures are independent of cache availability.
            (None, False, ["invalid_rest_snapshot"], True),
            (None, True, ["clock_domain_changed"], True),
            (None, False, ["invalid_clock_domain_or_age", "cache_unavailable"], True),
            (None, True, ["invalid_input"], True),
            (0, False, ["cache_unavailable"], True),
            (2000, False, ["cache_unavailable"], True),
            (2000, False, ["cache_unavailable", "transport_degraded"], True),
            (5000, False, ["cache_unavailable", "transport_degraded"], True),
            (10, True, ["cache_unavailable"], False),
            (None, True, ["invalid_input", "cache_unavailable"], False),
            (None, False, ["invalid_input"], False),
            (None, False, ["cache_unavailable"], False),
            (None, False, ["invalid_rest_snapshot", "cache_unavailable"], False),
            (None, True, ["invalid_rest_snapshot", "invalid_rest_mark"], False),
            (100, False, ["invalid_rest_mark"], False),
            (2001, False, ["cache_unavailable"], False),
            (1999, False, ["cache_unavailable", "transport_degraded"], False),
            (5001, False, ["cache_unavailable", "transport_degraded"], False),
            (None, False, ["invalid_input", "transport_degraded"], False),
        )
        for age, cache, reasons, expected in cases:
            with self.subTest(age=age, cache=cache, reasons=reasons):
                value = copy.deepcopy(self.status)
                value.update(cache_valid=cache,
                             cache_remaining_seconds=1 if cache else 0)
                value["rest_operational_freshness"].update(
                    state="INVALID", age_ms=age,
                    reason_codes=reasons + ["missing_exchange_provenance"])
                self.assertIs(smoke.verify_payload("/market/status", value), expected)

    def test_21_active_429_cooldown_rejects_retained_receipts(self):
        value = copy.deepcopy(self.status)
        value.update(state="cooldown", retry_after_seconds=30,
                     consecutive_rate_limits=1, upstream_request_allowed=False,
                     throttle_reason="rate_limit")
        diag = value["rest_operational_freshness"]
        diag["reason_codes"].append("upstream_cooldown")
        self.assertTrue(smoke.verify_payload("/market/status", value))
        for state, age, reasons in (
            ("INVALID", None, ["invalid_rest_snapshot"]),
            ("INVALID", 100, ["cache_unavailable"]),
            ("FRESH", 10, []),
            ("DEGRADED", 3000, ["transport_degraded"]),
            ("STALE", 6000, ["transport_stale", "cache_unavailable"]),
        ):
            with self.subTest(state=state):
                diag.update(state=state, age_ms=age, reason_codes=reasons +
                            ["upstream_cooldown", "missing_exchange_provenance"])
                self.assertFalse(smoke.verify_payload("/market/status", value))

    def test_22_expired_cooldown_does_not_restore_receipt(self):
        value = copy.deepcopy(self.status)
        value["consecutive_rate_limits"] = 2
        self.assertTrue(smoke.verify_payload("/market/status", value))
        diag = value["rest_operational_freshness"]
        diag.update(state="STALE", age_ms=60000,
                    reason_codes=["transport_stale", "cache_unavailable",
                                  "missing_exchange_provenance"])
        self.assertFalse(smoke.verify_payload("/market/status", value))
        value["consecutive_rate_limits"] = 0
        self.assertTrue(smoke.verify_payload("/market/status", value))

    def test_23_real_app_429_expiry_and_recovery_offline(self):
        import legacy_market_fixture as app
        snapshot = {
            "symbol": "ETH/USDC", "source": "hyperliquid",
            "market_type": "perpetual", "price_type": "mark",
            "mark_price": "3000.25", "exchange_timestamp": None,
            "exchange_freshness_verified": False,
            "mark_price_freshness_verified": False,
            "recommendation_eligible": False,
        }
        now = [100.0]
        with patch.multiple(app, _market_cache=None, _market_cache_until=0.0,
                            _rate_limit_until=0.0, _consecutive_429=0,
                            _next_upstream_attempt=0.0, _last_rest_receipt=None), \
             patch.object(app.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(app.time, "monotonic_ns", side_effect=lambda: int(now[0] * 1e9)), \
             patch.object(app.random, "uniform", return_value=0), \
             patch.object(app, "eth_market", side_effect=[
                 dict(snapshot), HTTPError("mock", 429, "limited", {}, None),
                 dict(snapshot)]) as fetch:
            app.cached_eth_market()
            self.assertTrue(smoke.verify_payload("/market/status", app.market_status()))
            now[0] = 131.0
            with self.assertRaises(HTTPError):
                app.cached_eth_market()
            during = app.market_status()
            self.assertEqual(during["state"], "cooldown")
            self.assertEqual(during["rest_operational_freshness"]["state"], "NO_DATA")
            self.assertTrue(smoke.verify_payload("/market/status", during))
            now[0] = 192.0
            expired = app.market_status()
            self.assertEqual(expired["state"], "ready_to_retry")
            self.assertEqual(expired["consecutive_rate_limits"], 1)
            self.assertEqual(expired["rest_operational_freshness"]["state"], "NO_DATA")
            self.assertTrue(smoke.verify_payload("/market/status", expired))
            app.cached_eth_market()
            recovered = app.market_status()
            self.assertEqual(recovered["consecutive_rate_limits"], 0)
            self.assertEqual(recovered["rest_operational_freshness"]["state"], "FRESH")
            self.assertTrue(smoke.verify_payload("/market/status", recovered))
            self.assertEqual(fetch.call_count, 3)


if __name__ == "__main__":
    unittest.main()
