"""Offline end-to-end tests: real PostgreSQL Unix socket, real ES256, TLS loopback."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from production_pg import PgLedger,Denied
from production_owner import Owner,b64,RP,ORIGIN
from production_candidate import Candidate,LocalTLS,handler_for
import production_candidate as pc

ROOT=Path(__file__).resolve().parents[1]
STORE="a"*32
CAND="b"*40
EPOCH="c"*32
GRANT="d"*32
BODY=b'[{"universe":[{"name":"ETH"}]},[{"markPx":"1700.25"}]]'

def command(args,**kwargs):
    return subprocess.run([str(a) for a in args],check=True,capture_output=True,timeout=20,**kwargs)

@contextmanager
def tls_server(cert,key,mode="ok"):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body=self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path,body))
            if mode=="stall":
                time.sleep(2)
                return
            if mode=="redirect":
                self.send_response(302)
                self.send_header("Location","/other")
                self.send_header("Content-Length","0")
                self.end_headers()
                return
            if mode=="429":
                self.send_response(429)
                self.send_header("Content-Length","0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    server.daemon_threads=True
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert,key)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,kwargs={"poll_interval":.02},daemon=True)
    thread.start()
    try: yield server.server_port,requests
    finally:
        server.shutdown();server.server_close();thread.join(2)

class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bins=sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        if not bins or os.geteuid()==0 or not Path("/usr/bin/openssl").exists():
            if os.environ.get("GITHUB_ACTIONS")=="true":
                raise RuntimeError("mandatory local PostgreSQL/OpenSSL fixture missing")
            raise unittest.SkipTest("local PostgreSQL non-root fixture unavailable")
        cls.tmp=tempfile.TemporaryDirectory(prefix="ei_pg_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.dir=Path(cls.tmp.name)
        cls.sock=cls.dir/"socket"
        cls.sock.mkdir(mode=0o700)
        cls.pg=bins[-1].parent
        cls.psql=cls.pg/"psql"
        cls.data=cls.dir/"db"
        cls.port=55432
        command([cls.pg/"initdb","-D",cls.data,"-U","ei_admin","-A","trust","--no-locale"])
        command([cls.pg/"pg_ctl","-D",cls.data,"-l",cls.dir/"pg.log","-o",
                 "-c listen_addresses='' -k "+str(cls.sock)+" -p "+str(cls.port),"-w","start"])
        cls.addClassCleanup(lambda: command([cls.pg/"pg_ctl","-D",cls.data,"-m","immediate","-w","stop"]))
        cls.admin((ROOT/"research/production_schema.sql").read_text()+"\nCREATE ROLE ei_app LOGIN IN ROLE ei_runtime;")
        cls.priv=cls.dir/"owner.pem"
        cls.pub=cls.dir/"owner.der"
        command(["/usr/bin/openssl","ecparam","-name","prime256v1","-genkey","-noout","-out",cls.priv])
        command(["/usr/bin/openssl","pkey","-in",cls.priv,"-pubout","-outform","DER","-out",cls.pub])
        cls.cert=cls.dir/"cert.pem";cls.tlskey=cls.dir/"tls.pem"
        command(["/usr/bin/openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1",
                 "-subj","/CN=127.0.0.1","-addext","subjectAltName=IP:127.0.0.1",
                 "-keyout",cls.tlskey,"-out",cls.cert])
        cls.badcert=cls.dir/"bad.pem";cls.badkey=cls.dir/"badkey.pem"
        command(["/usr/bin/openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1",
                 "-subj","/CN=wrong.local","-addext","subjectAltName=DNS:wrong.local",
                 "-keyout",cls.badkey,"-out",cls.badcert])

    @classmethod
    def admin(cls,sql,role="ei_admin",check=True):
        return subprocess.run([str(cls.psql),"-X","-qAt","-w","-v","ON_ERROR_STOP=1",
                               "-h",str(cls.sock),"-p",str(cls.port),"-U",role,"-d","postgres","-f","-"],
                              input=sql.encode(),capture_output=True,timeout=10,check=check,
                              env={"PATH":"/usr/bin:/bin","LANG":"C"})

    def setUp(self):
        self.admin("TRUNCATE ei.events,ei.grants,ei.system_state RESTART IDENTITY; "
                   "INSERT INTO ei.system_state(store_id) VALUES('"+STORE+"');")
        self.ledger=PgLedger(self.psql,self.sock,self.port,STORE,CAND,EPOCH)
        self.owner=Owner(b64(b"offline-owner"),self.pub.read_bytes())

    def candidate(self,port,cert=None):
        return Candidate(self.ledger,Owner(self.owner.credential_id,self.pub.read_bytes()),
                         LocalTLS(port,cert or self.cert))

    def assertion(self,challenge,*,flags=5,origin=ORIGIN,rp=RP,client_challenge=None):
        raw=json.dumps({"type":"webauthn.get","challenge":client_challenge or challenge["challenge"],
                        "origin":origin,"crossOrigin":False},separators=(",",":")).encode()
        auth=hashlib.sha256(rp.encode()).digest()+bytes([flags])+b"\0"*4
        sig=command(["/usr/bin/openssl","dgst","-sha256","-sign",self.priv],
                    input=auth+hashlib.sha256(raw).digest()).stdout
        return {"id":self.owner.credential_id,"challenge":challenge["challenge"],
                "clientDataJSON":b64(raw),"authenticatorData":b64(auth),"signature":b64(sig)}

    def issue(self,c):
        ch=c.challenge("ISSUE")
        return c.confirm("ISSUE",ch["grant"],self.assertion(ch))["grant"]

    def execute(self,c,g):
        ch=c.challenge("CONSUME",g)
        return c.confirm("CONSUME",g,self.assertion(ch))

    def test_real_postgres_one_reservation_under_concurrency(self):
        self.ledger.apply("ISSUE",GRANT)
        def attempt(_):
            try: self.ledger.apply("CONSUME",GRANT);return 1
            except Denied:return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt,range(12))),1)
        self.assertEqual(self.admin("SELECT count(*) FROM ei.events WHERE kind='CONSUMED';").stdout.strip(),b"1")
        with self.assertRaises(Denied): self.ledger.apply("CONSUME",GRANT)

    def test_postgres_role_cannot_mutate_or_read_tables(self):
        for sql in ("SELECT * FROM ei.grants;","DELETE FROM ei.events;",
                    "UPDATE ei.system_state SET frozen=false;","DROP TABLE ei.grants;",
                    "TRUNCATE ei.grants;"):
            self.assertNotEqual(self.admin(sql,role="ei_app",check=False).returncode,0)

    def test_postgres_global_pending_and_unknown_freeze(self):
        self.ledger.apply("ISSUE",GRANT)
        self.ledger.apply("CONSUME",GRANT)
        other=replace(self.ledger,epoch="e"*32)
        with self.assertRaises(Denied):other.apply("ISSUE","f"*32)
        self.ledger.apply("UNKNOWN",GRANT)
        with self.assertRaises(Denied):other.apply("ISSUE","f"*32)
        self.assertEqual(self.admin("SELECT frozen FROM ei.system_state;").stdout.strip(),b"t")

    def test_postgres_epoch_candidate_store_and_expiry(self):
        self.ledger.apply("ISSUE",GRANT)
        for changed in (replace(self.ledger,epoch="e"*32),replace(self.ledger,candidate="e"*40),
                        replace(self.ledger,store_id="e"*32)):
            with self.assertRaises(Denied):changed.apply("CONSUME",GRANT)
        self.admin("UPDATE ei.grants SET expires=clock_timestamp()-interval '1 second';")
        with self.assertRaises(Denied):self.ledger.apply("CONSUME",GRANT)

    def test_postgres_revoke_terminal(self):
        self.ledger.apply("ISSUE",GRANT)
        self.ledger.apply("REVOKE",GRANT)
        for op in ("CONSUME","ISSUE","REVOKE"):
            with self.assertRaises(Denied):self.ledger.apply(op,GRANT)

    def test_owner_bad_origin_rp_flags_signature_replay(self):
        c=self.candidate(1)
        for kwargs in ({"origin":"https://evil.invalid"},{"rp":"evil.invalid"},{"flags":1},
                       {"flags":4},{"flags":0x85},{"flags":0x15},{"client_challenge":"x"*43}):
            ch=c.challenge("ISSUE");a=self.assertion(ch,**kwargs)
            with self.assertRaises(Denied):c.confirm("ISSUE",ch["grant"],a)
        ch=c.challenge("ISSUE");a=self.assertion(ch)
        a["signature"]=b64(b"x"*64)
        with self.assertRaises(Denied):c.confirm("ISSUE",ch["grant"],a)
        with self.assertRaises(Denied):c.confirm("ISSUE",ch["grant"],self.assertion(ch))
        self.assertEqual(self.admin("SELECT count(*) FROM ei.grants;").stdout.strip(),b"0")

    def test_owner_wrong_credential_and_purpose(self):
        c=self.candidate(1)
        ch=c.challenge("ISSUE");a=self.assertion(ch);a["id"]=b64(b"other")
        with self.assertRaises(Denied):c.confirm("ISSUE",ch["grant"],a)
        ch=c.challenge("ISSUE")
        with self.assertRaises(Denied):c.confirm("REVOKE",ch["grant"],self.assertion(ch))

    def test_owner_success_and_restart_invalidates_grant(self):
        c=self.candidate(1);g=self.issue(c)
        new=self.candidate(1)
        self.assertNotEqual(new.ledger.epoch,c.ledger.epoch)
        with self.assertRaises(Denied):new.challenge("CONSUME",g)
        self.assertEqual(new.status()["rest_operational_freshness"]["state"],"NO_DATA")

    def test_real_tls_cache_and_lifecycle(self):
        with tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.candidate(port)
            self.assertFalse(c.status()["cache_valid"])
            g=self.issue(c)
            reply=self.execute(c,g)
            self.assertFalse(reply["recommendation_eligible"])
            self.assertEqual(c.market()["mark_price"],"1700.25")
            received=c._receipt.observation.received_monotonic_ns
            clock=[received]
            def sleep(delay):clock[0]+=round(delay*1e9)
            with patch.object(pc.time,"monotonic_ns",side_effect=lambda:clock[0]),patch.object(pc.time,"sleep",side_effect=sleep):
                samples=c.lifecycle()
                self.assertEqual([s["state"] for s in samples],["FRESH","DEGRADED","STALE","STALE"])
                self.assertEqual([s["cache_valid"] for s in samples],[True,True,True,False])
                self.assertTrue(all(s["recommendation_eligible"] is False for s in samples))
                with self.assertRaises(Denied):c.market()
            with self.assertRaises(Denied):c.challenge("CONSUME",g)
            self.assertEqual(requests,[("/info",b'{"type":"metaAndAssetCtxs"}')])
            self.assertEqual(self.admin("SELECT string_agg(kind,',' ORDER BY seq) FROM ei.events;").stdout.strip(),b"ISSUED,CONSUMED,SUCCESS")

    def test_tls_wrong_hostname_freezes_before_http(self):
        with tls_server(self.badcert,self.badkey) as (port,requests):
            c=self.candidate(port,self.badcert);g=self.issue(c)
            with self.assertRaises(Exception):self.execute(c,g)
            self.assertEqual(requests,[])
            self.assertTrue(c.status()["frozen"])
            self.assertEqual(self.admin("SELECT frozen FROM ei.system_state;").stdout.strip(),b"t")

    def test_tls_untrusted_certificate_freezes(self):
        with tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.candidate(port,self.badcert);g=self.issue(c)
            with self.assertRaises(Exception):self.execute(c,g)
            self.assertEqual(requests,[])
            self.assertTrue(c.status()["frozen"])

    def test_tls_redirect_and_429_no_retry(self):
        for mode in ("redirect","429"):
            self.setUp()
            with tls_server(self.cert,self.tlskey,mode) as (port,requests):
                c=self.candidate(port);g=self.issue(c)
                with self.assertRaises(Exception):self.execute(c,g)
                self.assertEqual(len(requests),1)
                self.assertTrue(c.status()["frozen"])

    def test_tls_stalled_response_deadline(self):
        with tls_server(self.cert,self.tlskey,"stall") as (port,requests):
            c=self.candidate(port);g=self.issue(c)
            start=time.monotonic()
            with patch.object(pc,"BUDGET_NS",900_000_000):
                with self.assertRaises(Exception):self.execute(c,g)
            self.assertLess(time.monotonic()-start,4)
            self.assertEqual(len(requests),1)
            self.assertTrue(c.status()["frozen"])

    def test_public_http_routes_never_fetch(self):
        with tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.candidate(port)
            server=ThreadingHTTPServer(("127.0.0.1",0),handler_for(c))
            thread=threading.Thread(target=server.serve_forever,kwargs={"poll_interval":.02},daemon=True)
            thread.start()
            try:
                conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=3)
                for route,status in (("/health",200),("/market/status",200),("/market/eth",503),
                                     ("/market/eth?refresh=1",404),("/admin/rest/execute",404)):
                    conn.request("GET",route);response=conn.getresponse()
                    self.assertEqual(response.status,status);response.read()
                conn.request("POST","/admin/rest/execute",body=b"{}",headers={"Content-Type":"application/json"})
                response=conn.getresponse();self.assertEqual(response.status,403);response.read()
                conn.close()
                self.assertEqual(requests,[])
                self.assertEqual(self.admin("SELECT count(*) FROM ei.grants;").stdout.strip(),b"0")
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_ledger_unavailable_never_launches_worker(self):
        with tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.candidate(port);g=self.issue(c)
            c.ledger=replace(c.ledger,port=55433)
            with self.assertRaises(Denied):self.execute(c,g)
            self.assertEqual(requests,[])
            self.assertTrue(c.status()["frozen"])

    def test_production_app_unchanged_and_no_live_entrypoint(self):
        import app
        self.assertNotIn("production_candidate",Path(app.__file__).read_text())
        self.assertNotIn('api.hyperliquid.xyz', (ROOT/"production_tls_worker.py").read_text())

if __name__=="__main__":unittest.main()
