"""No production trust anchor. Research fixtures must never authorize market data."""
from typing import Protocol
from .models import MarketObservation, ProvenanceEvidence, VerifiedBinding

class TrustedEvidenceProvider(Protocol):
    def verify(self, observation: MarketObservation,
               evidence: ProvenanceEvidence) -> VerifiedBinding | None: ...

class NoTrustedEvidenceProvider:
    def verify(self, observation, evidence):
        return None

def is_trusted_provider(provider):
    """Deliberate deny-all until a separately audited Hyperliquid implementation."""
    return False
