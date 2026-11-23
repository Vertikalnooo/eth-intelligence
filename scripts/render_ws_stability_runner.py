"""Inert health-only runner for the isolated ETH WS test service.

Startup and restart MUST NEVER run the legacy WebSocket probe.
The Render /health endpoint verifies process liveness only; it is not
market data, connectivity, exchange freshness or recommendation evidence.

The bounded manual probe script remains separate and needs an individually
authorized, explicitly initiated operation. Environment flags cannot enable
a probe from this service entry point.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def diagnostic_status():
    """Always refuse to represent a disabled probe as successful."""
    return {
        "probe_state": "disabled",
        "reason_code": "manual_probe_requires_separate_approval",
        "exchange_freshness": "UNVERIFIED",
        "source_reliability_verified": False,
        "recommendation_eligible": False,
        "stage2_complete": False,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(diagnostic_status()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    # A healthy HTTP process is NOT authorization to access the exchange.
    ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler
    ).serve_forever()


if __name__ == "__main__":
    main()
