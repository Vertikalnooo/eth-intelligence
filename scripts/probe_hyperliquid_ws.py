"""Standalone read-only Hyperliquid WebSocket connectivity probe.

Run manually: python scripts/probe_hyperliquid_ws.py
No changes to the production API; no trading or automatic deployment.
"""
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
from urllib.parse import urlsplit

URL = "wss://api.hyperliquid.xyz/ws"
TIMEOUT = 12


def receive_exact(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("WebSocket closed")
        data += chunk
    return data


def receive_frame(sock):
    header = receive_exact(sock, 2)
    opcode = header[0] & 15
    length = header[1] & 127
    if length == 126:
        length = struct.unpack("!H", receive_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", receive_exact(sock, 8))[0]
    if length > 1_000_000:
        raise ValueError("Oversized frame")
    masked = bool(header[1] & 128)
    mask = receive_exact(sock, 4) if masked else None
    payload = receive_exact(sock, length)
    if mask:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return opcode, payload


def send_frame(sock, opcode, payload=b""):
    mask = os.urandom(4)
    size = len(payload)
    if size < 126:
        header = bytes([0x80 | opcode, 0x80 | size])
    elif size < 65536:
        header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", size)
    else:
        header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", size)
    sock.sendall(header + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))


def main():
    parts = urlsplit(URL)
    key = base64.b64encode(os.urandom(16)).decode()
    start = time.monotonic()
    with socket.create_connection((parts.hostname, 443), timeout=TIMEOUT) as tcp:
        with ssl.create_default_context().wrap_socket(tcp, server_hostname=parts.hostname) as sock:
            sock.settimeout(TIMEOUT)
            request = (
                f"GET {parts.path} HTTP/1.1\r\nHost: {parts.hostname}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(request.encode())
            response = b""
            while b"\r\n\r\n" not in response:
                response += sock.recv(4096)
                if len(response) > 16384:
                    raise ValueError("Handshake too large")
            head, _, remainder = response.partition(b"\r\n\r\n")
            if remainder:
                raise ValueError("Unexpected buffered data after handshake")
            if not head.startswith(b"HTTP/1.1 101 "):
                raise ConnectionError("WebSocket handshake rejected: " + head.split(b"\r\n", 1)[0].decode(errors="replace"))
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if ("sec-websocket-accept: " + expected).lower() not in head.decode().lower():
                raise ConnectionError("Invalid WebSocket accept header")
            print("handshake=ok")
            send_frame(sock, 1, json.dumps({"method": "subscribe", "subscription": {"type": "allMids"}}).encode())
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                sock.settimeout(max(0.1, min(TIMEOUT, deadline - time.monotonic())))
                try:
                    opcode, payload = receive_frame(sock)
                except socket.timeout:
                    continue
                if opcode == 9:
                    send_frame(sock, 10, payload)
                    continue
                if opcode == 8:
                    raise ConnectionError("Server closed WebSocket")
                if opcode != 1:
                    continue
                event = json.loads(payload)
                if event.get("channel") == "allMids":
                    mids = event.get("data", {}).get("mids", {})
                    eth = mids.get("ETH")
                    if eth is not None:
                        print("subscription=ok")
                        print("eth_mid=" + str(eth))
                        print("elapsed_seconds=" + str(round(time.monotonic() - start, 2)))
                        print("note=allMids ETH is mid price, NOT mark price; freshness not exchange-verified")
                        send_frame(sock, 8)
                        return
            raise TimeoutError("No ETH allMids update within 30 seconds")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("probe=failed; reason=" + type(exc).__name__ + ": " + str(exc))
        raise SystemExit(1)
