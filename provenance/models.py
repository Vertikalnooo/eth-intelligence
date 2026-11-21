from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

@dataclass(frozen=True)
class MarketObservation:
    observation_id: str
    venue: str
    symbol: str
    price_type: str
    mark_px: Decimal
    source_channel: str
    received_at_utc: datetime
    received_monotonic_ns: int

@dataclass(frozen=True)
class ProvenanceEvidence:
    observation_id: str
    method: str
    exchange_timestamp: datetime | None
    event_id: str | None = None
    block_number: int | None = None
    block_hash: str | None = None
    state_reference: str | None = None
    evidence_digest: str | None = None
    timestamp_semantics: str | None = None

@dataclass(frozen=True)
class TrustedClock:
    now_utc: datetime
    uncertainty_ms: int | None
    synchronization_source: str | None = None
    measured_at_monotonic_ns: int | None = None

@dataclass(frozen=True)
class VerifiedBinding:
    """Verifier assertion about EXACT observation, mark, event and update time."""
    observation_id: str
    venue: str
    symbol: str
    price_type: str
    mark_px: Decimal
    event_id: str
    exchange_timestamp: datetime
    timestamp_semantics: str

@dataclass(frozen=True)
class ValidationResult:
    provenance_verified: bool = False
    mark_price_freshness_verified: bool = False
    recommendation_eligible: bool = False  # permanently false: downstream gate is separate
    exchange_timestamp: datetime | None = None
    rejection_reason: str | None = None
    replay_key: str | None = None
