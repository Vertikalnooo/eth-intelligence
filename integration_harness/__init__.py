"""Offline-only integration boundary for pinned PR20 and PR21 research models.

Never an admission gate. No exchange, network, or production integration.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
import os
import threading

from freshness import Observation, WsSession, assess_transport, evaluate, TransportState
from provenance.models import MarketObservation
from provenance.decision_gate import accept_market_data
from provenance.replay import ClaimResult, SQLiteReplayStore, ReplayKey

_domains_lock = threading.RLock()
_active_domains = set()
_registry_pid = os.getpid()

def _refresh_process_registry():
    global _registry_pid
    pid = os.getpid()
    if _registry_pid != pid:
        _active_domains.clear()
        _registry_pid = pid

@dataclass(frozen=True)
class LocalClockDomain:
    token: str
    process_id: int

def new_clock_domain():
    with _domains_lock:
        _refresh_process_registry()
        domain = LocalClockDomain(uuid4().hex, os.getpid())
        _active_domains.add(domain.token)
        return domain

def revoke_clock_domain(domain):
    """Revoke an issued local token; repeat revocation is harmless."""
    with _domains_lock:
        _refresh_process_registry()
        if type(domain) is not LocalClockDomain or domain.process_id != os.getpid():
            return False
        if domain.token not in _active_domains:
            return False
        _active_domains.remove(domain.token)
        return True

def rotate_clock_domain(domain):
    """Explicit lifecycle transition; an invalid domain cannot mint a successor."""
    with _domains_lock:
        if not valid_domain(domain):
            return None
        _active_domains.remove(domain.token)
        return new_clock_domain()

def valid_domain(domain):
    with _domains_lock:
        _refresh_process_registry()
        return (type(domain) is LocalClockDomain and type(domain.token) is str
                and len(domain.token)==32 and type(domain.process_id) is int
                and domain.process_id==os.getpid() and domain.token in _active_domains)

def to_market_observation(observation, received_at_utc, domain):
    """Explicitly maps receipt fields, never fabricates exchange evidence."""
    from freshness import _valid_observation
    if (not valid_domain(domain) or not _valid_observation(observation)
            or type(observation) is not Observation
            or observation.clock_domain_id!=domain.token
            or not observation.observation_id or type(observation.observation_id) is not str
            or (observation.market_id,observation.price_type) != ('ETH-PERP','mark')
            or observation.source_channel not in ('REST','WS')
            or type(observation.mark_px) is not Decimal
            or not observation.mark_px.is_finite() or observation.mark_px<=0
            or type(observation.received_monotonic_ns) is not int
            or observation.received_monotonic_ns<0
            or type(received_at_utc) is not datetime or received_at_utc.tzinfo is None
            or received_at_utc.utcoffset()!=timedelta(0)):
        return None
    if observation.source_channel=='REST':
        if (type(observation.request_start_monotonic_ns) is not int
                or type(observation.request_end_monotonic_ns) is not int
                or not 0<=observation.request_start_monotonic_ns<=observation.request_end_monotonic_ns<=observation.received_monotonic_ns
                or observation.connection_epoch is not None):
            return None
    return MarketObservation(observation.observation_id,'Hyperliquid','ETH','mark',
                             observation.mark_px,observation.source_channel,
                             received_at_utc,observation.received_monotonic_ns)

def diagnostic_consistency(rest,ws,now_ns,domain,session):
    """Both inputs must be fresh in the same live local domain."""
    from freshness import compare_rest_ws, Consistency
    if not valid_domain(domain) or type(now_ns) is not int:
        return Consistency.INVALID_INPUT
    a=assess_transport(rest,now_ns,domain.token)
    b=assess_transport(ws,now_ns,domain.token,session=session)
    if a.transport_state is TransportState.INVALID or b.transport_state is TransportState.INVALID:
        return Consistency.INVALID_INPUT
    if a.transport_state is TransportState.DISCONNECTED or b.transport_state is TransportState.DISCONNECTED:
        return Consistency.DISCONNECTED
    if a.transport_state is TransportState.STALE or b.transport_state is TransportState.STALE:
        return Consistency.STALE
    if a.transport_state is not TransportState.FRESH or b.transport_state is not TransportState.FRESH:
        return Consistency.NOT_COMPARABLE
    return compare_rest_ws(rest,ws,ws_session=session)

def replay_diagnostic(store,key,digest):
    """Offline-only atomic claim, returns a diagnostic; never eligibility."""
    if type(store) is not SQLiteReplayStore or type(key) is not ReplayKey or not key.valid() or type(digest) is not bytes or len(digest) != 32:
        return ClaimResult.UNAVAILABLE
    try:
        result=store.claim_once(key,digest)
        return result if type(result) is ClaimResult else ClaimResult.UNAVAILABLE
    except Exception:
        return ClaimResult.UNAVAILABLE

def combined_deny(observation,now_ns,domain,session=None,replay_result=None):
    """Research diagnostics and production acceptance are intentionally disjoint."""
    if not valid_domain(domain):
        return False
    assessment=evaluate(observation,now_ns,domain.token,session=session)
    acceptance=accept_market_data(None,None,None,None,None,now_ns,'untrusted')
    return bool(assessment.recommendation_eligible and acceptance.accepted)
