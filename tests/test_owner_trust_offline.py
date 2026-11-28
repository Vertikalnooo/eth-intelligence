"""Local workerd bootstrap + owner journal + real PG and WebAuthn."""
import hashlib
import hmac
import json
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch
from production_owner_journal import Journal,recovery,compact,digest,ZERO,DOMAIN
from production_owner_trust import OwnerSession,bootstrap_once,BOOT_DOMAIN
from production_cross_provider import Plan,Audit,CrossController
from production_recovery import Anchor
from production_passkey import Ceremonies,SAFARI_RP,SAFARI_ORIGIN
from production_candidate import LocalTLS
from production_credential_store import CredentialStore,DurableCeremonies,fingerprint
from production_pg import Denied
import test_cross_provider_offline as cross
import test_production_unified as prev
import test_production_integration_offline as fixture

S,CP,C=cross.S,cross.CP,cross.C
I=cross.IDENTITY

def sign(key,domain,body):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives import hashes
    r,s=decode_dss_signature(key.sign(domain+body,ec.ECDSA(hashes.SHA256())))
    return (r.to_bytes(32,"big")+s.to_bytes(32,"big")).hex()

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.signer=cross.OwnerSigner()
        self.j=Journal.pin(Path(self.tmp.name),bytes.fromhex(self.signer.public))
        self.id="2"*64
    def record(self,index,previous,kind,**kw):
        return dict(index=index,previous=previous,kind=kind,store=S,identity=I,object_id=self.id,
                    checkpoint=CP,seq=0,head=ZERO,binding="3"*64)|kw
    def append(self,pin,p):
        return self.j.append(pin,p,sign(self.signer.key,DOMAIN,compact(p)))
    def test_pending_genesis_and_external_pin(self):
        p=self.record(0,ZERO,"BOOTSTRAP_INTENT")
        head=self.append(ZERO,p)
        self.assertEqual(recovery(self.j,head,{},{}),"FROZEN_PENDING_OR_EMPTY")
        with self.assertRaises(Denied):self.append(ZERO,p)
        g=self.record(1,head,"GENESIS");new=self.append(head,g)
        pg=dict(store=S,checkpoint=CP,mode="DISARMED",active=None)
        w=dict(store=S,identity=I,object_id=self.id,checkpoint=CP,seq=0,head=ZERO,mode="READY")
        self.assertEqual(recovery(self.j,new,pg,w),"MANUAL_REVIEW_ONLY")
        self.assertEqual(recovery(self.j,new,pg,dict(w,seq=1)),"FROZEN_WITNESS_MISMATCH")
        self.assertEqual(recovery(self.j,new,dict(pg,checkpoint="9"*64),w),"FROZEN_PG_MISMATCH")
        # Restore old journal on same inode: separately pinned latest head detects it.
        (Path(self.tmp.name)/"0001.json").unlink()
        self.assertEqual(recovery(self.j,new,pg,w),"FROZEN_UNCONFIRMED")
    def test_wrong_signer_and_tampering(self):
        p=self.record(0,ZERO,"BOOTSTRAP_INTENT")
        with self.assertRaises(Denied):self.j.append(ZERO,p,sign(cross.OwnerSigner().key,DOMAIN,compact(p)))
        head=self.append(ZERO,p)
        f=Path(self.tmp.name)/"0000.json"
        f.write_bytes(f.read_bytes().replace(b"BOOTSTRAP_INTENT",b"GENESIS"))
        self.assertEqual(recovery(self.j,head,{},{}),"FROZEN_UNCONFIRMED")
    def test_concurrent_bootstrap_only_one(self):
        from concurrent.futures import ThreadPoolExecutor
        p=self.record(0,ZERO,"BOOTSTRAP_INTENT")
        sig=sign(self.signer.key,DOMAIN,compact(p))
        def run(_):
            try:self.j.append(ZERO,p,sig);return 1
            except Denied:return 0
        with ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(run,range(12))),1)

class SafariTests(unittest.TestCase):
    def test_profile_registration_and_assertion(self):
        class Safari(prev.VirtualAuthenticator):
            def client(self,opts,kind,origin=SAFARI_ORIGIN):
                return super().client(opts,kind,origin)
            def registration(self,opts):
                import cbor2
                from production_owner import b64
                numbers=self.key.public_key().public_numbers()
                cose=cbor2.dumps({1:2,3:-7,-1:1,-2:numbers.x.to_bytes(32,"big"),-3:numbers.y.to_bytes(32,"big")})
                data=hashlib.sha256(SAFARI_RP.encode()).digest()+b"\x45"+b"\0"*4+b"\0"*16+len(self.id).to_bytes(2,"big")+self.id+cose
                att=cbor2.dumps({"fmt":"none","attStmt":{},"authData":data})
                return json.dumps({"id":b64(self.id),"rawId":b64(self.id),"type":"public-key","response":{"clientDataJSON":b64(self.client(opts,"webauthn.create")),"attestationObject":b64(att)}})
            def assertion(self,opts,origin=SAFARI_ORIGIN,flags=5):
                from cryptography.hazmat.primitives.asymmetric import ec
                from cryptography.hazmat.primitives import hashes
                from production_owner import b64
                self.count+=1;raw=self.client(opts,"webauthn.get",origin)
                data=hashlib.sha256(SAFARI_RP.encode()).digest()+bytes([flags])+self.count.to_bytes(4,"big")
                sig=self.key.sign(data+hashlib.sha256(raw).digest(),ec.ECDSA(hashes.SHA256()))
                return json.dumps({"id":b64(self.id),"rawId":b64(self.id),"type":"public-key","response":{"clientDataJSON":b64(raw),"authenticatorData":b64(data),"signature":b64(sig),"userHandle":None}})
        token=b"x"*32;auth=Safari()
        reg=Ceremonies(bootstrap_digest=hashlib.sha256(token).digest(),profile="safari-preparation")
        opts=reg.begin_registration(token)
        self.assertEqual(json.loads(opts)["rp"]["id"],SAFARI_RP)
        pin=reg.finish_registration(auth.registration(opts))
        c=Ceremonies(pin,profile="safari-preparation")
        opts=c.begin("ARM","bound")
        self.assertEqual(json.loads(opts)["rpId"],SAFARI_RP)
        with self.assertRaises(Denied):c.verify("ARM","bound",auth.assertion(opts,"https://localhost"))
        opts=c.begin("ARM","bound");c.verify("ARM","bound",auth.assertion(opts))
        with self.assertRaises(Denied):c.verify("ARM","bound",auth.assertion(opts))

class BootstrapTests(unittest.TestCase):
    admin=classmethod(fixture.IntegrationTests.admin.__func__)
    @classmethod
    def setUpClass(cls):cross.CrossTests.setUpClass.__func__(cls)
    def setUp(self):
        prev.RealDependencyTests.setUp(self)
        self.audit=Audit(self.port,self.cert,prev.PASSWORD)
        self.signer=cross.OwnerSigner()
        self.rt_dir=tempfile.TemporaryDirectory(prefix="ei_boot_");self.addCleanup(self.rt_dir.cleanup)
        self.rt=cross.Runtime(Path(self.rt_dir.name),self.signer);self.addCleanup(self.rt.stop)
        self.owner_dir=tempfile.TemporaryDirectory();self.addCleanup(self.owner_dir.cleanup)
        self.j=Journal.pin(Path(self.owner_dir.name),bytes.fromhex(self.signer.public))
        self.token="6"*64
        self.rt.env.update(WITNESS_MODE="OFFLINE_BOOTSTRAP",BOOTSTRAP_TOKEN_HASH=hashlib.sha256(bytes.fromhex(self.token)).hexdigest())
        self.transport=self.rt.start("wrangler.bootstrap-offline.jsonc")
        status,raw=self.rt.call("/v3/identity");self.assertEqual(status,200);self.id=json.loads(raw)["object_id"]
        self.body=compact(dict(store=S,identity=I,object_id=self.id,candidate=C,checkpoint=CP,owner=self.signer.public,token=self.token,issued_at=int(time.time())))
        self.p=self.record(0,ZERO,"BOOTSTRAP_INTENT",binding=digest(BOOT_DOMAIN+self.body))
    def record(self,index,previous,kind,**kw):
        return dict(index=index,previous=previous,kind=kind,store=S,identity=I,object_id=self.id,checkpoint=CP,seq=0,head=ZERO,binding="3"*64)|kw
    def sig(self,p):return sign(self.signer.key,DOMAIN,compact(p))
    def boot(self,owner=None,record_sig=None):
        return bootstrap_once(self.j,self.p,record_sig or self.sig(self.p),self.transport,self.body,owner or sign(self.signer.key,BOOT_DOMAIN,self.body),hmac.new(cross.MAC,BOOT_DOMAIN+self.body,hashlib.sha256).hexdigest())
    def test_bootstrap_bad_signature_burns_intent_no_retry(self):
        sig=self.sig(self.p)
        with self.assertRaises(Denied):self.boot("0"*128,sig)
        with self.assertRaises(Denied):self.boot()
        pin=digest(compact({"record":self.p,"signature":sig}))
        self.assertEqual(recovery(self.j,pin,{},{}),"FROZEN_PENDING_OR_EMPTY")
    def test_full_bootstrap_owner_journal_and_reconciliation(self):
        pin,genesis=self.boot();self.assertEqual(genesis["checkpoint"],CP)
        with self.assertRaises(Denied):self.boot()
        g=self.record(1,pin,"GENESIS",binding=self.p["binding"]);pin=self.j.append(pin,g,self.sig(g))
        self.rt.stop();self.rt.env["WITNESS_MODE"]="OFFLINE_V2";self.rt.env.pop("BOOTSTRAP_TOKEN_HASH")
        self.transport=self.rt.start()
        self.assertEqual(self.rt.call("/v3/bootstrap","POST")[0],403)
        plan=Plan.new(Anchor(S,CP,C),I,self.id,0,ZERO)
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            # Separate durable credential directory, retained by test cleanup.
            cred_dir=tempfile.TemporaryDirectory();self.addCleanup(cred_dir.cleanup)
            store=CredentialStore(Path(cred_dir.name))
            cp=store.install_verified(self.pin,"offline",fingerprint(self.pin,"offline"))
            c=CrossController(plan,self.adapters,DurableCeremonies(store,cp),self.transport,self.witness,self.audit,LocalTLS(port,self.cert),journal=self.j)
            session=OwnerSession(c,self.j);binding=plan.binding("ARM")
            p=self.record(2,pin,"CLAIM_INTENT",binding=binding)
            packet=self.signer.sign(plan.packet("CLAIM",int(time.time())))
            pin,claim_head=session.arm(pin,p,self.sig(p),self.virtual.assertion(c.challenge("ARM")),packet)
            with self.assertRaises(Denied):session.issue(pin,"invalid")
            p=self.record(3,pin,"CLAIM_RECEIPT",binding=binding,seq=1,head=claim_head)
            pin=session.accept_claim(pin,p,self.sig(p))
            session.issue(pin,self.virtual.assertion(c.challenge("ISSUE")))
            session.execute(pin,self.virtual.assertion(c.challenge("CONSUME")))
            ev=c.evidence()
            p=self.record(4,pin,"ADVANCE_INTENT",binding=binding,seq=1,head=claim_head)
            packet=self.signer.sign(plan.packet("ADVANCE",int(time.time()),ev,claim_head))
            pin,receipt=session.advance(pin,p,self.sig(p),self.virtual.assertion(c.challenge("ADVANCE")),packet)
            self.assertEqual(recovery(self.j,pin,{},{}),"FROZEN_PENDING_OR_EMPTY")
            self.assertFalse(c.status(owner_pin=pin)["cache_valid"])
            p=self.record(5,pin,"ADVANCE_RECEIPT",binding=c.cache_binding(),seq=2,head=receipt["head"],checkpoint=receipt["checkpoint"])
            pin=session.accept_advance(pin,p,self.sig(p))
            self.assertTrue(c.status(owner_pin=pin)["cache_valid"])
            pg=dict(store=S,checkpoint=receipt["checkpoint"],mode="CLOSED",active=None)
            w=dict(store=S,identity=I,object_id=self.id,checkpoint=receipt["checkpoint"],seq=2,head=receipt["head"],mode="READY")
            self.assertEqual(recovery(self.j,pin,pg,w),"MANUAL_REVIEW_ONLY")
            self.assertEqual(recovery(self.j,pin,dict(pg,checkpoint=CP),w),"FROZEN_PG_MISMATCH")
            self.assertEqual(len(requests),1)
            self.assertFalse(receipt["recommendation_eligible"])
