import concurrent.futures
import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path
from ei_cap3 import FIELDS, RULES, frame
from ei3_durable_journal import Journal, Denied

class DurableJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"trust.db"
        self.key = b"a"*32
        self.j = Journal(self.path, "TRUST", self.key)
        self.f = {n: ("1" if r == 0 else "a"*r) if type(r) is int else r[0] if type(r) is list else r for n,r in zip(FIELDS,RULES)}
        self.f.update(old_checkpoint="b"*64,new_checkpoint="c"*64,issued_at="100",not_before="100",expires_at="130")
        self.j.initialize_fixture(self.f["key_epoch"],self.f["old_checkpoint"],0)

    def spend(self,j=None,f=None):
        return (j or self.j).spend(f or self.f,validate=lambda _: True,now=101)

    def test_restart_replay(self):
        self.assertEqual(self.spend(),hmac.digest(self.key,b"EI-CAP-3/TRUST\n"+frame(self.f),"sha256"))
        with self.assertRaises(Denied):
            self.spend(Journal(self.path,"TRUST",self.key))

    def test_concurrent(self):
        def attempt(_):
            try:
                self.spend()
                return 1
            except Denied:
                return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt,range(20))),1)

    def test_freeze_restart(self):
        self.j.freeze()
        with self.assertRaises(Denied):
            self.spend(Journal(self.path,"TRUST",self.key))

    def test_policy_and_expiry(self):
        with self.assertRaises(Denied):
            self.j.spend(self.f,validate=lambda _: False,now=101)
        with self.assertRaises(Denied):
            self.j.spend(self.f,validate=lambda _: True,now=130)
        self.spend()

    def test_independent_domains(self):
        w=Journal(Path(self.tmp.name)/"witness.db","WITNESS",b"b"*32)
        w.initialize_fixture(self.f["key_epoch"],self.f["old_checkpoint"],0)
        self.assertNotEqual(self.spend(),self.spend(w))
        with self.assertRaises(Denied):
            self.spend(w)
