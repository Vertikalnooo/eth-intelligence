"""Owner-side orchestration; separate trust process is a future deployment gate.
Only in-process research callers, no HTTP routes. Journal records are externally
signed; caller explicitly passes its previously pinned journal head.
"""
from production_owner_journal import Journal,ZERO,compact,digest
from production_cross_provider import CrossController,LocalWitness
from production_pg import Denied
import http.client
import json
import ssl

BOOT_DOMAIN=b"EI-WITNESS-BOOTSTRAP-OFFLINE-V1\n"

def bootstrap_once(journal,record,sig,transport,body,owner,mac):
    if type(journal) is not Journal or type(transport) is not LocalWitness:
        raise Denied("bootstrap_configuration")
    p=json.loads(body)
    if (type(body) is not bytes or len(body)>2048 or record["kind"]!="BOOTSTRAP_INTENT"
        or record["binding"]!=digest(BOOT_DOMAIN+body)
        or any(record[k]!=p[k] for k in ("store","identity","object_id","checkpoint"))
        or p["owner"]!=journal.public.hex()):
        raise Denied("bootstrap_binding")
    # Only an empty journal with an externally pinned ZERO can issue genesis.
    # On any later failure, intent is left durable and cannot be reused.
    pin=journal.append(ZERO,record,sig)
    conn=None
    try:
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE
        if type(transport.port) is not int or not 1<=transport.port<=65535:raise Denied("bootstrap_port")
        conn=http.client.HTTPSConnection("127.0.0.1",transport.port,context=ctx,timeout=3)
        conn.connect()
        if digest(conn.sock.getpeercert(binary_form=True))!=transport.fingerprint:raise Denied("bootstrap_pin")
        conn.request("POST","/v3/bootstrap",body,{"Content-Type":"application/json","X-EI-Owner":owner,"X-EI-Signature":mac})
        res=conn.getresponse();raw=res.read(2049)
        if res.status!=201 or len(raw)>2048:raise Denied("bootstrap_unconfirmed")
        want={"code":"genesis",**{k:p[k] for k in ("store","identity","object_id","checkpoint")},"seq":0,"head":ZERO,"mode":"READY"}
        if json.loads(raw)!=want:raise Denied("bootstrap_receipt")
        return pin,want
    except Exception as exc:raise Denied("bootstrap_unconfirmed") from exc
    finally:
        if conn:conn.close()

class OwnerSession:
    def __init__(self,controller,journal):
        if type(controller) is not CrossController or type(journal) is not Journal or controller.journal is not journal:raise Denied("owner_session")
        self.controller=controller;self.journal=journal
    def _record(self,expected,kind,record,sig,*,checkpoint,seq,head):
        p=self.controller.plan
        if record["kind"]!=kind or any(record[k]!=v for k,v in {
            "store":p.anchor.store,"identity":p.identity,"object_id":p.object_id,"checkpoint":checkpoint,
            "seq":seq,"head":head,"binding":self.controller.cache_binding() if kind=="ADVANCE_RECEIPT" else p.binding("ARM")}.items()):raise Denied("owner_record_mismatch")
        return self.journal.append(expected,record,sig)
    def arm(self,expected,record,sig,assertion,packet):
        p=self.controller.plan
        pin=self._record(expected,"CLAIM_INTENT",record,sig,checkpoint=p.anchor.checkpoint,seq=p.seq,head=p.head)
        self.controller.arm(assertion,packet,owner_pin=pin)
        return pin,self.controller.claim_head
    def accept_claim(self,expected,record,sig):
        c=self.controller;p=c.plan
        if c.mode!="ARMED":raise Denied("claim_unconfirmed")
        return self._record(expected,"CLAIM_RECEIPT",record,sig,checkpoint=p.anchor.checkpoint,seq=p.seq+1,head=c.claim_head)
    def _claim_pin(self,expected):
        c=self.controller;p=c.plan;last=self.journal.read(expected)
        if not last or last["kind"]!="CLAIM_RECEIPT" or last["binding"]!=p.binding("ARM") or last["head"]!=c.claim_head:
            raise Denied("owner_claim_pin")
    def issue(self,expected,assertion):
        self._claim_pin(expected);self.controller.issue(assertion,owner_pin=expected)
    def execute(self,expected,assertion):
        self._claim_pin(expected);return self.controller.execute(assertion,owner_pin=expected)
    def advance(self,expected,record,sig,assertion,packet):
        self._claim_pin(expected);c=self.controller;p=c.plan
        pin=self._record(expected,"ADVANCE_INTENT",record,sig,checkpoint=p.anchor.checkpoint,seq=p.seq+1,head=c.claim_head)
        return pin,c.advance(assertion,packet,owner_pin=pin)
    def accept_advance(self,expected,record,sig):
        c=self.controller;p=c.plan
        if c.mode!="ADVANCED":raise Denied("advance_unconfirmed")
        return self._record(expected,"ADVANCE_RECEIPT",record,sig,checkpoint=p.checkpoints[-1],seq=p.seq+2,head=c.advance_head)
