"""Offline fail-closed tests for Hyperliquid l2Book timestamp freshness.

Run: python -m unittest discover -s tests -p 'test_exchange_timestamp_guard.py' -v
This test does not contact Hyperliquid and never enables trade recommendations.
"""
import unittest

MAX_AGE_MS = 10_000
MAX_FUTURE_SKEW_MS = 1_000


def assess_l2book_freshness(message, received_at_ms, connected, mark_verified=False):
    """Assess transport freshness; l2Book freshness NEVER verifies mark price."""
    result = {
        "exchange_freshness_verified": False,
        "mark_price_freshness_verified": False,
        "recommendation_eligible": False,
        "status": "unknown",
    }
    if not connected:
        result["status"] = "disconnected"
        return result
    if not isinstance(message, dict) or message.get("channel") != "l2Book":
        result["status"] = "invalid_message"
        return result
    data = message.get("data")
    if not isinstance(data, dict) or data.get("coin") != "ETH":
        result["status"] = "invalid_message"
        return result
    timestamp = data.get("time")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
        result["status"] = "missing_or_invalid_timestamp"
        return result
    if isinstance(received_at_ms, bool) or not isinstance(received_at_ms, int):
        result["status"] = "invalid_receive_time"
        return result
    age = received_at_ms - timestamp
    if age < -MAX_FUTURE_SKEW_MS:
        result["status"] = "future_timestamp"
    elif age < 0:
        result["status"] = "clock_skew"
    elif age > MAX_AGE_MS:
        result["status"] = "stale"
    else:
        result["status"] = "fresh_l2book_only"
        result["exchange_freshness_verified"] = True
    # Even when mark_verified is supplied, this isolated probe is NOT an
    # authorization to recommend trades. Mark provenance needs separate proof.
    return result


class ExchangeTimestampGuardTests(unittest.TestCase):
    NOW = 1_800_000_000_000

    def msg(self, timestamp):
        return {"channel": "l2Book", "data": {"coin": "ETH", "time": timestamp, "levels": [[], []]}}

    def test_fresh_book_not_mark(self):
        r = assess_l2book_freshness(self.msg(self.NOW - 500), self.NOW, True)
        self.assertEqual(r["status"], "fresh_l2book_only")
        self.assertTrue(r["exchange_freshness_verified"])
        self.assertFalse(r["recommendation_eligible"])
        self.assertFalse(r["mark_price_freshness_verified"])

    def test_stale(self):
        self.assertEqual(assess_l2book_freshness(self.msg(self.NOW - 10_001), self.NOW, True)["status"], "stale")

    def test_missing_timestamp(self):
        self.assertEqual(assess_l2book_freshness(self.msg(None), self.NOW, True)["status"], "missing_or_invalid_timestamp")

    def test_disconnected(self):
        self.assertEqual(assess_l2book_freshness(self.msg(self.NOW), self.NOW, False)["status"], "disconnected")

    def test_future_timestamp(self):
        self.assertEqual(assess_l2book_freshness(self.msg(self.NOW + 2_000), self.NOW, True)["status"], "future_timestamp")

    def test_wrong_market(self):
        m = self.msg(self.NOW)
        m["data"]["coin"] = "BTC"
        self.assertEqual(assess_l2book_freshness(m, self.NOW, True)["status"], "invalid_message")

    def test_boundary(self):
        self.assertEqual(assess_l2book_freshness(self.msg(self.NOW - 10_000), self.NOW, True)["status"], "fresh_l2book_only")

    def test_reconnect_requires_new_message(self):
        stale = self.msg(self.NOW - 20_000)
        self.assertFalse(assess_l2book_freshness(stale, self.NOW, True)["exchange_freshness_verified"])
        self.assertFalse(assess_l2book_freshness(stale, self.NOW, False)["recommendation_eligible"])
        fresh = self.msg(self.NOW + 100)
        self.assertEqual(assess_l2book_freshness(fresh, self.NOW + 200, True)["status"], "fresh_l2book_only")

    def test_never_authorizes_trade_even_with_mark_flag(self):
        r = assess_l2book_freshness(self.msg(self.NOW), self.NOW, True, mark_verified=True)
        self.assertFalse(r["recommendation_eligible"])


if __name__ == "__main__":
    unittest.main()
