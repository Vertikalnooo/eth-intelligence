"""Fail-closed read-only freshness gate; not wired to production.

Only trusted ingestion code may set source_verified=True after independent
verification. A raw exchange payload or HTTP fetch time is NOT proof.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math

MAX_EXCHANGE_AGE_SECONDS = 10.0
MAX_RECEIVE_AGE_SECONDS = 5.0
MAX_FUTURE_SKEW_SECONDS = 2.0

@dataclass(frozen=True)
class MarketObservation:
    price: str
    exchange_timestamp_ms: int | None
    received_monotonic: float
    source_verified: bool
    price_type: str = "mark"

class FreshnessGuard:
    def __init__(self):
        self._observation = None
        self._connected = False

    def connect(self):
        self._connected = True
        self._observation = None

    def disconnect(self):
        self._connected = False
        self._observation = None

    def ingest(self, observation):
        self._observation = observation if self._connected else None

    def evaluate(self, *, now_utc_ms, now_monotonic):
        def blocked(reason):
            return {"freshness_status": reason, "exchange_freshness_verified": False,
                    "recommendation_eligible": False}
        if not self._connected:
            return blocked("disconnected")
        obs = self._observation
        if obs is None:
            return blocked("no_data")
        if not obs.source_verified:
            return blocked("unverified_source")
        if obs.price_type != "mark":
            return blocked("wrong_price_type")
        try:
            price = Decimal(obs.price)
            if not price.is_finite() or price <= 0:
                return blocked("invalid_price")
        except (InvalidOperation, ValueError, TypeError):
            return blocked("invalid_price")
        if isinstance(obs.exchange_timestamp_ms, bool) or not isinstance(obs.exchange_timestamp_ms, int) or obs.exchange_timestamp_ms <= 0:
            return blocked("missing_exchange_timestamp")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                   for x in (now_utc_ms, now_monotonic, obs.received_monotonic)):
            return blocked("invalid_clock")
        received_age = now_monotonic - obs.received_monotonic
        if received_age < 0 or received_age > MAX_RECEIVE_AGE_SECONDS:
            return blocked("stale_receive")
        exchange_age = (now_utc_ms - obs.exchange_timestamp_ms) / 1000
        if exchange_age < -MAX_FUTURE_SKEW_SECONDS:
            return blocked("future_exchange_timestamp")
        if exchange_age > MAX_EXCHANGE_AGE_SECONDS:
            return blocked("stale_exchange")
        return {"freshness_status": "fresh", "exchange_freshness_verified": True,
                "recommendation_eligible": False, "freshness_gate_passed": True,
                "note": "Freshness gate only; other recommendation gates remain blocked."}
