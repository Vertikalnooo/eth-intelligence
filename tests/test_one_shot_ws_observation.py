"""Offline contract tests; never opens a real WS or dispatches workflows."""
import importlib.util
import json
import pathlib
import threading
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "one_shot_ws_observation", ROOT / "scripts" / "one_shot_ws_observation.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class FakeObserver:
    def __init__(self, adapter, max_attempts, max_session_seconds):
        assert max_attempts == 1
        assert max_session_seconds == 30
        self.adapter = adapter
        self.error = None

    def run(self):
        generation = self.adapter.connect_ws()
        self.adapter.ingest_ws(
            {"channel": "activeAssetCtx", "data": {
                "coin": "ETH", "ctx": {"markPx": "2000"}}}, generation)
        self.adapter.disconnect_ws(generation)

    def diagnostics(self):
        return {"last_error": self.error, "running": False,
                "recommendation_eligible": False}


class EmptyObserver(FakeObserver):
    def run(self):
        self.error = "transport_error"


class OneShotTests(unittest.TestCase):
    def test_successful_read_only_contract(self):
        report = probe.collect(observer_factory=FakeObserver)
        self.assertTrue(probe.validate_report(report))
        self.assertEqual(report["accepted_eth_observations"], 1)
        self.assertEqual(report["ws_result"], "observed")
        self.assertFalse(report["recommendation_eligible"])
        self.assertIsNone(report["exchange_timestamp"])
        self.assertNotIn("2000", json.dumps(report))

    def test_failed_connection_stays_fail_closed(self):
        report = probe.collect(observer_factory=EmptyObserver)
        self.assertEqual(report["ws_result"], "no_observation")
        self.assertEqual(report["last_error"], "transport_error")
        self.assertFalse(report["mark_price_freshness_verified"])
        self.assertFalse(report["exchange_timestamp_verified"])

    def test_report_rejects_trading_eligibility_and_extra_fields(self):
        report = probe.collect(observer_factory=FakeObserver)
        for key, value in (
            ("recommendation_eligible", True),
            ("exchange_timestamp_verified", True),
            ("mark_price_freshness_verified", True),
            ("exchange_timestamp", 123),
            ("connection_attempt_limit", 2),
            ("session_limit_seconds", 60),
            ("accepted_eth_observations", -1),
            ("ws_result", "no_observation"),
            ("last_error", "SECRET"),
        ):
            with self.subTest(key=key):
                bad = dict(report)
                bad[key] = value
                self.assertFalse(probe.validate_report(bad))
        bad = dict(report)
        bad["raw_payload"] = "SECRET"
        self.assertFalse(probe.validate_report(bad))

    def test_counting_adapter_ignores_non_eth_messages(self):
        adapter = probe.CountingAdapter()
        gen = adapter.connect_ws()
        self.assertFalse(adapter.ingest_ws(
            {"channel": "activeAssetCtx", "data": {
                "coin": "BTC", "ctx": {"markPx": "2000"}}}, gen))
        self.assertEqual(adapter.count(), 0)
        adapter.disconnect_ws(gen)

    def test_counting_adapter_concurrent(self):
        adapter = probe.CountingAdapter()
        gen = adapter.connect_ws()
        def writer():
            for _ in range(100):
                adapter.ingest_ws({"channel": "activeAssetCtx", "data": {
                    "coin": "ETH", "ctx": {"markPx": "2000"}}}, gen)
        workers = [threading.Thread(target=writer) for _ in range(3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(adapter.count(), 300)
        adapter.disconnect_ws(gen)

    def test_main_failure_exit_and_safe_report(self):
        with patch.object(probe, "collect", return_value=probe.collect(
                observer_factory=EmptyObserver)):
            with self.assertRaises(SystemExit) as error:
                probe.main()
        self.assertEqual(error.exception.code, 1)

    def test_workflow_is_manual_only_and_branch_guarded(self):
        workflow = (ROOT / ".github" / "workflows" /
                    "research-one-shot-eth-ws.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("  push:", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("refs/heads/research/read-only-mark-provenance-probe-20261121", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("timeout-minutes: 2", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertIn("scripts/one_shot_ws_observation.py", workflow)


if __name__ == "__main__":
    unittest.main()
