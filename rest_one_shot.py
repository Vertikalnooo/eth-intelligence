"""Stage 2 research-only, explicit one-shot REST transport. No startup I/O.

The trusted operator must provision a durable, non-replaceable ledger
separately. There is deliberately no grant creation, CLI, or app.py import.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import http.client
import json
from pathlib import Path
import re
import sqlite3
import ssl
import time

import rest_operational
from freshness import valid_mark_price

HOST = "api.hyperliquid.xyz"
PORT = 443
PATH = "/info"
BODY = b'{"type":"metaAndAssetCtxs"}'
SCOPE = "hyperliquid:ETH-PERP:mark:metaAndAssetCtxs:v1"
MAX_BYTES = 2 * 1024 * 1024
IO_TIMEOUT = 8.0
CACHE_NS = 30_000_000_000
APPROVAL_ID = re.compile(r"[0-9a-f]{32}")

# Schema for offline fixtures and a future separately reviewed operator tool.
# The executor NEVER creates a ledger or inserts a grant.
LEDGER_SCHEMA = """CREATE TABLE approvals (
    id TEXT PRIMARY KEY NOT NULL,
    scope TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL CHECK(attempts IN (0,1)),
    consumed_at INTEGER,
    CHECK((attempts=0 AND consumed_at IS NULL) OR
          (attempts=1 AND consumed_at IS NOT NULL))
)"""


class OneShotError(Exception):
    """Safe, non-retryable failure for this authorization."""


@dataclass(frozen=True)
class OneShotGuard:
    ledger: Path
    approval_id: str

    def _connect(self):
        if (type(self.approval_id) is not str or
                APPROVAL_ID.fullmatch(self.approval_id) is None):
            raise OneShotError("invalid_approval_id")
        if not isinstance(self.ledger, Path) or not self.ledger.is_absolute():
            raise OneShotError("absolute_ledger_required")
        # mode=rw, not r/w/create; a missing file is a denial.
        conn = sqlite3.connect(self.ledger.as_uri() + "?mode=rw",
                               uri=True, timeout=2, isolation_level=None)
        return conn

    def consume(self):
        """Durably reserve exactly one attempt, before any transport call.

        Failure/unknown commit outcome means stop, never retry automatically.
        The store is a trusted single host persistence boundary, not a
        distributed or adversarially rollback-resistant authorization service.
        """
        conn = None
        try:
            conn = self._connect()
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            row = conn.execute(
                "SELECT scope, expires_at, attempts, consumed_at FROM approvals WHERE id=?",
                (self.approval_id,)).fetchone()
            if (row is None or row[0] != SCOPE or type(row[1]) is not int
                    or row[1] <= now or type(row[2]) is not int
                    or row[2] != 0 or row[3] is not None):
                raise OneShotError("approval_missing_expired_or_spent")
            changed = conn.execute(
                "UPDATE approvals SET attempts=1, consumed_at=? "
                "WHERE id=? AND scope=? AND attempts=0 AND consumed_at IS NULL",
                (now, self.approval_id, SCOPE)).rowcount
            if changed != 1:
                raise OneShotError("approval_not_unique")
            conn.commit()
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise OneShotError("ledger_unavailable") from exc
        finally:
            if conn is not None:
                conn.close()

    def attempt_count(self):
        conn = None
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT scope, attempts, consumed_at FROM approvals WHERE id=?",
                (self.approval_id,)).fetchone()
            if (row is None or row[0] != SCOPE or type(row[1]) is not int
                    or row[1] not in (0, 1)
                    or (row[1] == 0 and row[2] is not None)
                    or (row[1] == 1 and type(row[2]) is not int)):
                raise OneShotError("invalid_attempt_record")
            return row[1]
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise OneShotError("ledger_unavailable") from exc
        finally:
            if conn is not None:
                conn.close()


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise OneShotError("duplicate_json_key")
        out[key] = value
    return out


def _reject_constant(value):
    raise OneShotError("nonfinite_json_constant")


def _decode(body):
    try:
        return json.loads(body.decode("utf-8", errors="strict"),
                          object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant,
                          parse_float=Decimal)
    except (UnicodeError, ValueError, RecursionError, InvalidOperation) as exc:
        raise OneShotError("invalid_json") from exc


def _market(payload):
    if type(payload) is not list or len(payload) != 2:
        raise OneShotError("invalid_payload")
    metadata, contexts = payload
    universe = metadata.get("universe") if type(metadata) is dict else None
    if (type(universe) is not list or type(contexts) is not list
            or len(universe) != len(contexts)
            or any(type(item) is not dict or type(item.get("name")) is not str
                   for item in universe)):
        raise OneShotError("invalid_universe")
    indices = [i for i, item in enumerate(universe) if item["name"] == "ETH"]
    if len(indices) != 1 or type(contexts[indices[0]]) is not dict:
        raise OneShotError("invalid_eth_context")
    mark = contexts[indices[0]].get("markPx")
    if (type(mark) is not str or not 0 < len(mark) <= 160
            or not mark.isascii() or mark != mark.strip()):
        raise OneShotError("invalid_mark")
    try:
        price = Decimal(mark)
    except InvalidOperation as exc:
        raise OneShotError("invalid_mark") from exc
    if not valid_mark_price(price):
        raise OneShotError("invalid_mark")
    return {"symbol": "ETH/USDC", "market_type": "perpetual",
            "source": "hyperliquid", "price_type": "mark",
            "mark_price": mark, "exchange_timestamp": None,
            "exchange_freshness_verified": False,
            "mark_price_freshness_verified": False,
            "recommendation_eligible": False}


def _exchange_once(connection):
    """Single fixed POST. http.client has no redirect/retry policy.

    Private transport seam used with a loopback fixture in offline tests.
    Socket timeout and elapsed checks are NOT a hard wall-clock deadline
    (DNS, header parsing, and individual blocking reads can overrun).
    """
    deadline = time.monotonic() + IO_TIMEOUT

    def check_time():
        if time.monotonic() >= deadline:
            raise OneShotError("elapsed_budget_exceeded")

    try:
        connection.request("POST", PATH, body=BODY, headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Accept-Encoding": "identity", "Connection": "close",
            "User-Agent": "eth-intelligence-research/0.1"})
        check_time()
        response = connection.getresponse()
        with response:
            check_time()
            if response.status != 200:
                raise OneShotError("upstream_http_" + str(response.status))
            ct = response.headers.get_all("Content-Type", [])
            ce = response.headers.get_all("Content-Encoding", [])
            cl = response.headers.get_all("Content-Length", [])
            te = response.headers.get_all("Transfer-Encoding", [])
            if (len(ct) != 1 or ct[0].split(";", 1)[0].strip().lower()
                    != "application/json"):
                raise OneShotError("unexpected_content_type")
            if ce and (len(ce) != 1 or ce[0].strip().lower() != "identity"):
                raise OneShotError("unexpected_content_encoding")
            if len(cl) > 1 or len(te) > 1 or (cl and te):
                raise OneShotError("ambiguous_framing")
            if te and te[0].strip().lower() != "chunked":
                raise OneShotError("unsupported_transfer_encoding")
            if not cl and not te:
                raise OneShotError("missing_framing")
            expected = None
            if cl:
                if re.fullmatch(r"[0-9]{1,12}", cl[0]) is None:
                    raise OneShotError("invalid_content_length")
                expected = int(cl[0])
                if expected > MAX_BYTES:
                    raise OneShotError("response_too_large")
            data = bytearray()
            while True:
                check_time()
                part = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
                check_time()
                if not part:
                    break
                data.extend(part)
                if len(data) > MAX_BYTES:
                    raise OneShotError("response_too_large")
            if expected is not None and len(data) != expected:
                raise OneShotError("truncated_response")
            return _market(_decode(bytes(data)))
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise OneShotError("transport_failure") from exc
    finally:
        connection.close()


@dataclass(frozen=True)
class OneShotObservation:
    approval_id: str
    receipt: rest_operational.RestReceipt
    received_ns: int
    mark_price: str


def run_one_shot(guard):
    """Retired unsupervised path. No live execution is authorized."""
    raise OneShotError("legacy_execution_disabled")


def _run_legacy_fixture(guard, connection_factory):
    """Private regression fixture seam; no default network transport."""
    if type(guard) is not OneShotGuard:
        raise OneShotError("invalid_guard")
    guard.consume()
    start = time.monotonic_ns()
    try:
        connection = connection_factory(
            HOST, PORT, timeout=IO_TIMEOUT, context=ssl.create_default_context())
    except (OSError, ValueError) as exc:
        raise OneShotError("transport_setup_failure") from exc
    market = _exchange_once(connection)
    end = time.monotonic_ns()
    receipt = rest_operational.capture(market, start, end)
    if receipt.observation is None:
        raise OneShotError("invalid_receipt")
    return OneShotObservation(guard.approval_id, receipt, end, market["mark_price"])


@dataclass(frozen=True)
class LifecycleSample:
    at_ns: int
    cache_valid: bool
    diagnostic: dict


def sample_lifecycle(observation, now_ns=None):
    """Inspect a single local receipt; zero network and no cache refresh."""
    now = time.monotonic_ns() if now_ns is None else now_ns
    if type(observation) is not OneShotObservation or type(now) is not int:
        raise OneShotError("invalid_sample")
    if (type(observation.received_ns) is not int
            or type(observation.receipt) is not rest_operational.RestReceipt
            or observation.receipt.observation is None
            or observation.receipt.observation.received_monotonic_ns != observation.received_ns
            or type(observation.mark_price) is not str
            or str(observation.receipt.observation.mark_px) != str(Decimal(observation.mark_price))
            or now < observation.received_ns):
        raise OneShotError("invalid_sample_clock_or_receipt")
    valid = now - observation.received_ns < CACHE_NS
    return LifecycleSample(now, valid, rest_operational.inspect(
        observation.receipt, now, cache_valid=valid))


def verify_lifecycle(guard, observation, samples):
    """Require ordered FRESH, DEGRADED, STALE (valid and expired cache).

    Only trusted local observations; not authenticated external evidence.
    Missing a window fails the report, never causes another request.
    """
    if (type(guard) is not OneShotGuard or type(observation) is not OneShotObservation
            or guard.approval_id != observation.approval_id
            or guard.attempt_count() != 1
            or type(samples) not in (tuple, list) or len(samples) != 4):
        raise OneShotError("invalid_lifecycle_evidence")
    expected_states = (("FRESH", True), ("DEGRADED", True),
                       ("STALE", True), ("STALE", False))
    previous = observation.received_ns - 1
    for sample, (state, valid) in zip(samples, expected_states):
        if (type(sample) is not LifecycleSample or type(sample.at_ns) is not int
                or sample.at_ns <= previous or type(sample.cache_valid) is not bool
                or sample.cache_valid is not valid or type(sample.diagnostic) is not dict):
            raise OneShotError("invalid_lifecycle_sample")
        expected = sample_lifecycle(observation, sample.at_ns)
        # JSON representation distinguishes false from 0, true from 1.
        if (expected.cache_valid is not valid or expected.diagnostic["state"] != state
                or json.dumps(sample.diagnostic, sort_keys=True, allow_nan=False)
                != json.dumps(expected.diagnostic, sort_keys=True, allow_nan=False)):
            raise OneShotError("lifecycle_mismatch")
        previous = sample.at_ns
    return {"status": "PASS", "scope": "local_receipt_only",
            "attempt_reservations": 1, "exchange_freshness_verified": False,
            "recommendation_eligible": False, "stage2_complete": False}


def collect_lifecycle(guard, observation):
    """Bounded four-sample local collector; never calls the network.

    Schedule against the same receipt. Actual observed monotonic times,
    not scheduled times, are checked. Missed windows are failures.
    """
    if (type(guard) is not OneShotGuard or type(observation) is not OneShotObservation
            or guard.approval_id != observation.approval_id or guard.attempt_count() != 1):
        raise OneShotError("invalid_lifecycle_evidence")
    samples = []
    for delta in (0, 3_000_000_000, 6_000_000_000, 31_000_000_000):
        remaining = observation.received_ns + delta - time.monotonic_ns()
        if remaining > 0:
            time.sleep(remaining / 1_000_000_000)
        samples.append(sample_lifecycle(observation))
    return verify_lifecycle(guard, observation, samples), tuple(samples)
