"""Offline fault injection + real PostgreSQL role/epoch/checkpoint tests."""
from dataclasses import replace
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock,patch

import test_production_integration_offline as fixture
from production_pg import Denied
from production_psycopg import LocalPg,ROLE
from production_recovery import Anchor,Fence
from production_passkey import Ceremonies,Credential
from production_candidate import LocalTLS
import safe_rollback_api as rollback

S="a"*32;CP="b"*64;C="c"*40;E="d"*32;G="e"*32
PIN=Credential(b"owner",b"public",0)
RESPONSE=json.dumps({"id":"b3duZXI","rawId":"b3duZXI","response":{}})

class AdapterTests(unittest.TestCase):
    def test_psycopg_tls_parameterized_commit_and_no_retry(self):
        with tempfile.NamedTemporaryFile() as ca:
            cfg=LocalPg(5432,Path(ca.name),ROLE["CONSUME"],"synthetic")
            driver=MagicMock();conn=driver.connect.return_value
            cur=conn.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value=(True,)
            values=(S,CP,"f"*64,E,C,G)
            with patch.dict(sys.modules,{"psycopg":driver}):
                cfg.apply("CONSUME",values)
                kwargs=driver.connect.call_args.kwargs
                self.assertEqual(kwargs["sslmode"],"verify-full")
                self.assertEqual(kwargs["channel_binding"],"require")
                self.assertEqual(kwargs["host"],"127.0.0.1")
                self.assertEqual(cur.execute.call_args.args[1],values)
                conn.commit.assert_called_once()
                conn.commit.side_effect=OSError("lost commit ack")
                with self.assertRaises(Denied):cfg.apply("CONSUME",values)
                self.assertEqual(driver.connect.call_count,2)
                self.assertEqual(conn.commit.call_count,2)
                with self.assertRaises(Denied):cfg.apply("ARM",values)
                self.assertEqual(driver.connect.call_count,2)

    def test_passkey_bootstrap_and_registration_requires_new_pin(self):
        token=b"x"*32
        c=Ceremonies(bootstrap_digest=hashlib.sha256(token).digest())
        w=MagicMock()
        w.verify_registration_response.return_value=NS(credential_id=PIN.id,credential_public_key=PIN.public_key,sign_count=0)
        lib=(w,MagicMock(),NS(REQUIRED="required"),NS(REQUIRED="required"),MagicMock(),lambda _: "{}")
        with patch("production_passkey.library",return_value=lib):
            with self.assertRaises(Denied):c.begin_registration(b"y"*32)
            self.assertEqual(c.begin_registration(token),"{}")
            pin=c.finish_registration("{}")
            self.assertEqual(pin,PIN)
            self.assertTrue(w.verify_registration_response.call_args.kwargs["require_user_verification"])
            with self.assertRaises(Denied):c.begin_registration(token)
            with self.assertRaises(Denied):c.begin("ARM","bind")
            new=Ceremonies(pin)
            w.verify_authentication_response.return_value=NS(credential_id=PIN.id,new_sign_count=1)
            new.begin("ARM","bind")
            new.verify("ARM","bind",RESPONSE)
            self.assertEqual(w.verify_authentication_response.call_args.kwargs["expected_origin"],"https://localhost")
            self.assertTrue(w.verify_authentication_response.call_args.kwargs["require_user_verification"])
            with self.assertRaises(Denied):new.verify("ARM","bind",RESPONSE)

    def test_passkey_wrong_binding_burns_challenge(self):
        c=Ceremonies(PIN)
        w=MagicMock()
        lib=(w,MagicMock(),NS(REQUIRED="required"),NS(REQUIRED="required"),MagicMock(),lambda _: "{}")
        with patch("production_passkey.library",return_value=lib):
            c.begin("ARM","bind")
            with self.assertRaises(Denied):c.verify("ISSUE","bind",RESPONSE)
            with self.assertRaises(Denied):c.verify("ARM","bind",RESPONSE)
            w.verify_authentication_response.assert_not_called()

    def test_safe_rollback_http_zero_outbound(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),rollback.Handler)
        t=threading.Thread(target=server.serve_forever,kwargs={"poll_interval":.02},daemon=True);t.start()
        try:
            conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=2)
            for method,path,code in (("GET","/health",200),("GET","/market/status",200),
                                     ("GET","/market/eth",503),("POST","/admin/rest/execute",403)):
                conn.request(method,path);res=conn.getresponse();data=json.loads(res.read())
                self.assertEqual(res.status,code)
                if path=="/market/status":self.assertFalse(data["upstream_request_allowed"])
                if path!="/health":self.assertFalse(data["recommendation_eligible"])
            conn.close()
        finally:server.shutdown();server.server_close();t.join(2)

    def test_worker_environment_no_inherited_secrets(self):
        with tempfile.NamedTemporaryFile() as ca,patch.dict(os.environ,{"DATABASE_URL":"SECRET","PGPASSWORD":"SECRET","OWNER_TOKEN":"SECRET"}):
            with patch("production_candidate.subprocess.Popen") as pop:
                proc=pop.return_value;proc.communicate.return_value=(b'{"mark_price":"1700"}',None)
                proc.returncode=0;proc.poll.return_value=0
                LocalTLS(1234,Path(ca.name)).fetch()
                env=pop.call_args.kwargs["env"]
                self.assertEqual(set(env),{"PATH","LANG","PYTHONPATH"})
                self.assertNotIn("SECRET",str(env))

class AuthFixture:
    def begin(self,op,binding):return binding
    def verify(self,op,binding,credential):
        if credential!=binding:raise Denied("fixture_auth")

class RecoveryTests(unittest.TestCase):
    admin=classmethod(fixture.IntegrationTests.admin.__func__)
    @classmethod
    def setUpClass(cls):
        fixture.IntegrationTests.setUpClass.__func__(cls)
        cls.admin((fixture.ROOT/"research/production_security_schema.sql").read_text())
        for role in set(ROLE.values()):
            cls.admin("ALTER ROLE "+role+" LOGIN;")
    def setUp(self):
        self.admin("TRUNCATE ei2.events,ei2.grants,ei2.state RESTART IDENTITY; "
                   "INSERT INTO ei2.state(singleton,store_id,checkpoint,mode) VALUES(true,'"+S+"','"+CP+"','DISARMED');")
        self.lose=None
        self.ops=[]
    def adapter(self,op,values):
        self.ops.append(op)
        sql="SELECT ei2."+op.lower()+"("+",".join("'"+v+"'" for v in values)+");"
        result=self.admin(sql,role=ROLE[op],check=False)
        if result.returncode or result.stdout.strip()!=b"OK":raise Denied("sql_denied")
        if self.lose==op:raise Denied("ack_lost_after_real_commit")
    def fence(self,anchor=None):
        backend=NS(apply=self.adapter)
        return Fence(anchor or Anchor(S,CP,C),{op:backend for op in ROLE},AuthFixture())
    def advance(self,f,op):return f.confirm(op,f.challenge(op))
    def test_startup_disarmed_and_role_isolation(self):
        f=self.fence()
        self.assertEqual(f.mode,"DISARMED")
        with self.assertRaises(Denied):f.challenge("CONSUME")
        values=",".join("'"+v+"'" for v in (S,CP,"f"*64,E,C,G))
        for role in set(ROLE.values()):
            for fn in ("arm","issue","consume","success","unknown"):
                op=fn.upper()
                if ROLE[op]==role:continue
                self.assertNotEqual(self.admin("SELECT ei2."+fn+"("+values+");",role=role,check=False).returncode,0)
            self.assertNotEqual(self.admin("UPDATE ei2.state SET mode='DISARMED';",role=role,check=False).returncode,0)
    def test_lost_consume_ack_real_commit_no_worker_and_restart_denial(self):
        f=self.fence();self.advance(f,"ARM");self.advance(f,"ISSUE")
        self.lose="CONSUME"
        with patch.object(LocalTLS,"fetch") as fetch:
            with self.assertRaises(Denied):f.execute(f.challenge("CONSUME"),LocalTLS(1,self.cert))
            fetch.assert_not_called()
        self.assertEqual(f.mode,"FROZEN")
        self.assertEqual(self.admin("SELECT mode FROM ei2.state;").stdout.strip(),b"PENDING")
        new=self.fence()
        with self.assertRaises(Denied):self.advance(new,"ARM")
        self.assertEqual(new.mode,"FROZEN")
    def test_lost_success_ack_real_commit_no_rearm_with_old_anchor(self):
        f=self.fence();self.advance(f,"ARM");self.advance(f,"ISSUE")
        self.lose="SUCCESS"
        with patch.object(LocalTLS,"fetch",return_value={"mark_price":"1700"}) as fetch:
            with self.assertRaises(Denied):f.execute(f.challenge("CONSUME"),LocalTLS(1,self.cert))
            fetch.assert_called_once()
        self.assertEqual(self.admin("SELECT mode FROM ei2.state;").stdout.strip(),b"CLOSED")
        with self.assertRaises(Denied):f.checkpoint_for_manual_review()
        new=self.fence()
        with self.assertRaises(Denied):self.advance(new,"ARM")
        self.assertEqual(new.mode,"FROZEN")
    def test_worker_error_freezes_and_no_reuse(self):
        f=self.fence();self.advance(f,"ARM");self.advance(f,"ISSUE")
        with patch.object(LocalTLS,"fetch",side_effect=OSError):
            with self.assertRaises(OSError):f.execute(f.challenge("CONSUME"),LocalTLS(1,self.cert))
        self.assertEqual(self.admin("SELECT mode FROM ei2.state;").stdout.strip(),b"FROZEN")
        with self.assertRaises(Denied):f.challenge("CONSUME")
    def test_success_once_and_manual_checkpoint(self):
        f=self.fence();self.advance(f,"ARM");self.advance(f,"ISSUE")
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            result=f.execute(f.challenge("CONSUME"),LocalTLS(port,self.cert))
            self.assertFalse(result["recommendation_eligible"])
            with self.assertRaises(Denied):f.challenge("CONSUME")
            self.assertEqual(len(requests),1)
        anchor=f.checkpoint_for_manual_review()
        self.assertNotEqual(anchor.checkpoint,CP)
        self.assertEqual(self.admin("SELECT mode FROM ei2.state;").stdout.strip(),b"CLOSED")
        with self.assertRaises(Denied):self.advance(self.fence(anchor),"ARM") # cooldown
    def test_restored_checkpoint_mismatch_denies(self):
        f=self.fence();self.advance(f,"ARM");self.advance(f,"ISSUE")
        with patch.object(LocalTLS,"fetch",return_value={"mark_price":"1700"}):
            f.execute(f.challenge("CONSUME"),LocalTLS(1,self.cert))
        anchor=f.checkpoint_for_manual_review()
        self.admin("UPDATE ei2.state SET checkpoint='"+CP+"',mode='DISARMED',active=NULL,next_at='-infinity';")
        with self.assertRaises(Denied):self.advance(self.fence(anchor),"ARM")

if __name__=="__main__":unittest.main()
