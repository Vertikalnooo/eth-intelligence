"""24 negative/edge offline freshness tests, without exchange network access."""
import unittest
from dataclasses import replace
from datetime import datetime,timezone
from decimal import Decimal
from freshness import (Observation,Policy,WsSession,TransportState,ExchangeState,
                       Consistency,ReplayState,assess_transport,assess_exchange,
                       compare_rest_ws,evaluate)

P=Policy()
R=Observation('obs1','ETH-PERP','mark','REST',Decimal('3000'),10000000000,'clock1',
              request_start_monotonic_ns=9000000000,request_end_monotonic_ns=9900000000)
W=Observation('obs2','ETH-PERP','mark','WS',Decimal('3000'),10000000000,'clock1',connection_epoch='epoch1')
S=WsSession('epoch1',True,'clock1')
def t(o=R,now=10000000000,domain='clock1',session=None):
    return assess_transport(o,now,domain,P,session)

class FreshnessNegativeTests(unittest.TestCase):
    def test_01_nan(self):
        self.assertEqual(t(replace(R,mark_px=Decimal('NaN'))).transport_state,TransportState.INVALID)
    def test_02_negative_price(self):
        self.assertEqual(t(replace(R,mark_px=Decimal('-1'))).transport_state,TransportState.INVALID)
    def test_03_wrong_market(self):
        self.assertEqual(t(replace(R,market_id='BTC-PERP')).transport_state,TransportState.INVALID)
    def test_04_naive_exchange_timestamp(self):
        self.assertEqual(t(replace(R,exchange_timestamp=datetime(2026,1,1))).transport_state,TransportState.INVALID)
    def test_05_fresh_boundary(self):
        self.assertEqual(t(now=12000000000).transport_state,TransportState.FRESH)
    def test_06_degraded_boundary(self):
        self.assertEqual(t(now=15000000000).transport_state,TransportState.DEGRADED)
    def test_07_stale_after_boundary(self):
        self.assertEqual(t(now=15001000000).transport_state,TransportState.STALE)
    def test_08_negative_age(self):
        self.assertEqual(t(now=9999999999).transport_state,TransportState.INVALID)
    def test_09_different_clock_domain(self):
        self.assertEqual(t(domain='another').transport_state,TransportState.INVALID)
    def test_10_ws_disconnected(self):
        self.assertEqual(t(W,session=replace(S,connected=False)).transport_state,TransportState.DISCONNECTED)
    def test_11_ws_reconnect_new_epoch(self):
        self.assertEqual(t(W,session=replace(S,epoch='epoch2')).transport_state,TransportState.INVALID)
    def test_12_ws_requires_session(self):
        self.assertEqual(t(W).transport_state,TransportState.INVALID)
    def test_13_old_epoch_replay(self):
        self.assertEqual(t(replace(W,connection_epoch='old'),session=S).reason_codes,('old_ws_epoch',))
    def test_14_rest_ws_equal_not_proven(self):
        self.assertEqual(compare_rest_ws(R,W),Consistency.CONSISTENT)
        self.assertFalse(evaluate(R,10000000000,'clock1',counterpart=W).recommendation_eligible)
    def test_15_rest_ws_mismatch(self):
        self.assertEqual(compare_rest_ws(R,replace(W,mark_px=Decimal('3100'))),Consistency.MISMATCH)
    def test_16_rest_ws_time_window(self):
        self.assertEqual(compare_rest_ws(R,replace(W,received_monotonic_ns=13000000001)),Consistency.NOT_COMPARABLE)
    def test_17_rest_ws_clock_mismatch(self):
        self.assertEqual(compare_rest_ws(R,replace(W,clock_domain_id='other')),Consistency.NOT_COMPARABLE)
    def test_18_no_exchange_proof(self):
        self.assertEqual(assess_exchange(R),ExchangeState.UNVERIFIED)
    def test_19_forged_timestamp(self):
        fake=replace(R,exchange_timestamp=datetime(2026,1,1,tzinfo=timezone.utc))
        self.assertNotEqual(assess_exchange(fake,claimed_binding=object()),ExchangeState.VERIFIED_FRESH)
    def test_20_stale_exchange_claim(self):
        self.assertEqual(assess_exchange(R,claimed_clock=object()),ExchangeState.INVALID_EVIDENCE)
    def test_21_wrong_event_binding(self):
        self.assertEqual(assess_exchange(replace(R,event_id='wrong'),claimed_binding=object()),ExchangeState.INVALID_EVIDENCE)
    def test_22_fresh_transport_cannot_authorize(self):
        result=evaluate(R,10000000000,'clock1')
        self.assertEqual(result.transport_state,TransportState.FRESH)
        self.assertEqual(result.exchange_mark_state,ExchangeState.UNVERIFIED)
        self.assertFalse(result.recommendation_eligible)
    def test_23_synthetic_cannot_authorize(self):
        result=evaluate(R,10000000000,'clock1',claimed_binding=object(),claimed_clock=object())
        self.assertEqual(result.exchange_mark_state,ExchangeState.INVALID_EVIDENCE)
        self.assertFalse(result.exchange_provenance_verified)
        self.assertFalse(result.recommendation_eligible)
    def test_24_replay_unavailable_denies(self):
        result=evaluate(R,10000000000,'clock1',replay_state=ReplayState.UNAVAILABLE)
        self.assertIn('replay_unavailable',result.reason_codes)
        self.assertFalse(result.recommendation_eligible)

if __name__=='__main__':unittest.main()
