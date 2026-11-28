"""Read-only, independently keyed recovery evidence from local real providers.
Never accepts supplied snapshots, never enables operations or recovery.
"""
from dataclasses import dataclass
import hashlib
import hmac
import http.client
import json
import secrets
import ssl
from production_cross_provider import Plan,Audit,DOMAIN,ZERO,compact,digest
from production_owner_journal import Journal
from production_deadline import bounded
from production_pg import Denied
from production_recovery import valid

@dataclass(frozen=True)
class WitnessAudit:
    port:int
    fingerprint:str
    key:bytes
    def snapshot(self):
        if type(self.port) is not int or not 1<=self.port<=65535 or not valid(self.fingerprint,64) or type(self.key) is not bytes or len(self.key)!=32 or not any(self.key):raise Denied("audit_configuration")
        nonce=secrets.token_hex(32)
        mac=hmac.new(self.key,b"EI-AUDIT-REQUEST-V1\n"+nonce.encode(),hashlib.sha256).hexdigest()
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE
        conn=http.client.HTTPSConnection("127.0.0.1",self.port,context=ctx,timeout=3)
        try:
            conn.connect()
            if digest(conn.sock.getpeercert(binary_form=True))!=self.fingerprint:raise Denied("audit_pin")
            conn.request("GET","/v4/audit",headers={"X-EI-Nonce":nonce,"X-EI-Audit":mac})
            res=conn.getresponse();raw=res.read(8193)
            want=hmac.new(self.key,b"EI-AUDIT-RESPONSE-V1\n"+raw,hashlib.sha256).hexdigest()
            if res.status!=200 or len(raw)>8192 or not hmac.compare_digest(res.getheader("X-EI-Audit") or "",want):raise Denied("audit_response")
            obj=json.loads(raw)
            if type(obj) is not dict or obj.get("nonce")!=nonce:raise Denied("audit_nonce")
            return obj
        finally:conn.close()

def verified_recovery(plan,journal,pin,pg,witness):
    """Only a completed cycle can yield MANUAL_REVIEW_ONLY, never permission."""
    try:
        if type(plan) is not Plan or type(journal) is not Journal or type(pg) is not Audit or type(witness) is not WitnessAudit:raise Denied("audit_sources")
        plan.check();last=journal.read(pin)
        if not last or last["kind"]!="ADVANCE_RECEIPT":return "FROZEN_PENDING_OR_EMPTY"
        p=plan
        if any(last[k]!=v for k,v in dict(store=p.anchor.store,identity=p.identity,object_id=p.object_id,seq=p.seq+2,checkpoint=p.checkpoints[-1]).items()):raise Denied("owner_anchor")
        evidence=bounded(pg.terminal,p)
        snapshot=bounded(witness.snapshot)
        anchor={k:last[k] for k in ("store","identity","object_id","seq","head","checkpoint")}|{"mode":"READY"}
        if snapshot["anchor"]!=anchor or snapshot["used"]!=[{"checkpoint":p.anchor.checkpoint}]:raise Denied("witness_anchor")
        events=snapshot["events"]
        if type(events) is not list or len(events)!=2:raise Denied("witness_events")
        head=None
        for i,op in enumerate(("CLAIM","ADVANCE")):
            ev=events[i];body=ev["body"].encode();parsed=json.loads(body)
            expected=p.packet(op,parsed["issued_at"],ZERO if i==0 else evidence,head)
            d=digest(DOMAIN+body)
            if body!=compact(expected) or ev!={"seq":p.seq+i+1,"op":op,"digest":d,"body":body.decode()}:raise Denied("witness_history")
            head=d
        if head!=last["head"] or journal.read(pin)!=last or bounded(pg.terminal,p)!=evidence:raise Denied("recovery_race")
        return "MANUAL_REVIEW_ONLY"
    except Exception:return "FROZEN_UNCONFIRMED"
