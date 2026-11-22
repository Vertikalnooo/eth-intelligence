"""Offline trust-boundary exploration; no exchange connection and no admission path.

These verdicts are RESEARCH DIAGNOSTICS, not verification of mark updates.
No VERIFIED state exists until separately audited trust anchors are designed.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from freshness import Observation, WsSession, Policy, assess_transport, TransportState


class ClaimState(str, Enum):
    INVALID = "INVALID"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class MarkUpdateClaim:
    """Untrusted assertion supplied by a research fixture or data adapter.

    event_id, asserted_at_utc and semantic_label are never independently proven.
    """
    observation: Observation
    event_id: str | None = None
    asserted_at_utc: datetime | None = None
    semantic_label: str | None = None
    claimed_evidence: object | None = None


@dataclass(frozen=True)
class ClaimDiagnostic:
    state: ClaimState
    reason_codes: tuple[str, ...]
    exchange_provenance_verified: bool = False
    recommendation_eligible: bool = False


def inspect_mark_update_claim(claim, now_monotonic_ns, clock_domain_id,
                              policy=Policy(), session=None):
    """Fail closed even when caller supplies a plausible event/time/proof.

    UTC receipt times, WS receipt age, comparisons, content hashes, TLS and
    caller-produced event IDs are not authenticated per-update exchange times.
    """
    if type(claim) is not MarkUpdateClaim:
        return ClaimDiagnostic(ClaimState.INVALID, ("invalid_claim_type",))
    transport = assess_transport(claim.observation, now_monotonic_ns,
                                 clock_domain_id, policy, session)
    if transport.transport_state is TransportState.INVALID:
        return ClaimDiagnostic(ClaimState.INVALID,
                               ("invalid_observation_or_transport",) + transport.reason_codes)
    if transport.transport_state is TransportState.DISCONNECTED:
        return ClaimDiagnostic(ClaimState.UNVERIFIED,
                               ("ws_disconnected", "mark_update_time_unproven"))
    if transport.transport_state is TransportState.STALE:
        return ClaimDiagnostic(ClaimState.UNVERIFIED,
                               ("transport_stale", "mark_update_time_unproven"))
    if (claim.event_id is not None
            and (type(claim.event_id) is not str or not claim.event_id.strip())):
        return ClaimDiagnostic(ClaimState.INVALID, ("invalid_claimed_event_id",))
    if claim.asserted_at_utc is not None:
        from freshness import is_utc_datetime
        if not is_utc_datetime(claim.asserted_at_utc):
            return ClaimDiagnostic(ClaimState.INVALID, ("invalid_claimed_utc",))
    if claim.semantic_label is not None and (
            type(claim.semantic_label) is not str
            or claim.semantic_label not in ("mark_update", "snapshot", "block", "trade", "receipt")):
        return ClaimDiagnostic(ClaimState.INVALID, ("invalid_timestamp_semantics",))

    reasons = ["mark_update_time_unproven", "no_trusted_evidence_provider",
               "no_trusted_clock"]
    if claim.asserted_at_utc is not None:
        reasons.append("caller_timestamp_untrusted")
    if claim.event_id is not None:
        reasons.append("caller_event_id_untrusted")
    if claim.claimed_evidence is not None:
        reasons.append("caller_evidence_untrusted")
    if claim.semantic_label not in (None, "mark_update"):
        reasons.append("unrelated_timestamp_semantics")
    if transport.transport_state is TransportState.DEGRADED:
        reasons.append("transport_degraded")
    return ClaimDiagnostic(ClaimState.UNVERIFIED, tuple(reasons))
