"""16 focused offline v1.2 regression tests; no live network."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from provenance.replay import ReplayKey, SQLiteReplayStore, ClaimResult, binding_digest
from provenance.clock import ClockSample, check_clock, is_trusted_clock_provider
from provenance.trust import is_trusted_provider, NoTrustedEvidenceProvider
from provenance.decision_gate import accept_market_data, inspect_offline_pipeline
from provenance.models import VerifiedBinding
from test_provenance_validator import fixtures, NOW

KEY=ReplayKey('Hyperliquid','ETH-PERP','mark-update','event-1')

class V12Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,'r.sqlite')
        self.store=SQLiteReplayStore(self.path)
        self.o,self.e,self.p,self.c,self.v=fixtures()
        self.binding=VerifiedBinding(self.o.observation_id,self.o.venue,self.o.symbol,
             self.o.price_type,self.o.mark_px,self.e.event_id,self.e.exchange_timestamp,'mark_update')
        self.sample=ClockSample(NOW,100,1000,2000000,'test-clock')
    def tearDown(self): self.tmp.cleanup()
    def diagnostic(self,**kw):
        values=dict(observation=self.o,evidence=self.e,verified_binding=self.binding,
                    clock_sample=self.sample,current_monotonic_ns=1000,
                    store=self.store,approved_clock_source='test-clock')
        values.update(kw)
        return inspect_offline_pipeline(**values)
    def test_01_crash_before_commit(self):
        script="""import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1],isolation_level=None)
db.execute('BEGIN IMMEDIATE')
db.execute('INSERT INTO replay(key,digest) VALUES (?,?)',('crash',b'0'*32))
os._exit(17)
"""
        p=subprocess.run([sys.executable,'-c',script,self.path],capture_output=True,timeout=10)
        self.assertEqual(p.returncode,17)
        self.assertEqual(self.store.claim_once(KEY,b'1'*32),ClaimResult.CLAIMED)
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT key FROM replay WHERE key='crash'").fetchone())
    def test_02_crash_after_commit(self):
        script="""import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1],isolation_level=None)
db.execute('BEGIN IMMEDIATE')
db.execute('INSERT INTO replay(key,digest) VALUES (?,?)',('committed',b'0'*32))
db.execute('COMMIT')
os._exit(17)
"""
        p=subprocess.run([sys.executable,'-c',script,self.path],capture_output=True,timeout=10)
        self.assertEqual(p.returncode,17)
        with sqlite3.connect(self.path) as db:
            self.assertIsNotNone(db.execute("SELECT key FROM replay WHERE key='committed'").fetchone())
    def test_03_rollback(self):
        with sqlite3.connect(self.path,isolation_level=None) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO replay(key,digest) VALUES (?,?)',('rollback',b'0'*32))
            db.execute('ROLLBACK')
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT key FROM replay WHERE key='rollback'").fetchone())
    def test_04_corrupted_db(self):
        self.store.path=os.path.join(self.tmp.name,'corrupt.db')
        with open(self.store.path,'wb') as f:f.write(b'not a database')
        self.assertEqual(self.store.claim_once(KEY,b'1'*32),ClaimResult.UNAVAILABLE)
    def test_05_multi_process(self):
        script="""import sys
from provenance.replay import SQLiteReplayStore,ReplayKey
r=SQLiteReplayStore(sys.argv[1]).claim_once(ReplayKey('Hyperliquid','ETH-PERP','mark-update','event-1'),b'1'*32)
print(r.value)
"""
        env=os.environ.copy()
        env['PYTHONPATH']=os.pathsep.join(sys.path)
        ps=[subprocess.Popen([sys.executable,'-c',script,self.path],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env) for _ in range(4)]
        results=[p.communicate(timeout=15) for p in ps]
        self.assertEqual([out.strip() for out,err in results].count('claimed'),1)
        self.assertEqual([out.strip() for out,err in results].count('duplicate'),3)
    def test_06_namespace_rejected(self):
        self.assertEqual(self.store.claim_once(replace(KEY,event_namespace='other'),b'1'*32),ClaimResult.UNAVAILABLE)
    def test_07_price_changes_digest(self):
        self.assertNotEqual(binding_digest(self.binding),binding_digest(replace(self.binding,mark_px=Decimal('3001'))))
    def test_08_timestamp_changes_digest(self):
        self.assertNotEqual(binding_digest(self.binding),binding_digest(replace(self.binding,exchange_timestamp=NOW)))
    def test_09_event_changes_digest(self):
        self.assertNotEqual(binding_digest(self.binding),binding_digest(replace(self.binding,event_id='event-2')))
    def test_10_synthetic_provider_untrusted(self):
        class Synthetic:
            def verify(self,*args):return self.binding
        self.assertFalse(is_trusted_provider(Synthetic()))
        self.assertFalse(accept_market_data(self.o,self.e,Synthetic(),None,self.store,1000,'test-clock').accepted)
    def test_11_legacy_registry_cannot_authorize(self):
        self.assertFalse(accept_market_data(self.o,self.e,self.v,None,self.store,1000,'test-clock').accepted)
    def test_12_forged_clock_label(self):
        class Forged:
            def sample(self):return self.sample
        self.assertFalse(is_trusted_clock_provider(Forged()))
    def test_13_expired_clock(self):
        self.assertEqual(self.diagnostic(current_monotonic_ns=2000001).reason,'clock_untrusted')
    def test_14_backwards_clock(self):
        self.assertEqual(self.diagnostic(current_monotonic_ns=999).reason,'clock_untrusted')
    def test_15_gate_always_denies(self):
        result=accept_market_data(self.o,self.e,NoTrustedEvidenceProvider(),None,self.store,1000,'test-clock')
        self.assertFalse(result.accepted)
        self.assertFalse(result.recommendation_eligible)
    def test_16_diagnostic_never_authorizes(self):
        result=self.diagnostic()
        self.assertTrue(result.checks_passed)
        self.assertFalse(hasattr(result,'accepted'))
        self.assertFalse(hasattr(result,'recommendation_eligible'))
        self.assertEqual(self.diagnostic().reason,'replay_duplicate')

if __name__=='__main__':unittest.main()
