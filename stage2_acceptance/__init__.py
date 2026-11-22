"""Stage 2 unified offline diagnostics: read-only, no trusted admission.

The unified report intentionally NEVER calls SQLite, REST/WS or any other
external service. Synthetic replay claims belong to separate, explicit
low-level tests. No status can authorize a recommendation or complete Stage 2.
"""
from dataclasses import dataclass

from freshness import (
    Consistency, ExchangeState, TransportState, evaluate, evaluate_rest_ws,
    valid_observation, valid_mark_price, is_utc_datetime,
)
from integration_harness import clock_domain_guard, to_market_observation
from provenance.decision_gate import accept_market_data
from provenance.models import VerifiedBinding
from provenance.replay import ClaimResult, ReplayKey, NAMESPACE, binding_digest
from trust_research import ClaimState, MarkUpdateClaim, inspect_mark_update_claim


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
    if (not valid_observation(rest)
            or rest.source_channel != "REST"
            or type(binding) is not VerifiedBinding
            or (binding.observation_id, binding.venue, binding.symbol,
                binding.price_type, binding.mark_px, binding.timestamp_semantics)
            != (rest.observation_id, "Hyperliquid", "ETH", "mark",
                rest.mark_px, "mark_update")
            or type(binding.event_id) is not str
            or not ReplayKey("Hyperliquid", "ETH-PERP", NAMESPACE,
                             binding.event_id).valid()
            or not valid_mark_price(binding.mark_px)
            or not is_utc_datetime(binding.exchange_timestamp)):
        return False
    if type(claim) is not MarkUpdateClaim or claim.observation != rest:
        return False
    if claim.event_id is not None and claim.event_id != binding.event_id:
        return False
    if (claim.asserted_at_utc is not None
            and claim.asserted_at_utc != binding.exchange_timestamp):
        return False
    if claim.semantic_label not in (None, "mark_update"):
        return False
    try:
        binding_digest(binding)
    except (TypeError, ValueError, ArithmeticError):
        return False
    return True


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
                ("inactive_clock_domain",))

        adapter = to_market_observation(rest, receipt_utc, domain)
        assessment = evaluate(rest, now_ns, domain.token, counterpart=ws, session=session)
        consistency, pair_reasons = evaluate_rest_ws(
            rest, ws, now_ns, domain.token, session=session)

        if claim is None:
            claim = MarkUpdateClaim(rest)
        claim_channel = (
            claim.observation.source_channel
            if type(claim) is MarkUpdateClaim
            and getattr(claim.observation, "source_channel", None) == "WS"
            else None
        )
        trust = inspect_mark_update_claim(
            claim, now_ns, domain.token,
            session=session if claim_channel == "WS" else None)

        binding_matches = (
            binding is not None and _binding_matches_rest(rest, binding, claim))
        replay_requested = (store is not None or binding is not None
                            or allow_synthetic_replay_claim is not False)
        reasons = tuple(dict.fromkeys(
            assessment.reason_codes + pair_reasons + trust.reason_codes
            + (("adapter_invalid",) if adapter is None else ())
            + (("binding_structural_mismatch",) if binding is not None
               and not binding_matches else ())
            + (("synthetic_replay_disabled",) if replay_requested
               else ("replay_not_requested",))
        ))
        report = Stage2OfflineReport(
            True, adapter is not None, assessment.transport_state,
            consistency, assessment.exchange_mark_state, trust.state,
            ClaimResult.UNAVAILABLE, reasons,
            binding_structurally_matches=bool(binding_matches))

    # Under no circumstances does a synthetic report obtain true admission.
    admission = accept_market_data(None, None, None, None, None, now_ns, "untrusted")
    if admission.accepted or admission.recommendation_eligible:
        raise AssertionError("production acceptance boundary unexpectedly enabled")
    return report
