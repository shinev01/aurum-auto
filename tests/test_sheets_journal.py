import unittest
from datetime import datetime, timezone
from pathlib import Path

from aurum_bot.config import GoogleSheetsConfig
from aurum_bot.models import Direction, ExecutionResult, Signal
from aurum_bot.sheets_journal import SheetsTradeJournal, TradeSnapshot


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, id_values=None):
        self.id_values = id_values or []
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "GET":
            return FakeResponse({"values": self.id_values})
        return FakeResponse({})


def config():
    return GoogleSheetsConfig(
        enabled=True,
        spreadsheet_id="sheet-id",
        credentials_file=Path("unused.json"),
        account="fxpro_demo510",
        sync_interval_seconds=120,
        history_lookback_days=45,
    )


class SheetsJournalTests(unittest.TestCase):
    def test_execution_writes_only_target_account(self):
        session = FakeSession()
        journal = SheetsTradeJournal(config(), session=session)
        signal = Signal(123, "XAUUSD", Direction.LONG, 2400, 2390, 2420)

        journal.record_execution(
            signal,
            ExecutionResult(
                "secondary_demo",
                "executed",
                "done",
                ticket=1,
                volume=0.1,
                execution_kind="market",
            ),
            risk_base_usd=1000,
            risk_percent=1,
            recorded_at=datetime.now(timezone.utc),
        )
        self.assertEqual(session.calls, [])

    def test_execution_preserves_formula_columns(self):
        session = FakeSession()
        journal = SheetsTradeJournal(config(), session=session)
        signal = Signal(123, "XAUUSD", Direction.LONG, 2400, 2390, 2420)
        journal.record_execution(
            signal,
            ExecutionResult(
                "fxpro_demo510",
                "executed",
                "done",
                ticket=77,
                volume=0.1,
                execution_kind="market",
            ),
            risk_base_usd=1000,
            risk_percent=1,
            recorded_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        )

        batch = session.calls[-1][2]["json"]["data"]
        ranges = {item["range"] for item in batch}
        self.assertIn("'Сделки'!A2:B2", ranges)
        self.assertIn("'Сделки'!S2:T2", ranges)
        self.assertFalse(any("O:" in item for item in ranges))
        self.assertFalse(any("U" in item for item in ranges))

    def test_snapshot_updates_close_and_pnl_fields(self):
        session = FakeSession(id_values=[[123]])
        journal = SheetsTradeJournal(config(), session=session)
        count = journal.upsert_snapshots(
            [
                TradeSnapshot(
                    message_id=123,
                    account="fxpro_demo510",
                    symbol="XAUUSD",
                    direction="BUY",
                    volume=0.1,
                    open_time="2026-07-25T10:00:00+00:00",
                    open_price=2400,
                    close_time="2026-07-25T12:00:00+00:00",
                    close_price=2420,
                    commission=-1,
                    swap=0,
                    gross_pnl=20,
                    status="CLOSED",
                )
            ]
        )
        self.assertEqual(count, 1)
        batch = session.calls[-1][2]["json"]["data"]
        ranges = {item["range"] for item in batch}
        self.assertIn("'Сделки'!C2:D2", ranges)
        self.assertIn("'Сделки'!L2:N2", ranges)


if __name__ == "__main__":
    unittest.main()
