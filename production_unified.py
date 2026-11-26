"""Unified offline candidate; explicit construction only. No live configuration.

Default executable startup is safe_rollback_api, never this controller.
Offline admin methods are deliberately in-process; no public enrollment or
admin HTTP routes. Actual browser/owner session remains a separate gate.
"""
import os
import threading
import time
from production_pg import Denied
from production_psycopg import LocalPg,ROLE
from production_passkey import Ceremonies
from production_recovery import Fence,Anchor
from production_witness import Witness
from production_candidate import LocalTLS,DENIAL,CACHE_NS
from rest_one_shot import _market
import rest_operational
import safe_rollback_api

class Unified:
    def __init__(self,anchor,adapters,auth,witness,worker):
        if (type(anchor) is not Anchor or type(auth) is not Ceremonies
                or type(witness) is not Witness or type(worker) is not LocalTLS
                or set(adapters)!=set(ROLE)
                or any(type(v) is not LocalPg or v.user!=ROLE[k] for k,v in adapters.items())):
            raise Denied("offline_configuration_only")
        self._fence=Fence(anchor,adapters,auth)
        self._anchor=anchor
        self._witness=witness
        self._worker=worker
        self._pid=os.getpid()
        self._lock=threading.RLock()
        self._receipt=None
        self._market=None
        self._until=0
        self._failed=False

    def _domain(self):
        if os.getpid()!=self._pid or self._failed:raise Denied("controller_disabled")

    def challenge(self,op):
        with self._lock:
            self._domain()
            return self._fence.challenge(op)

    def confirm(self,op,credential):
        with self._lock:
            self._domain()
            if op not in ("ARM","ISSUE"):raise Denied("invalid_operation")
            try:
                # ARM verifies owner then spends independent witness BEFORE SQL.
                # Inject callback into Fence via explicit guarded method.
                return self._fence.confirm(op,credential,
                    before_apply=(lambda:self._witness.claim(self._anchor)) if op=="ARM" else None)
            except BaseException:
                self._failed=True
                raise

    def execute(self,credential):
        with self._lock:
            self._domain()
            try:
                # Receipt timestamp captured immediately after worker returns,
                # before final DB commit, and never renewed by cache access.
                timing=[]
                def receipt_callback(result,start,end):
                    market=_market([{"universe":[{"name":"ETH"}]},[{"markPx":result["mark_price"]}]])
                    receipt=rest_operational.capture(market,start,end)
                    if receipt.observation is None:raise Denied("receipt_invalid")
                    timing.append((market,receipt,end))
                result=self._fence.execute(credential,self._worker,on_receipt=receipt_callback)
                if len(timing)!=1:raise Denied("missing_receipt")
                market,receipt,end=timing[0]
                self._market=market
                self._receipt=receipt
                self._until=end+CACHE_NS
                return {"scope":"OFFLINE_ONLY","state":"CLOSED",**DENIAL}
            except BaseException:
                self._failed=True
                self._receipt=self._market=None
                self._until=0
                raise

    def _valid(self,now):
        return (os.getpid()==self._pid and not self._failed and self._market is not None
                and self._receipt is not None and self._fence.mode=="CLOSED"
                and self._receipt.observation.received_monotonic_ns<=now<self._until)

    def market(self):
        with self._lock:
            if not self._valid(time.monotonic_ns()):raise Denied("cache_unavailable")
            return {**self._market,**DENIAL,"scope":"OFFLINE_ONLY"}

    def status(self):
        with self._lock:
            now=time.monotonic_ns()
            valid=self._valid(now)
            receipt=self._receipt if os.getpid()==self._pid and not self._failed else None
            status=safe_rollback_api.status()
            status.update(scope="OFFLINE_ONLY",state="disabled",cache_valid=valid,
                cache_remaining_seconds=max(0,(self._until-now+999999999)//1000000000) if valid else 0,
                throttle_reason="offline_only",recovery_state="FROZEN" if self._failed else self._fence.mode,
                rest_operational_freshness=rest_operational.inspect(receipt,now,cache_valid=valid))
            return status

    def lifecycle(self):
        with self._lock:
            if self._receipt is None:raise Denied("no_receipt")
            receipt=self._receipt
            start=receipt.observation.received_monotonic_ns
        samples=[]
        for delta,state,valid in ((0,"FRESH",True),(3_000_000_000,"DEGRADED",True),
                                  (6_000_000_000,"STALE",True),(31_000_000_000,"STALE",False)):
            delay=(start+delta-time.monotonic_ns())/1e9
            if delay>0:time.sleep(delay)
            with self._lock:
                if self._receipt is not receipt or os.getpid()!=self._pid or self._failed:raise Denied("receipt_changed")
                status=self.status()
                if status["cache_valid"] is not valid or status["rest_operational_freshness"]["state"]!=state:
                    raise Denied("missed_window")
                samples.append(status)
        return samples

def handler_for(controller):
    if type(controller) is not Unified:raise Denied("offline_controller_required")
    class Handler(safe_rollback_api.Handler):
        def do_GET(self):
            if self.path=="/market/status":self.respond(200,controller.status())
            elif self.path=="/market/eth":
                try:self.respond(200,controller.market())
                except Denied:self.respond(503,{"error":"market_data_unavailable",**DENIAL})
            else:super().do_GET()
    return Handler

if __name__=="__main__":
    # No environment flag can activate a market worker. Disabled API only.
    from http.server import ThreadingHTTPServer
    ThreadingHTTPServer(("0.0.0.0",int(os.environ.get("PORT","10000"))),
                        safe_rollback_api.Handler).serve_forever()
