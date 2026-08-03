from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np

from .history_signals import HistoricalSignal
from .models import Direction


@dataclass(frozen=True)
class StrategySpec:
    key: str
    title: str
    target_number: int


STRATEGIES = (
    StrategySpec("sl_tp1", "SL / TP1", 1),
    StrategySpec("sl_tp2", "SL / TP2", 2),
    StrategySpec("sl_tp3", "SL / TP3", 3),
    StrategySpec("sl_tp4", "SL / TP4", 4),
)


@dataclass
class BacktestRecord:
    strategy: str
    strategy_title: str
    message_id: int
    signal_time_utc: str
    signal_time_moscow: str
    symbol: str
    broker_symbol: str
    direction: str
    call_entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    tp4: float
    status: str = "new"
    order_kind: str | None = None
    decision_time_utc: str | None = None
    entry_delay_seconds: float | None = None
    decision_bid: float | None = None
    decision_ask: float | None = None
    entry_time_utc: str | None = None
    entry_price: float | None = None
    exit_time_utc: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    tp1_touched: bool = False
    raw_lot: float | None = None
    rounded_lot: float | None = None
    commission_per_lot_usd: float | None = None
    commission_usd: float | None = None
    commission_source: str | None = None
    actual_risk_usd: float | None = None
    actual_risk_percent: float | None = None
    pnl_usd: float | None = None
    pnl_percent: float | None = None
    r_multiple: float | None = None
    price_data_source: str = "ticks"
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Trade:
    signal: HistoricalSignal
    record: BacktestRecord
    pending: bool
    entry_price: float | None = None
    be_armed: bool = False


def _iso_from_msc(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )


def _first_true(condition: np.ndarray, offset: int) -> int | None:
    indices = np.flatnonzero(condition)
    return offset + int(indices[0]) if len(indices) else None


def _earlier(
    first: tuple[int | None, str],
    second: tuple[int | None, str],
    *,
    prefer_second_on_tie: bool = False,
) -> tuple[int | None, str | None]:
    first_index, first_label = first
    second_index, second_label = second
    if first_index is None:
        return second_index, second_label if second_index is not None else None
    if second_index is None:
        return first_index, first_label
    if second_index < first_index or (
        prefer_second_on_tie and second_index == first_index
    ):
        return second_index, second_label
    return first_index, first_label


def _make_record(
    signal: HistoricalSignal,
    strategy: StrategySpec,
    broker_symbol: str,
) -> BacktestRecord:
    return BacktestRecord(
        strategy=strategy.key,
        strategy_title=strategy.title,
        message_id=signal.message_id,
        signal_time_utc=signal.timestamp_utc.isoformat(),
        signal_time_moscow=signal.timestamp_moscow.isoformat(),
        symbol=signal.symbol,
        broker_symbol=broker_symbol,
        direction=signal.direction.value,
        call_entry=signal.entry,
        stop_loss=signal.stop_loss,
        tp1=signal.take_profits[0],
        tp2=signal.take_profits[1],
        tp3=signal.take_profits[2],
        tp4=signal.take_profits[3],
    )


def _close_trade(
    trade: _Trade,
    ticks: np.ndarray,
    index: int,
    reason: str,
    target_price: float | None = None,
) -> None:
    quote = (
        float(ticks["bid"][index])
        if trade.signal.direction is Direction.LONG
        else float(ticks["ask"][index])
    )
    trade.record.status = "closed"
    trade.record.exit_reason = reason
    trade.record.exit_time_utc = _iso_from_msc(int(ticks["time_msc"][index]))
    trade.record.exit_price = target_price if target_price is not None else quote


def _advance_trade(
    trade: _Trade,
    strategy: StrategySpec,
    ticks: np.ndarray,
    start_index: int,
    end_index: int,
) -> bool:
    """Advance through an inclusive tick slice. Return True when still active."""
    if start_index > end_index or len(ticks) == 0:
        return True

    signal = trade.signal
    side = ticks["bid"] if signal.direction is Direction.LONG else ticks["ask"]
    cursor = start_index

    if trade.pending:
        if signal.direction is Direction.LONG:
            fill_trigger = (
                ticks["ask"][cursor : end_index + 1] >= signal.entry
                if trade.record.order_kind == "stop"
                else ticks["ask"][cursor : end_index + 1] <= signal.entry
            )
        else:
            fill_trigger = (
                ticks["bid"][cursor : end_index + 1] <= signal.entry
                if trade.record.order_kind == "stop"
                else ticks["bid"][cursor : end_index + 1] >= signal.entry
            )
        fill_index = _first_true(fill_trigger, cursor)
        if fill_index is None:
            return True
        trade.pending = False
        trade.entry_price = signal.entry
        trade.record.status = "open"
        trade.record.entry_time_utc = _iso_from_msc(
            int(ticks["time_msc"][fill_index])
        )
        trade.record.entry_price = signal.entry
        cursor = fill_index

    target_price = signal.take_profits[strategy.target_number - 1]
    prices = side[cursor : end_index + 1]
    if signal.direction is Direction.LONG:
        stop_index = _first_true(prices <= signal.stop_loss, cursor)
        target_index = _first_true(prices >= target_price, cursor)
    else:
        stop_index = _first_true(prices >= signal.stop_loss, cursor)
        target_index = _first_true(prices <= target_price, cursor)
    exit_index, reason = _earlier(
        (stop_index, "stop_loss"),
        (target_index, f"tp{strategy.target_number}"),
        prefer_second_on_tie=False,
    )
    if exit_index is None:
        return True
    _close_trade(
        trade,
        ticks,
        exit_index,
        reason or "unknown",
        target_price if reason and reason.startswith("tp") else None,
    )
    return False


def _entry_kind(
    signal: HistoricalSignal,
    executable_price: float,
    minimum_distance: float,
    min_market_risk_ratio: float,
    max_market_risk_ratio: float,
    strict_call_entry: bool = False,
) -> str:
    if strict_call_entry:
        if signal.direction is Direction.LONG:
            return "limit" if signal.entry < executable_price else "stop"
        return "limit" if signal.entry > executable_price else "stop"

    if signal.direction is Direction.LONG:
        if not signal.stop_loss < executable_price < signal.take_profits[0]:
            return "limit" if signal.entry < executable_price else "stop"
        risk_ratio = abs(executable_price - signal.stop_loss) / abs(
            signal.entry - signal.stop_loss
        )
        if min_market_risk_ratio <= risk_ratio <= max_market_risk_ratio:
            return "market"
        return "limit" if signal.entry < executable_price else "stop"

    if not signal.take_profits[0] < executable_price < signal.stop_loss:
        return "limit" if signal.entry > executable_price else "stop"
    risk_ratio = abs(signal.stop_loss - executable_price) / abs(
        signal.stop_loss - signal.entry
    )
    if min_market_risk_ratio <= risk_ratio <= max_market_risk_ratio:
        return "market"
    return "limit" if signal.entry > executable_price else "stop"


def simulate_strategy(
    signals: Iterable[HistoricalSignal],
    ticks: np.ndarray,
    strategy: StrategySpec,
    broker_symbol: str,
    point: float,
    trade_stops_level: int,
    max_market_risk_ratio: float = 1.1,
    min_market_risk_ratio: float = 0.9,
    max_entry_delay_seconds: float = 60,
    execution_delay_seconds: float = 0.4,
    strict_call_entry: bool = False,
) -> list[BacktestRecord]:
    ordered = sorted(signals, key=lambda item: (item.timestamp_utc, item.message_id))
    records: list[BacktestRecord] = []
    active: _Trade | None = None
    cursor = 0
    minimum_distance = max(point * trade_stops_level, point)
    tick_times = ticks["time_msc"] if len(ticks) else np.array([], dtype=np.int64)

    for signal in ordered:
        signal_msc = int(signal.timestamp_utc.timestamp() * 1000)
        eligible_msc = signal_msc + int(execution_delay_seconds * 1000)
        decision_index = int(np.searchsorted(tick_times, eligible_msc, side="left"))
        record = _make_record(signal, strategy, broker_symbol)
        records.append(record)
        if decision_index >= len(ticks):
            record.status = "no_tick"
            record.note = (
                f"No FxPro tick at/after signal time + "
                f"{execution_delay_seconds:g}s"
            )
            continue

        if active is not None:
            still_active = _advance_trade(
                active,
                strategy,
                ticks,
                cursor,
                decision_index,
            )
            if not still_active:
                active = None

        decision_msc = int(ticks["time_msc"][decision_index])
        delay_seconds = (decision_msc - signal_msc) / 1000
        record.decision_time_utc = _iso_from_msc(decision_msc)
        record.entry_delay_seconds = delay_seconds
        record.decision_bid = float(ticks["bid"][decision_index])
        record.decision_ask = float(ticks["ask"][decision_index])
        if delay_seconds > max_entry_delay_seconds:
            record.status = "no_fresh_tick"
            record.note = f"First tick was {delay_seconds:.3f}s after signal"
            cursor = decision_index + 1
            continue

        if active is not None and not active.pending:
            record.status = "skipped_existing_position"
            record.note = "New signal ignored because a position is already open"
            cursor = decision_index + 1
            continue
        if active is not None and active.pending:
            active.record.status = "cancelled_by_new_signal"
            active.record.exit_reason = "pending_replaced"
            active.record.exit_time_utc = record.decision_time_utc
            active = None

        executable_price = (
            record.decision_ask
            if signal.direction is Direction.LONG
            else record.decision_bid
        )
        kind = _entry_kind(
            signal,
            float(executable_price),
            minimum_distance,
            min_market_risk_ratio,
            max_market_risk_ratio,
            strict_call_entry,
        )
        record.order_kind = kind
        if kind == "market":
            record.status = "open"
            record.entry_time_utc = record.decision_time_utc
            record.entry_price = float(executable_price)
            active = _Trade(
                signal=signal,
                record=record,
                pending=False,
                entry_price=float(executable_price),
            )
        else:
            record.status = "pending"
            active = _Trade(signal=signal, record=record, pending=True)
        cursor = decision_index + 1

    if active is not None and len(ticks):
        still_active = _advance_trade(
            active,
            strategy,
            ticks,
            cursor,
            len(ticks) - 1,
        )
        if still_active:
            active.record.status = "pending_end" if active.pending else "open_end"
    return records


def simulate_independent_strategy(
    signals: Iterable[HistoricalSignal],
    ticks: np.ndarray,
    strategy: StrategySpec,
    broker_symbol: str,
    point: float,
    trade_stops_level: int,
    max_market_risk_ratio: float = 1.1,
    min_market_risk_ratio: float = 0.9,
    max_entry_delay_seconds: float = 60,
    execution_delay_seconds: float = 0.4,
    strict_call_entry: bool = False,
) -> list[BacktestRecord]:
    """Backtest every signal independently from entry until its first exit."""
    ordered = sorted(signals, key=lambda item: (item.timestamp_utc, item.message_id))
    records: list[BacktestRecord] = []
    minimum_distance = max(point * trade_stops_level, point)
    tick_times = ticks["time_msc"] if len(ticks) else np.array([], dtype=np.int64)

    for signal in ordered:
        signal_msc = int(signal.timestamp_utc.timestamp() * 1000)
        eligible_msc = signal_msc + int(execution_delay_seconds * 1000)
        decision_index = int(np.searchsorted(tick_times, eligible_msc, side="left"))
        record = _make_record(signal, strategy, broker_symbol)
        records.append(record)
        if decision_index >= len(ticks):
            record.status = "no_tick"
            record.note = (
                f"No FxPro tick at/after signal time + "
                f"{execution_delay_seconds:g}s"
            )
            continue

        decision_msc = int(ticks["time_msc"][decision_index])
        delay_seconds = (decision_msc - signal_msc) / 1000
        record.decision_time_utc = _iso_from_msc(decision_msc)
        record.entry_delay_seconds = delay_seconds
        record.decision_bid = float(ticks["bid"][decision_index])
        record.decision_ask = float(ticks["ask"][decision_index])
        if delay_seconds > max_entry_delay_seconds:
            record.status = "no_fresh_tick"
            record.note = f"First tick was {delay_seconds:.3f}s after signal"
            continue

        executable_price = (
            record.decision_ask
            if signal.direction is Direction.LONG
            else record.decision_bid
        )
        kind = _entry_kind(
            signal,
            float(executable_price),
            minimum_distance,
            min_market_risk_ratio,
            max_market_risk_ratio,
            strict_call_entry,
        )

        if kind == "market":
            record.order_kind = kind
            record.status = "open"
            record.entry_time_utc = record.decision_time_utc
            record.entry_price = float(executable_price)
            trade = _Trade(
                signal=signal,
                record=record,
                pending=False,
                entry_price=float(executable_price),
            )
            start_index = decision_index
        else:
            record.order_kind = kind
            record.status = "pending"
            trade = _Trade(signal=signal, record=record, pending=True)
            start_index = decision_index

        still_active = _advance_trade(
            trade,
            strategy,
            ticks,
            start_index,
            len(ticks) - 1,
        )
        if still_active:
            record.status = "pending_end" if trade.pending else "open_end"

    return records
