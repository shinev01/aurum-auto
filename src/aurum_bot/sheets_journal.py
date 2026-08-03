from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterable
from urllib.parse import quote

from .config import GoogleSheetsConfig
from .models import Direction, ExecutionResult, Signal


SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
TRADE_SHEET = "Сделки"
FIRST_DATA_ROW = 2
LAST_DATA_ROW = 1000


@dataclass(frozen=True)
class TradeSnapshot:
    message_id: int
    account: str
    symbol: str
    direction: str
    volume: float
    open_time: str
    open_price: float
    close_time: str | None
    close_price: float | None
    commission: float
    swap: float
    gross_pnl: float | None
    status: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TradeSnapshot":
        return cls(
            message_id=int(data["message_id"]),
            account=str(data["account"]),
            symbol=str(data["symbol"]),
            direction=str(data["direction"]),
            volume=float(data["volume"]),
            open_time=str(data["open_time"]),
            open_price=float(data["open_price"]),
            close_time=(
                None if data.get("close_time") in (None, "") else str(data["close_time"])
            ),
            close_price=(
                None
                if data.get("close_price") in (None, "")
                else float(data["close_price"])
            ),
            commission=float(data.get("commission", 0)),
            swap=float(data.get("swap", 0)),
            gross_pnl=(
                None if data.get("gross_pnl") is None else float(data["gross_pnl"])
            ),
            status=str(data["status"]),
        )


class SheetsTradeJournal:
    """Failure-isolated Google Sheets writer for the fxpro trade journal."""

    def __init__(
        self,
        config: GoogleSheetsConfig,
        *,
        session: Any | None = None,
    ):
        self.config = config
        self._session = session
        self._operation_lock = RLock()

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        credentials_path = Path(self.config.credentials_file)
        if not credentials_path.is_file():
            raise FileNotFoundError(
                f"Google service-account key not found: {credentials_path}"
            )
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.service_account import Credentials

        credentials = Credentials.from_service_account_file(
            str(credentials_path),
            scopes=SCOPES,
        )
        self._session = AuthorizedSession(credentials)
        return self._session

    def _values_url(self, range_name: str | None = None) -> str:
        base = (
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{self.config.spreadsheet_id}/values"
        )
        if range_name is None:
            return base
        return f"{base}/{quote(range_name, safe='')}"

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._ensure_session().request(
            method,
            url,
            params=params,
            json=body,
            timeout=20,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def check_access(self) -> None:
        self._request_json(
            "GET",
            self._values_url(f"'{TRADE_SHEET}'!A1:B2"),
        )

    def _trade_rows(self) -> tuple[dict[int, int], list[int]]:
        payload = self._request_json(
            "GET",
            self._values_url(
                f"'{TRADE_SHEET}'!A{FIRST_DATA_ROW}:A{LAST_DATA_ROW}"
            ),
            params={"valueRenderOption": "UNFORMATTED_VALUE"},
        )
        rows = payload.get("values", [])
        existing: dict[int, int] = {}
        free: list[int] = []
        for offset in range(LAST_DATA_ROW - FIRST_DATA_ROW + 1):
            row_number = FIRST_DATA_ROW + offset
            value = rows[offset][0] if offset < len(rows) and rows[offset] else ""
            if value in ("", None):
                free.append(row_number)
                continue
            try:
                existing[int(value)] = row_number
            except (TypeError, ValueError):
                continue
        return existing, free

    def _batch_write(self, data: list[dict[str, Any]]) -> None:
        if not data:
            return
        self._request_json(
            "POST",
            self._values_url() + ":batchUpdate",
            body={
                "valueInputOption": "USER_ENTERED",
                "data": data,
            },
        )

    @staticmethod
    def _range(row: int, columns: str, values: list[Any]) -> dict[str, Any]:
        if ":" in columns:
            start_column, end_column = columns.split(":", 1)
            a1_range = f"{start_column}{row}:{end_column}{row}"
        else:
            a1_range = f"{columns}{row}"
        return {
            "range": f"'{TRADE_SHEET}'!{a1_range}",
            "majorDimension": "ROWS",
            "values": [values],
        }

    def record_execution(
        self,
        signal: Signal,
        result: ExecutionResult,
        *,
        risk_base_usd: float,
        risk_percent: float,
        recorded_at: datetime,
    ) -> None:
        with self._operation_lock:
            self._record_execution(
                signal,
                result,
                risk_base_usd=risk_base_usd,
                risk_percent=risk_percent,
                recorded_at=recorded_at,
            )

    def _record_execution(
        self,
        signal: Signal,
        result: ExecutionResult,
        *,
        risk_base_usd: float,
        risk_percent: float,
        recorded_at: datetime,
    ) -> None:
        if result.account != self.config.account or result.status != "executed":
            return
        existing, free = self._trade_rows()
        row = existing.get(signal.message_id)
        is_new = row is None
        if row is None:
            if not free:
                raise RuntimeError("No free rows remain in Сделки!A2:A1000")
            row = free[0]

        side = "BUY" if signal.direction is Direction.LONG else "SELL"
        detail = (
            f"{result.execution_kind or 'executed'}; "
            f"ticket={result.ticket or ''}; {result.detail}"
        )
        updates = [
            self._range(row, "A:B", [signal.message_id, result.account]),
            self._range(
                row,
                "E:H",
                [signal.symbol, side, result.volume or "", signal.entry],
            ),
            self._range(row, "J:K", [signal.stop_loss, signal.take_profit]),
            self._range(
                row,
                "S:T",
                [
                    risk_base_usd,
                    risk_base_usd * risk_percent / 100,
                ],
            ),
            self._range(row, "V", [detail]),
        ]
        if is_new and result.execution_kind == "market":
            updates.append(
                self._range(
                    row,
                    "C",
                    [recorded_at.astimezone().strftime("%d.%m.%Y %H:%M:%S")],
                )
            )
        self._batch_write(updates)

    def upsert_snapshots(self, snapshots: Iterable[TradeSnapshot]) -> int:
        with self._operation_lock:
            return self._upsert_snapshots(snapshots)

    @staticmethod
    def _sheet_datetime(value: str | None) -> str:
        if not value:
            return ""
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%d.%m.%Y %H:%M:%S")

    def _upsert_snapshots(self, snapshots: Iterable[TradeSnapshot]) -> int:
        filtered = [
            snapshot
            for snapshot in snapshots
            if snapshot.account == self.config.account
        ]
        if not filtered:
            return 0
        existing, free = self._trade_rows()
        next_free = iter(free)
        updates: list[dict[str, Any]] = []
        count = 0
        for snapshot in sorted(filtered, key=lambda item: item.message_id):
            row = existing.get(snapshot.message_id)
            if row is None:
                try:
                    row = next(next_free)
                except StopIteration as exc:
                    raise RuntimeError(
                        "No free rows remain in Сделки!A2:A1000"
                    ) from exc
                existing[snapshot.message_id] = row
            updates.extend(
                [
                    self._range(
                        row,
                        "A:B",
                        [snapshot.message_id, snapshot.account],
                    ),
                    self._range(
                        row,
                        "C:D",
                        [
                            self._sheet_datetime(snapshot.open_time),
                            self._sheet_datetime(snapshot.close_time),
                        ],
                    ),
                    self._range(
                        row,
                        "E:I",
                        [
                            snapshot.symbol,
                            snapshot.direction,
                            snapshot.volume,
                            snapshot.open_price,
                            snapshot.close_price or "",
                        ],
                    ),
                    self._range(
                        row,
                        "L:N",
                        [
                            snapshot.commission,
                            snapshot.swap,
                            (
                                ""
                                if snapshot.gross_pnl is None
                                else snapshot.gross_pnl
                            ),
                        ],
                    ),
                    self._range(row, "V", [snapshot.status]),
                ]
            )
            count += 1
        self._batch_write(updates)
        return count
