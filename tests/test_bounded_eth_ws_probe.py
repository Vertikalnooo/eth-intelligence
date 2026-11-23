"""All tests synthetic: no network, no WebSocket and no trading actions."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import contextlib
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import probe_hyperliquid_ws_stability as p


class TestEthOnlyProbe(unittest.TestCase):
    def test_hard_bounds(self):
        self.assertLessEqual(p.MAX_SECONDS, 50)
        self.assertEqual(p.MAX_FRAMES, 30)
        self.assertEqual(p.MAX_RECONNECTS, 1)
        self.assertEqual(p.CHANNEL, "activeAssetCtx")
        self.assertEqual(p.COIN, "ETH")
        self.assertEqual(p.MAX_MARK_MESSAGES, 2)
        self.assertEqual(p.URL, "wss://api.hyperliquid.xyz/ws")

    def test_valid_mark_receipt_no_timestamps(self):
        msg = {"channel": "activeAssetCtx", "data": {"coin": "ETH", "ctx": {
            "markPx": "3123.40", "time": 9999999999999, "event_id": "fake"
        }}}
        self.assertEqual(p.classify_message(1, json.dumps(msg).encode()), "eth_mark_receipt_only")

    def test_expected_subscription_response(self):
        msg = {"channel": "subscriptionResponse", "data": {"method": "subscribe", "subscription": {
            "type": "activeAssetCtx", "coin": "ETH"}}}
        self.assertEqual(p.classify_message(1, json.dumps(msg).encode()), "subscription_ack")

    def test_wrong_subscription_disallowed(self):
        for which in ("allMids", "trades", "activeAssetData"):
            msg = {"channel": "subscriptionResponse", "data": {"method": "subscribe", "subscription": {
                "type": which, "coin": "ETH"}}}
            with self.assertRaises(p.ProbeFailure):
                p.classify_message(1, json.dumps(msg).encode())

    def test_wrong_coin_disallowed(self):
        for coin in ("BTC", None, "ETH/USDC"):
            msg = {"channel": "activeAssetCtx", "data": {"coin": coin,
                "ctx": {"markPx": "3100"}}}
            with self.assertRaises(p.ProbeFailure):
                p.classify_message(1, json.dumps(msg).encode())

    def test_bad_prices_disallowed(self):
        for v in (0, "0", "NaN", "Infinity", "-1", "", " 5", "1e99999", "9"*161):
            msg = {"channel": "activeAssetCtx", "data": {"coin": "ETH",
                "ctx": {"markPx": v}}}
            with self.subTest(value=str(v)[:8]), self.assertRaises(p.ProbeFailure):
                p.classify_message(1, json.dumps(msg).encode())

    def test_unexpected_and_close_frames_disallowed(self):
        with self.assertRaises(p.ProbeFailure):
            p.classify_message(8, b"")
        with self.assertRaises(p.ProbeFailure):
            p.classify_message(2, b"something")
        with self.assertRaises(p.ProbeFailure):
            p.classify_message(1, b"not json")
        with self.assertRaises(p.ProbeFailure):
            p.classify_message(1, b'{"channel":"userFills","data":{}}')

    def test_controlled_reconnect_exact_two_public_subscriptions(self):
        class FakeSocket:
            def settimeout(self, n): pass
            def sendall(self, data): pass
            def close(self): pass
        msg = {"channel":"activeAssetCtx", "data":{"coin":"ETH", "ctx":{"markPx":"3000"}}}
        responses = [(1, json.dumps(msg).encode()), (1, json.dumps(msg).encode())]
        sent = []
        def collect(_sock, opcode, payload=b''):
            sent.append((opcode, payload))
        with (patch.object(p, 'strict_socket_connect', side_effect=[FakeSocket(), FakeSocket()]),
              patch.object(p, 'receive_frame', side_effect=responses),
              patch.object(p, 'send_frame', side_effect=collect),
              patch.object(p.time, 'sleep'),
              contextlib.redirect_stdout(io.StringIO()) as output):
            p.main()
        subscriptions = [json.loads(data) for opcode,data in sent if opcode==1]
        self.assertEqual(subscriptions, [{"method":"subscribe", "subscription":{
            "type":"activeAssetCtx", "coin":"ETH"}}]*2)
        final = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(final['sessions'], 2)
        self.assertEqual(final['reconnects'], 1)
        self.assertEqual(final['mark_messages'], 2)
        self.assertLessEqual(final['frames_received'], 30)
        self.assertFalse(final['recommendation_eligible'])
        self.assertNotIn('3000', output.getvalue())

    def test_frame_cap_blocks_unlimited_ack_stream(self):
        class FakeSocket:
            def settimeout(self, n): pass
            def sendall(self, data): pass
            def close(self): pass
        ack = (1, json.dumps({"channel": "subscriptionResponse", "data": {
            "method": "subscribe", "subscription": {"type":"activeAssetCtx", "coin":"ETH"}}}).encode())
        with (patch.object(p, 'strict_socket_connect', return_value=FakeSocket()),
              patch.object(p, 'receive_frame', return_value=ack),
              patch.object(p, 'send_frame')):
            counters = {'frames':0,'mark_messages':0}
            with self.assertRaisesRegex(p.ProbeFailure, 'frame_cap'):
                p.run_session(1, p.time.monotonic()+5, counters)
            self.assertEqual(counters['frames'],30)

    def test_coalesced_handshake_bytes_not_discarded(self):
        class Sock:
            def __init__(self): self.calls=[]
            def recv(self, n): self.calls.append(n); return b'xyz'
        sock = Sock()
        wrapped = p.PrebufferedSocket(sock, b'abcde')
        self.assertEqual(wrapped.recv(2), b'ab')
        self.assertEqual(wrapped.recv(2), b'cd')
        self.assertEqual(wrapped.recv(2), b'e')
        self.assertEqual(wrapped.recv(2), b'xyz')
        self.assertEqual(sock.calls,[2])


if __name__ == "__main__":
    unittest.main()
