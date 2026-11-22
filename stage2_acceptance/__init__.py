"""Unified offline Stage 2 diagnostic — never a production admission gate.

All synthetic replay claims are explicit opt-in, structurally bound to the
current REST observation and validated only after other offline checks pass.
No network, exchange subscriptions, or trading.
"""
from dataclasses import dataclass
from decimal import Decimal

from freshness import (
    Consistency, ExchangeState, TransportState, evaluate, evaluate_rest_ws,
    valid_observation, valid_mark_price, is_utc_datetime,
)
from integration_harness import (
    clock_domain_guard, to_market_observation, replay_diagnostic,
)
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


def _binding_matches_rest(rest, binding, claim):
    """Strict structural matching only; never authenticates exchange evidence."""
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
    # When the fixture supplies a claimed event/time, it must agree exactly
    # with the binding; no invented exchange timestamp is accepted.
    if type(claim) is not MarkUpdateClaim:
        return False
    if claim.observation != rest:
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
    """Process-local consistent *diagnostic* snapshot, permanently deny-only.

    A synthetic replay claim requires explicit opt-in and valid fresh,
    matching REST/WS, valid receipt adapter and structurally matched binding.
    This is NOT verification; no real trusted timestamp/provider exists.
    """
    # Do not permit accidental truthy values (e.g. int(1)) to opt in.
    if type(allow_synthetic_replay_claim) is not bool:
        allow_synthetic_replay_claim = False
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

        # No replay write occurs until all synthetic preconditions are met.
        replay = ClaimResult.UNAVAILABLE
        replay_requested = store is not None or binding is not None
        eligible_for_synthetic_claim = (
            allow_synthetic_replay_claim is True
            and replay_requested
            and adapter is not None
            and assessment.transport_state is TransportState.FRESH
            and consistency is Consistency.CONSISTENT
            and trust.state is ClaimState.UNVERIFIED
            and _binding_matches_rest(rest, binding, claim)
        )
        if eligible_for_synthetic_claim:
            replay = replay_diagnostic(store, binding)

        # Production admission stays independent and always denies.
        admission = accept_market_data(None, None, None, None, None, now_ns, "untrusted")
        if admission.accepted or admission.recommendation_eligible:
            raise AssertionError("offline acceptance boundary unexpectedly enabled")

        replay_reason = (
            ("replay_" + replay.value,) if eligible_for_synthetic_claim else
            ("synthetic_replay_not_authorized",) if replay_requested else
            ("replay_not_requested",)
        )
        reasons = tuple(dict.fromkeys(
            assessment.reason_codes + pair_reasons + trust.reason_codes
            + (("adapter_invalid",) if adapter is None else ())
            + replay_reason
        ))
        return Stage2OfflineReport(
            True, adapter is not None, assessment.transport_state,
            consistency, assessment.exchange_mark_state,
            trust.state, replay, reasons)
