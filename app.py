"""Safe production baseline: diagnostics only; no upstream transport.

The existing Render startup command (python app.py) uses this entrypoint.
No environment flag or HTTP route enables market retrieval. A later live
candidate needs a separately approved implementation.
"""
import json
import os
import time
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rest_operational

DENY_FLAGS = {
    "exchange_freshness_verified": False,
    "recommendation_eligible": False,
    "stage2_complete": False,
}
VERSION = "0.2.2"

def market_status():
    """Exact existing status keys; explicit disabled mode, no cache or receipt."""
    return {
        "source": "hyperliquid",
        "state": "disabled",
        "retry_after_seconds": 0,
        "consecutive_rate_limits": 0,
        "next_upstream_attempt_seconds": 0,
        "cache_valid": False,
        "cache_remaining_seconds": 0,
        "upstream_request_allowed": False,
        "throttle_reason": "upstream_disabled",
        **DENY_FLAGS,
        "rest_operational_freshness": rest_operational.inspect(
            None, time.monotonic_ns(), cache_valid=False, rate_limited=False),
    }

def assess_market_recommendation(market):
    """Fail closed for real API responses: a local fetch time is not an exchange tick."""
    timestamp = market.get("exchange_timestamp")
    verified = market.get("exchange_freshness_verified") is True
    mark_verified = market.get("mark_price_freshness_verified") is True
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp <= 0:
        reason = "missing_exchange_timestamp"
    elif not verified:
        reason = "unverified_exchange_freshness"
    elif not mark_verified:
        reason = "unverified_mark_price_freshness"
    else:
        # This API has no end-to-end authenticated mark tick freshness proof yet.
        reason = "mark_price_provenance_not_implemented"
    return {"recommendation_eligible": False, "recommendation_block_reason": reason}


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        try:
            path = urlsplit(self.path).path
        except ValueError:
            self.respond(400, {"error": "bad_path"})
            return
        if path in ("/", "/health"):
            self.respond(200, {"status": "ok", "service": "eth-intelligence",
                               "version": VERSION, "mode": "SAFE_BASELINE"})
        else:
            self.respond(404, {"error": "not_found"})

    def do_GET(self):
        try:
            path = urlsplit(self.path).path
        except ValueError:
            self.respond(400, {"error": "bad_path"})
            return
        if path in ("/", "/health"):
            self.respond(200, {"status": "ok", "service": "eth-intelligence",
                               "version": VERSION, "mode": "SAFE_BASELINE"})
        elif path == "/market/status":
            self.respond(200, market_status())
        elif path == "/market/eth":
            self.respond(503, {"error": "market_data_unavailable",
                               "reason": "upstream_disabled", **DENY_FLAGS})
        else:
            self.respond(404, {"error": "not_found"})

def main():
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))),
                        Handler).serve_forever()

if __name__ == "__main__":
    main()
