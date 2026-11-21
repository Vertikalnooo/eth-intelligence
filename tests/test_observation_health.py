"""Deterministic offline tests; no network and no production integration."""
import unittest
from observation_health import ObservationHealth, ObservationPolicy

class ObservationHealthTests(unittest.TestCase):
    def setUp(self):
        self.monitor = ObservationHealth(ObservationPolicy(degraded_after=5, stale_after=15, max_source_gap=10))

    def assert_blocked(self, result):
        self.assertFalse(result["exchange_timestamp_verified"])
        self.assertFalse(result["mark_price_freshness_verified"])
        self.assertFalse(result["recommendation_eligible"])
        self.assertIsNone(result["exchange_timestamp"])

    def test_disconnected_and_unknown(self):
        self.assertEqual(self.monitor.evaluate(20)["observation_health"], "disconnected")
        self.monitor.connect()
        self.assertEqual(self.monitor.evaluate(20)["observation_health"], "unknown")

    def test_healthy_is_not_verified_freshness(self):
        self.monitor.connect()
        self.monitor.websocket_observation("2000", 10)
        result = self.monitor.evaluate(11)
        self.assertEqual(result["observation_health"], "healthy")
        self.assert_blocked(result)

    def test_delayed_and_stale(self):
        self.monitor.connect()
        self.monitor.websocket_observation("2000", 10)
        self.assertEqual(self.monitor.evaluate(16)["observation_health"], "degraded")
        self.assertEqual(self.monitor.evaluate(26)["observation_health"], "stale")
        self.assert_blocked(self.monitor.evaluate(26))

    def test_reconnect_clears_observation(self):
        self.monitor.connect()
        self.monitor.websocket_observation("2000", 10)
        self.monitor.disconnect()
        self.monitor.connect()
        self.assertEqual(self.monitor.evaluate(11)["observation_health"], "unknown")

    def test_rest_disagreement_only_degrades(self):
        self.monitor.connect()
        self.monitor.websocket_observation("2000", 10)
        self.monitor.rest_observation("2001", 10)
        result = self.monitor.evaluate(11)
        self.assertEqual(result["observation_health"], "degraded")
        self.assertEqual(result["observation_reason"], "asynchronous_source_disagreement")
        self.assert_blocked(result)

    def test_old_rest_disagreement_not_proof(self):
        self.monitor.rest_observation("2001", 0)
        self.monitor.connect()
        self.monitor.websocket_observation("2000", 20)
        self.assertEqual(self.monitor.evaluate(21)["observation_health"], "healthy")

    def test_clock_rollback_fails_closed(self):
        self.monitor.connect()
        self.monitor.websocket_observation("2000", 10)
        self.assertEqual(self.monitor.evaluate(9)["observation_health"], "degraded")
        with self.assertRaises(ValueError):
            self.monitor.websocket_observation("2000", 9)
        self.assertEqual(self.monitor.evaluate(11)["observation_health"], "degraded")

    def test_invalid_values_rejected(self):
        self.monitor.connect()
        for price in ("NaN", "Infinity", "-1", "0", "oops", None):
            with self.subTest(price=price), self.assertRaises(ValueError):
                self.monitor.websocket_observation(price, 10)
        for timestamp in (float("nan"), float("inf"), -1, True):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                self.monitor.websocket_observation("2000", timestamp)

    def test_recovery_after_stale(self):
        self.monitor.connect()
        self.monitor.websocket_observation("2000", 10)
        self.assertEqual(self.monitor.evaluate(30)["observation_health"], "stale")
        self.monitor.websocket_observation("2001", 30)
        self.assertEqual(self.monitor.evaluate(31)["observation_health"], "healthy")
        self.assert_blocked(self.monitor.evaluate(31))

    def test_policy_validation(self):
        for kwargs in ({"degraded_after": 10, "stale_after": 5},
                       {"degraded_after": 0},
                       {"max_source_gap": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ObservationPolicy(**kwargs)

if __name__ == "__main__":
    unittest.main()
