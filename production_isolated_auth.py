"""Separate Linux UID credential verifier. No SQL/witness secrets or adapters."""
import os
from pathlib import Path
import socket
import sys
from production_credential_store import CredentialStore,DurableCeremonies
from production_ipc_protocol import request,peer,recv,send,decode,private_file,private_directory,burn_start,lock_process,socket_directory
from production_pg import Denied
from production_recovery import valid

OPS=("ARM","ISSUE","CONSUME","ADVANCE")
def check_bindings(bindings):
    if type(bindings) is not dict or set(bindings)!=set(OPS) or any(not valid(v,64) for v in bindings.values()) or len(set(bindings.values()))!=4:raise Denied("trust_bindings")

class RemoteCeremonies:
    """Only the broker can call the verifier. No automatic retries or head adoption."""
    profile="offline"
    def __init__(self,path,uid,pin,bindings):
        if not isinstance(path,Path) or not path.is_absolute() or type(uid) is not int or uid<=0 or not valid(pin,64):raise Denied("trust_client_config")
        check_bindings(bindings)
        self.path=path;self.uid=uid;self.pin=pin;self.bindings=dict(bindings);self.failed=False
    def _call(self,d):
        if self.failed:raise Denied("trust_client_frozen")
        try:return request(self.path,self.uid,d,timeout=4)
        except BaseException:self.failed=True;raise
    def check(self):
        if self._call({"cmd":"check","pin":self.pin})!={"profile":"offline"}:raise Denied("trust_profile")
    def begin(self,op,binding):
        if self.bindings.get(op)!=binding:raise Denied("trust_binding")
        return self._call({"cmd":"begin","op":op,"binding":binding,"pin":self.pin})["options"]
    def verify(self,op,binding,assertion):
        if self.bindings.get(op)!=binding:raise Denied("trust_binding")
        out=self._call({"cmd":"verify","op":op,"binding":binding,"pin":self.pin,"assertion":assertion})
        if type(out) is not dict or set(out)!={"pin"} or not valid(out["pin"],64) or out["pin"]==self.pin:
            self.failed=True;raise Denied("trust_receipt")
        self.pin=out["pin"];return self.pin

def serve(root,path,uid):
    lock_process(uid)
    root=Path(root);path=Path(path)
    private_directory(root);private_directory(root/"credentials");socket_directory(path,uid)
    cfg=decode(private_file(root/"config.json"))
    if set(cfg)!={"trust_uid","broker_uid","pin","bindings"} or cfg["trust_uid"]!=uid or type(cfg["broker_uid"]) is not int or cfg["broker_uid"] in (0,uid) or cfg["broker_uid"]<0:raise Denied("trust_config")
    check_bindings(cfg["bindings"])
    auth=DurableCeremonies(CredentialStore(root/"credentials"),cfg["pin"])
    if auth.profile!="offline":raise Denied("trust_profile")
    burn_start(root/"started")
    frozen=False
    with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as listener:
        listener.bind(str(path));os.chmod(path,0o666);listener.listen(4)
        while True:
            client,_=listener.accept()
            with client:
                client.settimeout(2);verification=False
                try:
                    if peer(client)!=cfg["broker_uid"]:raise Denied("trust_peer")
                    d=recv(client);cmd=d.get("cmd")
                    verification=cmd=="verify"
                    fields={"check":{"cmd","pin"},"begin":{"cmd","op","binding","pin"},"verify":{"cmd","op","binding","pin","assertion"}}
                    if frozen or type(cmd) is not str or cmd not in fields or set(d)!=fields[cmd] or d["pin"]!=auth.pin:raise Denied("trust_command")
                    if cmd=="check":
                        auth.store.load(auth.pin);result={"profile":"offline"}
                    else:
                        if type(d["op"]) is not str or cfg["bindings"].get(d["op"])!=d["binding"]:raise Denied("trust_binding")
                        if cmd=="begin":result={"options":auth.begin(d["op"],d["binding"])}
                        else:result={"pin":auth.verify(d["op"],d["binding"],d["assertion"])}
                    send(client,{"ok":True,"value":result})
                except Exception:
                    if verification:frozen=True
                    try:send(client,{"ok":False,"value":None})
                    except Exception:pass

if __name__=="__main__":
    if len(sys.argv)!=4:sys.exit(2)
    try:serve(sys.argv[1],sys.argv[2],int(sys.argv[3]))
    except Exception as exc:
        # Fixed diagnostics: never print config, credentials, exception text or traceback.
        print("startup_denied:"+type(exc).__name__,file=sys.stderr)
        sys.exit(2)
