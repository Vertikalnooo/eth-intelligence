"""Offline-only acceptance boundary: unconditional denial without real trust anchors."""
from dataclasses import dataclass
from datetime import timedelta
from .trust import is_trusted_provider
from .clock import is_trusted_clock_provider, check_clock
from .replay import ReplayKey, binding_digest, ClaimResult

@dataclass(frozen=True)
class MarketDataAcceptanceResult:
    accepted: bool = False
    recommendation_eligible: bool = False
    reason: str = 'untrusted_evidence'

@dataclass(frozen=True)
class OfflineDiagnostic:
    checks_passed: bool = False
    reason: str = 'untrusted_evidence'
    # Never has an accepted/recommendation_eligible property.

def inspect_offline_pipeline(observation, evidence, verified_binding, clock_sample,
                             current_monotonic_ns, store, approved_clock_source,
                             max_age_us=5000000, max_uncertainty_us=1000000):
    """TEST DIAGNOSTIC ONLY. Synthetic binding and sample are not authenticated.
    Performs the ordered checks and consumes a replay key in an offline store.
    Cannot authorize acceptance or recommendations.
    """
    try:
        from .models import VerifiedBinding
        if (type(verified_binding) is not VerifiedBinding or
                verified_binding.observation_id != observation.observation_id or
                (verified_binding.venue, verified_binding.symbol, verified_binding.price_type,
                 verified_binding.mark_px, verified_binding.event_id,
                 verified_binding.exchange_timestamp, verified_binding.timestamp_semantics) !=
                (observation.venue, observation.symbol, observation.price_type,
                 observation.mark_px, evidence.event_id, evidence.exchange_timestamp, 'mark_update')):
            return OfflineDiagnostic(reason='binding_mismatch')
        if type(max_age_us) is not int or max_age_us <= 0:
            return OfflineDiagnostic(reason='invalid_policy')
        checked = check_clock(clock_sample, current_monotonic_ns,
                              approved_clock_source, max_uncertainty_us)
        if checked is None:
            return OfflineDiagnostic(reason='clock_untrusted')
        now, uncertainty = checked
        age_us = (now - verified_binding.exchange_timestamp) // timedelta(microseconds=1)
        if age_us < -uncertainty:
            return OfflineDiagnostic(reason='invalid_timestamp')
        if age_us + uncertainty > max_age_us:
            return OfflineDiagnostic(reason='stale_price')
        key = ReplayKey('Hyperliquid', 'ETH-PERP', 'mark-update', verified_binding.event_id)
        result = store.claim_once(key, binding_digest(verified_binding))
        if result is not ClaimResult.CLAIMED:
            return OfflineDiagnostic(reason='replay_' + result.value)
        return OfflineDiagnostic(checks_passed=True, reason='offline_checks_only')
    except Exception:
        return OfflineDiagnostic(reason='verification_failed')

def accept_market_data(observation, evidence, provider, clock_provider, store,
                       current_monotonic_ns, approved_clock_source, max_age_us=5000000,
                       max_uncertainty_us=1000000):
    """Real acceptance stays blocked, regardless of injected providers or clock."""
    try:
        if not is_trusted_provider(provider):
            return MarketDataAcceptanceResult(reason='untrusted_provider')
        if not is_trusted_clock_provider(clock_provider):
            return MarketDataAcceptanceResult(reason='clock_untrusted')
        return MarketDataAcceptanceResult(reason='no_production_trust_anchor')
    except Exception:
        return MarketDataAcceptanceResult(reason='verification_failed')
