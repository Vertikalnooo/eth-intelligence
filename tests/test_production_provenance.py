"""Regression tests: a successful HTTP fetch is not timestamp provenance.

These tests deliberately do not contact Hyperliquid and must never authorize trades.
"""
import unittest
from unittest.mock import patch
import legacy_market_fixture as app


class ProductionProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.payload = [
            {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
            [{"markPx": "100"}, {"markPx": "2500.25"}],
        ]

    def test_fetch_time_is_not_exchange_tick(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, *args):
                import json
                return json.dumps(self_payload).encode()
        self_payload = self.payload
        market = app.eth_market(fetch=lambda request, timeout: Response())
        self.assertEqual(market["mark_price"], "2500.25")
        self.assertIsNone(market["exchange_timestamp"])
        self.assertFalse(market["exchange_freshness_verified"])
        self.assertFalse(market["mark_price_freshness_verified"])
        self.assertFalse(market["recommendation_eligible"])
        self.assertEqual(
            app.assess_market_recommendation(market)["recommendation_block_reason"],
            "missing_exchange_timestamp",
        )

    def test_unverified_timestamp_is_blocked(self):
        market = {"exchange_timestamp": 1795180000000,
                  "exchange_freshness_verified": False,
                  "mark_price_freshness_verified": True}
        self.assertEqual(app.assess_market_recommendation(market)["recommendation_block_reason"],
                         "unverified_exchange_freshness")

    def test_mark_provenance_missing_is_blocked_even_if_flags_true(self):
        market = {"exchange_timestamp": 1795180000000,
                  "exchange_freshness_verified": True,
                  "mark_price_freshness_verified": True}
        result = app.assess_market_recommendation(market)
        self.assertFalse(result["recommendation_eligible"])
        self.assertEqual(result["recommendation_block_reason"],
                         "mark_price_provenance_not_implemented")

    def test_boolean_timestamp_does_not_pass(self):
        market = {"exchange_timestamp": True,
                  "exchange_freshness_verified": True,
                  "mark_price_freshness_verified": True}
        self.assertEqual(app.assess_market_recommendation(market)["recommendation_block_reason"],
                         "missing_exchange_timestamp")


if __name__ == "__main__":
    unittest.main()
