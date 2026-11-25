"""Bounded read-only Render health smoke. Never queries /market/eth.

By default prints the plan without making a request. Live execution is
separately opted in; output contains no prices, headers or credentials.
"""
import argparse
import json
import os
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


DENY_FLAGS = ("exchange_freshness_verified", "recommendation_eligible",
              "stage2_complete")
STATUS_KEYS = frozenset((
    "source", "state", "retry_after_seconds", "consecutive_rate_limits",
    "next_upstream_attempt_seconds", "cache_valid", "cache_remaining_seconds",
    "upstream_request_allowed", "throttle_reason", "rest_operational_freshness",
    *DENY_FLAGS,
))
REST_KEYS = frozenset((
    "state", "age_ms", "clock_basis", "source_channel", "reason_codes",
    *DENY_FLAGS,
))
INVALID_REASONS = frozenset((
    "clock_domain_changed", "invalid_local_receipt_clock", "invalid_rest_snapshot",
    "invalid_rest_mark", "invalid_rest_receipt", "invalid_input",
    "invalid_clock_domain_or_age", "invalid_rest_transport_state",
    "cache_unavailable", "upstream_cooldown",
))
KNOWN_REASONS = INVALID_REASONS | {
    "no_rest_receipt", "missing_exchange_provenance",
    "transport_degraded", "transport_stale",
}


def validate_route_url(url):
    # Exact allowlist, before any request or URL parsing. No alternate
    # ports, userinfo, redirects, queries, fragments or exchange hosts.
    return type(url) is str and url in tuple(BASE + route for route in ROUTES)


def _exact_keys(payload, keys):
    return (type(payload) is dict and all(type(key) is str for key in payload)
            and set(payload) == keys)


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _enum(value, options):
    return type(value) is str and value in options


def _denied(payload):
    return all(payload.get(key) is False for key in DENY_FLAGS)


def _verify_rest(payload, *, cache_valid, cooldown):
    if not _exact_keys(payload, REST_KEYS) or not _denied(payload):
        return False
    state, age, reasons = payload["state"], payload["age_ms"], payload["reason_codes"]
    if (not _enum(state, ("NO_DATA", "INVALID", "FRESH", "DEGRADED", "STALE"))
            or not _enum(payload["clock_basis"], ("process_local_monotonic",))
            or not _enum(payload["source_channel"], ("REST",))
            or (age is not None and not _nonnegative_int(age))
            or type(reasons) is not list
            or not all(_enum(code, KNOWN_REASONS) for code in reasons)
            or len(set(reasons)) != len(reasons)
            or "missing_exchange_provenance" not in reasons):
        return False
    codes = set(reasons)
    if ("upstream_cooldown" in codes) != cooldown:
        return False
    # The production 429 branch clears both cache and receipt atomically.
    if cooldown and state != "NO_DATA":
        return False
    if state == "NO_DATA":
        return (age is None and not cache_valid
                and codes == ({"no_rest_receipt", "missing_exchange_provenance"}
                              | ({"upstream_cooldown"} if cooldown else set())))
    if "no_rest_receipt" in codes:
        return False
    if state == "INVALID":
        evidence = codes - {"missing_exchange_provenance"}
        if "cache_unavailable" in evidence and cache_valid:
            return False
        if age is not None:
            # Only a lost cache can demote a valid local receipt to INVALID
            # outside cooldown. Retain the underlying degraded reason.
            if cache_valid or age > 5000:
                return False
            if evidence == {"cache_unavailable"}:
                return age <= 2000
            return (2000 <= age <= 5000
                    and evidence == {"cache_unavailable", "transport_degraded"})
        # Capture/identity failures occur before cache checking. Assessment
        # failures occur inside the cache-aware branch of inspect().
        capture_errors = {"clock_domain_changed", "invalid_local_receipt_clock",
                          "invalid_rest_snapshot", "invalid_rest_mark",
                          "invalid_rest_receipt"}
        if len(evidence) == 1 and evidence <= capture_errors:
            return True
        cache_code = {"cache_unavailable"} if not cache_valid else set()
        return any(evidence == {code} | cache_code for code in (
            "invalid_input", "invalid_clock_domain_or_age",
            "invalid_rest_transport_state"))
    if age is None:
        return False
    # Age is floor(ns/1e6), so DEGRADED can display 2000 and STALE
    # can display 5000 at the first nanosecond past each boundary.
    if state == "STALE":
        return (age >= 5000 and "transport_stale" in codes
                and codes == ({"transport_stale", "missing_exchange_provenance"}
                              | ({"cache_unavailable"} if not cache_valid else set())
                              | ({"upstream_cooldown"} if cooldown else set())))
    if not cache_valid or cooldown:
        return False
    if state == "FRESH":
        return age <= 2000 and codes == {"missing_exchange_provenance"}
    return (2000 <= age <= 5000
            and codes == {"transport_degraded", "missing_exchange_provenance"})


def verify_payload(route, payload):
    """Check exact local diagnostic schema; never establish source health."""
    if type(payload) is not dict or not all(type(key) is str for key in payload):
        return False
    if route == "/health":
        return (_enum(payload.get("status"), ("ok",))
                and _enum(payload.get("service"), ("eth-intelligence",))
                and type(payload.get("version")) is str
                and bool(payload["version"].strip()))
    if route != "/market/status" or not _exact_keys(payload, STATUS_KEYS):
        return False
    if (not _denied(payload) or not _enum(payload["source"], ("hyperliquid",))
            or not _enum(payload["state"], ("cooldown", "ready_to_retry"))
            or any(not _nonnegative_int(payload[key]) for key in (
                "retry_after_seconds", "consecutive_rate_limits",
                "next_upstream_attempt_seconds", "cache_remaining_seconds"))
            or type(payload["cache_valid"]) is not bool
            or type(payload["upstream_request_allowed"]) is not bool):
        return False
    cooldown = payload["state"] == "cooldown"
    throttle = payload["throttle_reason"]
    if cooldown:
        if (not _enum(throttle, ("rate_limit",)) or payload["upstream_request_allowed"]
                or payload["cache_valid"] or payload["consecutive_rate_limits"] == 0):
            return False
    else:
        if payload["retry_after_seconds"] != 0:
            return False
        if throttle is not None and not _enum(throttle, ("request_interval",)):
            return False
        if payload["upstream_request_allowed"] != (throttle is None):
            return False
        if throttle is None and payload["next_upstream_attempt_seconds"] != 0:
            return False
    if not payload["cache_valid"] and payload["cache_remaining_seconds"] != 0:
        return False
    rest = payload["rest_operational_freshness"]
    if not _verify_rest(rest, cache_valid=payload["cache_valid"], cooldown=cooldown):
        return False
    # The consecutive 429 counter is reset only by a successful fresh fetch.
    # Expiry of the cooldown alone cannot restore the discarded receipt.
    if payload["consecutive_rate_limits"] > 0 and rest["state"] != "NO_DATA":
        return False
    return True


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError("non-finite JSON constant")


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
            payload = json.loads(chunk.decode("utf-8", errors="strict"),
                                 object_pairs_hook=_unique_json_object,
                                 parse_constant=_reject_json_constant)
            if not verify_payload(route, payload):
                raise ValueError("unacceptable or untrusted diagnostics")
        except (HTTPError, URLError, UnicodeError, ValueError, TimeoutError, OSError, RecursionError):
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
