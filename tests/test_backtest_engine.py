import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from aurum_bot.backtest_engine import (
    STRATEGIES,
    simulate_independent_strategy,
    simulate_strategy,
)
from aurum_bot.history_signals import HistoricalSignal, MOSCOW_TZ
from aurum_bot.models import Direction
from aurum_bot.mt5_history import TICK_DTYPE


BASE_TIME = datetime(2026, 7, 20, 7, 0, 0, tzinfo=timezone.utc)


def signal(message_id=1, seconds=0):
    timestamp = BASE_TIME + timedelta(seconds=seconds)
    return HistoricalSignal(
        message_id=message_id,
        symbol="XAUUSD",
        direction=Direction.LONG,
        timestamp_utc=timestamp,
        timestamp_moscow=timestamp.astimezone(MOSCOW_TZ),
        entry=100,
        stop_loss=90,
        take_profits=(105, 110, 115, 120),
        raw_text="test",
    )


def ticks(*rows):
    result = np.empty(len(rows), dtype=TICK_DTYPE)
    for index, (seconds, bid, ask) in enumerate(rows):
        result[index] = (
            int((BASE_TIME + timedelta(seconds=seconds)).timestamp() * 1000),
            bid,
            ask,
        )
    return result


class BacktestEngineTests(unittest.TestCase):
    def test_decision_uses_first_tick_after_400_milliseconds(self):
        records = simulate_strategy(
            [signal()],
            ticks((0.399, 98, 99), (0.401, 100, 101), (3, 105, 106)),
            STRATEGIES[0],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )
        self.assertEqual(records[0].decision_ask, 101)
        self.assertEqual(records[0].entry_delay_seconds, 0.401)
        self.assertEqual(records[0].order_kind, "market")
        self.assertEqual(records[0].exit_reason, "tp1")

    def test_exactly_ten_percent_better_quote_enters_at_market(self):
        records = simulate_strategy(
            [signal()],
            ticks((2, 98.9, 99), (3, 105, 106)),
            STRATEGIES[0],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
            max_market_risk_ratio=0.9,
        )
        self.assertEqual(records[0].order_kind, "market")
        self.assertEqual(records[0].entry_price, 99)
        self.assertEqual(records[0].exit_reason, "tp1")

    def test_better_quote_below_0_9r_places_stop(self):
        records = simulate_strategy(
            [signal()],
            ticks((2, 98.4, 98.5), (3, 99.9, 100), (4, 105, 106)),
            STRATEGIES[0],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )
        self.assertEqual(records[0].order_kind, "stop")
        self.assertEqual(records[0].entry_price, 100)
        self.assertEqual(records[0].exit_reason, "tp1")

    def test_equal_quote_at_1r_enters_at_market(self):
        records = simulate_strategy(
            [signal()],
            ticks((2, 99.9, 100), (3, 105, 106)),
            STRATEGIES[0],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )
        self.assertEqual(records[0].order_kind, "market")
        self.assertEqual(records[0].entry_price, 100)

    def test_too_worse_quote_places_limit_then_fills(self):
        records = simulate_strategy(
            [signal()],
            ticks((2, 101, 102), (3, 99, 100), (4, 105, 106)),
            STRATEGIES[0],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )
        self.assertEqual(records[0].order_kind, "limit")
        self.assertEqual(records[0].entry_price, 100)
        self.assertEqual(records[0].exit_reason, "tp1")

    def test_pending_limit_stays_active_when_tp1_arrives_before_entry(self):
        records = simulate_independent_strategy(
            [signal()],
            ticks(
                (2, 101, 102),
                (3, 105, 106),
                (4, 99, 100),
                (5, 89, 90),
            ),
            STRATEGIES[0],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )
        record = records[0]
        self.assertEqual(record.order_kind, "limit")
        self.assertEqual(record.status, "closed")
        self.assertEqual(record.exit_reason, "stop_loss")
        self.assertEqual(record.entry_price, 100)

    def test_quote_beyond_tp1_at_call_places_limit(self):
        records = simulate_independent_strategy(
            [signal()],
            ticks((2, 105, 106), (3, 99, 100), (4, 105, 106)),
            STRATEGIES[0],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )

        record = records[0]
        self.assertEqual(record.order_kind, "limit")
        self.assertEqual(record.entry_price, 100)
        self.assertEqual(record.exit_reason, "tp1")

    def test_tp2_strategy_does_not_close_at_breakeven(self):
        records = simulate_strategy(
            [signal()],
            ticks((2, 98, 99), (3, 105, 106), (4, 99, 100), (5, 89, 90)),
            STRATEGIES[1],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )
        self.assertFalse(records[0].tp1_touched)
        self.assertEqual(records[0].exit_reason, "stop_loss")

    def test_open_position_causes_next_signal_to_be_ignored(self):
        records = simulate_strategy(
            [signal(1, 0), signal(2, 10)],
            ticks((2, 98, 99), (12, 101, 102), (20, 102, 103)),
            STRATEGIES[3],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )
        self.assertEqual(records[0].status, "open_end")
        self.assertEqual(records[1].status, "skipped_existing_position")

    def test_pending_limit_is_replaced_by_next_signal(self):
        records = simulate_strategy(
            [signal(1, 0), signal(2, 10)],
            ticks((2, 101, 102), (12, 101, 102), (20, 102, 103)),
            STRATEGIES[3],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )
        self.assertEqual(records[0].status, "cancelled_by_new_signal")
        self.assertEqual(records[0].exit_reason, "pending_replaced")
        self.assertEqual(records[1].status, "pending_end")

    def test_independent_mode_does_not_cancel_or_skip_signals(self):
        records = simulate_independent_strategy(
            [signal(1, 0), signal(2, 10)],
            ticks(
                (2, 99, 100),
                (3, 105, 106),
                (12, 99, 100),
                (13, 105, 106),
            ),
            STRATEGIES[0],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )
        self.assertEqual([record.status for record in records], ["closed", "closed"])
        self.assertEqual([record.exit_reason for record in records], ["tp1", "tp1"])

    def test_independent_mode_places_stop_after_stop_was_crossed(self):
        records = simulate_independent_strategy(
            [signal()],
            ticks(
                (2, 88, 89),
                (3, 99.9, 100),
                (4, 105, 106),
            ),
            STRATEGIES[0],
            "GOLD",
            point=0.01,
            trade_stops_level=0,
        )
        record = records[0]
        self.assertEqual(record.order_kind, "stop")
        self.assertEqual(record.entry_price, 100)
        self.assertEqual(record.exit_reason, "tp1")


if __name__ == "__main__":
    unittest.main()
