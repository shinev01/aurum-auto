import unittest
from types import SimpleNamespace

from aurum_bot.models import AccountConfig
from aurum_bot.mt5_journal_worker import build_snapshots


class FakeMT5:
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_OUT_BY = 3
    DEAL_TYPE_BUY = 0
    DEAL_TYPE_SELL = 1


def deal(**overrides):
    defaults = {
        "position_id": 44,
        "magic": 397897,
        "comment": "AURUM:123",
        "entry": 0,
        "type": 0,
        "symbol": "GOLD",
        "volume": 0.1,
        "price": 2400.0,
        "time": 1_720_000_000,
        "time_msc": 1_720_000_000_000,
        "commission": -0.5,
        "fee": 0.0,
        "swap": 0.0,
        "profit": 0.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class MT5JournalWorkerTests(unittest.TestCase):
    def test_close_deal_without_magic_is_still_included(self):
        account = AccountConfig(
            name="fxpro_demo510",
            enabled=True,
            risk_base_usd=1000,
            terminal_path="terminal64.exe",
            symbols={"XAUUSD": "GOLD", "DE40": "#Germany40"},
        )
        snapshots = build_snapshots(
            account=account,
            deals=[
                deal(),
                deal(
                    magic=0,
                    comment="",
                    entry=FakeMT5.DEAL_ENTRY_OUT,
                    price=2420,
                    time_msc=1_720_003_600_000,
                    commission=-0.5,
                    profit=20,
                ),
            ],
            open_position_ids=set(),
            magic=397897,
            mt5=FakeMT5,
        )

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["message_id"], 123)
        self.assertEqual(snapshots[0]["symbol"], "XAUUSD")
        self.assertEqual(snapshots[0]["status"], "CLOSED")
        self.assertEqual(snapshots[0]["gross_pnl"], 20)
        self.assertEqual(snapshots[0]["commission"], -1)


if __name__ == "__main__":
    unittest.main()
