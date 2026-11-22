"""Offline compatibility tests using exact pinned PR20 + PR21 research sources."""
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from decimal import Decimal

from freshness import Observation,WsSession,Consistency,evaluate
from provenance.models import MarketObservation,VerifiedBinding
from provenance.replay import SQLiteReplayStore,ReplayKey,ClaimResult,binding_digest
from provenance.decision_gate import accept_market_data,inspect_offline_pipeline
from provenance.clock import ClockSample
from integration_harness import (new_clock_domain,to_market_observation,
                                 diagnostic_consistency,replay_diagnostic,combined_deny,
                                 LocalClockDomain,valid_domain,revoke_clock_domain,rotate_clock_domain)

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.domain=new_clock_domain()
        self.utc=datetime(2026,11,22,tzinfo=timezone.utc)
        self.rest=Observation('rest','ETH-PERP','mark','REST',Decimal('3000'),10_000_000_000,
             self.domain.token,request_start_monotonic_ns=9_000_000_000,
             request_end_monotonic_ns=9_900_000_000)
        self.ws=Observation('ws','ETH-PERP','mark','WS',Decimal('3000'),10_000_000_000,
             self.domain.token,connection_epoch='epoch1')
        self.session=WsSession('epoch1',True,self.domain.token)
    def test_01_typed_adapter(self):
        mapped=to_market_observation(self.rest,self.utc,self.domain)
        self.assertIs(type(mapped),MarketObservation)
        self.assertEqual((mapped.venue,mapped.symbol,mapped.mark_px),('Hyperliquid','ETH',Decimal('3000')))
    def test_02_adapter_does_not_forge_exchange_timestamp(self):
        mapped=to_market_observation(self.rest,self.utc,self.domain)
        self.assertFalse(hasattr(mapped,'exchange_timestamp'))
    def test_03_reject_mismatched_domain(self):
        self.assertIsNone(to_market_observation(self.rest,self.utc,new_clock_domain()))
    def test_04_reject_wrong_process(self):
        bad=replace(self.domain,process_id=os.getpid()+1)
        self.assertIsNone(to_market_observation(self.rest,self.utc,bad))
    def test_05_reject_naive_utc(self):
        self.assertIsNone(to_market_observation(self.rest,self.utc.replace(tzinfo=None),self.domain))
    def test_06_reject_invalid_rest_timing(self):
        bad=replace(self.rest,request_end_monotonic_ns=11_000_000_000)
        self.assertIsNone(to_market_observation(bad,self.utc,self.domain))
    def test_07_fresh_matching_diagnostic(self):
        self.assertEqual(diagnostic_consistency(self.rest,self.ws,10_000_000_000,self.domain,self.session),Consistency.CONSISTENT)
        self.assertFalse(combined_deny(self.rest,10_000_000_000,self.domain))
    def test_08_old_matching_pair_not_comparable(self):
        self.assertEqual(diagnostic_consistency(self.rest,self.ws,16_000_000_000,self.domain,self.session),Consistency.STALE)
    def test_09_disconnected_pair_not_comparable(self):
        self.assertEqual(diagnostic_consistency(self.rest,self.ws,10_000_000_000,self.domain,replace(self.session,connected=False)),Consistency.DISCONNECTED)
    def test_10_old_epoch_not_comparable(self):
        self.assertEqual(diagnostic_consistency(self.rest,self.ws,10_000_000_000,self.domain,replace(self.session,epoch='epoch2')),Consistency.INVALID_INPUT)
    def test_11_atomic_replay_diagnostic_never_authorizes(self):
        with tempfile.TemporaryDirectory() as d:
            store=SQLiteReplayStore(os.path.join(d,'replay.db'))
            key=ReplayKey('Hyperliquid','ETH-PERP','mark-update','e1')
            digest=b'0'*32
            self.assertEqual(replay_diagnostic(store,key,digest),ClaimResult.CLAIMED)
            self.assertEqual(replay_diagnostic(store,key,digest),ClaimResult.DUPLICATE)
            self.assertFalse(combined_deny(self.rest,10_000_000_000,self.domain))
    def test_12_unavailable_replay(self):
        self.assertEqual(replay_diagnostic(None,None,None),ClaimResult.UNAVAILABLE)
    def test_13_synthetic_binding_does_not_authorize(self):
        self.assertFalse(evaluate(self.rest,10_000_000_000,self.domain.token,
                                  claimed_binding=object(),claimed_clock=object()).recommendation_eligible)
        self.assertFalse(accept_market_data(None,None,None,None,None,10_000_000_000,'test').accepted)
    def test_14_untrusted_clock_domain_cannot_authorize(self):
        fake=LocalClockDomain('not-a-valid-token',os.getpid())
        self.assertFalse(combined_deny(self.rest,10_000_000_000,fake))
    def test_15_ws_adapter(self):
        self.assertIs(type(to_market_observation(self.ws,self.utc,self.domain)),MarketObservation)
    def test_16_reject_future_exchange_claim_in_adapter(self):
        # Receipt UTC is not a timestamp of the exchange mark update.
        forged=replace(self.rest,exchange_timestamp=self.utc)
        mapped=to_market_observation(forged,self.utc,self.domain)
        self.assertIs(type(mapped),MarketObservation)
        self.assertFalse(hasattr(mapped,'exchange_timestamp'))

    def test_17_revoke_domain_invalidates_adapter_and_diagnostics(self):
        self.assertTrue(revoke_clock_domain(self.domain))
        self.assertFalse(valid_domain(self.domain))
        self.assertFalse(revoke_clock_domain(self.domain))
        self.assertIsNone(to_market_observation(self.rest,self.utc,self.domain))
        self.assertEqual(diagnostic_consistency(self.rest,self.ws,10_000_000_000,self.domain,self.session),Consistency.INVALID_INPUT)

    def test_18_rotate_revokes_old_token(self):
        successor=rotate_clock_domain(self.domain)
        self.assertTrue(valid_domain(successor))
        self.assertFalse(valid_domain(self.domain))
        self.assertIsNone(rotate_clock_domain(self.domain))

    def test_19_forged_well_formed_token_rejected(self):
        forged=LocalClockDomain('a'*32,os.getpid())
        self.assertFalse(valid_domain(forged))
        self.assertIsNone(to_market_observation(replace(self.rest,clock_domain_id=forged.token),self.utc,forged))

    def test_20_reject_ws_with_rest_timing_fields(self):
        self.assertIsNone(to_market_observation(replace(self.ws,request_start_monotonic_ns=1),self.utc,self.domain))

    def test_21_reject_bad_event_and_timestamp_metadata(self):
        self.assertIsNone(to_market_observation(replace(self.ws,event_id=''),self.utc,self.domain))
        self.assertIsNone(to_market_observation(replace(self.rest,exchange_timestamp=datetime(2026,11,22)),self.utc,self.domain))

    def test_22_invalid_observation_not_not_comparable(self):
        invalid=replace(self.ws,mark_px=Decimal('NaN'))
        self.assertEqual(diagnostic_consistency(self.rest,invalid,10_000_000_000,self.domain,self.session),Consistency.INVALID_INPUT)

    def test_23_degraded_pair_not_comparable(self):
        self.assertEqual(diagnostic_consistency(self.rest,self.ws,13_000_000_000,self.domain,self.session),Consistency.NOT_COMPARABLE)

    def test_24_injected_replay_store_denied(self):
        class ForgedStore:
            def claim_once(self,*args): return ClaimResult.CLAIMED
        key=ReplayKey('Hyperliquid','ETH-PERP','mark-update','e1')
        self.assertEqual(replay_diagnostic(ForgedStore(),key,b'0'*32),ClaimResult.UNAVAILABLE)

    def test_25_invalid_replay_key_or_digest_denied(self):
        with tempfile.TemporaryDirectory() as d:
            store=SQLiteReplayStore(os.path.join(d,'replay.db'))
            key=ReplayKey('Hyperliquid','ETH-PERP','mark-update','e1')
            self.assertEqual(replay_diagnostic(store,key,b'bad'),ClaimResult.UNAVAILABLE)
            self.assertEqual(replay_diagnostic(store,replace(key,event_namespace='wrong'),b'0'*32),ClaimResult.UNAVAILABLE)

    def test_26_replay_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            store=SQLiteReplayStore(os.path.join(d,'replay.db'))
            key=ReplayKey('Hyperliquid','ETH-PERP','mark-update','e1')
            self.assertEqual(replay_diagnostic(store,key,b'0'*32),ClaimResult.CLAIMED)
            self.assertEqual(replay_diagnostic(store,key,b'1'*32),ClaimResult.CONFLICT)

    def test_27_replay_unavailable_store(self):
        with tempfile.TemporaryDirectory() as d:
            store=SQLiteReplayStore(os.path.join(d,'replay.db'))
            store.path=os.path.join(d,'missing','replay.db')
            key=ReplayKey('Hyperliquid','ETH-PERP','mark-update','e1')
            self.assertEqual(replay_diagnostic(store,key,b'0'*32),ClaimResult.UNAVAILABLE)

    def test_28_fail_closed_after_revocation_and_replay_claim(self):
        with tempfile.TemporaryDirectory() as d:
            store=SQLiteReplayStore(os.path.join(d,'replay.db'))
            key=ReplayKey('Hyperliquid','ETH-PERP','mark-update','e1')
            self.assertEqual(replay_diagnostic(store,key,b'0'*32),ClaimResult.CLAIMED)
            self.assertTrue(revoke_clock_domain(self.domain))
            self.assertFalse(combined_deny(self.rest,10_000_000_000,self.domain,replay_result=ClaimResult.CLAIMED))

if __name__=='__main__':unittest.main()
