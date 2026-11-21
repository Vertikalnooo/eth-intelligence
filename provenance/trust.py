"""Production trust is intentionally unavailable; synthetic only through test harness."""
from dataclasses import dataclass
from typing import Protocol
from .models import MarketObservation, ProvenanceEvidence, VerifiedBinding

class TrustedEvidenceProvider(Protocol):
    def verify(self,observation:MarketObservation,evidence:ProvenanceEvidence)->VerifiedBinding | None: ...

class NoTrustedEvidenceProvider:
    """Only provider available to the offline research acceptance gate."""
    def verify(self,observation,evidence):
        return None
