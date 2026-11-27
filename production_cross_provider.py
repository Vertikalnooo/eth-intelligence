"""Cross-provider offline coordinator. No production route, remote hosts or keys.

The owner supplies signed Witness packets separately from WebAuthn assertions.
No automatic bootstrap, recovery, retry, advancement or cache resurrection.
"""
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import ssl
import threading
import time

from production_pg import Denied
from production_recovery import Anchor,valid
from production_witness import Witness
from production_psycopg import LocalPg,ROLE
from production_passkey import Ceremonies
from production_candidate import LocalTLS,DENIAL,CACHE_NS
from rest_one_shot import _market
import rest_operational

ZERO="0"*64
DOMAIN=b"EI-WITNESS-OFFLINE-V2\nPOST\n/v2/transition\n"
OPS=("ARM","ISSUE","CONSUME","SUCCESS")

def compact(x):return json.dumps(x,separators=(",",":"),ensure_ascii=True).encode()
def digest(x):return hashlib.sha256(x).hexdigest()

@dataclass(frozen=True)
class Plan:
    anchor:Anchor
    identity:str
    object_id:str
    seq:int
    head:str
    epoch:str
    grant:str
    checkpoints:tuple

    @classmethod
    def new(cls,anchor,identity,object_id,seq,head):
        return cls(anchor,identity,object_id,seq,head,secrets.token_hex(16),
                   secrets.token_hex(16),tuple(secrets.token_hex(32) for _ in range(4)))

    def check(self):
        a=self.anchor
        if (type(a) is not Anchor or not valid(a.store,32) or not valid(a.checkpoint,64)
            or not valid(a.candidate,40) or not valid(self.identity,64)
            or not valid(self.object_id,64) or type(self.seq) is not int or not 0<=self.seq<2**53-4
            or not valid(self.head,64) or not valid(self.epoch,32) or not valid(self.grant,32)
            or type(self.checkpoints) is not tuple or len(self.checkpoints)!=4
            or any(not valid(cp,64) or cp==ZERO for cp in self.checkpoints)
            or len(set((a.checkpoint,*self.checkpoints)))!=5):
            raise Denied("plan_invalid")

    def packet(self,op,issued,evidence=ZERO,claim_head=None):
        self.check()
        if op not in ("CLAIM","ADVANCE") or type(issued) is not int:
            raise Denied("packet_invalid")
        advance=op=="ADVANCE"
        return dict(op=op,store=self.anchor.store,identity=self.identity,object_id=self.object_id,
                    seq=self.seq+int(advance),checkpoint=self.anchor.checkpoint,candidate=self.anchor.candidate,
                    epoch=self.epoch,grant=self.grant,previous=claim_head if advance else self.head,
                    next=self.checkpoints[-1] if advance else ZERO,evidence=evidence,issued_at=issued)

    def binding(self,op):
        return digest(compact([op,self.anchor.store,self.identity,self.object_id,self.seq,self.head,
                               self.anchor.checkpoint,self.anchor.candidate,self.epoch,self.grant,self.checkpoints]))

@dataclass(frozen=True)
class LocalWitness:
    port:int
    fingerprint:str
    def transition(self,body,owner,mac):
        # Explicit pinned loopback test certificate; no trust of remote hosts,
        # redirects, proxy, env, or automatic retries. Pin provisioned by fixture.
        if not (type(self.port) is int and 1<=self.port<=65535 and valid(self.fingerprint,64)
                and type(body) is bytes and len(body)<=2048 and valid(owner,128) and valid(mac,64)):
            raise Denied("witness_config")
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE
        conn=http.client.HTTPSConnection("127.0.0.1",self.port,context=ctx,timeout=3)
        try:
            conn.connect()
            if digest(conn.sock.getpeercert(binary_form=True))!=self.fingerprint:
                raise Denied("witness_pin")
            conn.request("POST","/v2/transition",body,{"Content-Type":"application/json",
                        "X-EI-Owner":owner,"X-EI-Signature":mac})
            res=conn.getresponse()
            if res.status!=201:raise Denied("witness_denied")
            raw=res.read(2049)
            if len(raw)>2048:raise Denied("witness_size")
            obj=json.loads(raw)
            if type(obj) is not dict:raise Denied("witness_shape")
            return obj
        except Exception as exc:raise Denied("witness_unconfirmed") from exc
        finally:conn.close()

@dataclass(frozen=True)
class Audit:
    port:int
    ca:Path
    password:str
    def terminal(self,p):
        """Independent read-only role; no acceptance of caller-supplied evidence."""
        import psycopg
        if (type(self.port) is not int or not 1<=self.port<=65535 or not isinstance(self.ca,Path)
            or not self.ca.is_absolute() or not self.ca.is_file() or not self.password):
            raise Denied("audit_configuration")
        conn=None
        try:
            conn=psycopg.connect(host="127.0.0.1",hostaddr="127.0.0.1",port=self.port,
                dbname="postgres",user="ei_cross_audit",password=self.password,sslmode="verify-full",
                sslrootcert=str(self.ca),channel_binding="require",connect_timeout=5,
                options="-c statement_timeout=3000 -c lock_timeout=1000 -c default_transaction_read_only=on",
                autocommit=False)
            with conn.cursor() as c:
                c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                c.execute("SELECT store_id,checkpoint,epoch,candidate,active,mode FROM ei2.state WHERE singleton")
                state=c.fetchall()
                c.execute("SELECT id,epoch,candidate,state FROM ei2.grants WHERE id=%s",(p.grant,))
                grants=c.fetchall()
                c.execute("SELECT kind,grant_id,epoch,checkpoint FROM ei2.events WHERE grant_id=%s ORDER BY seq",(p.grant,))
                events=c.fetchall()
            # libpq SQL_ASCII fixtures may return bytes for text.
            def norm(rows):return [tuple(x.decode("ascii") if isinstance(x,bytes) else x for x in row) for row in rows]
            state,grants,events=map(norm,(state,grants,events))
            if (state!=[(p.anchor.store,p.checkpoints[-1],p.epoch,p.anchor.candidate,None,"CLOSED")]
                or grants!=[(p.grant,p.epoch,p.anchor.candidate,"CONSUMED")]
                or events!=[(op,p.grant,p.epoch,cp) for op,cp in zip(OPS,p.checkpoints)]):
                raise Denied("ledger_mismatch")
            return digest(compact([state,grants,events]))
        except Exception as exc:raise Denied("audit_denied") from exc
        finally:
            if conn is not None:conn.close()

class CrossController:
    def __init__(self,plan,adapters,auth,witness,intent,audit,worker):
        if (type(plan) is not Plan or type(auth) is not Ceremonies or type(witness) is not LocalWitness
            or type(intent) is not Witness or type(audit) is not Audit or type(worker) is not LocalTLS
            or set(adapters)!=set(ROLE) or any(type(v) is not LocalPg or v.user!=ROLE[k] for k,v in adapters.items())
            or any(v.port!=audit.port or v.ca!=audit.ca for v in adapters.values())):raise Denied("offline_only")
        plan.check()
        self.plan=plan;self.adapters=dict(adapters);self.auth=auth;self.witness=witness
        self.intent=intent;self.audit=audit;self.worker=worker
        self.mode="DISARMED";self.pid=os.getpid();self.lock=threading.RLock()
        self.claim_head=None;self.advance_head=None;self.receipt=None;self.snapshot=None

    def _check(self,op):
        if os.getpid()!=self.pid or {"ARM":"DISARMED","ISSUE":"ARMED","CONSUME":"ISSUED",
                                    "ADVANCE":"CLOSED"}.get(op)!=self.mode:raise Denied("closed")
    def _freeze(self):
        self.mode="FROZEN";self.receipt=self.snapshot=None
    def challenge(self,op):
        with self.lock:
            self._check(op)
            return self.auth.begin(op,self.plan.binding(op))
    def _owner(self,op,assertion):
        self._check(op);self.auth.verify(op,self.plan.binding(op),assertion)
    def _sql(self,op):
        p=self.plan;i=OPS.index(op);old=p.anchor.checkpoint if i==0 else p.checkpoints[i-1]
        self.adapters[op].apply(op,(p.anchor.store,old,p.checkpoints[i],p.epoch,p.anchor.candidate,p.grant))
    def _send(self,op,packet,evidence=ZERO):
        body,owner,mac=packet
        parsed=json.loads(body)
        expected=self.plan.packet(op,parsed["issued_at"],evidence,self.claim_head)
        if body!=compact(expected):raise Denied("packet_binding")
        now=int(time.time())
        if not 0<=now-expected["issued_at"]<=30:raise Denied("packet_expired")
        d=digest(DOMAIN+body)
        res=self.witness.transition(body,owner,mac)
        wanted=dict(code="committed",seq=expected["seq"]+1,head=d,
                    checkpoint=expected["next"] if op=="ADVANCE" else expected["checkpoint"],
                    mode="READY" if op=="ADVANCE" else "SPENT",scope="OFFLINE_ONLY",**DENIAL)
        if res!=wanted or type(res.get("seq")) is not int or any(res.get(k) is not False for k in DENIAL):
            raise Denied("witness_receipt_mismatch")
        return d
    def arm(self,assertion,packet):
        with self.lock:
            self._owner("ARM",assertion)
            try:
                # Independent third intent: burn before network or SQL; no reset.
                self.intent.claim(self.plan.anchor)
                self.claim_head=self._send("CLAIM",packet)
                self._sql("ARM");self.mode="ARMED"
            except BaseException:self._freeze();raise
    def issue(self,assertion):
        with self.lock:
            self._owner("ISSUE",assertion)
            try:self._sql("ISSUE");self.mode="ISSUED"
            except BaseException:self._freeze();raise
    def execute(self,assertion):
        with self.lock:
            self._owner("CONSUME",assertion)
            try:
                self._sql("CONSUME");self.mode="PENDING"
                start=time.monotonic_ns()
                try:
                    result=self.worker.fetch();end=time.monotonic_ns()
                    market=_market([{"universe":[{"name":"ETH"}]},[{"markPx":result["mark_price"]}]])
                    receipt=rest_operational.capture(market,start,end)
                    if receipt.observation is None:raise Denied("invalid_receipt")
                except BaseException:
                    p=self.plan
                    self.adapters["UNKNOWN"].apply("UNKNOWN",(p.anchor.store,p.checkpoints[2],p.checkpoints[3],p.epoch,p.anchor.candidate,p.grant))
                    raise
                self._sql("SUCCESS")
                self.audit.terminal(self.plan)
                self.receipt=receipt;self.snapshot=market;self.mode="CLOSED"
                return {"scope":"OFFLINE_ONLY",**DENIAL}
            except BaseException:self._freeze();raise
    def evidence(self):
        with self.lock:
            self._check("ADVANCE")
            try:return self.audit.terminal(self.plan)
            except BaseException:self._freeze();raise
    def advance(self,assertion,packet):
        with self.lock:
            self._owner("ADVANCE",assertion)
            try:
                evidence=self.audit.terminal(self.plan)
                self.advance_head=self._send("ADVANCE",packet,evidence)
                self.audit.terminal(self.plan)
                self.mode="ADVANCED"
                return {"seq":self.plan.seq+2,"head":self.advance_head,"checkpoint":self.plan.checkpoints[-1],**DENIAL}
            except BaseException:self._freeze();raise
    def status(self):
        with self.lock:
            now=time.monotonic_ns()
            receipt=self.receipt if os.getpid()==self.pid and self.mode=="ADVANCED" else None
            valid_cache=receipt is not None and receipt.observation.received_monotonic_ns<=now<receipt.observation.received_monotonic_ns+CACHE_NS
            return {"mode":self.mode,"cache_valid":valid_cache,"upstream_request_allowed":False,
                    "rest_operational_freshness":rest_operational.inspect(receipt,now,cache_valid=valid_cache),**DENIAL}
