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
    """Validate all caller metadata *before* operational transport classification.

    Operational receipt age, WS health and a plausible event/time assertion do
    not prove an exchange mark update. No admission decision is emitted.
    """
    if type(claim) is not MarkUpdateClaim:
        return ClaimDiagnostic(ClaimState.INVALID, ("invalid_claim_type",))

    # Untrusted metadata is validated before any STALE / DISCONNECTED return,
    # including when multiple independent fields are malformed.
    metadata_errors = []
    if claim.event_id is not None and (
            type(claim.event_id) is not str or not claim.event_id.strip()):
        metadata_errors.append("invalid_claimed_event_id")
    if claim.asserted_at_utc is not None:
        from freshness import is_utc_datetime
        if not is_utc_datetime(claim.asserted_at_utc):
            metadata_errors.append("invalid_claimed_utc")
    if claim.semantic_label is not None and (
            type(claim.semantic_label) is not str
            or claim.semantic_label not in
            ("mark_update", "snapshot", "block", "trade", "receipt")):
        metadata_errors.append("invalid_timestamp_semantics")
    if metadata_errors:
        return ClaimDiagnostic(ClaimState.INVALID, tuple(metadata_errors))

    # The transport result is only local receipt freshness / WS lifecycle.
    transport = assess_transport(claim.observation, now_monotonic_ns,
                                 clock_domain_id, policy, session)
    if transport.transport_state is TransportState.INVALID:
        return ClaimDiagnostic(ClaimState.INVALID,
                               ("invalid_observation_or_transport",) + transport.reason_codes)

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
    if transport.transport_state is TransportState.DISCONNECTED:
        reasons.append("ws_disconnected")
    elif transport.transport_state is TransportState.STALE:
        reasons.append("transport_stale")
    elif transport.transport_state is TransportState.DEGRADED:
        reasons.append("transport_degraded")
    return ClaimDiagnostic(ClaimState.UNVERIFIED, tuple(reasons))
