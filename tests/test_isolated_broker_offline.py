"""Actual Linux UID boundary + real local PG, workerd and WebAuthn.
The runner is trusted test supervisor/owner; the app UID has no sudo.
All setup is disposable inside the already isolated CI network namespace.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

import test_cross_provider_offline as cross
import test_production_integration_offline as fixture
from production_ipc_broker import request,peer,recv,send
from production_pg import Denied
from production_psycopg import ROLE

BROKER=60181
APP=60182
OTHER=60183
TRUST=60184

def sudo(*args,**kw):
    return subprocess.run(["sudo","-n",*map(str,args)],check=True,capture_output=True,timeout=20,**kw)

class IsolatedBrokerTests(unittest.TestCase):
    admin=classmethod(fixture.IntegrationTests.admin.__func__)
    @classmethod
    def setUpClass(cls):
        if sys.platform!="linux" or os.geteuid()==0 or not shutil.which("sudo"):
            if os.environ.get("GITHUB_ACTIONS")=="true":raise RuntimeError("required non-root Linux/sudo fixture")
            raise unittest.SkipTest("Linux CI UID fixture required")
        try:sudo("/usr/bin/true")
        except Exception:
            if os.environ.get("GITHUB_ACTIONS")=="true":raise
            raise unittest.SkipTest("isolated sudo fixture unavailable")
        links=subprocess.check_output(["ip","-o","link","show"],text=True).splitlines()
        if len(links)!=1 or ": lo:" not in links[0]:
            if os.environ.get("GITHUB_ACTIONS")=="true":raise RuntimeError("offline network namespace required")
            raise unittest.SkipTest("offline network namespace required")
        if os.getuid() in (BROKER,APP,OTHER,TRUST):raise RuntimeError("UID collision")
        import pwd
        for uid in (BROKER,APP,OTHER,TRUST):
            try:pwd.getpwuid(uid)
            except KeyError:continue
            raise RuntimeError("test UID already allocated")
        cross.CrossTests.setUpClass.__func__(cls)
    def setUp(self):
        cross.CrossTests.setUp(self)
        self.passwords={role:secrets.token_hex(32) for role in set(ROLE.values())}
        self.audit_password=secrets.token_hex(32)
        for role,password in self.passwords.items():self.admin("ALTER ROLE "+role+" PASSWORD '"+password+"';")
        self.admin("ALTER ROLE ei_cross_audit PASSWORD '"+self.audit_password+"';")
        self.runtime.stop()
        self.runtime.env["WITNESS_HMAC_KEY"]=secrets.token_hex(32)
        self.transport=self.runtime.start()
        self.base=Path(tempfile.mkdtemp(prefix="ei_broker_",dir="/tmp"))
        self.addCleanup(lambda:sudo("/bin/rm","-rf","--",self.base))
        self.base.chmod(0o755)
        self.code=self.base/"code";self.code.mkdir(mode=0o755)
        for path in fixture.ROOT.glob("*.py"):
            shutil.copy2(path,self.code/path.name)
            (self.code/path.name).chmod(0o644)
        # Copy required package, not just top-level modules. No credentials in code tree.
        shutil.copytree(fixture.ROOT/"freshness",self.code/"freshness",ignore=shutil.ignore_patterns("__pycache__"))
        self.private=self.base/"private";self.private.mkdir(mode=0o700)
        self.sockdir=self.base/"ipc";self.sockdir.mkdir(mode=0o755)
        self.sockpath=self.sockdir/"broker.sock"
        self.trust=self.base/"trust";self.trust.mkdir(mode=0o700)
        self.trust_ipc=self.base/"trust-ipc";self.trust_ipc.mkdir(mode=0o755)
        self.trust_sock=self.trust_ipc/"trust.sock"
        shutil.copytree(self.cred_dir.name,self.trust/"credentials")
        shutil.copytree(self.owner_dir.name,self.private/"journal")
        guard=self.code/"scripts/offline_guard";guard.mkdir(parents=True,mode=0o755)
        shutil.copyfile(fixture.ROOT/"scripts/offline_guard/sitecustomize.py",guard/"sitecustomize.py")
        (self.private/"intent").mkdir(mode=0o700)
        shutil.copyfile(self.cert,self.private/"ca.pem");(self.private/"ca.pem").chmod(0o600)
        shutil.copyfile(self.cert,self.base/"public-ca.pem");(self.base/"public-ca.pem").chmod(0o644)
        # Diagnostic output is captured only for synthetic fixture processes.
        self.proc=None;self.trust_proc=None;self.addCleanup(self.stop_trust);self.addCleanup(self.stop)
    def command(self,uid,*args):
        return ["sudo","-n","/usr/bin/setpriv","--reuid="+str(uid),"--regid="+str(uid),"--clear-groups","--no-new-privs",
                "/usr/bin/env","-i","PATH=/usr/bin:/bin","LANG=C","PYTHONPATH="+str(self.code/"scripts/offline_guard")+":"+str(self.code),sys.executable,*map(str,args)]
    def start(self,port):
        cfg=dict(broker_uid=BROKER,owner_uid=os.getuid(),app_uid=APP,plan=asdict(self.plan),
                 pg_port=self.port,passwords=self.passwords,audit_password=self.audit_password,
                 witness_port=self.transport.port,witness_fingerprint=self.transport.fingerprint,
                 witness_mac=self.runtime.env["WITNESS_HMAC_KEY"],worker_port=port,
                 owner_public=self.signer.public,credential_pin=self.cred_pin,owner_pin=self.owner_pin,trust_uid=TRUST,trust_socket=str(self.trust_sock))
        p=self.private/"config.json";p.write_text(json.dumps(cfg));p.chmod(0o600)
        sudo("/bin/chown","-R",f"{BROKER}:{BROKER}",self.private,self.sockdir)
        tc=self.trust/"config.json"
        tc.write_text(json.dumps(dict(trust_uid=TRUST,broker_uid=BROKER,pin=self.cred_pin,bindings={op:self.plan.binding(op) for op in ("ARM","ISSUE","CONSUME","ADVANCE")})))
        tc.chmod(0o600)
        sudo("/bin/chown","-R",f"{TRUST}:{TRUST}",self.trust,self.trust_ipc)
        self.trust_proc=subprocess.Popen(self.command(TRUST,self.code/"production_isolated_auth.py",self.trust,self.trust_sock,TRUST),
                            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,close_fds=True)
        until=time.monotonic()+10
        while not self.trust_sock.exists():
            if self.trust_proc.poll() is not None:raise RuntimeError("trust startup denied: "+self.trust_proc.stderr.read(4096).decode(errors="replace"))
            if time.monotonic()>=until:raise RuntimeError("trust startup timeout")
            time.sleep(.05)
        self.launch()
        until=time.monotonic()+10
        while time.monotonic()<until:
            if self.proc.poll() is not None:raise RuntimeError("broker startup denied: "+str(self.proc.returncode)+" "+self.proc.stderr.read(4096).decode(errors="replace"))
            if self.sockpath.exists():
                try:
                    with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as s:
                        s.settimeout(1);s.connect(str(self.sockpath))
                        self.broker_pid=struct.unpack("3i",s.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[0]
                        send(s,{"cmd":"status"});self.assertTrue(recv(s)["ok"])
                    return
                except OSError:pass
            time.sleep(.05)
        raise RuntimeError("broker startup timeout")
    def launch(self):
        self.proc=subprocess.Popen(self.command(BROKER,self.code/"production_ipc_broker.py",self.private,self.sockpath,BROKER),
                                   stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if hasattr(self,"broker_pid"):sudo("/bin/kill","-KILL",self.broker_pid)
                self.proc.wait(timeout=5)
        if self.proc is not None and self.proc.stderr:self.proc.stderr.close()
        self.proc=None
    def stop_trust(self):
        if self.trust_proc is not None and self.trust_proc.poll() is None:
            self.trust_proc.terminate();self.trust_proc.wait(timeout=5)
        if self.trust_proc is not None and self.trust_proc.stderr:self.trust_proc.stderr.close()
        self.trust_proc=None
    def call(self,cmd,**kw):return request(self.sockpath,BROKER,dict(cmd=cmd,**kw))
    def challenge(self,op):return self.call("challenge",op=op,owner_pin=self.owner_pin,credential_pin=self.cred_pin)["options"]
    def mutate(self,cmd,**kw):
        out=self.call(cmd,owner_pin=self.owner_pin,**kw)
        self.owner_pin=out["owner_pin"];self.cred_pin=out["credential_pin"]
        return out
    def record(self,index,kind,**kw):
        p=self.plan
        return dict(index=index,previous=self.owner_pin,kind=kind,store=p.anchor.store,identity=p.identity,object_id=p.object_id,
                    checkpoint=p.anchor.checkpoint,seq=p.seq,head=p.head,binding=p.binding("ARM"))|kw
    def signed_record(self,p):return dict(record=p,signature=cross.journal_signature(self.signer,p))
    def packet(self,op,ev=cross.ZERO,head=None):
        body,sig,_=self.signer.sign(self.plan.packet(op,int(time.time()),ev,head))
        return dict(body=body.decode(),owner_signature=sig)
    def run_uid(self,uid,code,*args):
        out=subprocess.run(self.command(uid,"-c",code,*args),capture_output=True,text=True,timeout=20)
        self.assertEqual(out.returncode,0,out.stderr)
        return json.loads(out.stdout)
    def test_real_uid_boundary_and_full_chain(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            self.start(port)
            code="""
import json,os,sys
from pathlib import Path
from production_ipc_broker import request
from production_psycopg import LocalPg,ROLE
from production_pg import Denied
sock,private,pid,pg,ca,values,trust,tsock=sys.argv[1:]
pid=int(pid); result={}
result["status"]=request(sock,60181,{"cmd":"status"})
for command in ({"cmd":"challenge","op":"ARM","credential_pin":"0"*64,"owner_pin":"0"*64},
                {"cmd":"arm"},{"cmd":"owner_status","owner_pin":"0"*64},{"cmd":"cache_binding"},
                {"cmd":"sql","statement":"SELECT 1"}):
 try:request(sock,60181,command);raise AssertionError("unexpected permission")
 except (Denied,OSError):pass
for name in ("config.json","journal/0000.json"):
 try:Path(private,name).read_bytes();raise AssertionError("secret readable")
 except PermissionError:pass
try:Path(trust,"credentials/0000.json").read_bytes();raise AssertionError("credential readable")
except PermissionError:pass
try:request(tsock,60184,{"cmd":"check","pin":"0"*64});raise AssertionError("trust bypass")
except (Denied,OSError):pass
try:Path("/proc",str(pid),"environ").read_bytes();raise AssertionError("process readable")
except PermissionError:pass
try:os.kill(pid,0);raise AssertionError("signal permission")
except PermissionError:pass
try:Path(sock).unlink();raise AssertionError("socket removable")
except PermissionError:pass
try:LocalPg(int(pg),Path(ca),ROLE["ARM"],"offline-fixture-not-a-real-secret").apply("ARM",tuple(json.loads(values)));raise AssertionError("SQL bypass")
except Denied:pass
result["denied"]=True
print(json.dumps(result))
"""
            p=self.plan;values=(p.anchor.store,p.anchor.checkpoint,p.checkpoints[0],p.epoch,p.anchor.candidate,p.grant)
            result=self.run_uid(APP,code,str(self.sockpath),str(self.private),str(self.broker_pid),str(self.port),str(self.base/"public-ca.pem"),json.dumps(values),str(self.trust),str(self.trust_sock))
            self.assertTrue(result["denied"]);self.assertFalse(result["status"]["recommendation_eligible"])
            self.assertEqual(self.admin("SELECT mode FROM ei2.state;").stdout.strip(),b"DISARMED")
            self.assertEqual(requests,[])
            unknown=self.run_uid(OTHER,"from production_ipc_broker import request; import sys,json\ntry: request(sys.argv[1],60181,{'cmd':'status'}); print('false')\nexcept Exception: print('true')",str(self.sockpath))
            self.assertTrue(unknown)
            # Broker and credential service cannot read each other's files.
            deny_file="import sys,json; from pathlib import Path\ntry: Path(sys.argv[1]).read_bytes(); print('false')\nexcept PermissionError: print('true')"
            self.assertTrue(self.run_uid(BROKER,deny_file,str(self.trust/"credentials/0000.json")))
            self.assertTrue(self.run_uid(TRUST,deny_file,str(self.private/"config.json")))
            # Strict parser; malformed/oversize/unknown command must not perform operations.
            for raw in (b'{"cmd":"status","cmd":"arm"}',b'x'*33000,b'{"cmd":"status","extra":1}'):
                with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as s:
                    s.settimeout(3);s.connect(str(self.sockpath));s.send(raw)
                    self.assertFalse(recv(s)["ok"])
            with self.assertRaises(Denied):request(self.sockpath,APP,{"cmd":"status"})
            opts=self.challenge("ARM")
            assertion=self.virtual.assertion(opts)
            p=self.record(2,"CLAIM_INTENT")
            out=self.mutate("arm",credential_pin=self.cred_pin,assertion=assertion,**self.signed_record(p),**self.packet("CLAIM"))
            claim=out["result"];self.assertEqual(out["mode"],"ARMED")
            p=self.record(3,"CLAIM_RECEIPT",seq=1,head=claim)
            self.mutate("accept_claim",**self.signed_record(p))
            self.mutate("issue",credential_pin=self.cred_pin,assertion=self.virtual.assertion(self.challenge("ISSUE")))
            self.mutate("execute",credential_pin=self.cred_pin,assertion=self.virtual.assertion(self.challenge("CONSUME")))
            ev=self.call("evidence",owner_pin=self.owner_pin)["evidence"]
            p=self.record(4,"ADVANCE_INTENT",seq=1,head=claim)
            out=self.mutate("advance",credential_pin=self.cred_pin,assertion=self.virtual.assertion(self.challenge("ADVANCE")),**self.signed_record(p),**self.packet("ADVANCE",ev,claim))
            receipt=out["result"]
            self.assertFalse(self.call("owner_status",owner_pin=self.owner_pin)["cache_valid"])
            p=self.record(5,"ADVANCE_RECEIPT",seq=2,head=receipt["head"],checkpoint=receipt["checkpoint"],binding=self.call("cache_binding")["binding"])
            self.mutate("accept_advance",**self.signed_record(p))
            self.assertTrue(self.call("owner_status",owner_pin=self.owner_pin)["cache_valid"])
            self.assertFalse(self.call("status")["cache_valid"])
            self.assertEqual(len(requests),1)
            # Kill broker, then no automatic re-admission even with same configuration.
            sudo("/bin/kill","-KILL",self.broker_pid);self.proc.wait(timeout=5);self.stop()
            self.launch();self.assertNotEqual(self.proc.wait(timeout=5),0);self.stop()
            with self.assertRaises(OSError):self.call("status")
            self.assertEqual(len(requests),1)
    def test_failed_owner_assertion_freezes_no_sql_or_worker(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            self.start(port)
            opts=self.challenge("ARM");bad=self.virtual.assertion(opts,flags=1)
            p=self.record(2,"CLAIM_INTENT")
            payload=dict(credential_pin=self.cred_pin,assertion=bad,**self.signed_record(p),**self.packet("CLAIM"))
            with self.assertRaises(Denied):self.call("arm",owner_pin=self.owner_pin,**payload)
            self.assertEqual(self.call("owner_status",owner_pin=self.owner_pin)["mode"],"FROZEN")
            with self.assertRaises(Denied):self.challenge("ARM")
            self.assertEqual(self.admin("SELECT mode FROM ei2.state;").stdout.strip(),b"DISARMED")
            self.assertEqual(requests,[])

    def test_crash_before_operation_burns_startup_receipt(self):
        with fixture.tls_server(self.cert,self.tlskey) as (port,requests):
            self.start(port)
            sudo("/bin/kill","-KILL",self.broker_pid);self.proc.wait(timeout=5);self.stop()
            # Remove the stale socket as trusted supervisor: admission must still deny.
            sudo("/bin/rm","--",self.sockpath)
            self.assertEqual(sudo("/bin/cat",self.private/"started").stdout,b"OFFLINE_BROKER_STARTED\n")
            self.launch();self.assertNotEqual(self.proc.wait(timeout=5),0);self.stop()
            self.assertFalse(self.sockpath.exists())
            self.assertEqual(self.admin("SELECT mode FROM ei2.state;").stdout.strip(),b"DISARMED")
            self.assertEqual(requests,[])
