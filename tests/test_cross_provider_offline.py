"""Cross-provider acceptance: actual local workerd, TLS/SCRAM PostgreSQL and WebAuthn."""
from contextlib import contextmanager
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import test_production_integration_offline as fixture
import test_production_unified as previous
from production_cross_provider import CrossController,Plan,LocalWitness,Audit,DOMAIN,ZERO,compact
from production_pg import Denied
from production_recovery import Anchor
from production_candidate import LocalTLS
from production_passkey import Ceremonies
from production_psycopg import LocalPg
from production_witness import Witness
from production_owner_journal import Journal,DOMAIN as JOURNAL_DOMAIN
from production_owner_trust import OwnerSession
from production_credential_store import CredentialStore,DurableCeremonies,fingerprint
from production_verified_recovery import WitnessAudit,verified_recovery

ROOT=fixture.ROOT
DIR=ROOT/"research/cloudflare-witness"
S,CP,C=previous.S,previous.CP,previous.C
PASSWORD=previous.PASSWORD
MAC=bytes.fromhex("1234567890abcdef"*4)
IDENTITY="1"*64

class OwnerSigner:
    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        self.key=ec.generate_private_key(ec.SECP256R1())
        self.public=self.key.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint).hex()
    def sign(self,payload):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        from cryptography.hazmat.primitives import hashes
        body=compact(payload);msg=DOMAIN+body
        r,s=decode_dss_signature(self.key.sign(msg,ec.ECDSA(hashes.SHA256())))
        return body,(r.to_bytes(32,"big")+s.to_bytes(32,"big")).hex(),hmac.new(MAC,msg,hashlib.sha256).hexdigest()

class Runtime:
    def __init__(self,root,signer):
        self.root=root;self.persist=root/"persist";self.proc=None
        self.env={"WITNESS_MODE":"OFFLINE_V2","WITNESS_STORE":S,"WITNESS_IDENTITY":IDENTITY,
                  "WITNESS_CANDIDATE":C,"WITNESS_OWNER_PUBLIC":signer.public,"WITNESS_HMAC_KEY":MAC.hex()}
        self.port=None
    def stop(self,hard=False):
        p=self.proc;self.proc=None
        if p is None:return
        try:os.killpg(p.pid,signal.SIGKILL if hard else signal.SIGTERM)
        except ProcessLookupError:pass
        try:p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=3)
        try:os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    def start(self,config="wrangler.jsonc"):
        self.stop()
        with socket.socket() as sock:sock.bind(("127.0.0.1",0));self.port=sock.getsockname()[1]
        node=os.environ.get("EI_NODE") or shutil.which("node")
        if not node:raise RuntimeError("node missing")
        args=[node,str(DIR/"node_modules/wrangler/bin/wrangler.js"),"dev","--local","--ip","127.0.0.1",
              "--port",str(self.port),"--local-protocol","https","--persist-to",str(self.persist),"--config",str(DIR/config)]
        for k,v in self.env.items():args.extend(["--var",k+":"+v])
        log=self.root/"wrangler.log"
        with log.open("wb") as f:
            self.proc=subprocess.Popen(args,cwd=DIR,start_new_session=True,stdout=f,stderr=subprocess.STDOUT,
                env={"PATH":os.environ["PATH"],"HOME":str(self.root),"CI":"true","WRANGLER_SEND_METRICS":"false",
                     "CLOUDFLARE_INCLUDE_PROCESS_ENV":"false","CLOUDFLARE_CF_FETCH_ENABLED":"false"})
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if self.proc.poll() is not None:raise RuntimeError(log.read_text()[-4000:])
            try:
                if self.call("/health")[0]==200:break
            except OSError:pass
            time.sleep(.1)
        else:raise RuntimeError("workerd startup timeout "+log.read_text()[-4000:])
        # Local fixture's TLS certificate obtained via a separate loopback handshake.
        pem=ssl.get_server_certificate(("127.0.0.1",self.port),timeout=3)
        return LocalWitness(self.port,hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest())
    def call(self,path,method="GET"):
        # Bootstrap is restricted to the disposable loopback fixture, not candidate.
        conn=http.client.HTTPSConnection("127.0.0.1",self.port,timeout=2,context=ssl._create_unverified_context())
        try:
            conn.request(method,path);r=conn.getresponse();return r.status,r.read().decode()
        finally:conn.close()

# Test-side owner: signs records independently of the controller. No production signer.
def journal_signature(signer,p):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives import hashes
    r,s=decode_dss_signature(signer.key.sign(JOURNAL_DOMAIN+compact(p),ec.ECDSA(hashes.SHA256())))
    return (r.to_bytes(32,"big")+s.to_bytes(32,"big")).hex()

class OwnerFixture:
    def __init__(self,test,controller):
        self.test=test;self.raw=controller;self.session=OwnerSession(controller,test.journal)
    def __getattr__(self,k):return getattr(self.raw,k)
    def record(self,kind,**extra):
        p=self.raw.plan;pin=self.test.owner_pin
        old=self.test.journal.read(pin)
        return dict(index=old["index"]+1,previous=pin,kind=kind,store=p.anchor.store,identity=p.identity,object_id=p.object_id,
                    checkpoint=p.anchor.checkpoint,seq=p.seq,head=p.head,binding=p.binding("ARM"))|extra
    def sig(self,p):return journal_signature(self.test.signer,p)
    def arm(self,assertion,packet):
        p=self.record("CLAIM_INTENT")
        pin,head=self.session.arm(self.test.owner_pin,p,self.sig(p),assertion,packet)
        self.test.owner_pin=pin
        p=self.record("CLAIM_RECEIPT",seq=self.plan.seq+1,head=head)
        self.test.owner_pin=self.session.accept_claim(pin,p,self.sig(p))
    def issue(self,assertion):return self.session.issue(self.test.owner_pin,assertion)
    def execute(self,assertion):return self.session.execute(self.test.owner_pin,assertion)
    def advance(self,assertion,packet):
        p=self.record("ADVANCE_INTENT",seq=self.plan.seq+1,head=self.claim_head)
        pin,receipt=self.session.advance(self.test.owner_pin,p,self.sig(p),assertion,packet)
        self.test.owner_pin=pin
        # No cache until the final owner-signed record binding the actual result.
        self.test.assertFalse(self.raw.status(owner_pin=pin)["cache_valid"])
        p=self.record("ADVANCE_RECEIPT",seq=receipt["seq"],head=receipt["head"],checkpoint=receipt["checkpoint"],binding=self.raw.cache_binding())
        self.test.owner_pin=self.session.accept_advance(pin,p,self.sig(p))
        return receipt
    def status(self):return self.raw.status(owner_pin=self.test.owner_pin)

class CrossTests(unittest.TestCase):
    admin=classmethod(fixture.IntegrationTests.admin.__func__)
    @classmethod
    def setUpClass(cls):
        if not (DIR/"node_modules/wrangler/bin/wrangler.js").is_file():
            if os.environ.get("GITHUB_ACTIONS")=="true":raise RuntimeError("required workerd missing")
            raise unittest.SkipTest("local Wrangler not installed")
        previous.RealDependencyTests.setUpClass.__func__(cls)
        cls.admin((ROOT/"research/cross_provider_audit.sql").read_text()+"\nALTER ROLE ei_cross_audit LOGIN PASSWORD '"+PASSWORD+"';")
    def setUp(self):
        previous.RealDependencyTests.setUp(self)
        self.audit=Audit(self.port,self.cert,PASSWORD)
        self.signer=OwnerSigner()
        self.runtime_dir=tempfile.TemporaryDirectory(prefix="ei_cross_");self.addCleanup(self.runtime_dir.cleanup)
        self.runtime=Runtime(Path(self.runtime_dir.name),self.signer);self.addCleanup(self.runtime.stop)
        # Explicit provisioning only via isolated test entrypoint, then use actual candidate.
        self.runtime.start("wrangler.fixture.jsonc")
        status,ident=self.runtime.call("/fixture/provision","POST")
        self.assertEqual(status,201)
        self.assertEqual(self.runtime.call("/fixture/provision","POST")[0],409)
        self.runtime.stop();self.transport=self.runtime.start()
        self.assertEqual(self.runtime.call("/fixture/provision","POST")[0],403)
        self.plan=Plan.new(Anchor(S,CP,C),IDENTITY,ident,0,ZERO)
        self.owner_dir=tempfile.TemporaryDirectory();self.addCleanup(self.owner_dir.cleanup)
        self.cred_dir=tempfile.TemporaryDirectory();self.addCleanup(self.cred_dir.cleanup)
        self.journal=Journal.pin(Path(self.owner_dir.name),bytes.fromhex(self.signer.public))
        p=dict(index=0,previous=ZERO,kind="BOOTSTRAP_INTENT",store=S,identity=IDENTITY,object_id=ident,checkpoint=CP,seq=0,head=ZERO,binding="3"*64)
        pin=self.journal.append(ZERO,p,journal_signature(self.signer,p))
        p=dict(p,index=1,previous=pin,kind="GENESIS")
        self.owner_pin=self.journal.append(pin,p,journal_signature(self.signer,p))
        self.cred_store=CredentialStore(Path(self.cred_dir.name))
        self.cred_pin=self.cred_store.install_verified(self.pin,"offline",fingerprint(self.pin,"offline"))
        self.last_auth=None
    def controller(self,port,plan=None,transport=None):
        if self.last_auth is not None:self.cred_pin=self.last_auth.pin
        self.last_auth=DurableCeremonies(self.cred_store,self.cred_pin)
        raw=CrossController(plan or self.plan,self.adapters,self.last_auth,transport or self.transport,
                               self.witness,self.audit,LocalTLS(port,self.cert),journal=self.journal)
        return OwnerFixture(self,raw)
    def assertion(self,c,op):return self.virtual.assertion(c.challenge(op))
    def packet(self,c,op="CLAIM",evidence=ZERO,**overrides):
        p=c.plan.packet(op,int(time.time()),evidence,c.claim_head)
        p.update(overrides);return self.signer.sign(p)
    def arm(self,c):c.arm(self.assertion(c,"ARM"),self.packet(c))
    def issue(self,c):c.issue(self.assertion(c,"ISSUE"))
    def run_once(self,c):
        self.arm(c);self.issue(c);c.execute(self.assertion(c,"CONSUME"))
    def reset_pg(self):
        self.admin("TRUNCATE ei2.events,ei2.grants,ei2.state RESTART IDENTITY; INSERT INTO ei2.state(singleton,store_id,checkpoint,mode) VALUES(true,'"+S+"','"+CP+"','DISARMED');")
    def mode(self):return self.admin("SELECT mode FROM ei2.state;").stdout.strip()
    def test_complete_real_chain_and_verified_advance(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port);self.run_once(c)
            self.assertEqual(c.mode,"CLOSED");self.assertFalse(c.status()["cache_valid"])
            # Separate owner-side audit, not opaque hash invented by controller.
            evidence=self.audit.terminal(self.plan)
            self.assertEqual(evidence,c.evidence())
            receipt=c.advance(self.assertion(c,"ADVANCE"),self.packet(c,"ADVANCE",evidence))
            self.assertEqual(receipt["checkpoint"],self.plan.checkpoints[-1]);self.assertEqual(receipt["seq"],2)
            self.assertEqual(self.mode(),b"CLOSED");self.assertEqual(len(requests),1)
            self.assertFalse(receipt["recommendation_eligible"])
            self.assertTrue(c.status()["cache_valid"])
            with self.assertRaises(Denied):c.challenge("ARM")
            self.assertNotEqual(self.admin("UPDATE ei2.state SET mode='DISARMED';",role="ei_cross_audit",check=False).returncode,0)
    def test_witness_ack_loss_no_sql_no_worker_restart_denied(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port);original=LocalWitness.transition
            def lost(obj,*args):original(obj,*args);raise Denied("ack lost")
            with patch.object(LocalWitness,"transition",lost):
                with self.assertRaises(Denied):self.arm(c)
            self.assertEqual(c.mode,"FROZEN");self.assertEqual(self.mode(),b"DISARMED")
            self.assertEqual(requests,[])
            self.runtime.stop(True);self.transport=self.runtime.start()
            restarted=self.controller(port)
            with self.assertRaises(Denied):self.arm(restarted)
            self.assertEqual(requests,[])
    def test_pg_consume_ack_lost_after_commit_no_worker(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port);self.arm(c);self.issue(c);original=LocalPg.apply
            def lost(obj,op,values):
                original(obj,op,values)
                if op=="CONSUME":raise Denied("ack lost")
            with patch.object(LocalPg,"apply",lost):
                with self.assertRaises(Denied):c.execute(self.assertion(c,"CONSUME"))
            self.assertEqual(self.mode(),b"PENDING");self.assertEqual(requests,[])
            self.assertEqual(c.mode,"FROZEN")
            with self.assertRaises(Denied):c.evidence()
    def test_pg_success_ack_lost_no_advance_or_cache(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port);self.arm(c);self.issue(c);original=LocalPg.apply
            def lost(obj,op,values):
                original(obj,op,values)
                if op=="SUCCESS":raise Denied("ack lost")
            with patch.object(LocalPg,"apply",lost):
                with self.assertRaises(Denied):c.execute(self.assertion(c,"CONSUME"))
            self.assertEqual(self.mode(),b"CLOSED");self.assertEqual(len(requests),1)
            self.assertFalse(c.status()["cache_valid"])
            with self.assertRaises(Denied):c.challenge("ADVANCE")
    def test_rollback_after_success_and_before_advance_denies(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port);self.run_once(c);evidence=self.audit.terminal(self.plan)
            self.reset_pg()
            with self.assertRaises(Denied):c.advance(self.assertion(c,"ADVANCE"),self.packet(c,"ADVANCE",evidence))
            self.assertEqual(c.mode,"FROZEN");self.assertFalse(c.status()["cache_valid"])
            restarted=self.controller(port)
            with self.assertRaises(Denied):self.arm(restarted)
            self.assertEqual(len(requests),1)
    def test_forged_evidence_even_owner_signature_denied(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port);self.run_once(c)
            with self.assertRaises(Denied):c.advance(self.assertion(c,"ADVANCE"),self.packet(c,"ADVANCE","8"*64))
            self.assertEqual(c.mode,"FROZEN");self.assertFalse(c.status()["cache_valid"]);self.assertEqual(len(requests),1)
    def test_worker_failure_freezes_both_protocols(self):
        with fixture.tls_server(self.cert,self.tlskey,"429") as (port,requests):
            c=self.controller(port);self.arm(c);self.issue(c)
            with self.assertRaises(Denied):c.execute(self.assertion(c,"CONSUME"))
            self.assertEqual(self.mode(),b"FROZEN");self.assertEqual(len(requests),1)
            with self.assertRaises(Denied):c.evidence()
    def test_advance_ack_loss_after_commit_never_returns_new_anchor(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port);self.run_once(c);ev=self.audit.terminal(self.plan)
            original=LocalWitness.transition
            def lost(obj,*args):original(obj,*args);raise Denied("advance ack lost")
            with patch.object(LocalWitness,"transition",lost):
                with self.assertRaises(Denied):c.advance(self.assertion(c,"ADVANCE"),self.packet(c,"ADVANCE",ev))
            self.assertEqual(c.mode,"FROZEN");self.assertFalse(c.status()["cache_valid"])
            self.assertIsNone(c.advance_head);self.assertEqual(len(requests),1)
    def test_wrong_owner_signature_prevents_sql(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port)
            p=c.plan.packet("CLAIM",int(time.time()))
            with self.assertRaises(Denied):c.arm(self.assertion(c,"ARM"),OwnerSigner().sign(p))
            self.assertEqual(self.mode(),b"DISARMED");self.assertEqual(requests,[])
    def test_fresh_empty_witness_denies_even_with_owner_signature(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            self.runtime.stop();self.runtime.persist=Path(self.runtime_dir.name)/"empty"
            self.transport=self.runtime.start()
            c=self.controller(port)
            with self.assertRaises(Denied):self.arm(c)
            self.assertEqual(self.mode(),b"DISARMED");self.assertEqual(requests,[])

    def test_direct_controller_cannot_bypass_signed_journal(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port);assertion=self.assertion(c,"ARM")
            with self.assertRaises(Denied):c.raw.arm(assertion,self.packet(c))
            with self.assertRaises(Denied):c.raw.arm(assertion,self.packet(c),owner_pin=self.owner_pin)
            self.assertEqual(requests,[]);self.assertEqual(self.mode(),b"DISARMED")
            c.arm(assertion,self.packet(c))
            with self.assertRaises(Denied):c.raw.issue(self.assertion(c,"ISSUE"))
            self.assertEqual(requests,[])
    def test_verified_recovery_reads_real_pg_and_witness(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            c=self.controller(port);self.run_once(c);ev=c.evidence()
            c.advance(self.assertion(c,"ADVANCE"),self.packet(c,"ADVANCE",ev))
            self.runtime.stop();key=b"r"*32
            self.runtime.env.update(WITNESS_MODE="OFFLINE_AUDIT",WITNESS_AUDIT_KEY=key.hex())
            transport=self.runtime.start("wrangler.audit-offline.jsonc")
            reader=WitnessAudit(transport.port,transport.fingerprint,key)
            self.assertEqual(verified_recovery(self.plan,self.journal,self.owner_pin,self.audit,reader),"MANUAL_REVIEW_ONLY")
            self.assertEqual(verified_recovery(self.plan,self.journal,self.owner_pin,self.audit,WitnessAudit(transport.port,transport.fingerprint,b"x"*32)),"FROZEN_UNCONFIRMED")
            self.assertEqual(verified_recovery(self.plan,self.journal,self.owner_pin,self.audit,{}),"FROZEN_UNCONFIRMED")
            self.reset_pg()
            self.assertEqual(verified_recovery(self.plan,self.journal,self.owner_pin,self.audit,reader),"FROZEN_UNCONFIRMED")
            self.assertEqual(len(requests),1)
