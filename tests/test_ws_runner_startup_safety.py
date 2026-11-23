"""Offline WS runner restart-safety regression tests; no external I/O."""
import ast
import io
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import render_ws_stability_runner as safe_runner

ENTRYPOINTS = (
    SCRIPTS / "render_ws_stability_runner.py",
    SCRIPTS / "render_ws_probe_runner.py",
)


class WsRunnerStartupSafetyTests(unittest.TestCase):
    def test_health_only_status_cannot_claim_source_or_exchange_proof(self):
        status = safe_runner.diagnostic_status()
        self.assertEqual(status["probe_state"], "disabled")
        self.assertEqual(status["reason_code"], "manual_probe_requires_separate_approval")
        self.assertEqual(status["exchange_freshness"], "UNVERIFIED")
        for field in ("source_reliability_verified", "recommendation_eligible",
                      "stage2_complete"):
            self.assertIs(status[field], False)

    def test_health_is_liveness_only_and_unknown_routes_do_not_probe(self):
        h = Mock()
        h.path = "/health"
        h.wfile = io.BytesIO()
        safe_runner.Handler.do_GET(h)
        h.send_response.assert_called_once_with(200)
        h.send_header.assert_any_call("Cache-Control", "no-store")
        status = json.loads(h.wfile.getvalue().decode("utf-8"))
        self.assertEqual(status, safe_runner.diagnostic_status())
        h2 = Mock()
        h2.path = "/probe"
        h2.wfile = io.BytesIO()
        safe_runner.Handler.do_GET(h2)
        h2.send_response.assert_called_once_with(404)
        self.assertEqual(h2.wfile.getvalue(), b"")

    def test_both_entrypoints_contain_no_auto_probe_execution_paths(self):
        forbidden = {"subprocess", "threading", "socket",
                     "probe_hyperliquid_ws", "probe_hyperliquid_ws_stability"}
        for entrypoint in ENTRYPOINTS:
            with self.subTest(entrypoint=entrypoint.name):
                tree = ast.parse(entrypoint.read_text(encoding="utf-8"))
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(item.name for item in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported.add(node.module)
                self.assertFalse(imported & forbidden)
                self.assertFalse(any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                     and node.name in ("run_probe", "run_session")
                                     for node in ast.walk(tree)))

    def test_restart_replay_and_legacy_entrypoint_do_not_spawn_probe(self):
        # No real HTTP server is bound. Every possible outbound/spawn primitive
        # is monitored, including the legacy startup entry point.
        env = {"STAGE2_WS_PROBE_APPROVED": "yes",
               "STAGE2_LIVE_READONLY_APPROVED": "yes",
               "RUN_WS_PROBE": "true",
               "PORT": "10000"}
        with (patch.dict(os.environ, env),
              patch("http.server.ThreadingHTTPServer") as fake_http,
              patch.object(safe_runner, "ThreadingHTTPServer", fake_http),
              patch.object(subprocess, "run", side_effect=AssertionError("subprocess is forbidden")) as run,
              patch.object(subprocess, "Popen", side_effect=AssertionError("spawn is forbidden")) as popen,
              patch.object(socket, "create_connection", side_effect=AssertionError("WS is forbidden")) as connection,
              patch.object(threading.Thread, "start", side_effect=AssertionError("background thread is forbidden")) as thread):
            for _ in range(3):
                for entrypoint in ENTRYPOINTS:
                    runpy.run_path(str(entrypoint), run_name="__main__")
            self.assertEqual(fake_http.call_count, 6)
            self.assertEqual(fake_http.return_value.serve_forever.call_count, 6)
            run.assert_not_called()
            popen.assert_not_called()
            connection.assert_not_called()
            thread.assert_not_called()

    def test_no_probe_flag_can_change_startup_verdict(self):
        for flag in ("yes", "true", "1", "", "no"):
            with self.subTest(flag=flag), patch.dict(os.environ, {"STAGE2_WS_PROBE_APPROVED": flag}):
                self.assertEqual(safe_runner.diagnostic_status()["probe_state"], "disabled")
                self.assertIs(safe_runner.diagnostic_status()["recommendation_eligible"], False)


if __name__ == "__main__":
    unittest.main()
