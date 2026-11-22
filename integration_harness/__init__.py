"""Process-local, offline diagnostics for the ETH mark research models."""
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4
import os
import threading

from freshness import Consistency, evaluate_rest_ws
from freshness.compatibility import to_provenance_fields
from provenance.models import MarketObservation
from provenance.replay import ClaimResult, claim_diagnostic_binding


_domains_lock = threading.RLock()
_active_domains = set()


def _reset_after_fork():
    global _domains_lock, _active_domains
    _domains_lock = threading.RLock()
    _active_domains = set()


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
        and domain.token in _active_domains
    )


def new_clock_domain():
    with _domains_lock:
        domain = LocalClockDomain(uuid4().hex, os.getpid())
        _active_domains.add(domain.token)
        return domain


def revoke_clock_domain(domain):
    with _domains_lock:
        if not _valid_domain_locked(domain):
            return False
        _active_domains.remove(domain.token)
        return True


def rotate_clock_domain(domain):
    with _domains_lock:
        if not _valid_domain_locked(domain):
            return None
        successor = new_clock_domain()
        _active_domains.remove(domain.token)
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
