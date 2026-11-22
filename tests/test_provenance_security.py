"""Adversarial offline tests for trust, replay, time semantics, clock and gates."""
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from provenance import EvidenceVerifierRegistry, ProvenancePolicy, TrustedClock, VerifiedBinding, validate_provenance
from test_provenance_validator import fixtures, NOW

def forged(obs, ev):
    return VerifiedBinding(obs.observation_id, obs.venue, obs.symbol, obs.price_type, obs.mark_px, ev.event_id, ev.exchange_timestamp, ev.timestamp_semantics)

class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.o,self.e,self.p,self.c,self.v = fixtures()

    def reject(self, reason, o=None, e=None, p=None, c=None, v=None, consumed=frozenset()):
        result=validate_provenance(o if o is not None else self.o,
            e if e is not None else self.e, p if p is not None else self.p,
            c if c is not None else self.c, v if v is not None else self.v, consumed)
        self.assertEqual(result.rejection_reason, reason)
        self.assertFalse(result.recommendation_eligible)
        self.assertFalse(result.provenance_verified)
        self.assertIsNone(result.exchange_timestamp)

    def test_default_denies_even_with_forged_verifier(self):
        self.reject('untrusted_method',p=replace(self.p,allow_synthetic_test_verifier=False),
                    v=EvidenceVerifierRegistry({'synthetic_only':forged}))
    def test_other_method_denied_even_with_registry(self):
        self.reject('untrusted_method',e=replace(self.e,method='real_claim'),
                    v=EvidenceVerifierRegistry({'real_claim':forged}))
    def test_block_time_rejected(self):
        self.reject('invalid_timestamp',e=replace(self.e,timestamp_semantics='block_time'))
    def test_snapshot_time_rejected(self):
        self.reject('invalid_timestamp',e=replace(self.e,timestamp_semantics='state_snapshot'))
    def test_trade_time_rejected(self):
        self.reject('invalid_timestamp',e=replace(self.e,timestamp_semantics='trade_time'))
    def test_wrong_price_binding_rejected(self):
        bad=EvidenceVerifierRegistry({'synthetic_only':lambda o,e:replace(forged(o,e),mark_px=Decimal('3001'))})
        self.reject('verification_failed',v=bad)
    def test_wrong_symbol_binding_rejected(self):
        bad=EvidenceVerifierRegistry({'synthetic_only':lambda o,e:replace(forged(o,e),symbol='BTC')})
        self.reject('verification_failed',v=bad)
    def test_wrong_timestamp_binding_rejected(self):
        bad=EvidenceVerifierRegistry({'synthetic_only':lambda o,e:replace(forged(o,e),exchange_timestamp=NOW)})
        self.reject('verification_failed',v=bad)
    def test_wrong_event_binding_rejected(self):
        bad=EvidenceVerifierRegistry({'synthetic_only':lambda o,e:replace(forged(o,e),event_id='different')})
        self.reject('verification_failed',v=bad)
    def test_bool_verifier_rejected(self):
        self.reject('verification_failed',v=EvidenceVerifierRegistry({'synthetic_only':lambda o,e:True}))
    def test_replay_same_event_rejected(self):
        self.reject('replay_detected',consumed=frozenset({'Hyperliquid:ETH:event-1'}))
    def test_replay_other_observation_same_event_rejected(self):
        self.reject('replay_detected',o=replace(self.o,observation_id='tick-2'),
                    e=replace(self.e,observation_id='tick-2'),
                    consumed=frozenset({'Hyperliquid:ETH:event-1'}))
    def test_clock_missing_sync_source(self):
        self.reject('clock_untrusted',c=replace(self.c,synchronization_source=None))
    def test_clock_missing_monotonic_marker(self):
        self.reject('clock_untrusted',c=replace(self.c,measured_at_monotonic_ns=None))
    def test_clock_monotonic_in_future(self):
        self.reject('clock_untrusted',c=replace(self.c,measured_at_monotonic_ns=101))
    def test_age_boundary_one_microsecond_stale(self):
        self.reject('stale_price',e=replace(self.e,exchange_timestamp=NOW-timedelta(microseconds=4900001)))
    def test_no_recommendation_even_on_verified_synthetic(self):
        result=validate_provenance(self.o,self.e,self.p,self.c,self.v)
        self.assertTrue(result.provenance_verified)
        self.assertFalse(result.recommendation_eligible)
    def test_invalid_replay_input_rejected(self):
        result=validate_provenance(self.o,self.e,self.p,self.c,self.v,{'Hyperliquid:ETH:event-1'})
        self.assertEqual(result.rejection_reason,'invalid_input')
    def test_empty_event_rejected(self):
        self.reject('verification_failed',e=replace(self.e,event_id=''))
    def test_price_change_without_new_binding_rejected(self):
        bad=EvidenceVerifierRegistry({'synthetic_only':lambda o,e:replace(forged(o,e),mark_px=Decimal('3000.25'))})
        self.reject('verification_failed',o=replace(self.o,mark_px=Decimal('3002')),v=bad)

if __name__ == '__main__': unittest.main()
