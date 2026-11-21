"""Pure offline research validator; no production verifiers or recommendation gate."""
from datetime import datetime, timedelta
from decimal import Decimal
from .models import MarketObservation, ProvenanceEvidence, TrustedClock, ValidationResult, VerifiedBinding
from .policy import ProvenancePolicy
from .evidence import EvidenceVerifierRegistry
from . import reasons as R

MARK_UPDATE = 'mark_update'

def _utc(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == timedelta(0)

def _reject(reason: str) -> ValidationResult:
    return ValidationResult(rejection_reason=reason)

def _microseconds(delta: timedelta) -> int:
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds

def validate_provenance(
    observation: MarketObservation,
    evidence: ProvenanceEvidence | None,
    policy: ProvenancePolicy,
    clock: TrustedClock,
    verifier_registry: EvidenceVerifierRegistry,
    consumed_replay_keys: frozenset[str] = frozenset(),
) -> ValidationResult:
    try:
        if (not isinstance(observation, MarketObservation)
                or not isinstance(policy, ProvenancePolicy) or not policy.valid()
                or not isinstance(clock, TrustedClock)
                or not isinstance(verifier_registry, EvidenceVerifierRegistry)
                or type(consumed_replay_keys) is not frozenset
                or any(type(k) is not str for k in consumed_replay_keys)):
            return _reject(R.INVALID_INPUT)
        if (not isinstance(observation.observation_id, str) or not observation.observation_id
                or not isinstance(observation.source_channel, str) or not observation.source_channel
                or type(observation.received_monotonic_ns) is not int
                or observation.received_monotonic_ns < 0
                or not _utc(observation.received_at_utc)
                or type(observation.mark_px) is not Decimal
                or not observation.mark_px.is_finite() or observation.mark_px <= 0):
            return _reject(R.INVALID_INPUT)
        if (observation.venue, observation.symbol, observation.price_type) != ('Hyperliquid', 'ETH', 'mark'):
            return _reject(R.UNSUPPORTED_INSTRUMENT)
        if evidence is None:
            return _reject(R.MISSING_EVIDENCE)
        if (not isinstance(evidence, ProvenanceEvidence)
                or type(evidence.method) is not str or not evidence.method):
            return _reject(R.INVALID_INPUT)
        if evidence.observation_id != observation.observation_id:
            return _reject(R.MISMATCHED_OBSERVATION)
        if not _utc(evidence.exchange_timestamp) or evidence.timestamp_semantics != MARK_UPDATE:
            return _reject(R.INVALID_TIMESTAMP)
        # No audited production verifier exists. Explicit synthetic test mode ONLY.
        if (not policy.allow_synthetic_test_verifier
                or evidence.method != 'synthetic_only'
                or evidence.method not in verifier_registry.verifiers):
            return _reject(R.UNTRUSTED_METHOD)
        binding = verifier_registry.verify(observation, evidence)
        if (type(binding) is not VerifiedBinding
                or (binding.observation_id, binding.venue, binding.symbol, binding.price_type,
                    binding.mark_px, binding.event_id, binding.exchange_timestamp,
                    binding.timestamp_semantics)
                != (observation.observation_id, observation.venue, observation.symbol,
                    observation.price_type, observation.mark_px, evidence.event_id,
                    evidence.exchange_timestamp, MARK_UPDATE)
                or not isinstance(binding.event_id, str) or not binding.event_id):
            return _reject(R.VERIFICATION_FAILED)
        # Event identity is scoped by venue/symbol; observation ID is not sufficient.
        replay_key = f'{binding.venue}:{binding.symbol}:{binding.event_id}'
        if replay_key in consumed_replay_keys:
            return _reject(R.REPLAY_DETECTED)
        if (not _utc(clock.now_utc) or type(clock.uncertainty_ms) is not int
                or clock.uncertainty_ms < 0
                or clock.uncertainty_ms > policy.max_clock_uncertainty_ms
                or type(clock.synchronization_source) is not str
                or not clock.synchronization_source
                or type(clock.measured_at_monotonic_ns) is not int
                or clock.measured_at_monotonic_ns < 0
                or clock.measured_at_monotonic_ns > observation.received_monotonic_ns):
            return _reject(R.CLOCK_UNTRUSTED)
        age_us = _microseconds(clock.now_utc - evidence.exchange_timestamp)
        uncertainty_us = clock.uncertainty_ms * 1000
        if age_us < -uncertainty_us:
            return _reject(R.INVALID_TIMESTAMP)
        if age_us + uncertainty_us > policy.max_age_ms * 1000:
            return _reject(R.STALE_PRICE)
        if not policy.source_healthy:
            return _reject(R.SOURCE_UNHEALTHY)
        # Never authorize recommendations from an isolated provenance checker.
        # Replay key must be atomically consumed by a future approved stateful gate.
        return ValidationResult(True, True, False, evidence.exchange_timestamp, None, replay_key)
    except Exception:
        return _reject(R.VERIFICATION_FAILED)
