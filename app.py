"""Read-only ETH Intelligence API."""
import json
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UPSTREAM = "https://api.hyperliquid.xyz/info"

CACHE_SECONDS = 10
RATE_LIMIT_SECONDS = 60
_market_lock = threading.Lock()
_market_cache = None
_market_cache_until = 0.0
_rate_limit_until = 0.0

class MarketRateLimited(Exception):
    pass

def cached_eth_market():
    """Serialize upstream calls, bound cache age, and honor 429 cooldown."""
    global _market_cache, _market_cache_until, _rate_limit_until
    with _market_lock:
        now = time.monotonic()
        if now < _rate_limit_until:
            raise MarketRateLimited("Upstream cooldown")
        if _market_cache is not None and now < _market_cache_until:
            return dict(_market_cache)
        try:
            result = eth_market()
        except HTTPError as exc:
            if exc.code == 429:
                delay = RATE_LIMIT_SECONDS
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after:
                    try:
                        delay = max(RATE_LIMIT_SECONDS, min(300, int(retry_after)))
                    except (ValueError, TypeError):
                        pass
                _rate_limit_until = time.monotonic() + delay
                _market_cache = None
                _market_cache_until = 0.0
            raise
        _market_cache = dict(result)
        _market_cache_until = time.monotonic() + CACHE_SECONDS
        return dict(result)


def eth_market(fetch=urlopen):
    request = Request(UPSTREAM, data=b'{"type":"metaAndAssetCtxs"}',
                      headers={"Content-Type": "application/json", "User-Agent": "eth-intelligence/0.2"},
                      method="POST")
    with fetch(request, timeout=8) as response:
        payload = json.load(response)
    if not isinstance(payload, list) or len(payload) != 2:
        raise ValueError("Invalid upstream payload")
    metadata, contexts = payload
    if not isinstance(metadata, dict):
        raise ValueError("Invalid market metadata")
    universe = metadata.get("universe")
    if not isinstance(universe, list) or not isinstance(contexts, list) or len(universe) != len(contexts):
        raise ValueError("Invalid or mismatched market metadata")
    if any(not isinstance(coin, dict) or not isinstance(coin.get("name"), str) for coin in universe):
        raise ValueError("Invalid universe entry")
    indices = [i for i, coin in enumerate(universe) if coin["name"] == "ETH"]
    if len(indices) != 1:
        raise ValueError("ETH market missing or duplicated")
    index = indices[0]
    ctx = contexts[index]
    if not isinstance(ctx, dict):
        raise ValueError("Invalid ETH context")
    def valid(field):
        try:
            value = Decimal(str(ctx[field]))
        except (InvalidOperation, TypeError, KeyError) as exc:
            raise ValueError("Invalid market price") from exc
        if not value.is_finite() or value <= 0:
            raise ValueError("Invalid market value")
        return str(value)
    return {
        "symbol": "ETH/USDC", "market_type": "perpetual",
        "source": "hyperliquid", "price_type": "mark",
        "mark_price": valid("markPx"),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "exchange_timestamp": None,
        "exchange_freshness_verified": False,
        "freshness_status": "unknown",
        "recommendation_eligible": False,
        "note": "Observed time is local fetch time, not an exchange tick timestamp."
    }

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
        if urlsplit(self.path).path in ("/", "/health"):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/health"):
            self.respond(200, {"status": "ok", "service": "eth-intelligence", "version": "0.2.1"})
        elif path == "/market/eth":
            try:
                self.respond(200, cached_eth_market())
            except HTTPError as exc:
                self.log_error("Hyperliquid HTTP status: %d", exc.code)
                self.respond(503, {"error": "market_data_unavailable"})
            except (MarketRateLimited, URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                self.log_error("Market data unavailable: %s", type(exc).__name__)
                self.respond(503, {"error": "market_data_unavailable"})
        else:
            self.respond(404, {"error": "not_found"})

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler).serve_forever()
