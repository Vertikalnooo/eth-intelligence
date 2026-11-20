"""Temporary Render Free probe runner; never imports production API.

Starts an HTTP health endpoint for Render and runs the WebSocket probe once.
Check Render logs for websocket_probe_result. No repeated upstream calls.
"""
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

result = {"state": "pending"}

def run_probe():
    try:
        completed = subprocess.run(
            [sys.executable, "-u", "scripts/probe_hyperliquid_ws.py"],
            capture_output=True, text=True, timeout=55, check=False,
        )
        print(completed.stdout, flush=True)
        if completed.stderr:
            print(completed.stderr[-2000:], flush=True)
        result["state"] = "passed" if completed.returncode == 0 else "failed"
    except Exception as exc:
        result["state"] = "failed"
        print("probe_runner_error=" + type(exc).__name__, flush=True)
    print("websocket_probe_result=" + result["state"], flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = ('{"probe_state":"' + result["state"] + '"}').encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    threading.Thread(target=run_probe, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler).serve_forever()
