"""32 offline-only provenance contract tests; no live network."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from provenance import (MarketObservation, ProvenanceEvidence, TrustedClock,
                        ProvenancePolicy, EvidenceVerifierRegistry, validate_provenance)

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)

def fixtures():
    o = MarketObservation('tick-1', 'Hyperliquid', 'ETH', 'mark', Decimal('3000.25'), 'activeAssetCtx', NOW, 100)
    e = ProvenanceEvidence('tick-1', 'synthetic_only', NOW - timedelta(seconds=1), 'event-1')
    p = ProvenancePolicy(5000, True)
    c = TrustedClock(NOW, 100)
    # Test-only stub, NOT proof of actual Hyperliquid provenance.
    v = EvidenceVerifierRegistry({'synthetic_only': lambda obs, ev: obs.observation_id == ev.observation_id and ev.event_id == 'event-1'})
    return o, e, p, c, v

def broken(o, e):
    raise RuntimeError('private details')

class ValidatorTests(unittest.TestCase):
    def check(self, change=None, expected=None):
        o,e,p,c,v = fixtures()
        if change:
            o,e,p,c,v = change(o,e,p,c,v)
        result = validate_provenance(o,e,p,c,v)
        if expected is None:
            self.assertTrue(result.recommendation_eligible)
            self.assertTrue(result.provenance_verified)
            self.assertTrue(result.mark_price_freshness_verified)
            self.assertEqual(result.exchange_timestamp, e.exchange_timestamp)
        else:
            self.assertFalse(result.recommendation_eligible)
            self.assertFalse(result.provenance_verified)
            self.assertFalse(result.mark_price_freshness_verified)
            self.assertIsNone(result.exchange_timestamp)
            self.assertEqual(result.rejection_reason, expected)

    def test_01_positive_synthetic(self): self.check()
    def test_02_missing_evidence(self): self.check(lambda o,e,p,c,v:(o,None,p,c,v), 'missing_evidence')
    def test_03_wrong_venue(self): self.check(lambda o,e,p,c,v:(replace(o,venue='Other'),e,p,c,v), 'unsupported_instrument')
    def test_04_wrong_symbol(self): self.check(lambda o,e,p,c,v:(replace(o,symbol='BTC'),e,p,c,v), 'unsupported_instrument')
    def test_05_wrong_price_type(self): self.check(lambda o,e,p,c,v:(replace(o,price_type='oracle'),e,p,c,v), 'unsupported_instrument')
    def test_06_zero_price(self): self.check(lambda o,e,p,c,v:(replace(o,mark_px=Decimal(0)),e,p,c,v), 'invalid_input')
    def test_07_negative_price(self): self.check(lambda o,e,p,c,v:(replace(o,mark_px=Decimal(-1)),e,p,c,v), 'invalid_input')
    def test_08_nan_price(self): self.check(lambda o,e,p,c,v:(replace(o,mark_px=Decimal('NaN')),e,p,c,v), 'invalid_input')
    def test_09_infinite_price(self): self.check(lambda o,e,p,c,v:(replace(o,mark_px=Decimal('Infinity')),e,p,c,v), 'invalid_input')
    def test_10_float_price(self): self.check(lambda o,e,p,c,v:(replace(o,mark_px=3000.25),e,p,c,v), 'invalid_input')
    def test_11_empty_id(self): self.check(lambda o,e,p,c,v:(replace(o,observation_id=''),e,p,c,v), 'invalid_input')
    def test_12_empty_channel(self): self.check(lambda o,e,p,c,v:(replace(o,source_channel=''),e,p,c,v), 'invalid_input')
    def test_13_negative_monotonic(self): self.check(lambda o,e,p,c,v:(replace(o,received_monotonic_ns=-1),e,p,c,v), 'invalid_input')
    def test_14_naive_receipt_time(self): self.check(lambda o,e,p,c,v:(replace(o,received_at_utc=NOW.replace(tzinfo=None)),e,p,c,v), 'invalid_input')
    def test_15_wrong_observation_id(self): self.check(lambda o,e,p,c,v:(o,replace(e,observation_id='other'),p,c,v), 'mismatched_observation')
    def test_16_missing_timestamp(self): self.check(lambda o,e,p,c,v:(o,replace(e,exchange_timestamp=None),p,c,v), 'invalid_timestamp')
    def test_17_naive_timestamp(self): self.check(lambda o,e,p,c,v:(o,replace(e,exchange_timestamp=NOW.replace(tzinfo=None)),p,c,v), 'invalid_timestamp')
    def test_18_unknown_method(self): self.check(lambda o,e,p,c,v:(o,replace(e,method='trades'),p,c,v), 'untrusted_method')
    def test_19_empty_registry(self): self.check(lambda o,e,p,c,v:(o,e,p,c,EvidenceVerifierRegistry()), 'untrusted_method')
    def test_20_wrong_event(self): self.check(lambda o,e,p,c,v:(o,replace(e,event_id='other'),p,c,v), 'verification_failed')
    def test_21_verifier_false(self): self.check(lambda o,e,p,c,v:(o,e,p,c,EvidenceVerifierRegistry({'synthetic_only':lambda o,e:False})), 'verification_failed')
    def test_22_verifier_raises(self): self.check(lambda o,e,p,c,v:(o,e,p,c,EvidenceVerifierRegistry({'synthetic_only':broken})), 'verification_failed')
    def test_23_clock_naive(self): self.check(lambda o,e,p,c,v:(o,e,p,replace(c,now_utc=NOW.replace(tzinfo=None)),v), 'clock_untrusted')
    def test_24_clock_uncertainty_missing(self): self.check(lambda o,e,p,c,v:(o,e,p,replace(c,uncertainty_ms=None),v), 'clock_untrusted')
    def test_25_clock_uncertainty_negative(self): self.check(lambda o,e,p,c,v:(o,e,p,replace(c,uncertainty_ms=-1),v), 'clock_untrusted')
    def test_26_clock_uncertainty_too_large(self): self.check(lambda o,e,p,c,v:(o,e,p,replace(c,uncertainty_ms=1001),v), 'clock_untrusted')
    def test_27_stale(self): self.check(lambda o,e,p,c,v:(o,replace(e,exchange_timestamp=NOW-timedelta(seconds=6)),p,c,v), 'stale_price')
    def test_28_future_timestamp(self): self.check(lambda o,e,p,c,v:(o,replace(e,exchange_timestamp=NOW+timedelta(seconds=2)),p,c,v), 'invalid_timestamp')
    def test_29_source_unhealthy(self): self.check(lambda o,e,p,c,v:(o,e,replace(p,source_healthy=False),c,v), 'source_unhealthy')
    def test_30_invalid_policy(self): self.check(lambda o,e,p,c,v:(o,e,replace(p,max_age_ms=0),c,v), 'invalid_input')
    def test_31_invalid_observation_type(self): self.check(lambda o,e,p,c,v:('bad',e,p,c,v), 'invalid_input')
    def test_32_timestamp_boundary(self): self.check(lambda o,e,p,c,v:(o,replace(e,exchange_timestamp=NOW-timedelta(milliseconds=4900)),p,c,v))

if __name__ == '__main__': unittest.main()
