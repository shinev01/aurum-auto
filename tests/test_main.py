import unittest

from aurum_bot.config import TradingConfig
from aurum_bot.main import _indicator_execution_enabled


class IndicatorExecutionTests(unittest.TestCase):
    def test_indicator_1_is_always_enabled(self):
        self.assertTrue(_indicator_execution_enabled(True, False))

    def test_indicator_2_follows_yaml_switch(self):
        self.assertFalse(_indicator_execution_enabled(False, False))
        self.assertTrue(_indicator_execution_enabled(False, True))

    def test_strict_call_entry_default_is_enabled(self):
        trading = TradingConfig(1, 0.9, 1.1, 0.01, 20, 3, 2, 397897)
        self.assertTrue(trading.strict_call_entry)
        self.assertFalse(trading.enable_indicator_2)


if __name__ == "__main__":
    unittest.main()
