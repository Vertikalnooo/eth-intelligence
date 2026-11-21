"""Separate offline market-data gate, always deny without a real trust anchor."""
from dataclasses import dataclass
from enum import Enum
from .trust import NoTrustedEvidenceProvider
from .replay import ClaimResult,ReplayKey
from .clock import check_clock
from .models import VerifiedBinding
from .validator import _utc,_microseconds

@dataclass(frozen=True)
class MarketDataAcceptanceResult:
    accepted: bool=False
    recommendation_eligible: bool=False
    reason: str='untrusted_evidence'

def accept_market_data(observation,evidence,provider,clock_provider,store,
                       current_monotonic_ns,approved_clock_source,max_age_us=5000000,
                       max_uncertainty_us=1000000):
    """No caller-provided verifier can authorize acceptance in v1.1 research."""
    try:
        if type(provider) is not NoTrustedEvidenceProvider:
            return MarketDataAcceptanceResult(reason='untrusted_provider')
        binding=provider.verify(observation,evidence)
        if binding is None:
            return MarketDataAcceptanceResult()
        # Reserved for separately approved trusted implementation; unreachable.
        return MarketDataAcceptanceResult(reason='no_production_trust_anchor')
    except Exception:
        return MarketDataAcceptanceResult(reason='verification_failed')
