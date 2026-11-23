"""Offline-only evidence verifier registry; no trusted Hyperliquid verifier exists."""
from dataclasses import dataclass, field
from typing import Callable, Mapping
from .models import MarketObservation, ProvenanceEvidence, VerifiedBinding

Verifier = Callable[[MarketObservation, ProvenanceEvidence], VerifiedBinding | None]

@dataclass(frozen=True)
class EvidenceVerifierRegistry:
    # This is a test injection point, NOT a production trust anchor.
    verifiers: Mapping[str, Verifier] = field(default_factory=dict)

    def verify(self, observation: MarketObservation, evidence: ProvenanceEvidence) -> VerifiedBinding | None:
        verifier = self.verifiers.get(evidence.method)
        if verifier is None:
            return None
        result = verifier(observation, evidence)
        return result if type(result) is VerifiedBinding else None
