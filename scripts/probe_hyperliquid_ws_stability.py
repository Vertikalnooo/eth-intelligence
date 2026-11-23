"""One-shot bounded, read-only ETH activeAssetCtx WS smoke.

Only public ETH mark context is inspected. No account keys, orders, trading,
external REST, raw payload/price logging, or exchange-time assertions.
This file is dedicated to the isolated Render eth-intelligence-ws-test service.
"""
import base64
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import socket
import ssl
import time
from urllib.parse import urlsplit

from probe_hyperliquid_ws import receive_frame, send_frame, URL

CHANNEL = "activeAssetCtx"
COIN = "ETH"
MAX_SECONDS = 50  # Runner enforces hard 60s subprocess wall-time limit.
MAX_FRAMES = 30  # Total received frames across both connections.
MAX_RECONNECTS = 1
SESSION_SECONDS = 23
PAUSE_SECONDS = 1
IO_TIMEOUT = 7
MAX_HANDSHAKE = 16384
MAX_MARK_MESSAGES = 2

class ProbeFailure(Exception):
    """Explicit, low-cardinality reason code, never price or raw data."""


class PrebufferedSocket:
    """Retain initial WS bytes coalesced with the HTTP 101 handshake."""
    def __init__(self, sock, buffered):
        self._socket = sock
        self._buffered = buffered

    def recv(self, count):
        if self._buffered:
            chunk, self._buffered = self._buffered[:count], self._buffered[count:]
            return chunk
        return self._socket.recv(count)

    def sendall(self, data):
        return self._socket.sendall(data)

    def settimeout(self, timeout):
        return self._socket.settimeout(timeout)

    def close(self):
        return self._socket.close()


def classify_message(opcode, payload):
    """Return accepted message kind; no source timestamp may be inferred."""
    if opcode == 8:
        raise ProbeFailure("server_closed")
    if opcode == 9:
        return "ping"
    if opcode == 10:
        return "pong_frame"
    if opcode != 1:
        raise ProbeFailure("unexpected_frame_opcode")
    try:
        msg = json.loads(payload)
    except (ValueError, UnicodeError):
        raise ProbeFailure("invalid_json") from None
    if type(msg) is not dict or type(msg.get("channel")) is not str:
        raise ProbeFailure("invalid_envelope")
    if msg["channel"] == "subscriptionResponse":
        data = msg.get("data")
        if (type(data) is not dict or data.get("method") != "subscribe"
                or data.get("subscription") != {"type": CHANNEL, "coin": COIN}):
            raise ProbeFailure("unexpected_subscription_response")
        return "subscription_ack"
    if msg["channel"] == "pong":
        return "pong"
    if msg["channel"] != CHANNEL:
        raise ProbeFailure("unexpected_channel")
    data = msg.get("data")
    if type(data) is not dict or data.get("coin") != COIN:
        raise ProbeFailure("unexpected_asset")
    ctx = data.get("ctx")
    if type(ctx) is not dict:
        raise ProbeFailure("invalid_eth_context")
    raw = ctx.get("markPx")
    if (type(raw) is not str or not 0 < len(raw) <= 160
            or not raw.isascii() or raw != raw.strip()):
        raise ProbeFailure("invalid_mark_type")
    try:
        px = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ProbeFailure("invalid_mark_decimal") from None
    if (not px.is_finite() or px <= 0 or len(px.as_tuple().digits) > 128
            or abs(px.as_tuple().exponent) > 128):
        raise ProbeFailure("invalid_mark_value")
    return "eth_mark_receipt_only"


def strict_socket_connect(deadline):
    parts = urlsplit(URL)
    if (parts.scheme != "wss" or parts.hostname != "api.hyperliquid.xyz"
            or parts.path != "/ws" or parts.query or parts.fragment or parts.port):
        raise ProbeFailure("endpoint_not_allowed")
    if time.monotonic() >= deadline:
        raise ProbeFailure("global_deadline")
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    tcp = socket.create_connection((parts.hostname, 443), timeout=IO_TIMEOUT)
    try:
        sock = ssl.create_default_context().wrap_socket(tcp, server_hostname=parts.hostname)
    except BaseException:
        tcp.close()
        raise
    try:
        sock.settimeout(max(0.1, min(IO_TIMEOUT, deadline - time.monotonic())))
        request = (
            f"GET {parts.path} HTTP/1.1\r\nHost: {parts.hostname}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            if time.monotonic() >= deadline:
                raise ProbeFailure("handshake_deadline")
            sock.settimeout(max(0.1, min(IO_TIMEOUT, deadline - time.monotonic())))
            chunk = sock.recv(4096)
            if not chunk:
                raise ProbeFailure("handshake_closed")
            response += chunk
            if len(response) > MAX_HANDSHAKE:
                raise ProbeFailure("handshake_oversize")
        head, _, remainder = response.partition(b"\r\n\r\n")
        if not head.startswith(b"HTTP/1.1 101 "):
            raise ProbeFailure("handshake_rejected")
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest()).decode("ascii")
        lines = head.decode("latin1").split("\r\n")
        headers = {}
        for line in lines[1:]:
            if ":" not in line:
                raise ProbeFailure("handshake_malformed_header")
            k, v = line.split(":", 1)
            headers[k.lower().strip()] = v.strip()
        if headers.get("sec-websocket-accept") != expected:
            raise ProbeFailure("handshake_bad_accept")
        return PrebufferedSocket(sock, remainder) if remainder else sock
    except BaseException:
        sock.close()
        raise


def run_session(index, deadline, counters):
    session_deadline = min(deadline, time.monotonic() + SESSION_SECONDS)
    sock = strict_socket_connect(session_deadline)
    started = time.monotonic()
    try:
        sock.settimeout(max(0.1, min(IO_TIMEOUT, session_deadline - time.monotonic())))
        subscribe = {"method": "subscribe", "subscription": {"type": CHANNEL, "coin": COIN}}
        send_frame(sock, 1, json.dumps(subscribe, separators=(",", ":")).encode("ascii"))
        valid_marks = 0
        while valid_marks < 1:
            if time.monotonic() >= session_deadline or time.monotonic() >= deadline:
                raise ProbeFailure("session_deadline")
            if counters["frames"] >= MAX_FRAMES:
                raise ProbeFailure("frame_cap")
            sock.settimeout(max(0.1, min(IO_TIMEOUT, session_deadline - time.monotonic())))
            try:
                opcode, payload = receive_frame(sock)
            except socket.timeout:
                continue
            counters["frames"] += 1
            kind = classify_message(opcode, payload)
            if kind == "ping":
                send_frame(sock, 10, payload)
            elif kind == "eth_mark_receipt_only":
                valid_marks += 1
                counters["mark_messages"] += 1
                if counters["mark_messages"] > MAX_MARK_MESSAGES:
                    raise ProbeFailure("mark_message_cap")
        print(json.dumps({"session": index, "status": "PASS",
                          "receipt_count": valid_marks,
                          "elapsed_ms": int((time.monotonic() - started)*1000),
                          "exchange_freshness": "UNVERIFIED"}), flush=True)
    finally:
        try:
            sock.settimeout(1)
            send_frame(sock, 8)
        except OSError:
            pass
        sock.close()


def main():
    start = time.monotonic()
    deadline = start + MAX_SECONDS
    counters = {"frames": 0, "mark_messages": 0}
    for index in (1, 2):
        if time.monotonic() >= deadline:
            raise ProbeFailure("global_deadline")
        run_session(index, deadline, counters)
        if index == 1:
            print(json.dumps({"reconnect": "controlled_once"}), flush=True)
            time.sleep(min(PAUSE_SECONDS, max(0, deadline-time.monotonic())))
    print(json.dumps({"status": "PASS", "channel": CHANNEL, "coin": COIN,
                      "sessions": 2, "reconnects": MAX_RECONNECTS,
                      "frames_received": counters["frames"],
                      "mark_messages": counters["mark_messages"],
                      "duration_ms": int((time.monotonic()-start)*1000),
                      "exchange_freshness": "UNVERIFIED",
                      "recommendation_eligible": False,
                      "stage2_complete": False}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except ProbeFailure as exc:
        print(json.dumps({"status": "FAIL", "reason_code": str(exc),
                          "exchange_freshness": "UNVERIFIED"}), flush=True)
        raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "reason_code": "unexpected_"+type(exc).__name__,
                          "exchange_freshness": "UNVERIFIED"}), flush=True)
        raise SystemExit(1)
