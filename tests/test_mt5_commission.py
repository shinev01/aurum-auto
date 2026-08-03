import unittest
from types import SimpleNamespace

from aurum_bot.mt5_commission import (
    DEFAULT_COMMISSION_PER_LOT_USD,
    infer_round_turn_commission_per_lot,
)


class FakeMT5:
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_OUT_BY = 3

    def __init__(self, deals):
        self.deals = deals

    def history_deals_get(self, start, end, *, group):
        return tuple(self.deals)


def deal(position_id, entry, volume, commission=0, fee=0, symbol="GOLD"):
    return SimpleNamespace(
        position_id=position_id,
        entry=entry,
        volume=volume,
        commission=commission,
        fee=fee,
        symbol=symbol,
    )


class MT5CommissionTests(unittest.TestCase):
    def test_unknown_commission_default_is_seven_usd_per_lot(self):
        self.assertEqual(DEFAULT_COMMISSION_PER_LOT_USD, 7.0)

    def test_infers_median_full_position_cost_per_entry_lot(self):
        mt5 = FakeMT5(
            [
                deal(1, 0, 0.5, -1.75),
                deal(1, 1, 0.5, -1.75),
                deal(2, 0, 1.0, -3.5),
                deal(2, 1, 1.0, -3.5),
            ]
        )
        self.assertEqual(infer_round_turn_commission_per_lot(mt5, "GOLD"), 7)

    def test_ignores_open_and_other_symbol_positions(self):
        mt5 = FakeMT5(
            [
                deal(1, 0, 1.0, -3.5),
                deal(2, 0, 1.0, -3.5, symbol="SILVER"),
                deal(2, 1, 1.0, -3.5, symbol="SILVER"),
            ]
        )
        self.assertIsNone(infer_round_turn_commission_per_lot(mt5, "GOLD"))


if __name__ == "__main__":
    unittest.main()
