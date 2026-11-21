"""Opt-in one-shot WS diagnostic: only safe aggregate output, no trading.

No network activity on import. The live connection is created only by main(),
which is exclusively called by a separately authorized manual workflow.
"""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observation_adapter import ETHObservationAdapter
from ws_observer import WSObserver

SESSION_SECONDS = 30.0
MAX_ATTEMPTS = 1


class CountingAdapter(ETHObservationAdapter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._counts_lock = threading.Lock()
        self._count = 0

    def ingest_ws(self, message, generation):
        accepted = super().ingest_ws(message, generation)
        if accepted:
            with self._counts_lock:
                self._count += 1
        return accepted

    def count(self):
        with self._counts_lock:
            return self._count


def collect(observer_factory=WSObserver, adapter_factory=CountingAdapter):
    """Read-only diagnostic with no price, payload, secrets, or exchange claims."""
    adapter = adapter_factory()
    observer = observer_factory(adapter=adapter, max_attempts=MAX_ATTEMPTS,
                                max_session_seconds=SESSION_SECONDS)
    observer.run()
    diagnostic = observer.diagnostics()
    count = adapter.count()
    report = {
        "probe": "read_only_eth_ws_one_shot",
        "symbol": "ETH",
        "subscription": "activeAssetCtx",
        "session_limit_seconds": 30,
        "connection_attempt_limit": 1,
        "accepted_eth_observations": count,
        "ws_result": "observed" if count else "no_observation",
        "last_error": diagnostic["last_error"],
        "exchange_timestamp": None,
        "exchange_timestamp_verified": False,
        "mark_price_freshness_verified": False,
        "recommendation_eligible": False,
    }
    if not validate_report(report):
        raise ValueError("invalid_diagnostic_report")
    return report


def validate_report(report):
    return (
        isinstance(report, dict)
        and report.get("probe") == "read_only_eth_ws_one_shot"
        and report.get("symbol") == "ETH"
        and report.get("subscription") == "activeAssetCtx"
        and report.get("session_limit_seconds") == 30
        and report.get("connection_attempt_limit") == 1
        and isinstance(report.get("accepted_eth_observations"), int)
        and not isinstance(report["accepted_eth_observations"], bool)
        and report["accepted_eth_observations"] >= 0
        and report.get("ws_result") in ("observed", "no_observation")
        and (report["ws_result"] == "observed") == (report["accepted_eth_observations"] > 0)
        and report.get("last_error") in (None, "timeout", "invalid_data",
            "frame_guard_unavailable", "transport_error", "close_error")
        and report.get("exchange_timestamp") is None
        and report.get("exchange_timestamp_verified") is False
        and report.get("mark_price_freshness_verified") is False
        and report.get("recommendation_eligible") is False
        and set(report) == {
            "probe", "symbol", "subscription", "session_limit_seconds",
            "connection_attempt_limit", "accepted_eth_observations",
            "ws_result", "last_error", "exchange_timestamp",
            "exchange_timestamp_verified", "mark_price_freshness_verified",
            "recommendation_eligible",
        }
    )


def main():
    report = collect()
    print(json.dumps(report, sort_keys=True))
    if report["ws_result"] != "observed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
