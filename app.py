"""Read-only ETH Intelligence API."""
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UPSTREAM = "https://api.hyperliquid.xyz/info"

def eth_market(fetch=urlopen):
    request = Request(UPSTREAM, data=b'{"type":"metaAndAssetCtxs"}',
                      headers={"Content-Type": "application/json", "User-Agent": "eth-intelligence/0.2"},
                      method="POST")
    with fetch(request, timeout=8) as response:
        metadata, contexts = json.load(response)
    universe = metadata["universe"]
    index = next(i for i, coin in enumerate(universe) if coin["name"] == "ETH")
    ctx = contexts[index]
    def valid(field):
        value = Decimal(str(ctx[field]))
        if not value.is_finite() or value <= 0:
            raise ValueError("Invalid market value")
        return str(value)
    return {
        "symbol": "ETH/USDC", "market_type": "perpetual",
        "source": "hyperliquid", "price_type": "mark",
        "mark_price": valid("markPx"),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "note": "Observed time is local fetch time, not an exchange tick timestamp."
    }

class Handler(BaseHTTPRequestHandler):
    def respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"status": "ok", "service": "eth-intelligence", "version": "0.2.0"})
        elif self.path == "/market/eth":
            try:
                self.respond(200, eth_market())
            except HTTPError as exc:
                self.log_error("Hyperliquid HTTP status: %d", exc.code)
                self.respond(503, {"error": "market_data_unavailable"})
            except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                self.log_error("Market data unavailable: %s", type(exc).__name__)
                self.respond(503, {"error": "market_data_unavailable"})
        else:
            self.respond(404, {"error": "not_found"})

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler).serve_forever()
