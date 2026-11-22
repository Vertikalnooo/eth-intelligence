"""Unified Stage 2 *offline diagnostic* composition; NEVER an admission gate.

No network, exchange subscription, production API or trading operations.
Returns separate assessments without upgrading local freshness to exchange proof.
"""
from dataclasses import dataclass
from enum import Enum

from freshness import Consistency, ExchangeState, TransportState, evaluate, evaluate_rest_ws
from integration_harness import valid_domain, to_market_observation, replay_diagnostic
from provenance.decision_gate import accept_market_data
from provenance.replay import ClaimResult
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


def inspect_stage2_offline(rest, ws, now_ns, domain, session, receipt_utc,
                           claim=None, store=None, binding=None):
    """Compose real research modules with a deny-all output.

    The domain check and component assessments are non-atomic diagnostic
    snapshots. A concurrent revoke is NOT proof of admission safety.
    SQLite replay may consume a synthetic key if explicitly requested.
    """
    domain_active = valid_domain(domain)
    # Nothing from caller can promote exchange evidence or admission.
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
    claim_channel = (claim.observation.source_channel
                     if type(claim) is MarkUpdateClaim
                     and getattr(claim.observation, "source_channel", None) == "WS"
                     else None)
    trust = inspect_mark_update_claim(
        claim, now_ns, domain.token,
        session=session if claim_channel == "WS" else None)
    replay = (replay_diagnostic(store, binding)
              if store is not None or binding is not None else ClaimResult.UNAVAILABLE)

    # Separately call the real deny-all gate; never interpret a synthetic
    # success as authority to enable a recommendation or an exchange timestamp.
    admission = accept_market_data(None, None, None, None, None, now_ns, "untrusted")
    if admission.accepted or admission.recommendation_eligible:
        raise AssertionError("offline acceptance boundary was unexpectedly enabled")

    reasons = tuple(dict.fromkeys(
        assessment.reason_codes + pair_reasons + trust.reason_codes
        + (("adapter_invalid",) if adapter is None else ())
        + (("replay_" + replay.value,) if replay is not ClaimResult.UNAVAILABLE
           else ("replay_unavailable",))
    ))
    return Stage2OfflineReport(
        valid_domain(domain), adapter is not None,
        assessment.transport_state, consistency, assessment.exchange_mark_state,
        trust.state, replay, reasons)
