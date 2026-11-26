"""Real PyPI dependencies, TLS SCRAM DB, synthetic ES256 authenticator, witness."""
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import test_production_integration_offline as fixture
from production_pg import Denied
from production_psycopg import LocalPg,ROLE
from production_passkey import Ceremonies,RP,ORIGIN
from production_owner import b64
from production_recovery import Anchor
from production_witness import Witness
from production_unified import Unified,handler_for
from production_candidate import LocalTLS
import production_unified as unified

S="a"*32;CP="b"*64;C="c"*40
PASSWORD="offline-fixture-not-a-real-secret"

class WitnessTests(unittest.TestCase):
    def test_atomic_claim_and_version_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Witness.pin(Path(tmp))
            a=Anchor(S,CP,C)
            def claim(_):
                try:w.claim(a);return 1
                except Denied:return 0
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(claim,range(12))),1)
            self.assertEqual(len(list(Path(tmp).glob("*.spent"))),1)
            with self.assertRaises(Denied):Witness.pin(Path(tmp)).claim(Anchor(S,CP,"d"*40))
    def test_missing_or_replaced_witness_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"w";p.mkdir(mode=0o700);w=Witness.pin(p)
            p.rename(Path(tmp)/"old");p.mkdir(mode=0o700)
            with self.assertRaises(Denied):w.claim(Anchor(S,CP,C))

class VirtualAuthenticator:
    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        self.key=ec.generate_private_key(ec.SECP256R1())
        self.id=b"offline-virtual-credential"
        self.count=0
    def client(self,opts,kind,origin=ORIGIN):
        return json.dumps({"type":kind,"challenge":json.loads(opts)["challenge"],"origin":origin,
                           "crossOrigin":False},separators=(",",":")).encode()
    def registration(self,opts):
        import cbor2
        numbers=self.key.public_key().public_numbers()
        cose=cbor2.dumps({1:2,3:-7,-1:1,-2:numbers.x.to_bytes(32,"big"),-3:numbers.y.to_bytes(32,"big")})
        data=hashlib.sha256(RP.encode()).digest()+b"\x45"+b"\0"*4+b"\0"*16+len(self.id).to_bytes(2,"big")+self.id+cose
        att=cbor2.dumps({"fmt":"none","attStmt":{},"authData":data})
        return json.dumps({"id":b64(self.id),"rawId":b64(self.id),"type":"public-key",
                           "response":{"clientDataJSON":b64(self.client(opts,"webauthn.create")),"attestationObject":b64(att)}})
    def assertion(self,opts,origin=ORIGIN,flags=5):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        self.count+=1
        raw=self.client(opts,"webauthn.get",origin)
        data=hashlib.sha256(RP.encode()).digest()+bytes([flags])+self.count.to_bytes(4,"big")
        sig=self.key.sign(data+hashlib.sha256(raw).digest(),ec.ECDSA(hashes.SHA256()))
        return json.dumps({"id":b64(self.id),"rawId":b64(self.id),"type":"public-key",
                           "response":{"clientDataJSON":b64(raw),"authenticatorData":b64(data),"signature":b64(sig),"userHandle":None}})

class RealDependencyTests(unittest.TestCase):
    admin=classmethod(fixture.IntegrationTests.admin.__func__)
    @classmethod
    def setUpClass(cls):
        try:import psycopg,webauthn,cbor2,cryptography
        except ImportError:
            if os.environ.get("GITHUB_ACTIONS")=="true":raise
            raise unittest.SkipTest("approved dependency wheels not installed")
        fixture.IntegrationTests.setUpClass.__func__(cls)
        cls.admin((fixture.ROOT/"research/production_security_schema.sql").read_text())
        for role in set(ROLE.values()):
            cls.admin("ALTER ROLE "+role+" LOGIN PASSWORD '"+PASSWORD+"';")
        cls.tlskey.chmod(0o600)
        (cls.data/"pg_hba.conf").write_text("local all all trust\nhostssl all all 127.0.0.1/32 scram-sha-256\n")
        fixture.command([cls.pg/"pg_ctl","-D",cls.data,"-l",cls.dir/"pg.log","-m","fast","-w","restart","-o",
            "-c listen_addresses=127.0.0.1 -k "+str(cls.sock)+" -p "+str(cls.port)+
            " -c ssl=on -c ssl_cert_file="+str(cls.cert)+" -c ssl_key_file="+str(cls.tlskey)])
    def setUp(self):
        self.admin("TRUNCATE ei2.events,ei2.grants,ei2.state RESTART IDENTITY; "
                   "INSERT INTO ei2.state(singleton,store_id,checkpoint,mode) VALUES(true,'"+S+"','"+CP+"','DISARMED');")
        self.tmpw=tempfile.TemporaryDirectory();self.addCleanup(self.tmpw.cleanup)
        self.witness=Witness.pin(Path(self.tmpw.name))
        self.virtual=VirtualAuthenticator()
        token=b"x"*32
        enrollment=Ceremonies(bootstrap_digest=hashlib.sha256(token).digest())
        opts=enrollment.begin_registration(token)
        self.pin=enrollment.finish_registration(self.virtual.registration(opts))
        self.adapters={op:LocalPg(self.port,self.cert,role,PASSWORD) for op,role in ROLE.items()}
    def controller(self,port,cert=None):
        return Unified(Anchor(S,CP,C),self.adapters,Ceremonies(self.pin),self.witness,LocalTLS(port,cert or self.cert))
    def confirm(self,c,op):
        return c.confirm(op,self.virtual.assertion(c.challenge(op)))
    def execute(self,c):
        return c.execute(self.virtual.assertion(c.challenge("CONSUME")))
    def test_real_webauthn_bad_origin_uv_replay(self):
        c=Ceremonies(self.pin)
        for origin,flags in (("https://evil.invalid",5),(ORIGIN,1)):
            opts=c.begin("ARM","test")
            bad=self.virtual.assertion(opts,origin,flags)
            with self.assertRaises(Denied):c.verify("ARM","test",bad)
            with self.assertRaises(Denied):c.verify("ARM","test",bad)
        opts=c.begin("ARM","test")
        good=self.virtual.assertion(opts)
        c.verify("ARM","test",good)
        with self.assertRaises(Denied):c.verify("ARM","test",good)
    def test_real_psycopg_wrong_ca_denies(self):
        cfg=LocalPg(self.port,self.badcert,ROLE["ARM"],PASSWORD)
        with self.assertRaises(Denied):cfg.apply("ARM",(S,CP,"f"*64,"e"*32,C,"d"*32))
        self.assertEqual(self.admin("SELECT mode FROM ei2.state;").stdout.strip(),b"DISARMED")
    def test_unified_actual_dependencies_one_tls_request_cache(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port)
            self.assertFalse(c.status()["cache_valid"])
            self.confirm(c,"ARM");self.confirm(c,"ISSUE")
            result=self.execute(c)
            self.assertFalse(result["recommendation_eligible"])
            self.assertEqual(c.market()["mark_price"],"1700.25")
            self.assertEqual(c.status()["recovery_state"],"CLOSED")
            self.assertEqual(self.admin("SELECT string_agg(kind,',' ORDER BY seq) FROM ei2.events;").stdout.strip(),b"ARM,ISSUE,CONSUME,SUCCESS")
            received=c._receipt.observation.received_monotonic_ns;clock=[received]
            def sleep(delay):clock[0]+=round(delay*1e9)
            with patch.object(unified.time,"monotonic_ns",side_effect=lambda:clock[0]),patch.object(unified.time,"sleep",side_effect=sleep):
                samples=c.lifecycle()
                self.assertEqual([s["rest_operational_freshness"]["state"] for s in samples],["FRESH","DEGRADED","STALE","STALE"])
                self.assertEqual([s["cache_valid"] for s in samples],[True,True,True,False])
                with self.assertRaises(Denied):c.market()
            with self.assertRaises(Denied):c.challenge("CONSUME")
            self.assertEqual(len(requests),1)
    def test_witness_denies_rollback_to_identical_old_anchor(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port);self.confirm(c,"ARM");self.confirm(c,"ISSUE");self.execute(c)
            self.admin("TRUNCATE ei2.events,ei2.grants,ei2.state RESTART IDENTITY; "
                       "INSERT INTO ei2.state(singleton,store_id,checkpoint,mode) VALUES(true,'"+S+"','"+CP+"','DISARMED');")
            restarted=self.controller(port)
            with self.assertRaises(Denied):self.confirm(restarted,"ARM")
            self.assertFalse(restarted.status()["cache_valid"])
            self.assertEqual(restarted.status()["recovery_state"],"FROZEN")
            self.assertEqual(len(requests),1)
            self.assertEqual(self.admin("SELECT count(*) FROM ei2.events;").stdout.strip(),b"0")
    def test_worker_failure_no_cache_and_witness_spent(self):
        with fixture.tls_server(self.cert,self.tlskey,"429") as (port,requests):
            c=self.controller(port);self.confirm(c,"ARM");self.confirm(c,"ISSUE")
            with self.assertRaises(Denied):self.execute(c)
            self.assertFalse(c.status()["cache_valid"])
            self.assertEqual(self.admin("SELECT mode FROM ei2.state;").stdout.strip(),b"FROZEN")
            self.assertEqual(len(requests),1)
            with self.assertRaises(Denied):self.witness.claim(Anchor(S,CP,C))
    def test_public_http_no_admin_or_upstream(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port)
            server=ThreadingHTTPServer(("127.0.0.1",0),handler_for(c))
            t=threading.Thread(target=server.serve_forever,kwargs={"poll_interval":.02},daemon=True);t.start()
            try:
                conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=2)
                for method,path,code in (("GET","/health",200),("GET","/market/status",200),("GET","/market/eth",503),("POST","/admin/rest/execute",403)):
                    conn.request(method,path);res=conn.getresponse();self.assertEqual(res.status,code);res.read()
                conn.close();self.assertEqual(requests,[])
            finally:server.shutdown();server.server_close();t.join(2)

if __name__=="__main__":unittest.main()
