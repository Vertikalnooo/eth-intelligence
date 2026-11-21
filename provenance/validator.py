"""Pure fail-closed validation. No network, exchange or trading interfaces."""
from datetime import datetime, timedelta
from decimal import Decimal
from .models import MarketObservation, ProvenanceEvidence, TrustedClock, ValidationResult
from .policy import ProvenancePolicy
from .evidence import EvidenceVerifierRegistry
from . import reasons as R

def _utc(value: object) -> bool:
    return (isinstance(value, datetime) and value.tzinfo is not None
            and value.utcoffset() == timedelta(0))

def _reject(reason: str) -> ValidationResult:
    return ValidationResult(rejection_reason=reason)

def validate_provenance(
    observation: MarketObservation,
    evidence: ProvenanceEvidence | None,
    policy: ProvenancePolicy,
    clock: TrustedClock,
    verifier_registry: EvidenceVerifierRegistry,
) -> ValidationResult:
    try:
        if (not isinstance(observation, MarketObservation)
                or not isinstance(policy, ProvenancePolicy) or not policy.valid()
                or not isinstance(clock, TrustedClock)
                or not isinstance(verifier_registry, EvidenceVerifierRegistry)):
            return _reject(R.INVALID_INPUT)
        if (not isinstance(observation.observation_id, str) or not observation.observation_id
                or not isinstance(observation.source_channel, str) or not observation.source_channel
                or type(observation.received_monotonic_ns) is not int
                or observation.received_monotonic_ns < 0
                or not _utc(observation.received_at_utc)
                or not isinstance(observation.mark_px, Decimal)
                or not observation.mark_px.is_finite() or observation.mark_px <= 0):
            return _reject(R.INVALID_INPUT)
        if (observation.venue, observation.symbol, observation.price_type) != ('Hyperliquid', 'ETH', 'mark'):
            return _reject(R.UNSUPPORTED_INSTRUMENT)
        if evidence is None:
            return _reject(R.MISSING_EVIDENCE)
        if (not isinstance(evidence, ProvenanceEvidence)
                or not isinstance(evidence.method, str) or not evidence.method):
            return _reject(R.INVALID_INPUT)
        if evidence.observation_id != observation.observation_id:
            return _reject(R.MISMATCHED_OBSERVATION)
        if not _utc(evidence.exchange_timestamp):
            return _reject(R.INVALID_TIMESTAMP)
        if evidence.method not in verifier_registry.verifiers:
            return _reject(R.UNTRUSTED_METHOD)
        if not verifier_registry.verify(observation, evidence):
            return _reject(R.VERIFICATION_FAILED)
        if (not _utc(clock.now_utc) or type(clock.uncertainty_ms) is not int
                or clock.uncertainty_ms < 0
                or clock.uncertainty_ms > policy.max_clock_uncertainty_ms):
            return _reject(R.CLOCK_UNTRUSTED)
        age_ms = (clock.now_utc - evidence.exchange_timestamp).total_seconds() * 1000
        if age_ms < -clock.uncertainty_ms:
            return _reject(R.INVALID_TIMESTAMP)
        if age_ms + clock.uncertainty_ms > policy.max_age_ms:
            return _reject(R.STALE_PRICE)
        if not policy.source_healthy:
            return _reject(R.SOURCE_UNHEALTHY)
        return ValidationResult(True, True, True, evidence.exchange_timestamp, None)
    except Exception:
        return _reject(R.VERIFICATION_FAILED)
