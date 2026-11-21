"""Research-only read-only ETH observation adapter; no network connections created.

REST ingress is called once for a *real* upstream fetch, never for cache hits.
WS ingress accepts only validated activeAssetCtx messages. An external read-only
subscriber may call connect_ws, ingest_ws, and disconnect_ws; no subscriber is
started by this module. All receive times use the same monotonic clock.
"""
import threading
import time
from observation_health import ObservationHealth


class ETHObservationAdapter:
    def __init__(self, monitor=None, clock=time.monotonic):
        self.monitor = monitor if monitor is not None else ObservationHealth()
        self.clock = clock
        self._lock = threading.RLock()
        self._generation = 0

    def connect_ws(self):
        with self._lock:
            self._generation += 1
            self.monitor.connect()
            return self._generation

    def disconnect_ws(self, generation):
        with self._lock:
            if generation != self._generation:
                return False
            self.monitor.disconnect()
            return True

    def ingest_ws(self, message, generation):
        """Accept a parsed Hyperliquid activeAssetCtx ETH update only."""
        with self._lock:
            if generation != self._generation:
                return False
            if not isinstance(message, dict) or message.get("channel") != "activeAssetCtx":
                return False
            data = message.get("data")
            if not isinstance(data, dict) or data.get("coin") != "ETH":
                return False
            ctx = data.get("ctx")
            if not isinstance(ctx, dict):
                raise ValueError("invalid_ws_context")
            self.monitor.websocket_observation(ctx.get("markPx"), self.clock())
            return True

    def ingest_rest_market(self, market):
        """Only call after an actual upstream REST response, never a cache hit."""
        if not isinstance(market, dict) or market.get("symbol") != "ETH/USDC" or market.get("price_type") != "mark":
            raise ValueError("invalid_rest_market")
        with self._lock:
            self.monitor.rest_observation(market.get("mark_price"), self.clock())

    def snapshot(self):
        with self._lock:
            return self.monitor.evaluate(self.clock())
