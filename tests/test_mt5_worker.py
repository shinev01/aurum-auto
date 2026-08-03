import unittest
from types import SimpleNamespace

from aurum_bot.models import Direction
from aurum_bot.mt5_worker import (
    _already_applied,
    _is_hedging_account,
    _pending_order_type,
    _prepare_for_new_signal,
)
from aurum_bot.models import ExecutionKind


class FakeMt5:
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5

    def __init__(self, positions):
        self.positions = positions
        self.orders_read = False
        self.orders = []
        self.requests = []

    def positions_get(self, *, symbol):
        return None if self.positions is None else tuple(self.positions)

    def orders_get(self, *, symbol):
        self.orders_read = True
        return tuple(self.orders)

    def order_send(self, request):
        self.requests.append(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="done")

    def last_error(self):
        return (0, "ok")


class HedgingPreparationTests(unittest.TestCase):
    def test_requires_hedging_margin_mode(self):
        mt5 = SimpleNamespace(ACCOUNT_MARGIN_MODE_RETAIL_HEDGING=2)
        self.assertTrue(_is_hedging_account(mt5, SimpleNamespace(margin_mode=2)))
        self.assertFalse(_is_hedging_account(mt5, SimpleNamespace(margin_mode=0)))

    def test_existing_position_does_not_block_new_signal(self):
        mt5 = FakeMt5([SimpleNamespace(ticket=42)])

        status, detail = _prepare_for_new_signal(mt5, "XAUUSD", 1234)

        self.assertEqual(status, "ready")
        self.assertIn("positions and pending orders are preserved", detail)
        self.assertFalse(mt5.orders_read)
        self.assertEqual(mt5.requests, [])

    def test_old_pending_order_is_preserved(self):
        mt5 = FakeMt5([])
        mt5.orders = [
            SimpleNamespace(
                ticket=77,
                magic=1234,
                type=FakeMt5.ORDER_TYPE_BUY_LIMIT,
            )
        ]

        status, detail = _prepare_for_new_signal(mt5, "XAUUSD", 1234)

        self.assertEqual(status, "ready")
        self.assertIn("pending orders are preserved", detail)
        self.assertFalse(mt5.orders_read)
        self.assertEqual(mt5.requests, [])

    def test_market_idempotency_is_scoped_to_message_comment(self):
        mt5 = FakeMt5(
            [
                SimpleNamespace(ticket=78, magic=1234, comment="AURUM:100"),
                SimpleNamespace(ticket=79, magic=1234, comment="AURUM:101"),
            ]
        )
        self.assertEqual(
            _already_applied(
                mt5, "XAUUSD", 1234, "AURUM:101", ExecutionKind.MARKET
            ),
            79,
        )
        self.assertIsNone(
            _already_applied(
                mt5, "XAUUSD", 1234, "AURUM:102", ExecutionKind.MARKET
            )
        )


class PendingOrderTypeTests(unittest.TestCase):
    def test_long_beyond_tp_uses_buy_limit(self):
        self.assertEqual(
            _pending_order_type(FakeMt5, Direction.LONG, 100, 106),
            FakeMt5.ORDER_TYPE_BUY_LIMIT,
        )

    def test_long_beyond_sl_uses_buy_stop(self):
        self.assertEqual(
            _pending_order_type(FakeMt5, Direction.LONG, 100, 89),
            FakeMt5.ORDER_TYPE_BUY_STOP,
        )

    def test_short_beyond_tp_uses_sell_limit(self):
        self.assertEqual(
            _pending_order_type(FakeMt5, Direction.SHORT, 100, 89),
            FakeMt5.ORDER_TYPE_SELL_LIMIT,
        )

    def test_short_beyond_sl_uses_sell_stop(self):
        self.assertEqual(
            _pending_order_type(FakeMt5, Direction.SHORT, 100, 111),
            FakeMt5.ORDER_TYPE_SELL_STOP,
        )


if __name__ == "__main__":
    unittest.main()
