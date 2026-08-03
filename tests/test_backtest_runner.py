import unittest

from aurum_bot.backtest_runner import _quantize_exit_legs


def leg(fraction, reason):
    return {"fraction": fraction, "price": 100.0, "reason": reason}


class QuantizedExitTests(unittest.TestCase):
    def test_one_minimum_lot_cannot_be_split(self):
        result = _quantize_exit_legs(
            [leg(0.5, "tp2"), leg(0.5, "tp4")],
            total_volume=0.01,
            volume_min=0.01,
            volume_step=0.01,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["reason"], "tp4")
        self.assertEqual(result[0]["executed_volume"], 0.01)

    def test_three_units_execute_40_60_as_one_and_two_units(self):
        result = _quantize_exit_legs(
            [leg(0.4, "tp2"), leg(0.6, "tp4")],
            total_volume=0.03,
            volume_min=0.01,
            volume_step=0.01,
        )
        self.assertEqual(
            [item["executed_volume"] for item in result],
            [0.01, 0.02],
        )

    def test_cumulative_rounding_keeps_equal_four_way_plan_executable(self):
        result = _quantize_exit_legs(
            [
                leg(0.25, "tp1"),
                leg(0.25, "tp2"),
                leg(0.25, "tp3"),
                leg(0.25, "tp4"),
            ],
            total_volume=0.02,
            volume_min=0.01,
            volume_step=0.01,
        )
        self.assertEqual([item["reason"] for item in result], ["tp2", "tp4"])
        self.assertEqual(
            [item["executed_volume"] for item in result],
            [0.01, 0.01],
        )


if __name__ == "__main__":
    unittest.main()
