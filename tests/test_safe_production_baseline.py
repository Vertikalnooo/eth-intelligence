"""Safety tests of the actual disabled production entrypoint (not legacy fixture)."""
import ast
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import app
from scripts import stage2_render_readonly_smoke as smoke
import legacy_market_fixture as legacy

ROOT=Path(__file__).resolve().parents[1]

def request(port,method,path,headers=None):
    conn=http.client.HTTPConnection("127.0.0.1",port,timeout=2)
    try:
        conn.request(method,path,headers=headers or {})
        response=conn.getresponse()
        raw=response.read()
        return response.status,json.loads(raw) if raw and response.getheader("Content-Type","").startswith("application/json") else raw,response.getheader("Cache-Control")
    finally:conn.close()

class SafeBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,kwargs={"poll_interval":.02},daemon=True)
        cls.thread.start()
        cls.port=cls.server.server_port
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(2)

    def test_actual_status_contract_disabled(self):
        code,status,cache=request(self.port,"GET","/market/status")
        self.assertEqual(code,200)
        self.assertEqual(cache,"no-store")
        self.assertEqual(set(status),smoke.STATUS_KEYS)
        self.assertTrue(smoke.verify_payload("/market/status",status))
        self.assertEqual(status["state"],"disabled")
        self.assertEqual(status["throttle_reason"],"upstream_disabled")
        self.assertIs(status["upstream_request_allowed"],False)
        self.assertIs(status["cache_valid"],False)
        self.assertEqual(status["rest_operational_freshness"]["state"],"NO_DATA")

    def test_public_market_always_unavailable_even_with_env_or_old_cache(self):
        with patch.dict(os.environ,{"LIVE_REST_ENABLED":"true","STAGE2_LIVE_READONLY_APPROVED":"yes",
                    "DATABASE_URL":"synthetic","HYPERLIQUID_ENABLED":"yes"}), \
             patch.object(legacy,"_market_cache",{"mark_price":"9999"}), \
             patch.object(legacy,"eth_market",side_effect=AssertionError("legacy fetch forbidden")) as fetch:
            for path in ("/market/eth","/market/eth?refresh=1","http://localhost/market/eth"):
                code,body,cache=request(self.port,"GET",path)
                self.assertEqual(code,503)
                self.assertEqual(body["error"],"market_data_unavailable")
                self.assertEqual(body["reason"],"upstream_disabled")
                for flag in smoke.DENY_FLAGS:self.assertIs(body[flag],False)
                self.assertNotIn("mark_price",body)
                self.assertEqual(cache,"no-store")
            fetch.assert_not_called()

    def test_concurrent_market_requests_never_change_status(self):
        before=app.market_status()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _:request(self.port,"GET","/market/eth")[0],range(18)))
        self.assertEqual(results,[503]*18)
        self.assertEqual(app.market_status(),before)

    def test_no_write_methods_or_admin_routes(self):
        for method in ("POST","PUT","PATCH","DELETE","OPTIONS","TRACE","CONNECT"):
            self.assertEqual(request(self.port,method,"/market/eth")[0],501)
        for path in ("/admin/rest/execute","/admin/approval/confirm","/ws","/unknown"):
            self.assertEqual(request(self.port,"GET",path)[0],404)
        self.assertEqual(request(self.port,"HEAD","/market/eth")[0],404)

    def test_health_and_head(self):
        for path in ("/","/health"):
            code,body,cache=request(self.port,"GET",path)
            self.assertEqual(code,200)
            self.assertEqual(body,{"status":"ok","service":"eth-intelligence","version":"0.2.2","mode":"SAFE_BASELINE"})
            self.assertEqual(cache,"no-store")
            self.assertEqual(request(self.port,"HEAD",path),(200,b"","no-store"))

    def test_smoke_rejects_forged_disabled_state(self):
        base=app.market_status()
        for key,value in (("upstream_request_allowed",True),("cache_valid",True),
                          ("retry_after_seconds",1),("consecutive_rate_limits",1),
                          ("next_upstream_attempt_seconds",1),("cache_remaining_seconds",1),
                          ("throttle_reason",None),("recommendation_eligible",True)):
            self.assertFalse(smoke.verify_payload("/market/status",dict(base,**{key:value})),key)
        forged=dict(base,rest_operational_freshness=dict(base["rest_operational_freshness"],state="FRESH",age_ms=0))
        self.assertFalse(smoke.verify_payload("/market/status",forged))

    def test_strict_smoke_only_accepts_safe_baseline(self):
        import io
        class Response(io.BytesIO):
            def getcode(self):return 200
        calls=[]
        def fetch(req,timeout):
            calls.append(req.full_url)
            data=app.market_status() if req.full_url.endswith("/market/status") else {
                "status":"ok","service":"eth-intelligence","version":app.VERSION,"mode":"SAFE_BASELINE"}
            return Response(json.dumps(data).encode())
        self.assertEqual(smoke.inspect(fetch,require_disabled=True)["status"],"DIAGNOSTICS_PASS")
        self.assertEqual(calls,[smoke.BASE+"/health",smoke.BASE+"/market/status"])
        def old(req,timeout):
            data=legacy.market_status() if req.full_url.endswith("/market/status") else {
                "status":"ok","service":"eth-intelligence","version":"0.2.1"}
            return Response(json.dumps(data).encode())
        self.assertEqual(smoke.inspect(old,require_disabled=True)["status"],"FAILED")

    def test_production_import_boundary_and_legacy_default_denial(self):
        tree=ast.parse((ROOT/"app.py").read_text())
        imports=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):imports.update(a.name for a in node.names)
            elif isinstance(node,ast.ImportFrom):imports.add(node.module)
        self.assertEqual(imports,{"json","os","time","urllib.parse","http.server","rest_operational"})
        for name in ("eth_market","cached_eth_market","urlopen","UPSTREAM","_market_cache"):
            self.assertFalse(hasattr(app,name),name)
        with self.assertRaises(ValueError):legacy.eth_market()

    def test_real_startup_command_ignores_enable_flags(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
        env=dict(os.environ,PORT=str(port),LIVE_REST_ENABLED="true",
                 STAGE2_LIVE_READONLY_APPROVED="yes",DATABASE_URL="synthetic")
        proc=subprocess.Popen([sys.executable,"app.py"],cwd=ROOT,env=env,
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+5
            while True:
                if proc.poll() is not None: self.fail("startup exited")
                try:
                    code,body,_=request(port,"GET","/health")
                    break
                except OSError:
                    if time.monotonic()>=deadline:raise
                    time.sleep(.05)
            self.assertEqual(code,200)
            self.assertEqual(body["mode"],"SAFE_BASELINE")
            self.assertEqual(request(port,"GET","/market/eth")[0],503)
            self.assertFalse(request(port,"GET","/market/status")[1]["upstream_request_allowed"])
        finally:
            proc.terminate()
            try:proc.wait(timeout=2)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=2)

if __name__=="__main__":unittest.main()
