import unittest

from aurum_bot.pending_monitor_worker import monitor


class PendingMonitorCompatibilityTests(unittest.TestCase):
    def test_monitor_never_cancels_pending_orders(self):
        result = monitor(
            {
                "account": {"name": "fxpro_demo"},
                "magic_number": 397897,
            }
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["events"], [])


if __name__ == "__main__":
    unittest.main()
