"""Offline-only verifier registry. No trusted live verifier is bundled."""
from dataclasses import dataclass, field
from typing import Callable
from .models import MarketObservation, ProvenanceEvidence

Verifier = Callable[[MarketObservation, ProvenanceEvidence], bool]

@dataclass(frozen=True)
class EvidenceVerifierRegistry:
    # Only independently authenticated price/event/timestamp binding is proof.
    # A matching observation_id or digest alone is never proof.
    verifiers: dict[str, Verifier] = field(default_factory=dict)

    def verify(self, observation: MarketObservation, evidence: ProvenanceEvidence) -> bool:
        verifier = self.verifiers.get(evidence.method)
        return verifier is not None and verifier(observation, evidence) is True
