"""Linux research-only local IPC boundary; no production entrypoint or cloud URLs."""
import ctypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import time

from production_candidate import LocalTLS,DENIAL
from production_cross_provider import CrossController,Plan,Audit,LocalWitness,DOMAIN
from production_owner import unique
from production_owner_journal import Journal
from production_owner_trust import OwnerSession
from production_pg import Denied
from production_psycopg import LocalPg,ROLE
from production_recovery import Anchor,valid
from production_witness import Witness

from production_ipc_protocol import request,peer,recv,send,decode,private_file,private_directory,burn_start,lock_process,socket_directory
from production_isolated_auth import RemoteCeremonies

FIELDS={
    "status":set(),
    "challenge":{"op","credential_pin","owner_pin"},
    "arm":{"credential_pin","owner_pin","record","signature","assertion","body","owner_signature"},
    "accept_claim":{"owner_pin","record","signature"},
    "issue":{"credential_pin","owner_pin","assertion"},
    "execute":{"credential_pin","owner_pin","assertion"},
    "evidence":{"owner_pin"},
    "advance":{"credential_pin","owner_pin","record","signature","assertion","body","owner_signature"},
    "cache_binding":set(),
    "accept_advance":{"owner_pin","record","signature"},
    "owner_status":{"owner_pin"},
}


class Broker:
    def __init__(self,root,config):
        if set(config)!={"broker_uid","owner_uid","app_uid","plan","pg_port","passwords","audit_password","witness_port","witness_fingerprint","witness_mac","worker_port","owner_public","credential_pin","owner_pin","trust_uid","trust_socket"}:raise Denied("config_shape")
        uids=[config[k] for k in ("broker_uid","owner_uid","app_uid","trust_uid")]
        if any(type(u) is not int or u<=0 for u in uids) or len(set(uids))!=4:raise Denied("distinct_uids")
        lock_process(uids[0])
        self.owner_uid,self.app_uid=uids[1:3]
        for path in (root,root/"journal",root/"intent"):private_directory(path)
        p=dict(config["plan"]);p["anchor"]=Anchor(**p["anchor"]);p["checkpoints"]=tuple(p["checkpoints"])
        plan=Plan(**p);plan.check()
        ca=root/"ca.pem"
        # Validate credential files and operator-retained pins before admitting startup.
        private_file(ca)
        auth=RemoteCeremonies(Path(config["trust_socket"]),config["trust_uid"],config["credential_pin"],{op:plan.binding(op) for op in ("ARM","ISSUE","CONSUME","ADVANCE")})
        auth.check()
        journal=Journal.pin(root/"journal",bytes.fromhex(config["owner_public"]))
        last=journal.read(config["owner_pin"])
        if not last or last["kind"]!="GENESIS" or any(last[k]!=v for k,v in dict(store=plan.anchor.store,identity=plan.identity,object_id=plan.object_id,checkpoint=plan.anchor.checkpoint,seq=plan.seq,head=plan.head).items()):raise Denied("initial_owner_anchor")
        if plan.seq!=0:raise Denied("no_recovery_start")
        pw=config["passwords"]
        if set(pw)!=set(ROLE.values()) or any(type(v) is not str or len(v)<32 for v in pw.values()) or len(set(pw.values()))!=len(pw):raise Denied("role_secrets")
        if type(config["audit_password"]) is not str or len(config["audit_password"])<32 or config["audit_password"] in pw.values():raise Denied("audit_secret")
        if not valid(config["witness_mac"],64) or config["witness_mac"]=="0"*64:raise Denied("witness_secret")
        self.mac=bytes.fromhex(config["witness_mac"])
        adapters={op:LocalPg(config["pg_port"],ca,role,pw[role]) for op,role in ROLE.items()}
        self.c=CrossController(plan,adapters,auth,LocalWitness(config["witness_port"],config["witness_fingerprint"]),Witness.pin(root/"intent"),Audit(config["pg_port"],ca,config["audit_password"]),LocalTLS(config["worker_port"],ca),journal=journal)
        self.session=OwnerSession(self.c,journal)
        self.owner_pin=config["owner_pin"]
        burn_start(root/"started")
    def dispatch(self,uid,d):
        if type(d) is not dict or type(d.get("cmd")) is not str:raise Denied("command")
        cmd=d["cmd"]
        if cmd not in FIELDS or set(d)!=(FIELDS[cmd]|{"cmd"}):raise Denied("command_fields")
        if uid==self.app_uid:
            if cmd!="status":raise Denied("app_read_only")
            return {"scope":"OFFLINE_ONLY","cache_valid":False,"upstream_request_allowed":False,**DENIAL}
        if uid!=self.owner_uid:raise Denied("owner_uid")
        c=self.c;s=self.session
        if cmd=="status":return {"scope":"OFFLINE_ONLY","cache_valid":False,"upstream_request_allowed":False,**DENIAL}
        if "owner_pin" in d and d["owner_pin"]!=self.owner_pin:raise Denied("owner_pin")
        if "credential_pin" in d and d["credential_pin"]!=c.auth.pin:raise Denied("credential_pin")
        if cmd=="challenge":
            if type(d["op"]) is not str:raise Denied("operation")
            return {"options":c.challenge(d["op"])}
        if cmd=="owner_status":return c.status(owner_pin=self.owner_pin)
        if cmd=="cache_binding":return {"binding":c.cache_binding()}
        if cmd=="evidence":return {"evidence":c.evidence()}
        if cmd in ("arm","advance"):
            if type(d["body"]) is not str or not d["body"].isascii() or len(d["body"])>2048 or not valid(d["owner_signature"],128):raise Denied("witness_packet")
            body=d["body"].encode("ascii")
            packet=(body,d["owner_signature"],hmac.new(self.mac,DOMAIN+body,hashlib.sha256).hexdigest())
            # Owner signature remains mandatory at witness; broker never holds owner private key.
            method=s.arm if cmd=="arm" else s.advance
            self.owner_pin,result=method(self.owner_pin,d["record"],d["signature"],d["assertion"],packet)
        elif cmd in ("accept_claim","accept_advance"):
            method=s.accept_claim if cmd=="accept_claim" else s.accept_advance
            self.owner_pin=method(self.owner_pin,d["record"],d["signature"])
            result=None
        elif cmd=="issue":result=s.issue(self.owner_pin,d["assertion"])
        elif cmd=="execute":result=s.execute(self.owner_pin,d["assertion"])
        else:raise Denied("command")
        return {"result":result,"owner_pin":self.owner_pin,"credential_pin":c.auth.pin,"mode":c.mode}

def serve(root,socket_path,uid):
    lock_process(uid)
    root=Path(root);socket_path=Path(socket_path)
    private_directory(root)
    socket_directory(socket_path,uid)
    config=decode(private_file(root/"config.json"))
    broker=Broker(root,config)
    # No inherited listener, no TCP, no dynamic dispatch, no automatic unlink/restart.
    with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as listener:
        listener.bind(str(socket_path));os.chmod(socket_path,0o666);listener.listen(8)
        while True:
            client,_=listener.accept()
            with client:
                client.settimeout(2)
                authorized=False
                try:
                    u=peer(client)
                    if u not in (broker.owner_uid,broker.app_uid):raise Denied("peer")
                    data=recv(client)
                    authorized=u==broker.owner_uid and data.get("cmd") in ("arm","accept_claim","issue","execute","advance","accept_advance")
                    result=broker.dispatch(u,data)
                    send(client,{"ok":True,"value":result})
                except Exception:
                    # Unknown mutation/ack outcome is terminal. Never retry or reveal exception details.
                    if authorized:broker.c._freeze()
                    try:send(client,{"ok":False,"value":None})
                    except Exception:pass

if __name__=="__main__":
    if len(sys.argv)!=4:sys.exit(2)
    try:serve(sys.argv[1],sys.argv[2],int(sys.argv[3]))
    except Exception:sys.exit(2)
