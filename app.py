"""Read-only ETH Intelligence API."""
import json
import os
import random
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
MAX_RATE_LIMIT_SECONDS = 900
MIN_REQUEST_INTERVAL_SECONDS = 15
TRANSIENT_FAILURE_SECONDS = 30
_market_lock = threading.Lock()
_market_cache = None
_market_cache_until = 0.0
_rate_limit_until = 0.0
_consecutive_429 = 0
_next_upstream_attempt = 0.0

class MarketRateLimited(Exception):
    pass

def cached_eth_market():
    """Serialize upstream calls, bound cache age, and honor 429 cooldown."""
    global _market_cache, _market_cache_until, _rate_limit_until, _consecutive_429, _next_upstream_attempt
    with _market_lock:
        now = time.monotonic()
        if now < _rate_limit_until:
            raise MarketRateLimited("Upstream cooldown")
        if _market_cache is not None and now < _market_cache_until:
            return dict(_market_cache)
        if now < _next_upstream_attempt:
            raise MarketRateLimited("Upstream request interval")
        # Reserve the next attempt before contacting upstream; the lock serializes callers.
        _next_upstream_attempt = now + MIN_REQUEST_INTERVAL_SECONDS
        try:
            result = eth_market()
        except HTTPError as exc:
            if exc.code == 429:
                _consecutive_429 += 1
                delay = min(MAX_RATE_LIMIT_SECONDS, RATE_LIMIT_SECONDS * (2 ** min(_consecutive_429 - 1, 4)))
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after:
                    try:
                        delay = max(delay, min(MAX_RATE_LIMIT_SECONDS, int(retry_after)))
                    except (ValueError, TypeError):
                        pass
                _rate_limit_until = time.monotonic() + min(MAX_RATE_LIMIT_SECONDS, delay + random.uniform(0, min(5, delay * 0.1)))
                _market_cache = None
                _market_cache_until = 0.0
            else:
                _next_upstream_attempt = max(_next_upstream_attempt, time.monotonic() + TRANSIENT_FAILURE_SECONDS)
            raise
        except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError):
            _next_upstream_attempt = max(_next_upstream_attempt, time.monotonic() + TRANSIENT_FAILURE_SECONDS)
            raise
        _consecutive_429 = 0
        _rate_limit_until = 0.0
        _next_upstream_attempt = max(_next_upstream_attempt, time.monotonic() + MIN_REQUEST_INTERVAL_SECONDS)
        _market_cache = dict(result)
        _market_cache_until = time.monotonic() + CACHE_SECONDS
        return dict(result)


def market_status():
    """Local diagnostic only; never contacts Hyperliquid."""
    with _market_lock:
        now = time.monotonic()
        remaining = max(0, _rate_limit_until - now)
        return {
            "source": "hyperliquid",
            "state": "cooldown" if remaining > 0 else "ready_to_retry",
            "retry_after_seconds": int(remaining + 0.999999) if remaining else 0,
            "consecutive_rate_limits": _consecutive_429,
            "next_upstream_attempt_seconds": max(0, int(_next_upstream_attempt - now + 0.999999)),
            "cache_valid": _market_cache is not None and now < _market_cache_until,
            "exchange_freshness_verified": False,
            "recommendation_eligible": False,
        }


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
        elif path == "/market/status":
            self.respond(200, market_status())
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
