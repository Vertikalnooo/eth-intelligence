"""Offline deterministic regression tests for isolated observation monitor."""
import threading
import unittest
from observation_health import ObservationHealth, ObservationPolicy


class ObservationHealthTests(unittest.TestCase):
    def setUp(self):
        self.monitor = ObservationHealth(ObservationPolicy(
            degraded_after=5, stale_after=15, max_source_gap=10,
            relative_price_tolerance="0.001"))

    def blocked(self, result):
        self.assertIsNone(result["exchange_timestamp"])
        self.assertFalse(result["exchange_timestamp_verified"])
        self.assertFalse(result["mark_price_freshness_verified"])
        self.assertFalse(result["recommendation_eligible"])

    def both(self, ws="2000", rest="2000", at=10):
        self.monitor.connect()
        self.monitor.websocket_observation(ws, at)
        self.monitor.rest_observation(rest, at)

    def test_disconnected_and_unknown(self):
        self.assertEqual(self.monitor.evaluate(20)["ws_health"], "disconnected")
        self.monitor.connect()
        self.assertEqual(self.monitor.evaluate(20)["overall_health"], "unknown")

    def test_independent_source_health(self):
        self.monitor.connect()
        self.monitor.websocket_observation("2000", 10)
        r = self.monitor.evaluate(11)
        self.assertEqual(r["ws_health"], "healthy")
        self.assertEqual(r["rest_health"], "unknown")
        self.assertEqual(r["overall_health"], "unknown")
        self.blocked(r)

    def test_both_healthy_never_authorize(self):
        self.both()
        r = self.monitor.evaluate(11)
        self.assertEqual(r["overall_health"], "healthy")
        self.blocked(r)

    def test_delay_stale_and_boundaries(self):
        self.both()
        self.assertEqual(self.monitor.evaluate(15)["overall_health"], "healthy")
        self.assertEqual(self.monitor.evaluate(25)["overall_health"], "degraded")
        self.assertEqual(self.monitor.evaluate(26)["overall_health"], "stale")
        self.blocked(self.monitor.evaluate(26))

    def test_rest_stops_while_ws_updates(self):
        self.both()
        self.monitor.websocket_observation("2000", 30)
        r = self.monitor.evaluate(31)
        self.assertEqual(r["ws_health"], "healthy")
        self.assertEqual(r["rest_health"], "stale")
        self.assertEqual(r["overall_health"], "stale")

    def test_reconnect_resets_both_sources(self):
        self.both(ws="2000", rest="2200")
        self.monitor.disconnect()
        self.monitor.connect()
        self.monitor.websocket_observation("2000", 12)
        r = self.monitor.evaluate(13)
        self.assertEqual(r["rest_health"], "unknown")
        self.assertFalse(r["asynchronous_price_disagreement"])

    def test_relative_tolerance(self):
        self.both(ws="2000", rest="2001")
        self.assertEqual(self.monitor.evaluate(11)["overall_health"], "healthy")
        self.monitor.rest_observation("2010", 12)
        r = self.monitor.evaluate(12)
        self.assertEqual(r["overall_health"], "degraded")
        self.assertTrue(r["asynchronous_price_disagreement"])
        self.blocked(r)

    def test_old_rest_not_compared(self):
        self.both()
        self.monitor.websocket_observation("3000", 25)
        self.assertFalse(self.monitor.evaluate(26)["asynchronous_price_disagreement"])

    def test_bad_ws_observation_sticky_fault(self):
        self.both()
        with self.assertRaises(ValueError):
            self.monitor.websocket_observation("NaN", 11)
        self.assertEqual(self.monitor.evaluate(12)["ws_health"], "degraded")
        self.monitor.websocket_observation("2000", 12)
        self.assertEqual(self.monitor.evaluate(12)["ws_health"], "degraded")
        self.monitor.connect()
        self.assertEqual(self.monitor.evaluate(12)["ws_health"], "unknown")

    def test_bad_rest_observation_sticky_fault(self):
        self.both()
        with self.assertRaises(ValueError):
            self.monitor.rest_observation("oops", 11)
        self.assertEqual(self.monitor.evaluate(12)["rest_health"], "degraded")
        self.blocked(self.monitor.evaluate(12))

    def test_clock_rollback(self):
        self.both()
        self.assertEqual(self.monitor.evaluate(9)["overall_health"], "degraded")
        with self.assertRaises(ValueError):
            self.monitor.websocket_observation("2000", 9)
        self.assertEqual(self.monitor.evaluate(11)["ws_health"], "degraded")

    def test_recovery_from_stale(self):
        self.both()
        self.assertEqual(self.monitor.evaluate(30)["overall_health"], "stale")
        self.monitor.websocket_observation("2001", 30)
        self.monitor.rest_observation("2001", 30)
        self.assertEqual(self.monitor.evaluate(31)["overall_health"], "healthy")
        self.blocked(self.monitor.evaluate(31))

    def test_invalid_prices_and_times(self):
        self.monitor.connect()
        for price in ("NaN", "Infinity", "-1", "0", "oops", None):
            with self.subTest(price=price), self.assertRaises(ValueError):
                self.monitor.websocket_observation(price, 10)
        for timestamp in (float("nan"), float("inf"), -1, True):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                self.monitor.rest_observation("2000", timestamp)

    def test_policy_validation(self):
        for kwargs in ({"degraded_after": 10, "stale_after": 5},
                       {"degraded_after": 0},
                       {"max_source_gap": float("nan")},
                       {"relative_price_tolerance": "-0.1"},
                       {"relative_price_tolerance": "NaN"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ObservationPolicy(**kwargs)

    def test_concurrent_reads_and_updates(self):
        self.both()
        errors = []
        def write():
            try:
                for i in range(100):
                    self.monitor.websocket_observation("2000", 10 + i)
                    self.monitor.rest_observation("2000", 10 + i)
            except Exception as exc:
                errors.append(exc)
        def read():
            try:
                for i in range(100):
                    self.blocked(self.monitor.evaluate(10 + i))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=write), threading.Thread(target=read)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
