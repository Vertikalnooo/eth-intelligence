"""Real subprocess crash/deadline tests, loopback only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import rest_one_shot_hardened as h

KEY=b"k"*32
CAND="a"*40
BODY=b'[{"universe":[{"name":"ETH"}]},[{"markPx":"1701.5"}]]'


@contextmanager
def wire(mode="success"):
    listener=socket.socket()
    listener.bind(("127.0.0.1",0))
    listener.listen(4)
    listener.settimeout(.1)
    requests=[]
    received=threading.Event()
    closed=threading.Event()
    stop=threading.Event()

    def server():
        while not stop.is_set():
            try:
                conn,_=listener.accept()
            except (OSError,TimeoutError):
                continue
            with conn:
                conn.settimeout(.1)
                data=b""
                try:
                    while b"\r\n\r\n" not in data:
                        chunk=conn.recv(4096)
                        if not chunk:
                            break
                        data+=chunk
                    if b"\r\n\r\n" not in data:
                        continue
                    headers,body=data.split(b"\r\n\r\n",1)
                    length=next(int(line.split(b":",1)[1]) for line in headers.split(b"\r\n")
                                if line.lower().startswith(b"content-length:"))
                    while len(body)<length:
                        chunk=conn.recv(4096)
                        if not chunk:
                            break
                        body+=chunk
                    requests.append((headers.split(b"\r\n")[0],body))
                    received.set()
                    if mode=="success":
                        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "+
                                     str(len(BODY)).encode()+b"\r\nConnection: close\r\n\r\n"+BODY)
                    elif mode=="body":
                        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "+
                                     str(len(BODY)).encode()+b"\r\n\r\n"+BODY[:1])
                    elif mode=="redirect":
                        conn.sendall(b"HTTP/1.1 302 Found\r\nLocation: /other\r\nContent-Length: 0\r\n\r\n")
                    elif mode=="disconnect":
                        continue
                    while not stop.is_set():
                        try:
                            if not conn.recv(4096):
                                closed.set()
                                break
                        except TimeoutError:
                            continue
                except (OSError,StopIteration,ValueError):
                    closed.set()
    thread=threading.Thread(target=server,daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1],requests,received,closed
    finally:
        stop.set()
        listener.close()
        thread.join(2)


@unittest.skipUnless(sys.platform=="linux","Linux worker and store boundary")
class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"ledger.sqlite"
        self.store=h.initialize_offline_store(self.path,CAND)
        self.grant=self.store.issue_offline(KEY)

    def test_success_and_no_second_request(self):
        with wire() as (port,requests,_,closed):
            result=h.run_offline(self.store,self.grant,KEY,port)
            self.assertEqual(result["mark_price"],"1701.5")
            self.assertEqual(result["scope"],"OFFLINE_ONLY")
            self.assertFalse(result["recommendation_eligible"])
            self.assertFalse(result["exchange_freshness_verified"])
            self.assertFalse(result["stage2_complete"])
            with self.assertRaises(h.OneShotError):
                h.run_offline(self.store,self.grant,KEY,port)
            self.assertEqual(requests,[(b"POST /info HTTP/1.1",b'{"type":"metaAndAssetCtxs"}')])
        self.assertEqual(self.store.state(self.grant,KEY),"CONSUMED")

    def test_signature_and_scope_tampering(self):
        for g in (replace(self.grant,scope="LIVE"),replace(self.grant,candidate="b"*40),
                  replace(self.grant,expires_at=self.grant.expires_at+1),
                  replace(self.grant,signature="0"*64),replace(self.grant,store_id="0"*32)):
            with self.subTest(g=g):
                with self.assertRaises(h.OneShotError):
                    self.store.consume(g,KEY)
        with self.assertRaises(h.OneShotError):
            self.store.consume(self.grant,b"x"*32)
        self.assertEqual(self.store.state(self.grant,KEY),"ISSUED")

    def test_revoke_is_terminal(self):
        self.store.revoke(self.grant,KEY)
        self.assertEqual(self.store.state(self.grant,KEY),"REVOKED")
        for action in (self.store.consume,self.store.revoke):
            with self.assertRaises(h.OneShotError):
                action(self.grant,KEY)

    def test_consumed_is_terminal(self):
        self.store.consume(self.grant,KEY)
        with self.assertRaises(h.OneShotError):
            self.store.revoke(self.grant,KEY)
        with self.assertRaises(h.OneShotError):
            self.store.consume(self.grant,KEY)

    def test_expiration_and_future_clock(self):
        from unittest.mock import patch
        with patch.object(h.time,"time",return_value=self.grant.expires_at):
            with self.assertRaises(h.OneShotError):
                self.store.consume(self.grant,KEY)
        with patch.object(h.time,"time",return_value=self.grant.issued_at-1):
            with self.assertRaises(h.OneShotError):
                self.store.consume(self.grant,KEY)

    def test_concurrent_consume_vs_revoke(self):
        def transition(i):
            try:
                (self.store.consume if i%2 else self.store.revoke)(self.grant,KEY)
                return True
            except h.OneShotError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(transition,range(12))),1)
        self.assertIn(self.store.state(self.grant,KEY),("REVOKED","CONSUMED"))

    def test_copied_and_replaced_store_rejected(self):
        replacement=self.path.with_name("copy.sqlite")
        shutil.copy2(self.path,replacement)
        with self.assertRaises(h.OneShotError):
            replace(self.store,path=replacement).consume(self.grant,KEY)
        os.replace(replacement,self.path)
        with self.assertRaises(h.OneShotError):
            self.store.consume(self.grant,KEY)

    def test_symlink_hardlink_permissions(self):
        link=self.path.with_name("link")
        link.symlink_to(self.path)
        with self.assertRaises(h.OneShotError):
            replace(self.store,path=link).consume(self.grant,KEY)
        link.unlink()
        os.link(self.path,link)
        with self.assertRaises(h.OneShotError):
            self.store.consume(self.grant,KEY)
        link.unlink()
        self.path.chmod(0o644)
        with self.assertRaises(h.OneShotError):
            self.store.consume(self.grant,KEY)

    def test_sidecar_symlink_denied(self):
        Path(str(self.path)+"-journal").symlink_to(self.path)
        with self.assertRaises(h.OneShotError):
            self.store.consume(self.grant,KEY)

    def test_no_overwrite_or_reset(self):
        with self.assertRaises(FileExistsError):
            h.initialize_offline_store(self.path,CAND)
        self.store.consume(self.grant,KEY)
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE grants SET state='ISSUED'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM grants")
        self.assertEqual(self.store.state(self.grant,KEY),"CONSUMED")

    def _deadline(self,mode):
        with wire(mode) as (port,requests,received,closed):
            start=time.monotonic()
            with self.assertRaises(h.OneShotError):
                h.run_offline(self.store,self.grant,KEY,port,budget_ns=900_000_000)
            self.assertLess(time.monotonic()-start,3)
            self.assertTrue(received.is_set())
            self.assertTrue(closed.wait(2))
            self.assertEqual(len(requests),1)
            self.assertEqual(self.store.state(self.grant,KEY),"CONSUMED")
            with self.assertRaises(h.OneShotError):
                h.run_offline(self.store,self.grant,KEY,port)
            self.assertEqual(len(requests),1)

    def test_hard_deadline_stalled_headers(self):
        self._deadline("headers")

    def test_hard_deadline_stalled_body(self):
        self._deadline("body")

    def test_worker_redirect_and_disconnect(self):
        for mode in ("redirect","disconnect"):
            grant=self.store.issue_offline(KEY)
            with wire(mode) as (port,requests,_,_):
                with self.assertRaises(h.OneShotError):
                    h.run_offline(self.store,grant,KEY,port)
                self.assertEqual(len(requests),1)
                self.assertEqual(self.store.state(grant,KEY),"CONSUMED")

    def _child(self,port,action):
        # Real process, synthetic key/approval only. No remote target accepted.
        script=("import sys; from pathlib import Path; import rest_one_shot_hardened as h\n"
                "p,i,d,n,c,g,s,a,e,x,port,action=sys.argv[1:]\n"
                "store=h.Store(Path(p),i,int(d),int(n),c)\n"
                "grant=h.Grant(i,g,c,s,int(a),int(e),x)\n"
                "key=b'k'*32\n"
                "if action=='consume':\n"
                " store.consume(grant,key); import os; os._exit(17)\n"
                "else: h.run_offline(store,grant,key,int(port))\n")
        s=self.store; g=self.grant
        return subprocess.Popen([sys.executable,"-c",script,str(s.path),s.store_id,
                                 str(s.device),str(s.inode),s.candidate,g.grant_id,g.scope,
                                 str(g.issued_at),str(g.expires_at),g.signature,str(port),action],
                                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def test_crash_after_commit_before_worker(self):
        child=self._child(1,"consume")
        try:
            self.assertEqual(child.wait(timeout=5),17)
        finally:
            if child.poll() is None:
                child.kill();child.wait()
        self.assertEqual(self.store.state(self.grant,KEY),"CONSUMED")
        with self.assertRaises(h.OneShotError):
            self.store.consume(self.grant,KEY)

    def test_parent_killed_during_http_worker_stops(self):
        with wire("headers") as (port,requests,received,closed):
            child=self._child(port,"run")
            try:
                self.assertTrue(received.wait(5))
                child.kill()
                child.wait(timeout=3)
                self.assertTrue(closed.wait(2),"worker socket did not close after parent death")
                self.assertEqual(len(requests),1)
                self.assertEqual(self.store.state(self.grant,KEY),"CONSUMED")
                with self.assertRaises(h.OneShotError):
                    h.run_offline(self.store,self.grant,KEY,port)
            finally:
                if child.poll() is None:
                    child.kill();child.wait()

    def test_no_remote_worker_argument(self):
        worker=Path(h.__file__).with_name("rest_one_shot_worker.py")
        result=subprocess.run([sys.executable,str(worker),str(os.getpid()),
                               str(time.monotonic_ns()+1_000_000_000),"api.hyperliquid.xyz"],
                              capture_output=True,timeout=3)
        self.assertEqual(result.returncode,2)

    def test_wrong_candidate_or_store_id(self):
        for store in (replace(self.store,candidate="b"*40),replace(self.store,store_id="0"*32)):
            with self.assertRaises(h.OneShotError):
                store.consume(self.grant,KEY)


if __name__=="__main__":
    unittest.main()
