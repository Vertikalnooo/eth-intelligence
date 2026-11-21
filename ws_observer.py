"""Explicitly invoked read-only ETH WebSocket observer; never starts on import.

Requires websocket-client 1.8.0 for the default transport. The private frame
length hook is deliberately fail-closed when unsupported. Deadline checks
bound normal socket operations, but cannot forcibly interrupt a broken
third-party transport that ignores socket timeouts.
"""
import json
import math
import threading
import time

from observation_adapter import ETHObservationAdapter

WS_URL = "wss://api.hyperliquid.xyz/ws"
SUBSCRIBE = {"method": "subscribe", "subscription": {"type": "activeAssetCtx", "coin": "ETH"}}


def enforce_frame_budget(conn, max_frame_bytes, max_session_bytes):
    """Reject oversized frame lengths before payload read (websocket-client 1.8.0).

    Uses the private frame_buffer parser; fails closed on incompatible transports.
    A cumulative session limit also covers fragmented frames.
    """
    frame_buffer = getattr(conn, "frame_buffer", None)
    original = getattr(frame_buffer, "recv_length", None)
    if not callable(original):
        raise RuntimeError("websocket_frame_limit_unavailable")
    consumed = 0

    def bounded_length():
        nonlocal consumed
        original()
        length = getattr(frame_buffer, "length", None)
        if (not isinstance(length, int) or isinstance(length, bool)
                or length < 0 or length > max_frame_bytes
                or consumed + length > max_session_bytes):
            raise ValueError("websocket_frame_budget_exceeded")
        consumed += length

    frame_buffer.recv_length = bounded_length


class WSObserver:
    def __init__(self, adapter=None, transport_factory=None, clock=time.monotonic,
                 sleeper=time.sleep, connect_timeout=5.0, read_timeout=2.0,
                 max_attempts=3, retry_delay=1.0, max_message_bytes=200_000,
                 max_idle_seconds=15.0, max_session_seconds=60.0,
                 max_session_bytes=1_000_000, frame_guard=enforce_frame_budget):
        if (not isinstance(max_attempts, int) or isinstance(max_attempts, bool)
                or not 1 <= max_attempts <= 20):
            raise ValueError("invalid_max_attempts")
        for value in (connect_timeout, read_timeout, retry_delay,
                      max_idle_seconds, max_session_seconds):
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or not 0 < value <= 300):
                raise ValueError("invalid_timeout")
        if (not isinstance(max_message_bytes, int) or isinstance(max_message_bytes, bool)
                or not 1024 <= max_message_bytes <= 1_000_000):
            raise ValueError("invalid_max_message_bytes")
        if (not isinstance(max_session_bytes, int) or isinstance(max_session_bytes, bool)
                or not max_message_bytes <= max_session_bytes <= 10_000_000):
            raise ValueError("invalid_max_session_bytes")
        self.adapter = adapter if adapter is not None else ETHObservationAdapter(clock=clock)
        self.transport_factory = transport_factory or self._default_transport
        self.frame_guard = frame_guard
        self.clock = clock
        self.sleeper = sleeper
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.max_message_bytes = max_message_bytes
        self.max_session_bytes = max_session_bytes
        self.max_idle_seconds = max_idle_seconds
        self.max_session_seconds = max_session_seconds
        self._stop = threading.Event()
        self._running = threading.Lock()
        self._socket_lock = threading.Lock()
        self._socket = None
        self._last_error = None

    @staticmethod
    def _default_transport(url, timeout):
        import websocket
        if getattr(websocket, "__version__", None) != "1.8.0":
            raise RuntimeError("unsupported_websocket_client_version")
        return websocket.create_connection(url, timeout=timeout, enable_multithread=True)

    def diagnostics(self):
        """Only allowlisted error codes; never expose payloads or exception text."""
        with self._socket_lock:
            return {"last_error": self._last_error, "running": self._running.locked(),
                    "stopped": self._stop.is_set(),
                    "recommendation_eligible": False}

    def _error(self, code):
        with self._socket_lock:
            self._last_error = code

    def stop(self):
        self._stop.set()
        with self._socket_lock:
            conn = self._socket
        if conn is not None:
            try:
                conn.close()
            except Exception:
                self._error("close_error")

    def _remaining(self, deadline):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError("session_deadline")
        return remaining

    def run(self):
        if not self._running.acquire(blocking=False):
            raise RuntimeError("observer_already_running")
        deadline = self.clock() + self.max_session_seconds
        try:
            for attempt in range(self.max_attempts):
                if self._stop.is_set() or self.clock() >= deadline:
                    break
                conn = None
                generation = None
                try:
                    conn = self.transport_factory(
                        WS_URL, min(self.connect_timeout, self._remaining(deadline)))
                    with self._socket_lock:
                        self._socket = conn
                    if self._stop.is_set():
                        break
                    self.frame_guard(conn, self.max_message_bytes, self.max_session_bytes)
                    conn.settimeout(min(self.read_timeout, self._remaining(deadline)))
                    generation = self.adapter.connect_ws()
                    conn.send(json.dumps(SUBSCRIBE, separators=(",", ":")))
                    last_message = self.clock()
                    while not self._stop.is_set():
                        remaining = self._remaining(deadline)
                        idle_remaining = self.max_idle_seconds - (self.clock() - last_message)
                        if idle_remaining <= 0:
                            raise TimeoutError("websocket_idle_timeout")
                        conn.settimeout(min(self.read_timeout, remaining, idle_remaining))
                        try:
                            raw = conn.recv()
                        except Exception as exc:
                            if isinstance(exc, TimeoutError) or self._is_timeout(exc):
                                continue
                            raise
                        self._remaining(deadline)
                        if not isinstance(raw, str) or len(raw.encode("utf-8")) > self.max_message_bytes:
                            raise ValueError("invalid_ws_message_size")
                        try:
                            message = json.loads(raw)
                        except ValueError:
                            raise ValueError("invalid_ws_json") from None
                        if self.adapter.ingest_ws(message, generation):
                            last_message = self.clock()
                except Exception as exc:
                    if isinstance(exc, TimeoutError):
                        self._error("timeout")
                    elif isinstance(exc, (ValueError, TypeError)):
                        self._error("invalid_data")
                    elif isinstance(exc, RuntimeError) and str(exc) == "websocket_frame_limit_unavailable":
                        self._error("frame_guard_unavailable")
                    else:
                        self._error("transport_error")
                finally:
                    if generation is not None:
                        self.adapter.disconnect_ws(generation)
                    with self._socket_lock:
                        if self._socket is conn:
                            self._socket = None
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            self._error("close_error")
                if attempt + 1 < self.max_attempts and not self._stop.is_set():
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        break
                    delay = min(self.retry_delay, remaining)
                    if self.sleeper is time.sleep:
                        self._stop.wait(delay)
                    else:
                        self.sleeper(delay)
        finally:
            self._running.release()

    @staticmethod
    def _is_timeout(exc):
        cls = type(exc)
        return (cls.__module__ == "websocket._exceptions"
                and cls.__name__ == "WebSocketTimeoutException")
