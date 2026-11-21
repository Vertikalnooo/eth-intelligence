"""Isolated observation-quality monitor; never proves exchange mark-price freshness.

All timestamps must be from the same monotonic clock. REST and WS observations
are asynchronous; disagreement is a heuristic, not evidence of stale markPx.
No production wiring, network I/O, or trading authorization.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from threading import RLock
import math


@dataclass(frozen=True)
class ObservationPolicy:
    degraded_after: float = 5.0
    stale_after: float = 15.0
    max_source_gap: float = 10.0
    relative_price_tolerance: str = "0"

    def __post_init__(self):
        values = (self.degraded_after, self.stale_after, self.max_source_gap)
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   and math.isfinite(v) and v > 0 for v in values):
            raise ValueError("invalid_policy")
        if self.stale_after <= self.degraded_after:
            raise ValueError("invalid_threshold_order")
        try:
            tolerance = Decimal(str(self.relative_price_tolerance))
        except (ValueError, InvalidOperation, TypeError):
            raise ValueError("invalid_tolerance") from None
        if not tolerance.is_finite() or tolerance < 0 or tolerance >= 1:
            raise ValueError("invalid_tolerance")


class ObservationHealth:
    def __init__(self, policy=None):
        self.policy = policy if policy is not None else ObservationPolicy()
        self._lock = RLock()
        self.connected = False
        self.last_seen = None
        self.last_price = None
        self.last_rest_seen = None
        self.last_rest_price = None
        self.ws_fault = False
        self.rest_fault = False

    @staticmethod
    def _time(value):
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value < 0):
            raise ValueError("invalid_monotonic_time")
        return float(value)

    @staticmethod
    def _price(value):
        try:
            price = Decimal(str(value))
        except (ValueError, InvalidOperation, TypeError):
            raise ValueError("invalid_price") from None
        if not price.is_finite() or price <= 0:
            raise ValueError("invalid_price")
        return price

    def _reset(self):
        self.last_seen = self.last_price = None
        self.last_rest_seen = self.last_rest_price = None
        self.ws_fault = self.rest_fault = False

    def connect(self):
        """Start a fresh session; a new connection invalidates both source caches."""
        with self._lock:
            self._reset()
            self.connected = True

    def disconnect(self):
        with self._lock:
            self._reset()
            self.connected = False

    def websocket_observation(self, price, received_monotonic):
        with self._lock:
            if not self.connected:
                raise ValueError("websocket_disconnected")
            try:
                now = self._time(received_monotonic)
                value = self._price(price)
                if self.last_seen is not None and now < self.last_seen:
                    raise ValueError("nonmonotonic_observation")
            except (ValueError, TypeError):
                self.ws_fault = True
                raise
            # Faults are sticky until a new session (connect/disconnect).
            self.last_seen, self.last_price = now, value

    def rest_observation(self, price, received_monotonic):
        with self._lock:
            try:
                now = self._time(received_monotonic)
                value = self._price(price)
                if self.last_rest_seen is not None and now < self.last_rest_seen:
                    raise ValueError("nonmonotonic_rest")
            except (ValueError, TypeError):
                self.rest_fault = True
                raise
            self.last_rest_seen, self.last_rest_price = now, value

    def _source_state(self, now, seen, fault):
        if fault:
            return "degraded", "observation_error"
        if seen is None:
            return "unknown", "no_observation"
        age = now - seen
        if age < 0:
            return "degraded", "clock_rollback"
        if age > self.policy.stale_after:
            return "stale", "no_recent_observation"
        if age > self.policy.degraded_after:
            return "degraded", "observation_delayed"
        return "healthy", "recent_observation"

    def evaluate(self, now_monotonic):
        with self._lock:
            now = self._time(now_monotonic)
            if self.connected:
                ws, ws_reason = self._source_state(now, self.last_seen, self.ws_fault)
            else:
                ws, ws_reason = "disconnected", "websocket_disconnected"
            rest, rest_reason = self._source_state(now, self.last_rest_seen, self.rest_fault)
            statuses = (ws, rest)
            if "disconnected" in statuses:
                overall = "disconnected"
            elif "stale" in statuses:
                overall = "stale"
            elif "degraded" in statuses:
                overall = "degraded"
            elif "unknown" in statuses:
                overall = "unknown"
            else:
                overall = "healthy"
            reason = "source_health"
            disagreement = False
            if (ws == rest == "healthy" and self.last_seen is not None
                    and self.last_rest_seen is not None
                    and abs(self.last_seen - self.last_rest_seen) <= self.policy.max_source_gap):
                tolerance = Decimal(str(self.policy.relative_price_tolerance))
                disagreement = (abs(self.last_price - self.last_rest_price)
                                / max(self.last_price, self.last_rest_price) > tolerance)
                # Diagnostic only: asynchronous prices cannot downgrade source health.
            return {
                "ws_health": ws, "ws_reason": ws_reason,
                "rest_health": rest, "rest_reason": rest_reason,
                "overall_health": overall,
                "observation_health": overall,
                "observation_reason": reason,
                "asynchronous_price_disagreement": disagreement,
                "exchange_timestamp": None,
                "exchange_timestamp_verified": False,
                "mark_price_freshness_verified": False,
                "recommendation_eligible": False,
            }
