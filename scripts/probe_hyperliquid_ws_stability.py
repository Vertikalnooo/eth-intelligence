"""Read-only bounded WebSocket stability probe. No production imports or trades."""
import base64
import hashlib
import json
import os
import socket
import ssl
import time
from urllib.parse import urlsplit
from probe_hyperliquid_ws import receive_frame, send_frame, URL

SESSIONS = 2
UPDATES_PER_SESSION = 2
SESSION_DEADLINE = 35
PAUSE_SECONDS = 3

def run_session(index):
    parts = urlsplit(URL)
    key = base64.b64encode(os.urandom(16)).decode()
    with socket.create_connection((parts.hostname, 443), timeout=12) as tcp:
        with ssl.create_default_context().wrap_socket(tcp, server_hostname=parts.hostname) as sock:
            sock.settimeout(12)
            request = (
                f"GET {parts.path} HTTP/1.1\r\nHost: {parts.hostname}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(request.encode())
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Handshake closed")
                response += chunk
                if len(response) > 16384:
                    raise ValueError("Handshake too large")
            head, _, remainder = response.partition(b"\r\n\r\n")
            if not head.startswith(b"HTTP/1.1 101 "):
                raise ConnectionError("Handshake rejected: " + head.split(b"\r\n")[0].decode(errors="replace"))
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if ("sec-websocket-accept: " + expected).lower() not in head.decode(errors="replace").lower():
                raise ConnectionError("Invalid WebSocket accept")
            if remainder:
                raise ValueError("Buffered WebSocket bytes not supported by this probe")
            send_frame(sock, 1, json.dumps({"method":"subscribe","subscription":{"type":"allMids"}}).encode())
            prices = []
            deadline = time.monotonic() + SESSION_DEADLINE
            while len(prices) < UPDATES_PER_SESSION and time.monotonic() < deadline:
                sock.settimeout(max(0.1, min(12, deadline - time.monotonic())))
                try:
                    opcode, payload = receive_frame(sock)
                except socket.timeout:
                    continue
                if opcode == 9:
                    send_frame(sock, 10, payload)
                elif opcode == 8:
                    raise ConnectionError("Server closed connection")
                elif opcode == 1:
                    event = json.loads(payload)
                    if event.get("channel") == "allMids":
                        value = event.get("data", {}).get("mids", {}).get("ETH")
                        if value is not None:
                            price = float(value)
                            if not 0 < price < float("inf"):
                                raise ValueError("Invalid ETH mid")
                            prices.append(price)
                            print(f"session={index} update={len(prices)} eth_mid={price}", flush=True)
            try:
                send_frame(sock, 8)
            except OSError:
                pass
            if len(prices) < UPDATES_PER_SESSION:
                raise TimeoutError(f"Only {len(prices)} updates in session {index}")
            print(f"session={index} result=passed", flush=True)

def main():
    for index in range(1, SESSIONS + 1):
        run_session(index)
        if index < SESSIONS:
            print("reconnect=starting_after_controlled_disconnect", flush=True)
            time.sleep(PAUSE_SECONDS)
    print("stability_probe_result=passed", flush=True)
    print("note=mid_price_only; exchange_freshness_unverified; no_trade_recommendations", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("stability_probe_result=failed reason=" + type(exc).__name__ + ": " + str(exc), flush=True)
        raise SystemExit(1)
