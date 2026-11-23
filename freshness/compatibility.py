"""Research-only compatibility contracts for PR #20; no trust or replay claim."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from . import is_utc_datetime, valid_observation

@dataclass(frozen=True)
class ProvenanceObservationFields:
    observation_id: str
    venue: str
    symbol: str
    price_type: str
    mark_px: Decimal
    source_channel: str
    received_at_utc: datetime
    received_monotonic_ns: int

def to_provenance_fields(observation, received_at_utc):
    """Maps fields only; never manufactures an exchange timestamp or evidence."""
    if not valid_observation(observation) or not is_utc_datetime(received_at_utc):
        return None
    return ProvenanceObservationFields(observation.observation_id,'Hyperliquid','ETH',
                                      'mark',observation.mark_px,observation.source_channel,
                                      received_at_utc,observation.received_monotonic_ns)

# No bridge to VerifiedBinding, TrustedClockProvider, ClaimResult or MarketDataAcceptanceResult.
# Any future replay claim must be made atomically in the separately audited gate.
