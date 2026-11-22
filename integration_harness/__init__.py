"""Process-local, offline diagnostics for the ETH mark research models."""
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4
from decimal import Decimal, InvalidOperation
import os
import threading

from freshness import Consistency, Observation, WsSession, evaluate_rest_ws, valid_observation
from freshness.compatibility import to_provenance_fields
from provenance.models import MarketObservation
from provenance.replay import ClaimResult, claim_diagnostic_binding


_domains_lock = threading.RLock()
# Exact issued object identity matters: a copied token is not an issued domain.
_active_domains = {}


def _reset_after_fork():
    global _domains_lock, _active_domains
    _domains_lock = threading.RLock()
    _active_domains = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


@dataclass(frozen=True)
class LocalClockDomain:
    token: str
    process_id: int


def _valid_domain_locked(domain):
    return (
        type(domain) is LocalClockDomain
        and type(domain.token) is str
        and type(domain.process_id) is int
        and domain.process_id == os.getpid()
        and _active_domains.get(domain.token) is domain
    )


def new_clock_domain():
    with _domains_lock:
        domain = LocalClockDomain(uuid4().hex, os.getpid())
        _active_domains[domain.token] = domain
        return domain


def revoke_clock_domain(domain):
    with _domains_lock:
        if not _valid_domain_locked(domain):
            return False
        del _active_domains[domain.token]
        return True


def rotate_clock_domain(domain):
    with _domains_lock:
        if not _valid_domain_locked(domain):
            return None
        successor = new_clock_domain()
        del _active_domains[domain.token]
        return successor


def valid_domain(domain):
    with _domains_lock:
        return _valid_domain_locked(domain)


def to_market_observation(observation, received_at_utc, domain):
    """Build the receipt-only mapping while the domain is active."""
    fields = to_provenance_fields(observation, received_at_utc)
    if fields is None:
        return None
    with _domains_lock:
        if not _valid_domain_locked(domain) or observation.clock_domain_id != domain.token:
            return None
        return MarketObservation(**vars(fields))



# Only read-only /market/eth JSON contract fields. A clock or market update
# timestamp asserted by that response is not accepted as exchange provenance.
_API_SNAPSHOT_KEYS = frozenset((
    "symbol", "market_type", "source", "price_type", "mark_price",
    "observed_at", "exchange_timestamp", "exchange_freshness_verified",
    "freshness_status", "recommendation_eligible",
    "mark_price_freshness_verified", "note", "recommendation_block_reason",
))


def rest_observation_from_api_snapshot(snapshot, observation_id, received_ns,
                                       request_start_ns, request_end_ns, domain):
    """Offline-only, strict adapter for the existing /market/eth payload.

    The caller, not the JSON payload, supplies all monotonic receipt metadata.
    Local 'observed_at' is ignored. No exchange timestamp, event ID, WS
    evidence or trusted source is minted; there is no network activity.
    """
    with clock_domain_guard(domain) as active:
        if not active or type(snapshot) is not dict:
            return None
        if not set(snapshot).issubset(_API_SNAPSHOT_KEYS):
            return None
        expected = (
            ("symbol", "ETH/USDC"), ("market_type", "perpetual"),
            ("source", "hyperliquid"), ("price_type", "mark"),
            ("freshness_status", "unknown"),
        )
        if any(type(snapshot.get(key)) is not str or snapshot[key] != value
               for key, value in expected):
            return None
        if (snapshot.get("exchange_timestamp") is not None
                or snapshot.get("exchange_freshness_verified") is not False
                or snapshot.get("mark_price_freshness_verified") is not False
                or snapshot.get("recommendation_eligible") is not False):
            return None
        price_text = snapshot.get("mark_price")
        if (type(price_text) is not str or not 0 < len(price_text) <= 160
                or not price_text.isascii() or price_text != price_text.strip()):
            return None
        try:
            price = Decimal(price_text)
        except (InvalidOperation, ValueError):
            return None
        result = Observation(
            observation_id, "ETH-PERP", "mark", "REST", price,
            received_ns, domain.token,
            request_start_monotonic_ns=request_start_ns,
            request_end_monotonic_ns=request_end_ns,
            exchange_timestamp=None, event_id=None,
        )
        return result if valid_observation(result) else None


def ws_observation_from_active_asset_ctx(message, observation_id, received_ns,
                                         session, domain):
    """Offline adapter for SDK ActiveAssetCtxMsg; never subscribes to a feed.

    The caller owns the local monotonic timestamp and the WS lifecycle.
    The message's ctx.markPx is only a price, NOT authenticated update time.
    Missing, disconnected or old-epoch sessions cannot create observations.
    """
    with clock_domain_guard(domain) as active:
        if not active or type(message) is not dict or set(message) != {"channel", "data"}:
            return None
        if message.get("channel") != "activeAssetCtx":
            return None
        data = message.get("data")
        if type(data) is not dict or data.get("coin") != "ETH":
            return None
        ctx = data.get("ctx")
        if type(ctx) is not dict:
            return None
        if (type(session) is not WsSession
                or type(session.connected) is not bool or not session.connected
                or type(session.epoch) is not str or not session.epoch
                or type(session.clock_domain_id) is not str
                or session.clock_domain_id != domain.token):
            return None
        price_text = ctx.get("markPx")
        if (type(price_text) is not str or not 0 < len(price_text) <= 160
                or not price_text.isascii() or price_text != price_text.strip()):
            return None
        try:
            price = Decimal(price_text)
        except (InvalidOperation, ValueError):
            return None
        observation = Observation(
            observation_id, "ETH-PERP", "mark", "WS", price,
            received_ns, domain.token, connection_epoch=session.epoch,
            exchange_timestamp=None, event_id=None,
        )
        return observation if valid_observation(observation) else None

def diagnostic_consistency(rest, ws, now_ns, domain, session):
    """Return the pair status from the shared offline evaluator."""
    with _domains_lock:
        if not _valid_domain_locked(domain):
            return Consistency.INVALID_INPUT
        return evaluate_rest_ws(rest, ws, now_ns, domain.token, session=session)[0]


def replay_diagnostic(store, binding, digest=None):
    """Claim a synthetic binding. Legacy raw digests are rejected."""
    if digest is not None:
        return ClaimResult.UNAVAILABLE
    return claim_diagnostic_binding(store, binding)


def combined_deny(observation, now_ns, domain, session=None, replay_result=None):
    """Research diagnostics are never an admission decision."""
    return False


@contextmanager
def clock_domain_guard(domain):
    """Serialize a complete offline diagnostic snapshot against revoke/rotate.

    This is a local process lock, NOT a trusted clock or production gate.
    The caller must not await external IO or use this for live admission.
    """
    with _domains_lock:
        yield _valid_domain_locked(domain)
