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

    def test_wrong_key_cannot_spend_after_restart(self):
        with self.assertRaises(Denied):
            self.spend(Journal(self.path,"TRUST",b"z"*32))
        self.spend()

    def test_rotation_requires_frozen_and_correct_key(self):
        epoch,cp=self.f["key_epoch"],self.f["old_checkpoint"]
        with self.assertRaises(Denied):
            self.j.rotate_frozen_fixture(epoch,cp,"d"*32,b"z"*32)
        self.j.freeze()
        with self.assertRaises(Denied):
            Journal(self.path,"TRUST",b"x"*32).rotate_frozen_fixture(epoch,cp,"d"*32,b"z"*32)
        with self.assertRaises(Denied):
            self.j.rotate_frozen_fixture(epoch,cp,"d"*32,self.key)
        self.j.rotate_frozen_fixture(epoch,cp,"d"*32,b"z"*32)
        reopened=Journal(self.path,"TRUST",b"z"*32)
        with self.assertRaises(Denied):
            self.spend(reopened,self.f|{"key_epoch":"d"*32})
        with self.assertRaises(Denied):
            reopened.rotate_frozen_fixture("d"*32,cp,epoch,b"x"*32)
        with self.assertRaises(Denied):
            reopened.rotate_frozen_fixture("d"*32,cp,"e"*32,self.key)
        reopened.rotate_frozen_fixture("d"*32,cp,"e"*32,b"x"*32)

    def test_concurrent_rotation_one_winner(self):
        epoch,cp=self.f["key_epoch"],self.f["old_checkpoint"]
        self.j.freeze()
        def attempt(i):
            try:
                self.j.rotate_frozen_fixture(epoch,cp,format(i+1,"032x"),bytes([i+1])*32)
                return 1
            except Denied:
                return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt,range(20))),1)

    def test_rotation_preserves_spent_history(self):
        from contextlib import closing
        self.spend()
        self.j.freeze()
        rotated=self.j.rotate_frozen_fixture(self.f["key_epoch"],self.f["new_checkpoint"],"d"*32,b"z"*32)
        with closing(rotated._connect()) as db:
            self.assertEqual(db.execute("SELECT authorization FROM spent").fetchall(),[(self.f["authorization_id"],)])
            self.assertEqual(db.execute("SELECT epoch,sequence,frozen FROM head").fetchone(),("d"*32,1,1))
            self.assertEqual(db.execute("SELECT retired FROM key_history WHERE epoch=?",(self.f["key_epoch"],)).fetchone(),(1,))
