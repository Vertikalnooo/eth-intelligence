"""Single-use isolated ETH mark-context WS runner; separate from production."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUN_MARKER = "/tmp/stage2_eth_activeassetctx_20261122_once"
RUN_TIMEOUT = 60
result = {"state": "pending", "reason_code": None}


def run_probe():
    try:
        # Prevent a second probe on process restart within the same test instance.
        # Ephemeral volume/container replacement is NOT a distributed lock.
        fd = os.open(RUN_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    except FileExistsError:
        result.update(state="not_repeated", reason_code="one_shot_marker_exists")
        print("stage2_eth_ws_probe=not_repeated", flush=True)
        return
    except OSError:
        result.update(state="failed", reason_code="marker_unavailable")
        print("stage2_eth_ws_probe=failed reason_code=marker_unavailable", flush=True)
        return
    try:
        completed = subprocess.run(
            [sys.executable, "-u", "scripts/probe_hyperliquid_ws_stability.py"],
            capture_output=True, text=True, timeout=RUN_TIMEOUT, check=False,
        )
        # Child only emits fixed diagnostic fields; cap log output.
        print(completed.stdout[:5000], flush=True)
        result.update(state="passed" if completed.returncode == 0 else "failed",
                      reason_code=None if completed.returncode == 0 else "probe_nonzero")
    except subprocess.TimeoutExpired:
        result.update(state="failed", reason_code="global_runner_timeout")
    except Exception as exc:
        result.update(state="failed", reason_code="runner_" + type(exc).__name__)
    print("stage2_eth_ws_probe=" + result["state"] +
          (" reason_code=" + result["reason_code"] if result["reason_code"] else ""), flush=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"probe_state": result["state"],
                           "exchange_freshness": "UNVERIFIED",
                           "recommendation_eligible": False}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    threading.Thread(target=run_probe, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler).serve_forever()
