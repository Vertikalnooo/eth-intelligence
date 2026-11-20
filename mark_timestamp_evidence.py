"""Independent, read-only evidence gate for ETH mark tick timestamps.

A trade timestamp, block timestamp, HTTP Date, local receive time, or a
different price stream cannot authenticate a mark-price tick.
This module deliberately does not authorize recommendations.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math

MAX_MARK_AGE_MS = 10_000
MAX_FUTURE_SKEW_MS = 2_000

@dataclass(frozen=True)
class MarkEvidence:
    source: str
    asset: str
    price_type: str
    mark_price: str
    tick_timestamp_ms: int | None
    timestamp_semantics: str
    timestamp_bound_to_mark: bool
    provenance_verified: bool

def assess_mark_evidence(evidence: MarkEvidence, *, now_utc_ms: int) -> dict:
    """Fail closed unless an independently verified mark-tick timestamp exists."""
    def blocked(reason):
        return {"status": reason, "exchange_mark_timestamp_verified": False,
                "mark_price_freshness_verified": False, "recommendation_eligible": False}
    if evidence.source != "hyperliquid" or evidence.asset != "ETH":
        return blocked("wrong_source_or_asset")
    if evidence.price_type != "mark":
        return blocked("wrong_price_type")
    try:
        price = Decimal(evidence.mark_price)
        if not price.is_finite() or price <= 0:
            return blocked("invalid_mark_price")
    except (InvalidOperation, TypeError, ValueError):
        return blocked("invalid_mark_price")
    if (not evidence.provenance_verified or not evidence.timestamp_bound_to_mark
            or evidence.timestamp_semantics != "exchange_mark_tick"):
        return blocked("unverified_timestamp_provenance")
    timestamp = evidence.tick_timestamp_ms
    if (isinstance(timestamp, bool) or not isinstance(timestamp, int)
            or timestamp <= 0 or isinstance(now_utc_ms, bool)
            or not isinstance(now_utc_ms, int) or now_utc_ms <= 0):
        return blocked("invalid_timestamp")
    age = now_utc_ms - timestamp
    if age < -MAX_FUTURE_SKEW_MS:
        return blocked("future_timestamp")
    if age > MAX_MARK_AGE_MS:
        return blocked("stale_mark_tick")
    return {"status": "mark_timestamp_evidence_passed",
            "exchange_mark_timestamp_verified": True,
            "mark_price_freshness_verified": True,
            "recommendation_eligible": False,
            "note": "Evidence check only; independent production gates remain closed."}
