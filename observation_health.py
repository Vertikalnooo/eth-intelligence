"""Offline observation-health monitor. NOT exchange mark-price freshness proof.

This module is intentionally not wired into app.py or production. Callers must
supply monotonic timestamps; receipt timestamps never authorize recommendations.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math

@dataclass(frozen=True)
class ObservationPolicy:
    degraded_after: float = 5.0
    stale_after: float = 15.0
    max_source_gap: float = 10.0

    def __post_init__(self):
        values = (self.degraded_after, self.stale_after, self.max_source_gap)
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   and math.isfinite(v) and v > 0 for v in values):
            raise ValueError("invalid_policy")
        if self.stale_after <= self.degraded_after:
            raise ValueError("invalid_threshold_order")

class ObservationHealth:
    def __init__(self, policy=None):
        self.policy = policy or ObservationPolicy()
        self.connected = False
        self.last_seen = None
        self.last_price = None
        self.last_rest_seen = None
        self.last_rest_price = None
        self.fault = False

    @staticmethod
    def _time(value):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid_monotonic_time")
        return float(value)

    @staticmethod
    def _price(value):
        try:
            price = Decimal(value)
        except (ValueError, InvalidOperation, TypeError):
            raise ValueError("invalid_price") from None
        if not price.is_finite() or price <= 0:
            raise ValueError("invalid_price")
        return price

    def connect(self):
        self.connected = True
        self.last_seen = None
        self.last_price = None
        self.fault = False

    def disconnect(self):
        self.connected = False
        self.last_seen = None
        self.last_price = None
        self.fault = False

    def websocket_observation(self, price, received_monotonic):
        now = self._time(received_monotonic)
        value = self._price(price)
        if not self.connected:
            raise ValueError("websocket_disconnected")
        if self.last_seen is not None and now < self.last_seen:
            self.fault = True
            raise ValueError("nonmonotonic_observation")
        self.last_seen, self.last_price = now, value

    def rest_observation(self, price, received_monotonic):
        now = self._time(received_monotonic)
        value = self._price(price)
        if self.last_rest_seen is not None and now < self.last_rest_seen:
            self.fault = True
            raise ValueError("nonmonotonic_rest")
        self.last_rest_seen, self.last_rest_price = now, value

    def evaluate(self, now_monotonic):
        now = self._time(now_monotonic)
        state, reason = "unknown", "no_observation"
        if not self.connected:
            state, reason = "disconnected", "websocket_disconnected"
        elif self.fault:
            state, reason = "degraded", "observation_error"
        elif self.last_seen is not None:
            age = now - self.last_seen
            if age < 0:
                state, reason = "degraded", "clock_rollback"
            elif age > self.policy.stale_after:
                state, reason = "stale", "no_recent_websocket_observation"
            elif age > self.policy.degraded_after:
                state, reason = "degraded", "websocket_observation_delayed"
            else:
                state, reason = "healthy", "recent_websocket_observation"
                if self.last_rest_seen is not None:
                    rest_age = now - self.last_rest_seen
                    if rest_age < 0:
                        state, reason = "degraded", "clock_rollback"
                    elif rest_age <= self.policy.max_source_gap and self.last_rest_price != self.last_price:
                        # Asynchronous source disagreement is a signal to inspect, not proof of corruption.
                        state, reason = "degraded", "asynchronous_source_disagreement"
        return {
            "observation_health": state,
            "observation_reason": reason,
            "exchange_timestamp": None,
            "exchange_timestamp_verified": False,
            "mark_price_freshness_verified": False,
            "recommendation_eligible": False,
        }
