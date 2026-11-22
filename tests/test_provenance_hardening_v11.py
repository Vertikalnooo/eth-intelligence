"""30 offline v1.1 negative tests. No network or production endpoints."""
import os
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from provenance.replay import ReplayKey,SQLiteReplayStore,ClaimResult
from provenance.clock import ClockSample,check_clock
from provenance.trust import NoTrustedEvidenceProvider
from provenance.decision_gate import accept_market_data
from test_provenance_validator import fixtures,NOW

KEY=ReplayKey('Hyperliquid','ETH-PERP','mark-update','event-1')
D=b'1'*32
S=ClockSample(NOW,100,1000,2000000,'approved')
def checked(sample=S,now=1000,source='approved',cap=1000000):
    return check_clock(sample,now,source,cap)

class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,'replay.sqlite')
        self.store=SQLiteReplayStore(self.path)
    def tearDown(self): self.tmp.cleanup()
    def test_01_duplicate(self):
        self.assertEqual(self.store.claim_once(KEY,D),ClaimResult.CLAIMED)
        self.assertEqual(self.store.claim_once(KEY,D),ClaimResult.DUPLICATE)
    def test_02_conflict(self):
        self.store.claim_once(KEY,D)
        self.assertEqual(self.store.claim_once(KEY,b'2'*32),ClaimResult.CONFLICT)
    def test_03_restart(self):
        self.store.claim_once(KEY,D)
        self.assertEqual(SQLiteReplayStore(self.path).claim_once(KEY,D),ClaimResult.DUPLICATE)
    def test_04_concurrent(self):
        barrier=threading.Barrier(8);results=[];lock=threading.Lock()
        def worker():
            barrier.wait()
            value=self.store.claim_once(KEY,D)
            with lock:results.append(value)
        threads=[threading.Thread(target=worker) for _ in range(8)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(results.count(ClaimResult.CLAIMED),1)
        self.assertEqual(results.count(ClaimResult.DUPLICATE),7)
    def test_05_store_missing_parent(self):
        obj=SQLiteReplayStore(self.path)
        obj.path=os.path.join(self.tmp.name,'missing','db.sqlite')
        self.assertEqual(obj.claim_once(KEY,D),ClaimResult.UNAVAILABLE)
    def test_06_store_invalid_digest(self):
        self.assertEqual(self.store.claim_once(KEY,b'bad'),ClaimResult.UNAVAILABLE)
    def test_07_store_wrong_digest_type(self):
        self.assertEqual(self.store.claim_once(KEY,'1'*32),ClaimResult.UNAVAILABLE)
    def test_08_store_bad_key_type(self):
        self.assertEqual(self.store.claim_once('bad',D),ClaimResult.UNAVAILABLE)
    def test_09_store_memory_denied(self):
        with self.assertRaises(ValueError):SQLiteReplayStore(':memory:')
    def test_10_different_namespace(self):
        self.assertEqual(self.store.claim_once(KEY,D),ClaimResult.CLAIMED)
        self.assertEqual(self.store.claim_once(replace(KEY,event_namespace='another'),D),ClaimResult.UNAVAILABLE)
    def test_11_bad_venue(self):
        self.assertEqual(self.store.claim_once(replace(KEY,venue='Other'),D),ClaimResult.UNAVAILABLE)
    def test_12_bad_market(self):
        self.assertEqual(self.store.claim_once(replace(KEY,market_id='BTC-PERP'),D),ClaimResult.UNAVAILABLE)
    def test_13_empty_event(self):
        self.assertEqual(self.store.claim_once(replace(KEY,event_id=''),D),ClaimResult.UNAVAILABLE)
    def test_14_delimiter_event(self):
        self.assertEqual(self.store.claim_once(replace(KEY,event_id='a:b'),D),ClaimResult.UNAVAILABLE)
    def test_15_unicode_event(self):
        self.assertEqual(self.store.claim_once(replace(KEY,event_id='é'),D),ClaimResult.UNAVAILABLE)
    def test_16_oversize_event(self):
        self.assertEqual(self.store.claim_once(replace(KEY,event_id='x'*129),D),ClaimResult.UNAVAILABLE)
    def test_17_no_sample(self):
        self.assertIsNone(checked(None,1000,'approved',1000000))
    def test_18_wrong_source(self):
        self.assertIsNone(checked(S,1000,'other',1000000))
    def test_19_expired(self):
        self.assertIsNone(checked(S,2000001,'approved',1000000))
    def test_20_backwards(self):
        self.assertIsNone(checked(S,999,'approved',1000000))
    def test_21_negative_uncertainty(self):
        self.assertIsNone(checked(replace(S,uncertainty_us=-1),1000,'approved',1000000))
    def test_22_missing_source(self):
        self.assertIsNone(checked(replace(S,source_id=''),1000,'approved',1000000))
    def test_23_naive_utc(self):
        self.assertIsNone(checked(replace(S,utc=NOW.replace(tzinfo=None)),1000,'approved',1000000))
    def test_24_invalid_cap(self):
        self.assertIsNone(checked(S,1000,'approved',-1))
    def test_25_drift_exceeds(self):
        self.assertIsNone(checked(S,1999999,'approved',100))
    def test_26_negative_monotonic(self):
        self.assertIsNone(checked(replace(S,sampled_monotonic_ns=-1),1000,'approved',1000000))
    def gate(self,provider):
        o,e,p,c,v=fixtures()
        return accept_market_data(o,e,provider,None,self.store,1000,'approved')
    def test_27_default_provider_denies(self):
        self.assertFalse(self.gate(NoTrustedEvidenceProvider()).accepted)
    def test_28_forged_provider_denies(self):
        class Forged:
            def verify(self,o,e): return True
        self.assertEqual(self.gate(Forged()).reason,'untrusted_provider')
    def test_29_none_provider_denies(self):
        self.assertFalse(self.gate(None).accepted)
    def test_30_recommendation_never_eligible(self):
        self.assertFalse(self.gate(NoTrustedEvidenceProvider()).recommendation_eligible)

if __name__=='__main__':unittest.main()
