from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from .models import (
    AccountConfig,
    Direction,
    ExecutionKind,
    ExecutionResult,
    Signal,
)
from .trading_math import choose_execution, raw_volume_for_risk, volume_for_risk


def _finish(result: ExecutionResult) -> None:
    print(json.dumps(result.to_dict(), ensure_ascii=False))


def _normalized(value: float, digits: int) -> float:
    return round(value, digits)


def _success_codes(mt5: Any) -> set[int]:
    return {
        mt5.TRADE_RETCODE_DONE,
        mt5.TRADE_RETCODE_PLACED,
        mt5.TRADE_RETCODE_DONE_PARTIAL,
    }


def _is_hedging_account(mt5: Any, account_info: Any) -> bool:
    hedging_mode = int(
        getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", 2)
    )
    return int(account_info.margin_mode) == hedging_mode


def _prepare_for_new_signal(
    mt5: Any, symbol: str, magic: int
) -> tuple[str, str]:
    del mt5, symbol, magic
    return (
        "ready",
        "hedging mode: existing positions and pending orders are preserved",
    )


def _pending_order_type(
    mt5: Any,
    direction: Direction,
    entry: float,
    executable_price: float,
) -> int:
    """Choose the valid pending type while keeping the call's entry price."""
    if direction is Direction.LONG:
        return (
            mt5.ORDER_TYPE_BUY_LIMIT
            if entry < executable_price
            else mt5.ORDER_TYPE_BUY_STOP
        )
    return (
        mt5.ORDER_TYPE_SELL_LIMIT
        if entry > executable_price
        else mt5.ORDER_TYPE_SELL_STOP
    )


def _already_applied(
    mt5: Any,
    symbol: str,
    magic: int,
    comment: str,
    execution_kind: ExecutionKind,
) -> int | None:
    if execution_kind is ExecutionKind.MARKET:
        positions = mt5.positions_get(symbol=symbol) or ()
        for position in positions:
            if (
                int(position.magic) == magic
                and str(position.comment).startswith(comment[:20])
            ):
                return int(position.ticket)
        return None

    orders = mt5.orders_get(symbol=symbol) or ()
    for order in orders:
        if int(order.magic) == magic and str(order.comment).startswith(comment[:20]):
            return int(order.ticket)
    return None


def _filling_candidates(
    mt5: Any, symbol_info: Any, execution_kind: ExecutionKind
) -> list[int]:
    # MQL5 requires RETURN for pending orders. For market orders,
    # symbol_info.filling_mode is a bit mask, not an ORDER_FILLING enum.
    if execution_kind is ExecutionKind.LIMIT:
        return [mt5.ORDER_FILLING_RETURN]

    flags = int(symbol_info.filling_mode)
    candidates: list[int] = []
    if flags & 1:  # SYMBOL_FILLING_FOK
        candidates.append(mt5.ORDER_FILLING_FOK)
    if flags & 2:  # SYMBOL_FILLING_IOC
        candidates.append(mt5.ORDER_FILLING_IOC)
    if int(symbol_info.trade_exemode) != mt5.SYMBOL_TRADE_EXECUTION_MARKET:
        candidates.append(mt5.ORDER_FILLING_RETURN)
    if not candidates:
        candidates.extend([mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK])

    unique: list[int] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _send_protected_order(
    mt5: Any,
    base_request: dict[str, Any],
    symbol_info: Any,
    symbol: str,
    magic: int,
    comment: str,
    execution_kind: ExecutionKind,
    attempts: int,
    retry_delay: float,
) -> tuple[bool, int | None, str, dict[str, int] | None]:
    last_detail = "order was not sent"
    for attempt in range(1, attempts + 1):
        existing_ticket = _already_applied(
            mt5, symbol, magic, comment, execution_kind
        )
        if existing_ticket is not None:
            return (
                True,
                existing_ticket,
                "reconciled after an ambiguous response",
                None,
            )

        for filling in _filling_candidates(mt5, symbol_info, execution_kind):
            request = dict(base_request)
            request["type_filling"] = filling
            check = mt5.order_check(request)
            if check is None:
                last_detail = f"order_check failed: {mt5.last_error()}"
                continue
            if int(check.retcode) != 0:
                last_detail = f"order_check retcode={check.retcode}: {check.comment}"
                continue

            send_started_at_ms = time.time_ns() // 1_000_000
            send_started_monotonic_ns = time.monotonic_ns()
            result = mt5.order_send(request)
            send_completed_at_ms = time.time_ns() // 1_000_000
            send_completed_monotonic_ns = time.monotonic_ns()
            if result is None:
                last_detail = f"order_send failed: {mt5.last_error()}"
                continue
            if int(result.retcode) in _success_codes(mt5):
                ticket = int(result.order or result.deal or 0) or None
                return (
                    True,
                    ticket,
                    f"retcode={result.retcode}: {result.comment}",
                    {
                        "send_started_at_ms": send_started_at_ms,
                        "send_completed_at_ms": send_completed_at_ms,
                        "send_completed_monotonic_ns": send_completed_monotonic_ns,
                        "round_trip_ms": round(
                            (send_completed_monotonic_ns - send_started_monotonic_ns)
                            / 1_000_000
                        ),
                    },
                )
            last_detail = f"retcode={result.retcode}: {result.comment}"

        if attempt < attempts:
            time.sleep(retry_delay)

    existing_ticket = _already_applied(mt5, symbol, magic, comment, execution_kind)
    if existing_ticket is not None:
        return True, existing_ticket, "reconciled after final send attempt", None
    return False, None, last_detail, None


def _execution_timing(
    payload: dict[str, Any], send_timing: dict[str, int] | None
) -> dict[str, int | None]:
    if send_timing is None:
        return {}
    incoming = payload.get("timing") or {}
    published_at_ms = incoming.get("published_at_ms")
    received_at_ms = incoming.get("received_at_ms")
    received_monotonic_ns = incoming.get("received_monotonic_ns")
    completed_at_ms = send_timing["send_completed_at_ms"]
    completed_monotonic_ns = send_timing["send_completed_monotonic_ns"]
    return {
        # Telegram publication timestamps only have one-second resolution, so this
        # is useful as an approximate end-to-end value rather than a precise RTT.
        "publication_to_receive_ms": (
            int(received_at_ms) - int(published_at_ms)
            if received_at_ms is not None and published_at_ms is not None
            else None
        ),
        "publication_to_confirmation_ms": (
            completed_at_ms - int(published_at_ms)
            if published_at_ms is not None
            else None
        ),
        # Both values use the same Windows monotonic clock, including across the
        # short-lived worker process, and therefore are safe from clock changes.
        "receive_to_confirmation_ms": (
            round((completed_monotonic_ns - int(received_monotonic_ns)) / 1_000_000)
            if received_monotonic_ns is not None
            else None
        ),
        "order_send_round_trip_ms": send_timing["round_trip_ms"],
    }


def execute(payload: dict[str, Any]) -> ExecutionResult:
    account = AccountConfig(**payload["account"])
    signal = Signal.from_dict(payload["signal"])
    trading = payload["trading"]
    magic = int(trading["magic_number"])

    try:
        import MetaTrader5 as mt5
    except ImportError:
        return ExecutionResult(
            account.name, "failed", "MetaTrader5 Python package is not installed"
        )

    terminal_path = Path(account.terminal_path)
    if not terminal_path.is_file():
        return ExecutionResult(
            account.name,
            "failed",
            f"MT5 terminal not found: {terminal_path}",
        )

    initialized = mt5.initialize(str(terminal_path), timeout=60_000)
    if not initialized:
        return ExecutionResult(
            account.name, "failed", f"mt5.initialize failed: {mt5.last_error()}"
        )

    try:
        account_info = mt5.account_info()
        terminal_info = mt5.terminal_info()
        if account_info is None or terminal_info is None:
            return ExecutionResult(
                account.name, "failed", f"MT5 account unavailable: {mt5.last_error()}"
            )
        if str(account_info.currency).upper() != "USD":
            return ExecutionResult(
                account.name,
                "failed",
                f"account currency is {account_info.currency}, expected USD",
            )
        if not _is_hedging_account(mt5, account_info):
            return ExecutionResult(
                account.name,
                "failed",
                "account is not in MT5 hedging mode; refusing independent "
                "same-symbol positions",
            )
        if not bool(account_info.trade_allowed) or not bool(terminal_info.trade_allowed):
            return ExecutionResult(
                account.name,
                "failed",
                "algorithmic trading is disabled in MT5/account",
            )

        broker_symbol = account.broker_symbol(signal.symbol)
        if not mt5.symbol_select(broker_symbol, True):
            return ExecutionResult(
                account.name,
                "failed",
                f"symbol unavailable: {broker_symbol}; {mt5.last_error()}",
            )
        symbol_info = mt5.symbol_info(broker_symbol)
        tick = mt5.symbol_info_tick(broker_symbol)
        if symbol_info is None or tick is None:
            return ExecutionResult(
                account.name,
                "failed",
                f"no symbol/tick data for {broker_symbol}: {mt5.last_error()}",
            )

        preparation_status, preparation_detail = _prepare_for_new_signal(
            mt5, broker_symbol, magic
        )
        if preparation_status != "ready":
            return ExecutionResult(account.name, preparation_status, preparation_detail)

        tick = mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            return ExecutionResult(
                account.name,
                "failed",
                f"no refreshed tick data: {mt5.last_error()}",
            )

        digits = int(symbol_info.digits)
        point = float(symbol_info.point)
        entry = _normalized(signal.entry, digits)
        stop_loss = _normalized(signal.stop_loss, digits)
        take_profit = _normalized(signal.take_profit, digits)

        order_side = (
            mt5.ORDER_TYPE_BUY
            if signal.direction is Direction.LONG
            else mt5.ORDER_TYPE_SELL
        )
        loss_one_lot = mt5.order_calc_profit(
            order_side, broker_symbol, 1.0, entry, stop_loss
        )
        if loss_one_lot is None or not math.isfinite(float(loss_one_lot)):
            return ExecutionResult(
                account.name,
                "failed",
                f"order_calc_profit failed: {mt5.last_error()}",
            )
        commission_one_lot = account.commission_for_one_lot(
            signal.symbol,
            entry,
            float(symbol_info.trade_contract_size),
        )
        raw_volume = raw_volume_for_risk(
            risk_base_usd=account.risk_base_usd,
            risk_percent=float(trading["risk_percent"]),
            loss_for_one_lot=abs(float(loss_one_lot)),
            commission_for_one_lot=commission_one_lot,
        )
        if raw_volume is None:
            return ExecutionResult(
                account.name,
                "failed",
                "cannot calculate theoretical lot volume",
            )

        def theoretical_stop_loss_at(price: float) -> float | None:
            loss_at_one_lot = mt5.order_calc_profit(
                order_side,
                broker_symbol,
                1.0,
                price,
                stop_loss,
            )
            if loss_at_one_lot is None or not math.isfinite(float(loss_at_one_lot)):
                return None
            return (
                abs(float(loss_at_one_lot)) + commission_one_lot
            ) * raw_volume

        volume = volume_for_risk(
            risk_base_usd=account.risk_base_usd,
            risk_percent=float(trading["risk_percent"]),
            loss_for_one_lot=abs(float(loss_one_lot)),
            volume_min=float(symbol_info.volume_min),
            volume_max=float(symbol_info.volume_max),
            # Respect a coarser broker step, but never use increments below 0.01.
            volume_step=max(
                float(symbol_info.volume_step),
                float(trading["lot_step"]),
            ),
            commission_for_one_lot=commission_one_lot,
        )
        if volume is None:
            return ExecutionResult(
                account.name,
                "failed",
                "cannot calculate a valid lot volume from symbol settings",
            )

        executable_price = (
            float(tick.ask)
            if signal.direction is Direction.LONG
            else float(tick.bid)
        )
        minimum_distance = max(float(symbol_info.trade_stops_level) * point, point)
        current_loss = theoretical_stop_loss_at(executable_price)
        min_market_loss = (
            account.risk_base_usd
            * float(trading["min_market_risk_percent"])
            / 100
        )
        max_market_loss = (
            account.risk_base_usd
            * float(trading["max_market_risk_percent"])
            / 100
        )
        valid_market_geometry = (
            stop_loss < executable_price < take_profit
            if signal.direction is Direction.LONG
            else take_profit < executable_price < stop_loss
        )
        market_risk_in_range = (
            current_loss is not None
            and math.isfinite(float(current_loss))
            and abs(float(current_loss)) >= min_market_loss - 1e-9
            and abs(float(current_loss)) <= max_market_loss + 1e-9
            and valid_market_geometry
        )
        execution_kind = choose_execution(
            signal.direction,
            entry,
            stop_loss,
            executable_price,
            minimum_distance,
            market_risk_in_range=market_risk_in_range,
        )
        comment = f"AURUM:{signal.message_id}"
        if execution_kind is ExecutionKind.MARKET:
            current_price = _normalized(executable_price, digits)
            protected_geometry = (
                stop_loss < current_price < take_profit
                if signal.direction is Direction.LONG
                else take_profit < current_price < stop_loss
            )
            if not protected_geometry:
                return ExecutionResult(
                    account.name,
                    "failed",
                    "current price cannot use the exact requested SL and TP2",
                    volume=volume,
                    execution_kind=execution_kind.value,
                )
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": broker_symbol,
                "volume": volume,
                "type": order_side,
                "price": current_price,
                "sl": stop_loss,
                "tp": take_profit,
                "deviation": int(trading["deviation_points"]),
                "magic": magic,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
            }
        else:
            pending_type = _pending_order_type(
                mt5,
                signal.direction,
                entry,
                executable_price,
            )
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": broker_symbol,
                "volume": volume,
                "type": pending_type,
                "price": entry,
                "sl": stop_loss,
                "tp": take_profit,
                "deviation": int(trading["deviation_points"]),
                "magic": magic,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
            }

        close_to_entry = abs(executable_price - entry) < minimum_distance
        initial_attempts = (
            1
            if execution_kind is ExecutionKind.LIMIT and close_to_entry
            else int(trading["send_attempts"])
        )
        ok, ticket, send_detail, send_timing = _send_protected_order(
            mt5=mt5,
            base_request=request,
            symbol_info=symbol_info,
            symbol=broker_symbol,
            magic=magic,
            comment=comment,
            execution_kind=execution_kind,
            attempts=initial_attempts,
            retry_delay=float(trading["retry_delay_seconds"]),
        )
        if not ok and execution_kind is ExecutionKind.LIMIT:
            # A rejected limit may become a protected market order only if the
            # refreshed quote has reduced stop risk to the configured threshold.
            refreshed_tick = mt5.symbol_info_tick(broker_symbol)
            if refreshed_tick is not None:
                refreshed_price = (
                    float(refreshed_tick.ask)
                    if signal.direction is Direction.LONG
                    else float(refreshed_tick.bid)
                )
                refreshed_kind = choose_execution(
                    signal.direction,
                    entry,
                    stop_loss,
                    refreshed_price,
                    minimum_distance,
                    market_risk_in_range=(
                        (
                            refreshed_loss := theoretical_stop_loss_at(refreshed_price)
                        )
                        is not None
                        and math.isfinite(float(refreshed_loss))
                        and abs(float(refreshed_loss)) >= min_market_loss - 1e-9
                        and abs(float(refreshed_loss)) <= max_market_loss + 1e-9
                        and (
                            stop_loss < refreshed_price < take_profit
                            if signal.direction is Direction.LONG
                            else take_profit < refreshed_price < stop_loss
                        )
                    ),
                )
                if refreshed_kind is ExecutionKind.MARKET:
                    market_price = _normalized(refreshed_price, digits)
                    protected_geometry = (
                        stop_loss < market_price < take_profit
                        if signal.direction is Direction.LONG
                        else take_profit < market_price < stop_loss
                    )
                    if not protected_geometry:
                        return ExecutionResult(
                            account.name,
                            "failed",
                            "pending order rejected and exact SL/TP2 cannot protect fallback market order",
                            volume=volume,
                            execution_kind=ExecutionKind.MARKET.value,
                        )
                    market_request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": broker_symbol,
                        "volume": volume,
                        "type": order_side,
                        "price": market_price,
                        "sl": stop_loss,
                        "tp": take_profit,
                        "deviation": int(trading["deviation_points"]),
                        "magic": magic,
                        "comment": comment,
                        "type_time": mt5.ORDER_TIME_GTC,
                    }
                    ok, ticket, market_detail, send_timing = _send_protected_order(
                        mt5=mt5,
                        base_request=market_request,
                        symbol_info=symbol_info,
                        symbol=broker_symbol,
                        magic=magic,
                        comment=comment,
                        execution_kind=ExecutionKind.MARKET,
                        attempts=int(trading["send_attempts"]),
                        retry_delay=float(trading["retry_delay_seconds"]),
                    )
                    execution_kind = ExecutionKind.MARKET
                    send_detail = (
                        f"limit rejected ({send_detail}); market fallback: {market_detail}"
                    )
        if not ok:
            return ExecutionResult(
                account.name,
                "failed",
                (
                    f"{preparation_detail}; protected order rejected: {send_detail}"
                ),
                volume=volume,
                execution_kind=execution_kind.value,
            )
        return ExecutionResult(
            account.name,
            "executed",
            f"{preparation_detail}; {send_detail}",
            ticket=ticket,
            volume=volume,
            execution_kind=execution_kind.value,
            **_execution_timing(payload, send_timing),
        )
    finally:
        mt5.shutdown()


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        result = execute(payload)
    except Exception as exc:
        account = "unknown"
        try:
            account = str(payload.get("account", {}).get("name", "unknown"))
        except Exception:
            pass
        result = ExecutionResult(account, "failed", f"{type(exc).__name__}: {exc}")
    _finish(result)


if __name__ == "__main__":
    main()
