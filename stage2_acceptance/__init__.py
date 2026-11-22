"""Stage 2 unified offline diagnostics: read-only, no trusted admission.

The unified report intentionally NEVER calls SQLite, REST/WS or any other
external service. Synthetic replay claims belong to separate, explicit
low-level tests. No status can authorize a recommendation or complete Stage 2.
"""
from dataclasses import dataclass

from freshness import (
    Consistency, ExchangeState, TransportState, assess_transport, evaluate_rest_ws,
    is_utc_datetime, valid_observation,
)
from integration_harness import clock_domain_guard, to_market_observation
from provenance.decision_gate import accept_market_data
from provenance.models import VerifiedBinding
from provenance.replay import ClaimResult, ReplayKey, NAMESPACE, binding_digest
from trust_research import ClaimDiagnostic, ClaimState, MarkUpdateClaim, inspect_mark_update_claim


@dataclass(frozen=True)
class Stage2OfflineReport:
    domain_active_at_check: bool
    adapter_valid: bool
    transport_state: TransportState
    consistency: Consistency
    exchange_state: ExchangeState
    trust_claim_state: ClaimState
    replay_result: ClaimResult
    reason_codes: tuple[str, ...]
    accepted: bool = False
    recommendation_eligible: bool = False
    stage2_complete: bool = False
    snapshot_consistent: bool = True
    binding_structurally_matches: bool = False


def _binding_matches_rest(rest, binding, claim):
    """Structural correspondence is NEVER authenticated source evidence."""
    if (not valid_observation(rest) or rest.source_channel != "REST"
            or type(claim) is not MarkUpdateClaim
            or not valid_observation(claim.observation) or claim.observation != rest
            or type(binding) is not VerifiedBinding
            or not ReplayKey("Hyperliquid", "ETH-PERP", NAMESPACE, binding.event_id).valid()):
        return False
    try:
        binding_digest(binding)
    except (TypeError, ValueError, ArithmeticError):
        return False

    event_ids = (rest.event_id, claim.event_id)
    timestamps = (rest.exchange_timestamp, claim.asserted_at_utc)
    return (
        (binding.observation_id, binding.mark_px) == (rest.observation_id, rest.mark_px)
        and all(value is None or (type(value) is str and value == binding.event_id)
                for value in event_ids)
        and all(value is None or (is_utc_datetime(value) and value == binding.exchange_timestamp)
                for value in timestamps)
        and (claim.semantic_label is None or
             (type(claim.semantic_label) is str and claim.semantic_label == "mark_update"))
    )


def _inspect_snapshot_claim(claim, rest, ws, now_ns, token, session):
    observation = claim.observation if type(claim) is MarkUpdateClaim else None
    is_ws = valid_observation(observation) and observation.source_channel == "WS"
    trust = inspect_mark_update_claim(claim, now_ns, token, session=session if is_ws else None)
    if trust.state is ClaimState.INVALID:
        return trust
    counterpart = ws if is_ws else rest
    if not valid_observation(counterpart) or observation != counterpart:
        return ClaimDiagnostic(ClaimState.INVALID, ("claim_observation_outside_snapshot",))
    return trust


def inspect_stage2_offline(rest, ws, now_ns, domain, session, receipt_utc,
                           claim=None, store=None, binding=None,
                           allow_synthetic_replay_claim=False):
    """Observe a process-local coherent snapshot WITHOUT replay mutations.

    Compatibility parameters `store` and `allow_synthetic_replay_claim`
    are ignored for writes. Even explicit True does not consume a replay key.
    Call low-level synthetic replay experiments separately, never as part of
    a trust or production admission decision.
    """
    with clock_domain_guard(domain) as domain_active:
        if not domain_active:
            return Stage2OfflineReport(
                False, False, TransportState.INVALID, Consistency.INVALID_INPUT,
                ExchangeState.UNVERIFIED, ClaimState.INVALID, ClaimResult.UNAVAILABLE,
                ("inactive_clock_domain",), snapshot_consistent=False)

        primary_rest = rest if valid_observation(rest) and rest.source_channel == "REST" else None
        adapter = to_market_observation(primary_rest, receipt_utc, domain)
        assessment = assess_transport(primary_rest, now_ns, domain.token)
        consistency, pair_reasons = evaluate_rest_ws(
            rest, ws, now_ns, domain.token, session=session)

        if claim is None:
            claim = MarkUpdateClaim(rest)
        trust = _inspect_snapshot_claim(claim, rest, ws, now_ns, domain.token, session)

        binding_matches = (
            binding is not None and _binding_matches_rest(rest, binding, claim))
        replay_requested = (store is not None or binding is not None
                            or allow_synthetic_replay_claim is not False)
        reasons = tuple(dict.fromkeys(
            assessment.reason_codes + ("missing_exchange_provenance",)
            + pair_reasons + trust.reason_codes
            + (("adapter_invalid",) if adapter is None else ())
            + (("binding_structural_mismatch",) if binding is not None
               and not binding_matches else ())
            + (("synthetic_replay_disabled",) if replay_requested
               else ("replay_not_requested",))
        ))
        report = Stage2OfflineReport(
            True, adapter is not None, assessment.transport_state,
            consistency, ExchangeState.UNVERIFIED, trust.state,
            ClaimResult.UNAVAILABLE, reasons,
            binding_structurally_matches=bool(binding_matches))

    # Under no circumstances does a synthetic report obtain true admission.
    admission = accept_market_data(None, None, None, None, None, now_ns, "untrusted")
    if admission.accepted or admission.recommendation_eligible:
        raise AssertionError("production acceptance boundary unexpectedly enabled")
    return report
