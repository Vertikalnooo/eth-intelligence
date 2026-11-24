"""Offline release boundaries for optional Stage 2 research modules.

These are change-detection tests, not a complete security proof.
"""
import ast
from pathlib import Path
import unittest

import app
from provenance.decision_gate import accept_market_data

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = {"freshness", "provenance", "integration_harness",
            "stage2_acceptance", "trust_research"}


class Stage2ReleaseBoundaryTests(unittest.TestCase):
    def test_app_imports_only_the_approved_rest_telemetry_adapter(self):
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(imports & RESEARCH,
                         "Only the reviewed rest_operational adapter may import freshness")
        self.assertIn("rest_operational", imports)
        # Exact minimal adapter: no provenance, replay, network, or WS imports.
        module_tree = ast.parse((ROOT / "rest_operational.py").read_text(encoding="utf-8"))
        module_imports = set()
        for node in ast.walk(module_tree):
            if isinstance(node, ast.Import):
                module_imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_imports.add(node.module.split(".", 1)[0])
        self.assertIn("freshness", module_imports)
        self.assertFalse(module_imports & (RESEARCH - {"freshness"}))
        self.assertFalse(module_imports & {"socket", "subprocess", "urllib",
                                           "requests", "websocket"})

    def test_current_http_handler_defines_no_write_methods(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
            with self.subTest(method=method):
                self.assertFalse(callable(getattr(app.Handler, "do_" + method, None)))

    def test_market_status_never_grants_eligibility(self):
        result = app.market_status()  # reads local in-memory status, no upstream call
        self.assertIs(result["exchange_freshness_verified"], False)
        self.assertIs(result["recommendation_eligible"], False)

    def test_forged_source_verification_cannot_enable_api_recommendation(self):
        for market in (
            {},
            {"exchange_timestamp": 1},
            {"exchange_timestamp": 123, "exchange_freshness_verified": True},
            {"exchange_timestamp": 123, "exchange_freshness_verified": True,
             "mark_price_freshness_verified": True},
            {"exchange_timestamp": True, "exchange_freshness_verified": True,
             "mark_price_freshness_verified": True},
            {"exchange_timestamp": 123, "exchange_freshness_verified": "true",
             "mark_price_freshness_verified": True},
        ):
            with self.subTest(market=market):
                result = app.assess_market_recommendation(market)
                self.assertIs(result["recommendation_eligible"], False)
                self.assertIsInstance(result["recommendation_block_reason"], str)

    def test_production_admission_remains_deny_only(self):
        for provider, clock in ((None, None), (object(), object())):
            with self.subTest(provider_type=type(provider).__name__):
                result = accept_market_data(
                    None, None, provider, clock, None, 0, "untrusted")
                self.assertIs(result.accepted, False)
                self.assertIs(result.recommendation_eligible, False)


if __name__ == "__main__":
    unittest.main()
