"""Offline trust boundary regression tests (no cloud or upstream traffic)."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from production_passkey import Ceremonies,Credential
from production_credential_store import CredentialStore,DurableCeremonies,fingerprint
from production_deadline import bounded
from production_pg import Denied
from test_production_unified import VirtualAuthenticator

class DurableCredentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name);self.store=CredentialStore(self.path)
        self.virtual=VirtualAuthenticator();token=b"t"*32
        reg=Ceremonies(bootstrap_digest=hashlib.sha256(token).digest())
        self.cred=reg.finish_registration(self.virtual.registration(reg.begin_registration(token)))
        self.pin=self.store.install_verified(self.cred,"offline",fingerprint(self.cred,"offline"))
    def test_restart_persists_count_and_rejects_old_pin(self):
        auth=DurableCeremonies(self.store,self.pin)
        opts=auth.begin("ARM","plan");assertion=self.virtual.assertion(opts)
        pin=auth.verify("ARM","plan",assertion)
        self.assertNotEqual(pin,self.pin)
        with self.assertRaises(Denied):DurableCeremonies(self.store,self.pin)
        fresh=DurableCeremonies(CredentialStore(self.path),pin)
        self.assertEqual(fresh.auth._pinned.count,1)
        opts=fresh.begin("ARM","plan");self.virtual.count=0
        with self.assertRaises(Denied):fresh.verify("ARM","plan",self.virtual.assertion(opts))
        self.assertEqual(self.store.load(pin)[0].count,1)
    def test_reenrollment_and_file_rollback_denied(self):
        with self.assertRaises(Denied):self.store.install_verified(self.cred,"offline",fingerprint(self.cred,"offline"))
        auth=DurableCeremonies(self.store,self.pin);opts=auth.begin("ARM","plan")
        pin=auth.verify("ARM","plan",self.virtual.assertion(opts))
        (self.path/"0001.json").unlink()
        with self.assertRaises(Denied):DurableCeremonies(self.store,pin)
        with self.assertRaises(Denied):auth.begin("ISSUE","plan")
    def test_concurrent_stale_credential_writer_denied(self):
        a=DurableCeremonies(self.store,self.pin);b=DurableCeremonies(self.store,self.pin)
        opta=a.begin("ARM","plan");optb=b.begin("ARM","plan")
        a.verify("ARM","plan",self.virtual.assertion(opta))
        with self.assertRaises(Denied):b.verify("ARM","plan",self.virtual.assertion(optb))
    def test_zero_counter_still_advances_history(self):
        z=Credential(self.cred.id,self.cred.public_key,0)
        pin=self.store.advance(self.pin,z,"offline")
        self.assertNotEqual(pin,self.pin);self.assertEqual(self.store.load(pin)[0].count,0)
        with self.assertRaises(Denied):self.store.load(self.pin)
    def test_wrong_fingerprint_and_missing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=CredentialStore(Path(tmp))
            with self.assertRaises(Denied):s.install_verified(self.cred,"offline","0"*64)
            with self.assertRaises(Denied):DurableCeremonies(s,self.pin)

class DeadlineTests(unittest.TestCase):
    def test_success_and_exception(self):
        self.assertEqual(bounded(lambda:{"value":42},seconds=1),{"value":42})
        with self.assertRaises(Denied):bounded(lambda:1/0,seconds=1)
    def test_stuck_call_is_killed_and_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/"attempt"
            def stuck():
                with f.open("a") as out:out.write("one\n");out.flush();os.fsync(out.fileno())
                time.sleep(10)
            start=time.monotonic()
            with self.assertRaises(Denied):bounded(stuck,seconds=.5)
            self.assertLess(time.monotonic()-start,1.5)
            self.assertEqual(f.read_text(),"one\n")
    def test_oversized_child_result_denied(self):
        with self.assertRaises(Denied):bounded(lambda:"a"*17000,seconds=1)
