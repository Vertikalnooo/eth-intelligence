"""Research-only compatibility contracts for PR #20; no trust or replay claim."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from . import Observation

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
    if (type(observation) is not Observation
            or (observation.market_id,observation.price_type) != ('ETH-PERP','mark')
            or observation.source_channel not in ('REST','WS')
            or type(observation.mark_px) is not Decimal
            or not observation.mark_px.is_finite() or observation.mark_px<=0
            or type(observation.received_monotonic_ns) is not int
            or observation.received_monotonic_ns<0
            or type(received_at_utc) is not datetime
            or received_at_utc.tzinfo is None
            or received_at_utc.utcoffset()!=timedelta(0)):
        return None
    return ProvenanceObservationFields(observation.observation_id,'Hyperliquid','ETH',
                                      'mark',observation.mark_px,observation.source_channel,
                                      received_at_utc,observation.received_monotonic_ns)

# No bridge to VerifiedBinding, TrustedClockProvider, ClaimResult or MarketDataAcceptanceResult.
# Any future replay claim must be made atomically in the separately audited gate.
