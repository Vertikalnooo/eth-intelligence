"""Actual disposable PostgreSQL 17 + pgcrypto, no cloud credentials or network."""
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
import json
import sys

from ei_cap3 import FIELDS,frame
from test_production_integration_offline import command,ROOT

ROLES={"ARM":"ei3_arm","ISSUE":"ei3_issue","CONSUME":"ei3_consume","SUCCESS":"ei3_finalize","UNKNOWN":"ei3_finalize","ADVANCE":"ei3_advance"}
ZERO="0"*32
def hx(n):return secrets.token_hex(n//2)
def mac(key,label,body):return hmac.digest(key,("EI-CAP-3/"+label+"\n").encode()+body,"sha256")

class AuthorizationCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.pg=Path("/usr/lib/postgresql/17/bin")
        if not (cls.pg/"initdb").is_file() or os.geteuid()==0:
            if os.environ.get("GITHUB_ACTIONS")=="true":raise RuntimeError("required non-root PostgreSQL 17 fixture")
            raise unittest.SkipTest("requires PostgreSQL 17 as non-root")
        cls.tmp=tempfile.TemporaryDirectory(prefix="ei3_pg_");cls.addClassCleanup(cls.tmp.cleanup)
        cls.dir=Path(cls.tmp.name);cls.sock=cls.dir/"socket";cls.sock.mkdir(mode=0o700)
        cls.data=cls.dir/"data";cls.port=55433
        cls.cert=cls.dir/"cert.pem";cls.key=cls.dir/"key.pem"
        command(["/usr/bin/openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1",
                 "-subj","/CN=127.0.0.1","-addext","subjectAltName=IP:127.0.0.1","-keyout",cls.key,"-out",cls.cert])
        cls.key.chmod(0o600)
        command([cls.pg/"initdb","-D",cls.data,"-U","ei_admin","-A","trust","--no-locale","--encoding=UTF8"])
        (cls.data/"pg_hba.conf").write_text("local all all trust\nhostssl all all 127.0.0.1/32 scram-sha-256\n")
        cls.options="-c listen_addresses=127.0.0.1 -k "+str(cls.sock)+" -p "+str(cls.port)+" -c ssl=on -c ssl_cert_file="+str(cls.cert)+" -c ssl_key_file="+str(cls.key)
        command([cls.pg/"pg_ctl","-D",cls.data,"-l",cls.dir/"pg.log","-o",cls.options,"-w","start"])
        cls.addClassCleanup(lambda:command([cls.pg/"pg_ctl","-D",cls.data,"-m","immediate","-w","stop"]))
        cls.passwords={role:hx(64) for role in set(ROLES.values())|{"ei3_audit","ei3_intruder"}}
        with cls.admin() as c:
            version=c.execute("SHOW server_version_num").fetchone()[0]
            if not 170000<=int(version)<180000:raise RuntimeError("not PostgreSQL 17")
            print("EI3_POSTGRES_VERSION="+version,flush=True)
            c.execute((ROOT/"research/production_security_schema.sql").read_text())
            c.execute((ROOT/"research/ei3_authorization_core.sql").read_text())
            c.execute("REVOKE TEMPORARY,CREATE ON DATABASE postgres FROM PUBLIC")
            c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            c.execute("CREATE ROLE ei3_intruder NOLOGIN")
            from psycopg import sql
            for role,pw in cls.passwords.items():
                c.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role),sql.Literal(pw)))
            print("EI3_PGCRYPTO_VERSION="+c.execute("SELECT extversion FROM pg_extension WHERE extname='pgcrypto'").fetchone()[0],flush=True)
    @classmethod
    def admin(cls):
        import psycopg
        return psycopg.connect(host=str(cls.sock),port=cls.port,dbname="postgres",user="ei_admin",autocommit=True)
    def connect(self,role):
        import psycopg
        return psycopg.connect(host="127.0.0.1",hostaddr="127.0.0.1",port=self.port,dbname="postgres",
            user=role,password=self.passwords[role],sslmode="verify-full",sslrootcert=str(self.cert),
            channel_binding="require",connect_timeout=5,options="-c statement_timeout=6000 -c lock_timeout=5000",autocommit=True)
    def setUp(self):
        self.kt=secrets.token_bytes(32);self.kw=secrets.token_bytes(32)
        self.ledger=hx(32);self.obj=hx(64);self.candidate=hx(40);self.ke=hx(32)
        self.cp=hx(64);self.head=hx(64);self.plan=hx(32);self.grant=hx(32);self.epoch=hx(32)
        self.last=ZERO;self.rev=0;self.seq=0
        with self.admin() as c:
            c.execute("TRUNCATE ei3.state,ei3.keys,ei3.grants,ei3.events,ei3.spent_checkpoints RESTART IDENTITY")
    def provision_fixture(self):
        # Explicit disposable test-only state; no production bootstrap implementation.
        with self.admin() as c:
            c.execute("INSERT INTO ei3.keys VALUES(%s,%s,%s)",(self.ke,self.kt,self.kw))
            c.execute("""INSERT INTO ei3.state(singleton,environment,ledger,object_id,candidate,key_epoch,checkpoint,owner_head,
                      credential_revision,trust_sequence,witness_sequence,mode) VALUES(true,'OFFLINE',%s,%s,%s,%s,%s,%s,0,0,0,'READY')""",
                      (self.ledger,self.obj,self.candidate,self.ke,self.cp,self.head))
    def payload(self,op="ARM",**overrides):
        now=int(time.time())
        return dict(version="EI-CAP-3",environment="OFFLINE",ledger=self.ledger,object_id=self.obj,candidate=self.candidate,
            key_epoch=self.ke,op=op,authorization_id=hx(32),plan=self.plan,grant=self.grant,epoch=self.epoch,
            old_checkpoint=self.cp,new_checkpoint=hx(64),owner_head=self.head,next_owner_head=hx(64),
            credential_revision=str(self.rev+(op in ("ARM","ISSUE","CONSUME","ADVANCE"))),trust_sequence=str(self.seq+1),
            witness_sequence=str(self.seq+1),evidence=hx(64),prior_authorization=self.last,scope="SYNTHETIC_ONLY",
            issued_at=str(now),not_before=str(now),expires_at=str(now+30))|overrides
    def args(self,p,body=None,mt=None,mw=None):
        b=frame(p) if body is None else body
        return ([p[k] for k in FIELDS],b,mac(self.kt,"TRUST",b) if mt is None else mt,mac(self.kw,"WITNESS",b) if mw is None else mw)
    def call(self,p,*,role=None,op=None,args=None,conn=None):
        from psycopg import sql
        op=op or p["op"];role=role or ROLES[op]
        q=sql.SQL("SELECT ei3.{}(%s,%s,%s,%s)").format(sql.Identifier(op.lower()))
        if conn is not None:return conn.execute(q,args or self.args(p)).fetchone()[0]
        with self.connect(role) as c:return c.execute(q,args or self.args(p)).fetchone()[0]
    def counts(self):
        with self.admin() as c:
            return c.execute("SELECT (SELECT count(*) FROM ei3.events),(SELECT count(*) FROM ei3.grants),(SELECT count(*) FROM ei3.spent_checkpoints)").fetchone()
    def state(self):
        with self.admin() as c:return c.execute("SELECT mode,checkpoint,owner_head,last_auth FROM ei3.state").fetchone()
    def accept(self,p):
        self.assertEqual(self.call(p),"OK")
        self.cp=p["new_checkpoint"];self.head=p["next_owner_head"];self.last=p["authorization_id"];self.rev=int(p["credential_revision"]);self.seq=int(p["trust_sequence"])
    def chain(self):
        self.provision_fixture()
        for op in ("ARM","ISSUE","CONSUME"):self.accept(self.payload(op))
    def test_empty_schema_denies_no_runtime_bootstrap(self):
        self.assertEqual(self.call(self.payload()),"DENY")
        self.assertEqual(self.counts(),(0,0,0))
    def test_tls_and_hmac_cross_language_frame(self):
        self.provision_fixture();p=self.payload()
        b=frame(p)
        with self.admin() as c:
            self.assertEqual(bytes(c.execute("SELECT ei3.frame(%s)",([p[k] for k in FIELDS],)).fetchone()[0]),b)
            self.assertEqual(bytes(c.execute("SELECT ei3_crypto.hmac(%s,%s,'sha256')",(b,self.kt)).fetchone()[0]),hmac.digest(self.kt,b,"sha256"))
        node=os.environ.get("EI_NODE") or shutil.which("node")
        self.assertTrue(node)
        module=(ROOT/"research/cloudflare-witness/src/ei-cap3.mjs").as_uri()
        script="import {frame} from "+json.dumps(module)+"; console.log(Buffer.from(frame(JSON.parse(process.argv[1]))).toString('hex'));"
        got=command([node,"--input-type=module","-e",script,json.dumps(p)]).stdout.strip()
        self.assertEqual(bytes.fromhex(got.decode()),b)
        with self.connect("ei3_arm") as c:
            self.assertTrue(c.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()[0])
            self.assertEqual(self.call(p,conn=c),"OK")
    def test_dual_mac_and_domain_separation(self):
        self.provision_fixture();p=self.payload();b=frame(p)
        for mt,mw in [(b"x"*32,mac(self.kw,"WITNESS",b)),(mac(self.kt,"TRUST",b),b"x"*32),
                      (mac(self.kw,"TRUST",b),mac(self.kt,"WITNESS",b)),(mac(self.kt,"WITNESS",b),mac(self.kw,"TRUST",b)),
                      (mac(self.kt,"TRUST",b),mac(self.kt,"WITNESS",b)),(b"",b"x"*32)]:
            self.assertEqual(self.call(p,args=self.args(p,mt=mt,mw=mw)),"DENY")
        self.assertEqual(self.counts(),(0,0,0));self.accept(p)
    def test_every_mac_byte_mutation_denied(self):
        self.provision_fixture();p=self.payload();a=self.args(p)
        with self.connect("ei3_arm") as c:
            for k in (2,3):
                for i in range(32):
                    bad=list(a);m=bytearray(bad[k]);m[i]^=1;bad[k]=bytes(m)
                    self.assertEqual(self.call(p,args=bad,conn=c),"DENY")
        self.assertEqual(self.counts(),(0,0,0))
    def test_canonical_rejection_and_field_substitution(self):
        self.provision_fixture();p=self.payload();a=self.args(p)
        for field,value in [("ledger",hx(32)),("object_id",hx(64)),("candidate",hx(40)),("key_epoch",hx(32)),("old_checkpoint",hx(64)),("owner_head",hx(64)),("trust_sequence","2"),("witness_sequence","2"),("credential_revision","2"),("prior_authorization",hx(32))]:
            q=p|{field:value};self.assertEqual(self.call(q),"DENY",field)
        for field,value in [("scope","LIVE"),("environment","PRODUCTION"),("trust_sequence","01"),("issued_at","9007199254740992"),("ledger","A"*32),("plan","é"*32)]:
            q=p|{field:value};bad=list(a);bad[0]=[q[k] for k in FIELDS]
            with self.assertRaises(ValueError):frame(q)
            self.assertEqual(self.call(p,args=bad),"DENY",field)
        for fields in (a[0][:-1],a[0]+["x"],a[0][:3]+[None]+a[0][4:], [a[0]]):
            bad=list(a);bad[0]=fields;self.assertEqual(self.call(p,args=bad),"DENY")
        self.assertEqual(self.call(p,args=self.args(p,body=a[1]+b" ")),"DENY")
        self.assertEqual(self.call(p,args=self.args(p,body=b"x"*4097)),"DENY")
        q=p|{"evidence":hx(64)};self.assertEqual(self.call(q,args=([q[k] for k in FIELDS],a[1],a[2],a[3])),"DENY")
        self.assertEqual(self.counts(),(0,0,0))
    def test_roles_cannot_access_keys_or_legacy_or_raw_transition(self):
        import psycopg
        self.provision_fixture();p=self.payload()
        for role in self.passwords:
            with self.connect(role) as c:
                for statement in ("SELECT * FROM ei3.keys","SELECT * FROM ei3.state","UPDATE ei3.state SET mode='READY'","SELECT ei3.frame(ARRAY['x'])","SELECT ei3.apply_transition('ARM','ei3_arm',ARRAY['x'],decode('00','hex'),decode('00','hex'),decode('00','hex'))","SELECT ei2.arm('x','x','x','x','x','x')","CREATE TEMP TABLE keys(x int)","SET ROLE ei3_owner","CREATE TABLE public.ei3_fake(x int)"):
                    with self.assertRaises(psycopg.Error,msg=role+" "+statement):c.execute(statement)
                if role!="ei3_intruder":
                    self.assertFalse(c.execute("SELECT has_function_privilege(current_user, 'ei3.apply_transition(text,name,text[],bytea,bytea,bytea)', 'EXECUTE')").fetchone()[0])
                if role!="ei3_arm":
                    with self.assertRaises(psycopg.Error):self.call(p,conn=c)
                if role=="ei3_arm":
                    with self.assertRaises(psycopg.Error):self.call(p,op="ISSUE",conn=c)
        with self.connect("ei3_audit") as c:
            self.assertEqual(c.execute("SELECT mode FROM ei3.audit_state").fetchone()[0],"READY")
            self.assertEqual(c.execute("SELECT count(*) FROM ei3.events").fetchone()[0],0)
        self.assertEqual(self.counts(),(0,0,0))
    def test_concurrent_same_capability_single_commit(self):
        self.provision_fixture();p=self.payload()
        with ThreadPoolExecutor(max_workers=10) as pool:results=list(pool.map(lambda _:self.call(p),range(20)))
        self.assertEqual(results.count("OK"),1);self.assertEqual(results.count("DENY"),19)
        self.assertEqual(self.counts(),(1,0,1))
    def test_full_cycle_and_replay_and_cooldown(self):
        self.chain();terminal=self.payload("SUCCESS");self.accept(terminal)
        self.assertEqual(self.call(terminal),"DENY")
        self.assertEqual(self.call(self.payload("ADVANCE")),"DENY")
        self.accept(self.payload("ADVANCE",evidence=terminal["evidence"]))
        self.assertEqual(self.state()[0],"ADVANCED");self.assertEqual(self.counts(),(5,1,5))
        self.plan=hx(32);self.grant=hx(32);self.epoch=hx(32)
        self.assertEqual(self.call(self.payload()),"DENY")
    def test_grant_expired_and_wrong_plan(self):
        self.provision_fixture();self.accept(self.payload());self.accept(self.payload("ISSUE"))
        self.assertEqual(self.call(self.payload("CONSUME",plan=hx(32))),"DENY")
        with self.admin() as c:c.execute("UPDATE ei3.grants SET expires_at=clock_timestamp()-interval '1 second'")
        self.assertEqual(self.call(self.payload("CONSUME")),"DENY")
        self.assertEqual(self.state()[0],"ISSUED")
    def test_unknown_freezes_and_no_advance(self):
        self.chain();self.accept(self.payload("UNKNOWN"))
        self.assertEqual(self.state()[0],"FROZEN")
        self.assertEqual(self.call(self.payload("ADVANCE")),"DENY")
        self.assertEqual(self.counts(),(4,1,4))
    def test_expiry_after_lock_and_invalid_window(self):
        self.provision_fixture();now=int(time.time())
        for q in (self.payload(issued_at=str(now-31),not_before=str(now-31),expires_at=str(now-1)),
                  self.payload(expires_at=str(now+31)),self.payload(issued_at=str(now+10),not_before=str(now+10),expires_at=str(now+20))):
            self.assertEqual(self.call(q),"DENY")
        p=self.payload(expires_at=str(now+2))
        with self.admin() as c,ThreadPoolExecutor(max_workers=1) as pool:
            c.execute("BEGIN");c.execute("SELECT 1 FROM ei3.state FOR UPDATE")
            future=pool.submit(self.call,p)
            time.sleep(2.4);c.execute("COMMIT")
            self.assertEqual(future.result(timeout=8),"DENY")
        self.assertEqual(self.counts(),(0,0,0))
    def test_rollback_and_lost_ack_after_restart(self):
        self.provision_fixture();p=self.payload()
        with self.connect("ei3_arm") as c:
            c.execute("BEGIN");self.assertEqual(self.call(p,conn=c),"OK");c.execute("ROLLBACK")
        self.assertEqual(self.counts(),(0,0,0))
        # Transaction rollback does not burn token; independent verifiers must never retry.
        self.assertEqual(self.call(p),"OK")
        command([self.pg/"pg_ctl","-D",self.data,"-l",self.dir/"pg.log","-m","immediate","-w","restart","-o",self.options])
        self.assertEqual(self.call(p),"DENY");self.assertEqual(self.counts(),(1,0,1))
    def test_atomic_failure_after_insert(self):
        import psycopg
        self.provision_fixture();p=self.payload()
        with self.admin() as c:c.execute("ALTER TABLE ei3.state ADD CONSTRAINT fixture_fault CHECK(mode<>'ARMED')")
        try:
            with self.assertRaises(psycopg.Error):self.call(p)
            self.assertEqual(self.counts(),(0,0,0));self.assertEqual(self.state()[0],"READY")
        finally:
            with self.admin() as c:c.execute("ALTER TABLE ei3.state DROP CONSTRAINT fixture_fault")
    def test_used_id_and_checkpoint_reuse(self):
        self.provision_fixture();p=self.payload();self.accept(p)
        self.assertEqual(self.call(self.payload("ISSUE",authorization_id=p["authorization_id"])),"DENY")
        self.assertEqual(self.call(self.payload("ISSUE",new_checkpoint=p["old_checkpoint"])),"DENY")
        self.assertEqual(self.counts(),(1,0,1))
