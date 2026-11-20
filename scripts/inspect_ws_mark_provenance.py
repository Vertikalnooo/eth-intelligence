"""Read-only Hyperliquid activeAssetCtx WebSocket field inventory.

Diagnostic only. A message arrival time is NOT an exchange mark-tick timestamp.
No recommendations, orders, production changes, or secrets.
"""
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time

HOST = "api.hyperliquid.xyz"
TIMEOUT = 12

def exact(sock, n):
    data = b""
    while len(data) < n:
        part = sock.recv(n - len(data))
        if not part:
            raise ConnectionError("socket_closed")
        data += part
    return data

def frame(sock):
    first, second = exact(sock, 2)
    if first & 0x70:
        raise ValueError("unexpected_rsv_bits")
    length = second & 127
    if length == 126:
        length = struct.unpack("!H", exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", exact(sock, 8))[0]
    if length > 1_000_000:
        raise ValueError("frame_too_large")
    mask = exact(sock, 4) if second & 128 else None
    data = exact(sock, length)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return first & 15, data

def send(sock, opcode, data=b""):
    key = os.urandom(4)
    n = len(data)
    if n < 126:
        header = bytes([0x80 | opcode, 0x80 | n])
    elif n < 65536:
        header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", n)
    else:
        header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", n)
    sock.sendall(header + key + bytes(b ^ key[i % 4] for i, b in enumerate(data)))

def inspect():
    started = time.monotonic()
    key = base64.b64encode(os.urandom(16)).decode()
    with socket.create_connection((HOST, 443), timeout=TIMEOUT) as tcp:
        with ssl.create_default_context().wrap_socket(tcp, server_hostname=HOST) as sock:
            sock.settimeout(TIMEOUT)
            request = (f"GET /ws HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\n"
                       f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                       "Sec-WebSocket-Version: 13\r\n\r\n")
            sock.sendall(request.encode())
            header = b""
            while b"\r\n\r\n" not in header:
                header += sock.recv(4096)
                if len(header) > 16384:
                    raise ValueError("handshake_too_large")
            head, _, remaining = header.partition(b"\r\n\r\n")
            if remaining:
                raise ValueError("unexpected_handshake_buffer")
            if not head.startswith(b"HTTP/1.1 101 "):
                raise ConnectionError("handshake_rejected")
            expected = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()).decode()
            if ("sec-websocket-accept: " + expected).lower() not in head.decode().lower():
                raise ConnectionError("bad_accept")
            send(sock, 1, json.dumps({"method": "subscribe",
                "subscription": {"type": "activeAssetCtx", "coin": "ETH"}}).encode())
            observations = []
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and len(observations) < 3:
                sock.settimeout(max(0.1, min(TIMEOUT, deadline - time.monotonic())))
                try:
                    opcode, payload = frame(sock)
                except socket.timeout:
                    continue
                if opcode == 9:
                    send(sock, 10, payload)
                    continue
                if opcode == 8:
                    break
                if opcode != 1:
                    continue
                event = json.loads(payload)
                if not isinstance(event, dict) or event.get("channel") != "activeAssetCtx":
                    continue
                data = event.get("data")
                if not isinstance(data, dict) or data.get("coin") != "ETH":
                    continue
                ctx = data.get("ctx")
                if not isinstance(ctx, dict):
                    continue
                observations.append({
                    "elapsed_monotonic_seconds": round(time.monotonic() - started, 3),
                    "envelope_fields": sorted(event.keys()),
                    "data_fields": sorted(data.keys()),
                    "context_fields": sorted(ctx.keys()),
                    "mark_price_present": ctx.get("markPx") is not None,
                    "candidate_time_fields": {
                        "envelope": sorted(k for k in event if "time" in k.lower() or "stamp" in k.lower()),
                        "data": sorted(k for k in data if "time" in k.lower() or "stamp" in k.lower()),
                        "context": sorted(k for k in ctx if "time" in k.lower() or "stamp" in k.lower()),
                    },
                })
            send(sock, 8)
            return observations

if __name__ == "__main__":
    try:
        samples = inspect()
        print(json.dumps({
            "probe": "read_only_ws_mark_field_inventory",
            "samples": samples,
            "sample_count": len(samples),
            "exchange_mark_timestamp_verified": False,
            "mark_price_freshness_verified": False,
            "recommendation_eligible": False,
            "conclusion": "field_inventory_only_no_authenticated_mark_tick_timestamp",
        }, sort_keys=True))
        if not samples:
            raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({"probe": "read_only_ws_mark_field_inventory",
            "status": "failed", "error_type": type(exc).__name__,
            "recommendation_eligible": False}))
        raise SystemExit(1)
