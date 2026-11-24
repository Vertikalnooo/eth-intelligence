"""Read-only REST receipt-age telemetry using research freshness math.

This is process-local monotonic evidence only, NEVER exchange mark-update
time. No network, storage, WebSocket, orders or replay integration.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from uuid import uuid4

from freshness import Observation, Policy, assess_transport, valid_observation

_PROCESS_ID = os.getpid()
_DOMAIN_ID = uuid4().hex
_POLICY = Policy()  # FRESH <= 2s; DEGRADED <= 5s.


@dataclass(frozen=True)
class RestReceipt:
    observation: Observation | None
    process_id: int
    error_code: str | None = None


def capture(snapshot, request_start_ns, receipt_ns):
    """Record bounds from an ALREADY COMPLETED REST fetch.

    Caller-owned monotonic times; observed_at and exchange assertions
    are never interpreted as authenticated mark update timestamps.
    """
    pid = os.getpid()
    if pid != _PROCESS_ID:
        return RestReceipt(None, pid, "clock_domain_changed")
    if (type(request_start_ns) is not int or type(receipt_ns) is not int
            or request_start_ns < 0 or receipt_ns < request_start_ns):
        return RestReceipt(None, pid, "invalid_local_receipt_clock")
    if type(snapshot) is not dict:
        return RestReceipt(None, pid, "invalid_rest_snapshot")
    if (snapshot.get("symbol") != "ETH/USDC"
            or snapshot.get("source") != "hyperliquid"
            or snapshot.get("market_type") != "perpetual"
            or snapshot.get("price_type") != "mark"
            or snapshot.get("exchange_timestamp") is not None
            or snapshot.get("exchange_freshness_verified") is not False
            or snapshot.get("mark_price_freshness_verified") is not False
            or snapshot.get("recommendation_eligible") is not False):
        return RestReceipt(None, pid, "invalid_rest_snapshot")
    price_text = snapshot.get("mark_price")
    if (type(price_text) is not str or not 0 < len(price_text) <= 160
            or not price_text.isascii() or price_text != price_text.strip()):
        return RestReceipt(None, pid, "invalid_rest_mark")
    try:
        mark = Decimal(price_text)
    except (InvalidOperation, ValueError, OverflowError):
        return RestReceipt(None, pid, "invalid_rest_mark")
    observation = Observation(
        observation_id="local-rest-receipt",
        market_id="ETH-PERP", price_type="mark", source_channel="REST",
        mark_px=mark, received_monotonic_ns=receipt_ns,
        clock_domain_id=_DOMAIN_ID,
        request_start_monotonic_ns=request_start_ns,
        request_end_monotonic_ns=receipt_ns,
        exchange_timestamp=None, event_id=None,
    )
    if not valid_observation(observation):
        return RestReceipt(None, pid, "invalid_rest_mark")
    return RestReceipt(observation, pid)


def inspect(receipt, now_ns, *, cache_valid, rate_limited=False):
    """Pure diagnostics; a 'fresh' state can never authorize trading."""
    state = "NO_DATA"
    age_ms = None
    reasons = []
    if receipt is None:
        reasons.append("no_rest_receipt")
    elif (type(receipt) is not RestReceipt
          or receipt.process_id != os.getpid()
          or os.getpid() != _PROCESS_ID):
        state = "INVALID"
        reasons.append("clock_domain_changed")
    elif receipt.observation is None:
        state = "INVALID"
        reasons.append(receipt.error_code or "invalid_rest_receipt")
    else:
        assessment = assess_transport(receipt.observation, now_ns,
                                      _DOMAIN_ID, _POLICY)
        state = assessment.transport_state.value
        age_ms = assessment.transport_age_ms
        reasons.extend(code for code in assessment.reason_codes
                       if code != "missing_exchange_provenance")
        if state not in ("FRESH", "DEGRADED", "STALE", "INVALID"):
            state = "INVALID"
            reasons.append("invalid_rest_transport_state")
        # Invalid/expired cache cannot be described as usable fresh data.
        if cache_valid is not True:
            reasons.append("cache_unavailable")
            if state in ("FRESH", "DEGRADED"):
                state = "INVALID"
    if rate_limited:
        reasons.append("upstream_cooldown")
        if state in ("FRESH", "DEGRADED"):
            state = "INVALID"
    reasons.append("missing_exchange_provenance")
    return {
        "state": state,
        "age_ms": age_ms,
        "clock_basis": "process_local_monotonic",
        "source_channel": "REST",
        "reason_codes": list(dict.fromkeys(reasons)),
        "exchange_freshness_verified": False,
        "recommendation_eligible": False,
        "stage2_complete": False,
    }
