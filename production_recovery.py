"""Offline single-authorization startup fence. No external worker capability."""
from dataclasses import dataclass
import re
import secrets
import threading
from uuid import uuid4
from production_pg import Denied

@dataclass(frozen=True)
class Anchor:
    store:str
    checkpoint:str
    candidate:str

def valid(v,n):
    return type(v) is str and re.fullmatch("[0-9a-f]{"+str(n)+"}",v) is not None

class Fence:
    def __init__(self,anchor,adapters,auth):
        if type(anchor) is not Anchor or not valid(anchor.store,32) or not valid(anchor.checkpoint,64) or not valid(anchor.candidate,40):
            raise Denied("invalid_external_anchor")
        if set(adapters)!={"ARM","ISSUE","CONSUME","SUCCESS","UNKNOWN"}:raise Denied("missing_role")
        self._anchor=anchor
        self._adapters=dict(adapters)
        self._auth=auth
        self._epoch=uuid4().hex
        self._grant=uuid4().hex
        self._cp=anchor.checkpoint
        self._mode="DISARMED"
        self._lock=threading.Lock()

    @property
    def mode(self):return self._mode

    def _binding(self,op):
        return ":".join((op,self._anchor.store,self._cp,self._epoch,self._anchor.candidate,self._grant))

    def _check(self,op):
        if type(op) is not str or {"ARM":"DISARMED","ISSUE":"ARMED","CONSUME":"ISSUED"}.get(op)!=self._mode:
            raise Denied("fence_closed")

    def challenge(self,op):
        with self._lock:
            self._check(op)
            return self._auth.begin(op,self._binding(op))

    def _apply(self,op):
        new=secrets.token_hex(32)
        try:
            self._adapters[op].apply(op,(self._anchor.store,self._cp,new,self._epoch,self._anchor.candidate,self._grant))
        except BaseException:
            self._mode="FROZEN"
            raise
        self._cp=new

    def confirm(self,op,credential):
        with self._lock:
            if op=="CONSUME":raise Denied("use_execute")
            self._check(op)
            self._auth.verify(op,self._binding(op),credential)
            # Durable commit acknowledgement is necessary before advancing.
            self._apply(op)
            self._mode={"ARM":"ARMED","ISSUE":"ISSUED","CONSUME":"PENDING"}[op]
            return self._mode

    def execute(self,credential,worker):
        from production_candidate import LocalTLS
        if type(worker) is not LocalTLS:raise Denied("offline_worker_required")
        with self._lock:
            self._check("CONSUME")
            self._auth.verify("CONSUME",self._binding("CONSUME"),credential)
            self._apply("CONSUME")
            self._mode="PENDING"
            try:
                result=worker.fetch()
            except BaseException:
                try:self._apply("UNKNOWN")
                finally:self._mode="FROZEN"
                raise
            self._apply("SUCCESS")
            self._mode="CLOSED"
            return {**result,"scope":"OFFLINE_ONLY","recommendation_eligible":False,
                    "exchange_freshness_verified":False,"stage2_complete":False}

    def checkpoint_for_manual_review(self):
        # Never automatically update the external anchor. Operator must
        # independently reconcile the terminal event and checkpoint.
        with self._lock:
            if self._mode!="CLOSED":raise Denied("not_closed")
            return Anchor(self._anchor.store,self._cp,self._anchor.candidate)
