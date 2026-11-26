"""Offline regression tests for cache, disconnect, reconnect, and recovery safety.

These tests never call Hyperliquid or authorize trading. Production remains unchanged.
"""
import unittest
from dataclasses import replace
from unittest.mock import patch

import legacy_market_fixture as app
from freshness_guard import FreshnessGuard, MarketObservation

NOW_MS = 1_800_000_000_000
NOW_MONO = 1000.0
OBS = MarketObservation("1700", NOW_MS - 1000, NOW_MONO - 1, True)


class FailClosedLifecycleTests(unittest.TestCase):
    def setUp(self):
        with app._market_lock:
            app._market_cache = None
            app._market_cache_until = 0.0
            app._rate_limit_until = 0.0
            app._next_upstream_attempt = 0.0
            app._consecutive_429 = 0

    def tearDown(self):
        with app._market_lock:
            app._market_cache = None
            app._market_cache_until = 0.0
            app._rate_limit_until = 0.0
            app._next_upstream_attempt = 0.0
            app._consecutive_429 = 0

    def test_cached_market_never_authorizes_recommendations(self):
        sample = {"symbol": "ETH/USDC", "mark_price": "1700",
                  "exchange_timestamp": NOW_MS, "exchange_freshness_verified": True,
                  "mark_price_freshness_verified": True,
                  "recommendation_eligible": True}
        with patch.object(app, "eth_market", return_value=sample) as fetch:
            first = app.cached_eth_market()
            second = app.cached_eth_market()
        fetch.assert_called_once()
        for result in (first, second):
            self.assertFalse(result["recommendation_eligible"])
            self.assertEqual(result["recommendation_block_reason"],
                             "mark_price_provenance_not_implemented")

    def test_expired_cache_is_not_served_during_upstream_failure(self):
        with app._market_lock:
            app._market_cache = {"symbol": "ETH/USDC", "mark_price": "1700",
                                 "recommendation_eligible": True}
            app._market_cache_until = 1.0
        with patch.object(app, "eth_market", side_effect=TimeoutError("offline")):
            with self.assertRaises(TimeoutError):
                app.cached_eth_market()
        self.assertFalse(app.market_status()["recommendation_eligible"])

    def test_disconnect_blocks_and_reconnect_discards_old_observation(self):
        guard = FreshnessGuard()
        guard.connect()
        guard.ingest(OBS)
        guard.disconnect()
        disconnected = guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)
        self.assertEqual(disconnected["freshness_status"], "disconnected")
        self.assertFalse(disconnected["recommendation_eligible"])
        guard.connect()
        after_reconnect = guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)
        self.assertEqual(after_reconnect["freshness_status"], "no_data")
        self.assertFalse(after_reconnect["recommendation_eligible"])

    def test_recovery_requires_new_fresh_observation_but_still_blocks_trading(self):
        guard = FreshnessGuard()
        guard.connect()
        guard.ingest(replace(OBS, exchange_timestamp_ms=NOW_MS - 11000))
        stale = guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)
        self.assertEqual(stale["freshness_status"], "stale_exchange")
        guard.disconnect()
        guard.connect()
        guard.ingest(OBS)
        fresh = guard.evaluate(now_utc_ms=NOW_MS, now_monotonic=NOW_MONO)
        self.assertEqual(fresh["freshness_status"], "fresh")
        self.assertTrue(fresh["freshness_gate_passed"])
        self.assertFalse(fresh["recommendation_eligible"])


if __name__ == "__main__":
    unittest.main()
