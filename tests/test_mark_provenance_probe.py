"""Offline-only tests; no network or external packages required."""
import unittest
from types import SimpleNamespace
from scripts.inspect_mark_timestamp_live import (
    MAX_MESSAGES, collect, context_keys, parse_ws_message, validate_report, enforce_frame_budget,
)

class ProbeTests(unittest.TestCase):
    def test_frame_budget_rejects_before_payload_read(self):
        class FrameBuffer:
            length = 0
            def recv_length(self):
                self.length = next(lengths)
        lengths = iter([100, 200001])
        frame = FrameBuffer()
        enforce_frame_budget(SimpleNamespace(frame_buffer=frame))
        frame.recv_length()
        with self.assertRaisesRegex(ValueError, "websocket_frame_budget_exceeded"):
            frame.recv_length()

    def test_fragmented_session_budget(self):
        class FrameBuffer:
            length = 0
            def recv_length(self):
                self.length = 100
        frame = FrameBuffer()
        enforce_frame_budget(SimpleNamespace(frame_buffer=frame),
                             max_frame_bytes=200, max_session_bytes=150)
        frame.recv_length()
        with self.assertRaises(ValueError):
            frame.recv_length()

    def test_unknown_frame_interface_fails_closed(self):
        with self.assertRaises(RuntimeError):
            enforce_frame_budget(SimpleNamespace())

    def test_valid_rest(self):
        result = context_keys([{"universe": [{"name": "BTC"}, {"name": "ETH"}]},
                               [{"markPx": "1"}, {"markPx": "2", "foo": 3}]])
        self.assertEqual(result["eth_context_keys"], ["foo", "markPx"])
        self.assertTrue(result["mark_price_present"])

    def test_bad_rest(self):
        for payload in ({}, [], [{"universe": []}, []],
                        [{"universe": [{"name": "ETH"}, {"name": "ETH"}]}, [{}, {}]]):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                context_keys(payload)

    def test_ws_matching(self):
        msg = '{"channel":"activeAssetCtx","data":{"coin":"ETH","ctx":{"markPx":"2"}}}'
        result = parse_ws_message(msg)
        self.assertEqual(result["ctx_keys"], ["markPx"])

    def test_ws_ignores_other_and_malformed(self):
        for msg in ('{', '[]', '{"channel":"trades"}',
                    '{"channel":"activeAssetCtx","data":{"coin":"BTC","ctx":{}}}',
                    '{"channel":"activeAssetCtx","data":{"coin":"ETH","ctx":[]}}'):
            self.assertIsNone(parse_ws_message(msg))

    def test_ws_rejects_oversized(self):
        self.assertIsNone(parse_ws_message("x" * 200001))

    def test_independent_error_statuses(self):
        def fail(_):
            raise ConnectionError("offline simulated")
        report = collect(rest=lambda _: {"mark_price_present": True},
                         ws=fail, clock=lambda: 100)
        self.assertEqual(report["rest_status"], "ok")
        self.assertEqual(report["ws_status"], "error")
        self.assertTrue(validate_report(report))
        self.assertFalse(report["recommendation_eligible"])

    def test_no_freshness_authorization(self):
        report = collect(rest=lambda _: {}, ws=lambda _: [], clock=lambda: 100)
        self.assertTrue(validate_report(report))
        self.assertFalse(report["exchange_mark_timestamp_verified"])
        self.assertFalse(report["mark_price_freshness_verified"])
        self.assertFalse(report["recommendation_eligible"])
        report["recommendation_eligible"] = True
        self.assertFalse(validate_report(report))

    def test_deadline_skips_network(self):
        ticks = iter([0, 16, 16])
        report = collect(rest=lambda _: self.fail("unexpected network"),
                         ws=lambda _: self.fail("unexpected ws"), clock=lambda: next(ticks))
        self.assertEqual(report["rest_status"], "error")
        self.assertEqual(report["ws_status"], "error")

if __name__ == "__main__":
    unittest.main()
