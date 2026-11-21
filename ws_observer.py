"""Opt-in, read-only Hyperliquid ETH WebSocket observer (research only).

No import-time connections, threads, or automatic startup. A caller must
explicitly invoke run(); app.py does not do so. All times are monotonic.
The transport factory is injectable for deterministic offline tests.
"""
import json
import math
import threading
import time

from observation_adapter import ETHObservationAdapter

WS_URL = "wss://api.hyperliquid.xyz/ws"
SUBSCRIBE = {"method": "subscribe", "subscription": {"type": "activeAssetCtx", "coin": "ETH"}}


class WSObserver:
    def __init__(self, adapter=None, transport_factory=None, clock=time.monotonic,
                 sleeper=time.sleep, connect_timeout=5.0, read_timeout=2.0,
                 max_attempts=3, retry_delay=1.0, max_message_bytes=200_000,
                 max_idle_seconds=15.0):
        if (not isinstance(max_attempts, int) or isinstance(max_attempts, bool)
                or not 1 <= max_attempts <= 20):
            raise ValueError("invalid_max_attempts")
        for value in (connect_timeout, read_timeout, retry_delay, max_idle_seconds):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 30:
                raise ValueError("invalid_timeout")
        if not isinstance(max_message_bytes, int) or not 1024 <= max_message_bytes <= 1_000_000:
            raise ValueError("invalid_max_message_bytes")
        self.adapter = adapter if adapter is not None else ETHObservationAdapter(clock=clock)
        self.transport_factory = transport_factory or self._default_transport
        self.clock = clock
        self.sleeper = sleeper
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.max_message_bytes = max_message_bytes
        self.max_idle_seconds = max_idle_seconds
        self._stop = threading.Event()
        self._running = threading.Lock()
        self._socket_lock = threading.Lock()
        self._socket = None

    @staticmethod
    def _default_transport(url, timeout):
        # Optional dependency, only imported after explicit run() invocation.
        import websocket
        return websocket.create_connection(url, timeout=timeout, enable_multithread=True)

    def stop(self):
        """Safe to call from another thread; interrupts an active recv when possible."""
        self._stop.set()
        with self._socket_lock:
            conn = self._socket
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def run(self):
        """Bounded attempts, no daemon/background work; never auto-started."""
        if not self._running.acquire(blocking=False):
            raise RuntimeError("observer_already_running")
        try:
            for attempt in range(self.max_attempts):
                if self._stop.is_set():
                    break
                conn = None
                generation = None
                try:
                    conn = self.transport_factory(WS_URL, self.connect_timeout)
                    with self._socket_lock:
                        self._socket = conn
                    if self._stop.is_set():
                        break
                    conn.settimeout(self.read_timeout)
                    generation = self.adapter.connect_ws()
                    conn.send(json.dumps(SUBSCRIBE, separators=(",", ":")))
                    last_message = self.clock()
                    while not self._stop.is_set():
                        if self.clock() - last_message >= self.max_idle_seconds:
                            raise TimeoutError("websocket_idle_timeout")
                        try:
                            raw = conn.recv()
                        except TimeoutError:
                            continue
                        except Exception as exc:
                            if self._is_timeout(exc):
                                continue
                            raise
                        if not isinstance(raw, str) or len(raw.encode("utf-8")) > self.max_message_bytes:
                            raise ValueError("invalid_ws_message_size")
                        try:
                            message = json.loads(raw)
                        except ValueError:
                            raise ValueError("invalid_ws_json") from None
                        if self.adapter.ingest_ws(message, generation):
                            last_message = self.clock()
                except Exception:
                    # Transport and malformed data fail closed through disconnect.
                    pass
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
                            pass
                if attempt + 1 < self.max_attempts and not self._stop.is_set():
                    # Event.wait is interruptible; sleeper injection is for tests.
                    if self.sleeper is time.sleep:
                        self._stop.wait(self.retry_delay)
                    else:
                        self.sleeper(self.retry_delay)
        finally:
            self._running.release()

    @staticmethod
    def _is_timeout(exc):
        # websocket-client timeout class is optional and should not be imported
        # at module import time. Check the exception class by exact module/name.
        cls = type(exc)
        return (cls.__module__ == "websocket._exceptions"
                and cls.__name__ == "WebSocketTimeoutException")
