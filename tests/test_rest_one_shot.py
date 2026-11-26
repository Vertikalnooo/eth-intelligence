"""Offline request-count fixtures; never use external network."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import app
import rest_one_shot as shot

APPROVAL = "a" * 32
VALID = b'[{"universe":[{"name":"BTC"},{"name":"ETH"}]},[{},{"markPx":"1700.125"}]]'


@contextmanager
def fixture(*, status=200, body=VALID, headers=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.command, self.path, payload))
            self.send_response(status)
            for key, value in (headers if headers is not None else [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body)))]):
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            requests.append((self.command, self.path, b""))
            self.send_error(500)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class OneShotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.sqlite"
        with sqlite3.connect(self.path) as db:
            db.execute(shot.LEDGER_SCHEMA)
            db.execute("INSERT INTO approvals VALUES(?,?,?,?,?)",
                       (APPROVAL, shot.SCOPE, int(time.time()) + 3600, 0, None))
        self.guard = shot.OneShotGuard(self.path, APPROVAL)

    def execute(self, port):
        # Production constructor is verified to request fixed HTTPS host;
        # actual wire-count tests substitute only a local HTTP connection.
        with patch.object(shot.http.client, "HTTPSConnection",
                          side_effect=lambda *a, **kw: http.client.HTTPConnection(
                              "127.0.0.1", port, timeout=1)) as transport:
            try:
                return shot._run_legacy_fixture(self.guard, shot.http.client.HTTPSConnection)
            finally:
                transport.assert_called_once()
                args, kwargs = transport.call_args
                self.assertEqual(args, (shot.HOST, 443))
                self.assertEqual(kwargs["timeout"], shot.IO_TIMEOUT)
                self.assertTrue(kwargs["context"].check_hostname)

    def fail_before_network(self, guard):
        with patch.object(shot.http.client, "HTTPSConnection") as transport:
            with self.assertRaises(shot.OneShotError):
                shot._run_legacy_fixture(guard, shot.http.client.HTTPSConnection)
            transport.assert_not_called()

    def rejected_wire(self, **kwargs):
        with fixture(**kwargs) as (port, requests):
            with self.assertRaises(shot.OneShotError):
                self.execute(port)
            self.assertEqual(requests, [("POST", "/info", shot.BODY)])
        self.assertEqual(self.guard.attempt_count(), 1)
        self.fail_before_network(self.guard)

    def test_success_fixed_post_and_consumed_before_connect(self):
        with fixture() as (port, requests):
            def create(*args, **kwargs):
                self.assertEqual(self.guard.attempt_count(), 1)
                return http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            with patch.object(shot.http.client, "HTTPSConnection", side_effect=create):
                result = shot._run_legacy_fixture(self.guard, shot.http.client.HTTPSConnection)
            self.assertEqual(result.mark_price, "1700.125")
            self.assertEqual(requests, [("POST", "/info", shot.BODY)])
            self.assertEqual(self.guard.attempt_count(), 1)
            self.fail_before_network(self.guard)

    def test_missing_ledger_never_created(self):
        missing = Path(self.tmp.name) / "absent.sqlite"
        self.fail_before_network(shot.OneShotGuard(missing, APPROVAL))
        self.assertFalse(missing.exists())

    def test_missing_approval(self):
        self.fail_before_network(shot.OneShotGuard(self.path, "b" * 32))

    def test_invalid_approval_id(self):
        self.fail_before_network(shot.OneShotGuard(self.path, "x"))

    def test_expired_approval(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE approvals SET expires_at=0")
        self.fail_before_network(self.guard)
        self.assertEqual(self.guard.attempt_count(), 0)

    def test_wrong_scope(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE approvals SET scope='other'")
        self.fail_before_network(self.guard)

    def test_corrupt_ledger(self):
        bad = Path(self.tmp.name) / "corrupt.sqlite"
        bad.write_bytes(b"not a database")
        self.fail_before_network(shot.OneShotGuard(bad, APPROVAL))

    def test_restart_new_guard_same_ledger_denied(self):
        self.guard.consume()
        self.fail_before_network(shot.OneShotGuard(self.path, APPROVAL))

    def test_spent_grant_survives_real_new_process(self):
        self.guard.consume()
        code = ("from pathlib import Path; import sys; import rest_one_shot as s\n"
                "g=s.OneShotGuard(Path(sys.argv[1]), sys.argv[2])\n"
                "try: g.consume()\n"
                "except s.OneShotError: sys.exit(0)\n"
                "sys.exit(1)\n")
        result = subprocess.run([sys.executable, "-c", code, str(self.path), APPROVAL],
                                capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.guard.attempt_count(), 1)

    def test_concurrent_reservation_only_one(self):
        def attempt(_):
            try:
                shot.OneShotGuard(self.path, APPROVAL).consume()
                return True
            except shot.OneShotError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.guard.attempt_count(), 1)

    def test_ledger_lock_denies_before_network(self):
        with sqlite3.connect(self.path, isolation_level=None) as db:
            db.execute("BEGIN EXCLUSIVE")
            self.fail_before_network(self.guard)
            db.rollback()
        self.assertEqual(self.guard.attempt_count(), 0)

    def test_constructor_failure_spends_grant(self):
        with patch.object(shot.http.client, "HTTPSConnection", side_effect=OSError):
            with self.assertRaises(shot.OneShotError):
                shot._run_legacy_fixture(self.guard, shot.http.client.HTTPSConnection)
        self.assertEqual(self.guard.attempt_count(), 1)
        self.fail_before_network(self.guard)

    def test_redirects_never_followed(self):
        # Each status uses a new *synthetic* ledger; no live grant is created.
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                with sqlite3.connect(self.path) as db:
                    db.execute("UPDATE approvals SET attempts=0, consumed_at=NULL")
                self.rejected_wire(status=status, body=b"", headers=[
                    ("Location", "/other"),
                    ("Content-Length", "0")])

    def test_429_never_retried(self):
        self.rejected_wire(status=429, body=b"", headers=[
            ("Retry-After", "0"), ("Content-Length", "0")])

    def test_503_never_retried(self):
        self.rejected_wire(status=503, body=b"")

    def test_declared_oversize(self):
        self.rejected_wire(headers=[("Content-Type", "application/json"),
                                    ("Content-Length", str(shot.MAX_BYTES + 1))])

    def test_chunked_oversize(self):
        data = b"x" * (shot.MAX_BYTES + 1)
        body = ("%x\r\n" % len(data)).encode() + data + b"\r\n0\r\n\r\n"
        self.rejected_wire(body=body, headers=[("Content-Type", "application/json"),
                                             ("Transfer-Encoding", "chunked")])

    def test_chunked_valid(self):
        body = ("%x\r\n" % len(VALID)).encode() + VALID + b"\r\n0\r\n\r\n"
        with fixture(body=body, headers=[("Content-Type", "application/json"),
                                        ("Transfer-Encoding", "chunked")]) as (port, requests):
            self.assertEqual(self.execute(port).mark_price, "1700.125")
            self.assertEqual(len(requests), 1)

    def test_truncated_body(self):
        self.rejected_wire(headers=[("Content-Type", "application/json"),
                                    ("Content-Length", str(len(VALID) + 1))])

    def test_duplicate_content_length(self):
        self.rejected_wire(headers=[("Content-Type", "application/json"),
                                    ("Content-Length", str(len(VALID))),
                                    ("Content-Length", str(len(VALID)))])

    def test_conflicting_framing(self):
        self.rejected_wire(headers=[("Content-Type", "application/json"),
                                    ("Content-Length", str(len(VALID))),
                                    ("Transfer-Encoding", "chunked")])

    def test_wrong_content_encoding(self):
        self.rejected_wire(headers=[("Content-Type", "application/json"),
                                    ("Content-Length", str(len(VALID))),
                                    ("Content-Encoding", "gzip")])

    def test_wrong_content_type(self):
        self.rejected_wire(headers=[("Content-Type", "text/html"),
                                    ("Content-Length", str(len(VALID)))])

    def test_duplicate_json_key(self):
        self.rejected_wire(body=b'[{"universe":[{"name":"ETH"}]},[{"markPx":"1","markPx":"2"}]]')

    def test_nonfinite_json(self):
        self.rejected_wire(body=b'[{"universe":[{"name":"ETH"}]},[{"markPx":"1","other":NaN}]]')

    def test_invalid_utf8(self):
        self.rejected_wire(body=b"\xff")

    def test_deep_json(self):
        self.rejected_wire(body=b"[" * 2000 + b"]" * 2000)

    def test_duplicate_eth(self):
        self.rejected_wire(body=b'[{"universe":[{"name":"ETH"},{"name":"ETH"}]},[{"markPx":"1"},{"markPx":"2"}]]')

    def test_mark_validation(self):
        for mark in (None, True, 12, "0", "-1", "NaN", "Infinity", "1e999", " 1", "9"*161):
            with self.subTest(mark=mark):
                with self.assertRaises(shot.OneShotError):
                    shot._market([{"universe":[{"name":"ETH"}]}, [{"markPx":mark}]])

    def test_elapsed_budget_no_retry(self):
        with fixture() as (port, requests):
            with patch.object(shot, "IO_TIMEOUT", -1):
                with self.assertRaises(shot.OneShotError):
                    self.execute(port)
        self.assertEqual(len(requests), 1)
        self.fail_before_network(self.guard)

    def test_lifecycle_and_no_additional_network(self):
        with fixture() as (port, requests):
            result = self.execute(port)
            samples = [shot.sample_lifecycle(result, result.received_ns + delta)
                       for delta in (0, 3_000_000_000, 6_000_000_000, 31_000_000_000)]
            with patch.object(shot.http.client, "HTTPSConnection") as transport:
                report = shot.verify_lifecycle(self.guard, result, samples)
                transport.assert_not_called()
            self.assertEqual(report["status"], "PASS")
            self.assertFalse(report["exchange_freshness_verified"])
            self.assertFalse(report["recommendation_eligible"])
            self.assertFalse(report["stage2_complete"])
            self.assertEqual(len(requests), 1)
            for sample in samples:
                self.assertFalse(sample.diagnostic["recommendation_eligible"])
                self.assertIn("missing_exchange_provenance", sample.diagnostic["reason_codes"])
            self.assertEqual(samples[-1].diagnostic["age_ms"], 31000)
            self.assertFalse(samples[-1].cache_valid)

    def test_lifecycle_rejects_missing_window_and_forged_flags(self):
        with fixture() as (port, requests):
            result = self.execute(port)
            samples = [shot.sample_lifecycle(result, result.received_ns + delta)
                       for delta in (0, 3_000_000_000, 6_000_000_000, 31_000_000_000)]
            for wrong in (samples[1:], samples[::-1], [samples[0]] * 4,
                          [replace(samples[0], diagnostic={
                              **samples[0].diagnostic, "recommendation_eligible": 0}), *samples[1:]]):
                with self.assertRaises(shot.OneShotError):
                    shot.verify_lifecycle(self.guard, result, wrong)
            self.assertEqual(len(requests), 1)

    def test_collector_uses_actual_local_times(self):
        with fixture() as (port, requests):
            result = self.execute(port)
            clock = [result.received_ns]
            def sleep(seconds):
                clock[0] += round(seconds * 1_000_000_000)
            with patch.object(shot.time, "monotonic_ns", side_effect=lambda: clock[0]), \
                    patch.object(shot.time, "sleep", side_effect=sleep), \
                    patch.object(shot.http.client, "HTTPSConnection") as transport:
                report, samples = shot.collect_lifecycle(self.guard, result)
                transport.assert_not_called()
            self.assertEqual(report["status"], "PASS")
            self.assertEqual([s.diagnostic["state"] for s in samples],
                             ["FRESH", "DEGRADED", "STALE", "STALE"])
            self.assertEqual(len(requests), 1)

    def test_legacy_public_execution_is_disabled(self):
        with patch.object(shot.http.client, "HTTPSConnection") as transport:
            with self.assertRaisesRegex(shot.OneShotError, "legacy_execution_disabled"):
                shot.run_one_shot(self.guard)
            transport.assert_not_called()
        self.assertEqual(self.guard.attempt_count(), 0)

    def test_production_remains_unwired(self):
        self.assertNotIn("rest_one_shot", Path(app.__file__).read_text(encoding="utf-8"))
        with patch.object(shot.http.client, "HTTPSConnection") as transport:
            app.market_status()
            transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
