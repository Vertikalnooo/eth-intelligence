"""Two-endpoint Render smoke stays read-only, bounded and explicitly opt-in."""
import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

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
        self.status = {"source": "hyperliquid", "state": "ready_to_retry",
                       "retry_after_seconds": 0,
                       "exchange_freshness_verified": False,
                       "recommendation_eligible": False,
                       "upstream_request_allowed": True}

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
        self.status["upstream_request_allowed"] = False
        report = smoke.inspect(fetch=self.fetch)
        self.assertEqual(report["status"], "DIAGNOSTICS_PASS")
        self.assertFalse(report["source_reliability_verified"])

    def test_05_forged_verification_flags_fail(self):
        for key in ("exchange_freshness_verified", "recommendation_eligible"):
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


if __name__ == "__main__":
    unittest.main()
