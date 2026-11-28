"""Isolated production-shaped candidate. No executable startup or live target."""
from dataclasses import dataclass,replace
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

from production_pg import PgLedger,Denied,hex_id
from production_owner import Owner,unique
from rest_one_shot import _market
import rest_operational

DENIAL={"exchange_freshness_verified":False,"recommendation_eligible":False,"stage2_complete":False}
CACHE_NS=30_000_000_000
BUDGET_NS=8_000_000_000

@dataclass(frozen=True)
class LocalTLS:
    port:int
    ca:Path

    def fetch(self):
        if (sys.platform!="linux" or type(self.port) is not int or not 0<self.port<=65535
                or not isinstance(self.ca,Path) or not self.ca.is_absolute() or not self.ca.is_file()):
            raise Denied("offline_tls_configuration")
        deadline=time.monotonic_ns()+BUDGET_NS
        proc=None
        try:
            proc=subprocess.Popen([sys.executable,str(Path(__file__).with_name("production_tls_worker.py")),
                                   str(os.getpid()),str(deadline),str(self.port),str(self.ca)],
                                  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL,close_fds=True,
                                  env={"PATH":"/usr/bin:/bin","LANG":"C",
                                       "PYTHONPATH":str(Path(__file__).resolve().parent/"scripts/offline_guard")})
            remaining=(deadline-time.monotonic_ns())/1e9
            if remaining<=0: raise Denied("deadline")
            out,_=proc.communicate(timeout=remaining)
            if proc.returncode!=0 or time.monotonic_ns()>=deadline or len(out)>512:
                raise Denied("worker_failed")
            payload=json.loads(out,object_pairs_hook=unique)
            if type(payload) is not dict or set(payload)!={"mark_price"}: raise Denied("bad_worker_result")
            return _market([{"universe":[{"name":"ETH"}]},[{"markPx":payload["mark_price"]}]])
        except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
            raise Denied("worker_failed") from exc
        finally:
            if proc is not None:
                if proc.poll() is None:
                    proc.kill()
                    try: proc.communicate(timeout=1)
                    except subprocess.TimeoutExpired as exc: raise Denied("worker_reap_unknown") from exc
                if proc.stdout: proc.stdout.close()

class Candidate:
    def __init__(self,ledger,owner,worker):
        if type(ledger) is not PgLedger or type(owner) is not Owner or type(worker) is not LocalTLS:
            raise Denied("offline_only")
        self.ledger=replace(ledger,epoch=uuid4().hex)
        self.owner=owner
        self.worker=worker
        self.pid=os.getpid()
        self._lock=threading.Lock()
        self._execution=threading.Lock()
        self._frozen=False
        self._grants=set()
        self._receipt=None
        self._market=None
        self._until=0

    def _domain(self):
        if os.getpid()!=self.pid: raise Denied("process_changed")

    def challenge(self,purpose,grant=None):
        self._domain()
        if type(purpose) is not str or purpose not in ("ISSUE","CONSUME","REVOKE"):
            raise Denied("bad_purpose")
        if purpose=="ISSUE":
            if grant is not None: raise Denied("unexpected_grant")
            grant=uuid4().hex
        else:
            hex_id(grant)
            with self._lock:
                if grant not in self._grants: raise Denied("unknown_local_grant")
        return self.owner.challenge(purpose,grant)

    def confirm(self,purpose,grant,assertion):
        self._domain()
        hex_id(grant)
        if type(purpose) is not str or purpose not in ("ISSUE","REVOKE","CONSUME"): raise Denied("bad_purpose")
        self.owner.assert_owner(purpose,grant,assertion)
        # Serialize all state transitions and upstream access. No retry of
        # unknown outcomes; local freeze + durable active reservation.
        with self._execution:
            with self._lock:
                if self._frozen: raise Denied("frozen")
                if purpose!="ISSUE" and grant not in self._grants:
                    raise Denied("unknown_local_grant")
                if purpose=="CONSUME": self._grants.remove(grant)
            try:
                self.ledger.apply(purpose,grant)
            except Denied:
                with self._lock:
                    self._frozen=True
                    self._market=None
                    self._until=0
                    self._receipt=None
                raise
            if purpose=="ISSUE":
                with self._lock: self._grants.add(grant)
                return {"grant":grant,"state":"ISSUED",**DENIAL}
            if purpose=="REVOKE":
                with self._lock: self._grants.discard(grant)
                return {"state":"REVOKED",**DENIAL}
            start=time.monotonic_ns()
            try:
                market=self.worker.fetch()
                received=time.monotonic_ns()
                receipt=rest_operational.capture(market,start,received)
                if receipt.observation is None: raise Denied("invalid_receipt")
                self.ledger.apply("SUCCESS",grant)
            except BaseException:
                with self._lock:
                    self._frozen=True
                    self._market=None
                    self._receipt=None
                    self._until=0
                # If outcome cannot be recorded, active remains durable.
                try: self.ledger.apply("UNKNOWN",grant)
                except Denied: pass
                raise
            with self._lock:
                self._market=dict(market)
                self._receipt=receipt
                self._until=received+CACHE_NS
            return {"state":"CONSUMED","scope":"OFFLINE_ONLY",**DENIAL}

    def _valid(self,now):
        return os.getpid()==self.pid and not self._frozen and self._market is not None and now<self._until

    def market(self):
        with self._lock:
            now=time.monotonic_ns()
            if not self._valid(now): raise Denied("cache_unavailable")
            return {**self._market,**DENIAL,"scope":"OFFLINE_ONLY"}

    def status(self):
        with self._lock:
            now=time.monotonic_ns()
            valid=self._valid(now)
            receipt=self._receipt if os.getpid()==self.pid else None
            return {"scope":"OFFLINE_ONLY","cache_valid":valid,
                    "upstream_request_allowed":False,"frozen":self._frozen,
                    **DENIAL,"rest_operational_freshness":rest_operational.inspect(receipt,now,cache_valid=valid)}

    def lifecycle(self):
        """Actual candidate cache, no database or upstream reads."""
        with self._lock:
            if self._receipt is None: raise Denied("no_receipt")
            receipt=self._receipt
            start=receipt.observation.received_monotonic_ns
        samples=[]
        for delta,state,valid in ((0,"FRESH",True),(3_000_000_000,"DEGRADED",True),
                                  (6_000_000_000,"STALE",True),(31_000_000_000,"STALE",False)):
            wait=(start+delta-time.monotonic_ns())/1e9
            if wait>0: time.sleep(wait)
            with self._lock:
                if self._receipt is not receipt: raise Denied("receipt_changed")
                now=time.monotonic_ns()
                actual=self._valid(now)
                diag=rest_operational.inspect(receipt,now,cache_valid=actual)
                if actual is not valid or diag["state"]!=state: raise Denied("missed_lifecycle_window")
                samples.append({"cache_valid":actual,**diag})
        return samples

def handler_for(candidate):
    """No bind/listen at import; offline test explicitly creates local server."""
    if type(candidate) is not Candidate: raise Denied("bad_candidate")
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)
        def log_message(self,*args): pass
        def send(self,status,data):
            body=json.dumps(data,separators=(",",":")).encode()
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            if self.path=="/health": self.send(200,{"status":"ok","scope":"OFFLINE_ONLY"})
            elif self.path=="/market/status": self.send(200,candidate.status())
            elif self.path=="/market/eth":
                try: self.send(200,candidate.market())
                except Denied: self.send(503,{"error":"cache_unavailable",**DENIAL})
            else: self.send(404,{"error":"not_found"})
        def do_POST(self):
            routes={"/admin/approval/confirm":"ISSUE","/admin/approval/revoke":"REVOKE",
                    "/admin/rest/execute":"CONSUME"}
            if self.path not in routes and self.path!="/admin/approval/challenge":
                self.send(404,{"error":"not_found"});return
            try:
                if (self.headers.get_all("Content-Type")!=["application/json"]
                        or self.headers.get_all("Transfer-Encoding")
                        or len(self.headers.get_all("Content-Length",[]))!=1):
                    raise Denied("bad_headers")
                rawlen=self.headers["Content-Length"]
                if not rawlen.isascii() or not rawlen.isdigit() or len(rawlen)>5:
                    raise Denied("bad_length")
                size=int(rawlen)
                if not 0<size<=8192: raise Denied("bad_length")
                data=json.loads(self.rfile.read(size),object_pairs_hook=unique)
                if type(data) is not dict: raise Denied("bad_body")
                if self.path=="/admin/approval/challenge":
                    if set(data)-{"purpose","grant"} or "purpose" not in data: raise Denied("bad_body")
                    reply=candidate.challenge(data["purpose"],data.get("grant"))
                else:
                    if set(data)!={"grant","assertion"}: raise Denied("bad_body")
                    reply=candidate.confirm(routes[self.path],data["grant"],data["assertion"])
                self.send(200,reply)
            except (Denied,ValueError,TypeError,KeyError,OSError,RecursionError):
                self.send(403,{"error":"operation_denied",**DENIAL})
            except Exception:
                self.send(503,{"error":"operation_failed",**DENIAL})
    return Handler
