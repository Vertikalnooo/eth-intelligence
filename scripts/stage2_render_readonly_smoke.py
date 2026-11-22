"""Bounded read-only Render health smoke. Never queries /market/eth.

By default prints the plan without making a request. Live execution is
separately opted in; output contains no prices, headers or credentials.
"""
import argparse
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError

BASE = "https://eth-intelligence-api.onrender.com"
ROUTES = ("/health", "/market/status")
MAX_BYTES = 8192
TIMEOUT_SECONDS = 5


from urllib.request import HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_route_url(url):
    if type(url) is not str:
        return False
    target = urlsplit(url)
    return (
        target.scheme == "https"
        and target.hostname == "eth-intelligence-api.onrender.com"
        and target.port is None
        and target.username is None and target.password is None
        and target.query == "" and target.fragment == ""
        and target.path in ROUTES
        and target.netloc == "eth-intelligence-api.onrender.com"
    )


def verify_payload(route, payload):
    """Verify only local status and fail-closed flags, never source freshness."""
    if type(payload) is not dict:
        return False
    if route == "/health":
        return (payload.get("status") == "ok"
                and payload.get("service") == "eth-intelligence"
                and type(payload.get("version")) is str)
    if route == "/market/status":
        return (
            payload.get("source") == "hyperliquid"
            and payload.get("state") in ("cooldown", "ready_to_retry")
            and type(payload.get("retry_after_seconds")) is int
            and payload["retry_after_seconds"] >= 0
            and payload.get("exchange_freshness_verified") is False
            and payload.get("recommendation_eligible") is False
            and type(payload.get("upstream_request_allowed")) is bool
        )
    return False


def inspect(fetch=None):
    """Only two GET requests. A successful run is NOT source-health proof."""
    if fetch is None:
        fetch = build_opener(NoRedirect()).open
    checks = []
    for route in ROUTES:
        url = BASE + route
        if not validate_route_url(url):
            raise ValueError("URL allowlist violation")
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with fetch(request, timeout=TIMEOUT_SECONDS) as response:
                status = response.getcode()
                chunk = response.read(MAX_BYTES + 1)
            if status != 200 or len(chunk) > MAX_BYTES:
                raise ValueError("unacceptable response status or size")
            payload = json.loads(chunk.decode("utf-8", errors="strict"))
            if not verify_payload(route, payload):
                raise ValueError("unacceptable or untrusted diagnostics")
        except (HTTPError, URLError, UnicodeError, ValueError, TimeoutError, OSError):
            return {"status": "FAILED", "route": route,
                    "recommendation_eligible": False, "stage2_complete": False}
        checks.append({"route": route, "http_status": status, "json_valid": True})
    return {
        "status": "DIAGNOSTICS_PASS",
        "checks": checks,
        "exchange_mark_verified": False,
        "source_reliability_verified": False,
        "recommendation_eligible": False,
        "stage2_complete": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-readonly", action="store_true")
    args = parser.parse_args()
    if not args.live_readonly:
        print(json.dumps({"status": "PLAN_ONLY", "routes": list(ROUTES),
                          "network_requests": 0, "stage2_complete": False}))
        return 0
    if os.environ.get("STAGE2_LIVE_READONLY_APPROVED") != "yes":
        print(json.dumps({"status": "APPROVAL_REQUIRED", "network_requests": 0,
                          "stage2_complete": False}))
        return 2
    result = inspect()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "DIAGNOSTICS_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
