"""Offline tests only: no Hyperliquid connection or workflow dispatch."""
import json
import threading
import unittest

from observation_adapter import ETHObservationAdapter
from ws_observer import WSObserver, WS_URL, SUBSCRIBE, enforce_frame_budget


class FakeSocket:
    def __init__(self, messages=(), failure=None):
        self.messages = iter(messages)
        self.failure = failure
        self.sent = []
        self.closed = False
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self):
        try:
            return next(self.messages)
        except StopIteration:
            if self.failure is not None:
                raise self.failure
            raise ConnectionError("offline_disconnect")

    def close(self):
        self.closed = True


def eth_message(price="2000"):
    return json.dumps({"channel": "activeAssetCtx",
                       "data": {"coin": "ETH", "ctx": {"markPx": price}}})


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.adapter = ETHObservationAdapter(clock=lambda: self.now)

    def observer(self, factory, **kwargs):
        return WSObserver(adapter=self.adapter, transport_factory=factory,
                          clock=lambda: self.now, sleeper=lambda delay: None,
                          max_attempts=kwargs.pop("max_attempts", 1),
                          frame_guard=kwargs.pop("frame_guard", lambda *args: None), **kwargs)

    def test_subscribe_and_disconnect(self):
        sock = FakeSocket([eth_message()])
        seen = []
        def factory(url, timeout):
            seen.append((url, timeout))
            return sock
        self.observer(factory).run()
        self.assertEqual(seen, [(WS_URL, 5.0)])
        self.assertEqual(sock.sent, [SUBSCRIBE])
        self.assertEqual(sock.timeout, 2.0)
        self.assertTrue(sock.closed)
        self.assertEqual(self.adapter.snapshot()["ws_health"], "disconnected")
        self.assertFalse(self.adapter.snapshot()["recommendation_eligible"])

    def test_bounded_retry_and_cleanup(self):
        sockets = [FakeSocket(), FakeSocket(), FakeSocket()]
        calls = []
        def factory(url, timeout):
            calls.append(len(calls))
            return sockets[len(calls) - 1]
        self.observer(factory, max_attempts=3).run()
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(sock.closed for sock in sockets))
        self.assertEqual(self.adapter.snapshot()["overall_health"], "disconnected")

    def test_connect_failure_bounded(self):
        calls = []
        def factory(url, timeout):
            calls.append(1)
            raise ConnectionError("offline")
        self.observer(factory, max_attempts=2).run()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.adapter.snapshot()["ws_health"], "disconnected")

    def test_invalid_json_fails_closed(self):
        sock = FakeSocket(["not-json"])
        self.observer(lambda *args: sock).run()
        self.assertTrue(sock.closed)
        self.assertEqual(self.adapter.snapshot()["ws_health"], "disconnected")

    def test_invalid_price_fails_closed(self):
        sock = FakeSocket([eth_message("NaN")])
        self.observer(lambda *args: sock).run()
        self.assertTrue(sock.closed)
        self.assertFalse(self.adapter.snapshot()["recommendation_eligible"])

    def test_oversized_payload_rejected(self):
        sock = FakeSocket(["x" * 200_001])
        self.observer(lambda *args: sock).run()
        self.assertTrue(sock.closed)
        self.assertEqual(self.adapter.snapshot()["ws_health"], "disconnected")

    def test_unrelated_asset_ignored(self):
        sock = FakeSocket([json.dumps({"channel": "activeAssetCtx",
                                       "data": {"coin": "BTC", "ctx": {"markPx": "1"}}})])
        self.observer(lambda *args: sock).run()
        self.assertTrue(sock.closed)
        self.assertEqual(self.adapter.snapshot()["ws_health"], "disconnected")

    def test_stop_before_run_never_connects(self):
        calls = []
        observer = self.observer(lambda *args: calls.append(1))
        observer.stop()
        observer.run()
        self.assertEqual(calls, [])

    def test_stop_interrupts_active_socket(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingSocket(FakeSocket):
            def recv(self):
                entered.set()
                release.wait(timeout=2)
                raise ConnectionError("closed")

            def close(self):
                super().close()
                release.set()

        sock = BlockingSocket()
        observer = self.observer(lambda *args: sock, max_attempts=3)
        worker = threading.Thread(target=observer.run)
        worker.start()
        self.assertTrue(entered.wait(timeout=2))
        observer.stop()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(sock.closed)
        self.assertEqual(self.adapter.snapshot()["ws_health"], "disconnected")

    def test_no_double_run(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingSocket(FakeSocket):
            def recv(self):
                entered.set()
                release.wait(timeout=2)
                raise ConnectionError("closed")

            def close(self):
                release.set()
                super().close()

        observer = self.observer(lambda *args: BlockingSocket())
        worker = threading.Thread(target=observer.run)
        worker.start()
        self.assertTrue(entered.wait(timeout=2))
        with self.assertRaises(RuntimeError):
            observer.run()
        observer.stop()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())

    def test_configuration_rejected(self):
        for kwargs in ({"max_attempts": 0}, {"max_attempts": True},
                       {"read_timeout": 0}, {"connect_timeout": -1},
                       {"max_idle_seconds": float("inf")},
                       {"max_message_bytes": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.observer(lambda *args: None, **kwargs)

    def test_idle_timeout_disconnects_and_retries_bounded(self):
        test = self

        class TimeoutSocket(FakeSocket):
            def recv(self):
                test.now += 2
                raise TimeoutError("offline_read_timeout")

        sockets = [TimeoutSocket(), TimeoutSocket()]
        attempts = []
        def factory(*args):
            attempts.append(1)
            return sockets[len(attempts) - 1]
        self.observer(factory, max_attempts=2, max_idle_seconds=5).run()
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(sock.closed for sock in sockets))
        self.assertEqual(self.adapter.snapshot()["ws_health"], "disconnected")

    def test_frame_guard_rejects_before_payload_read(self):
        class Buffer:
            length = 0
            def recv_length(self):
                self.length = next(lengths)
        class Connection:
            frame_buffer = Buffer()
        lengths = iter([10, 200_001])
        conn = Connection()
        enforce_frame_budget(conn, 200_000, 1_000_000)
        conn.frame_buffer.recv_length()
        with self.assertRaises(ValueError):
            conn.frame_buffer.recv_length()

    def test_fragmented_frame_session_budget(self):
        class Buffer:
            length = 0
            def recv_length(self):
                self.length = next(lengths)
        class Connection:
            frame_buffer = Buffer()
        lengths = iter([1000, 1000, 1000])
        conn = Connection()
        enforce_frame_budget(conn, 2000, 2500)
        conn.frame_buffer.recv_length()
        conn.frame_buffer.recv_length()
        with self.assertRaises(ValueError):
            conn.frame_buffer.recv_length()

    def test_missing_frame_guard_fails_closed(self):
        sock = FakeSocket([eth_message()])
        observer = WSObserver(adapter=self.adapter,
                              transport_factory=lambda *args: sock, max_attempts=1)
        observer.run()
        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.closed)
        self.assertEqual(observer.diagnostics()["last_error"], "frame_guard_unavailable")
        self.assertFalse(observer.diagnostics()["recommendation_eligible"])

    def test_absolute_deadline_limits_attempts(self):
        attempts = []
        def factory(*args):
            attempts.append(1)
            self.now += 4
            raise ConnectionError("offline")
        observer = self.observer(factory, max_attempts=10, max_session_seconds=5)
        observer.run()
        self.assertEqual(len(attempts), 2)
        self.assertFalse(observer.diagnostics()["running"])

    def test_deadline_even_with_valid_messages(self):
        test = self
        class RepeatingSocket(FakeSocket):
            def recv(self):
                test.now += 2
                return eth_message()
        sock = RepeatingSocket()
        observer = self.observer(lambda *args: sock, max_session_seconds=5)
        observer.run()
        self.assertTrue(sock.closed)
        self.assertEqual(observer.diagnostics()["last_error"], "timeout")
        self.assertFalse(self.adapter.snapshot()["recommendation_eligible"])

    def test_diagnostics_do_not_leak_exception_text(self):
        observer = self.observer(lambda *args: (_ for _ in ()).throw(
            ConnectionError("SECRET TOKEN must not leak")))
        observer.run()
        report = observer.diagnostics()
        self.assertEqual(report["last_error"], "transport_error")
        self.assertNotIn("SECRET", str(report))

    def test_no_automatic_network_start(self):
        observer = self.observer(lambda *args: self.fail("unexpected network"))
        self.assertEqual(observer.adapter.snapshot()["ws_health"], "disconnected")


if __name__ == "__main__":
    unittest.main()
