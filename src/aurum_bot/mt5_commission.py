from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any


DEFAULT_COMMISSION_PER_LOT_USD = 7.0


def infer_round_turn_commission_per_lot(
    mt5: Any,
    broker_symbol: str,
    *,
    lookback_days: int = 730,
) -> float | None:
    """Infer full open-and-close commission from completed MT5 positions."""
    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    deals = mt5.history_deals_get(start, end, group=broker_symbol)
    if deals is None:
        return None

    groups: dict[int, list[Any]] = defaultdict(list)
    for deal in deals:
        if str(getattr(deal, "symbol", "")) != broker_symbol:
            continue
        position_id = int(getattr(deal, "position_id", 0))
        if position_id and float(getattr(deal, "volume", 0)) > 0:
            groups[position_id].append(deal)

    entry_in = int(mt5.DEAL_ENTRY_IN)
    entry_out = {int(mt5.DEAL_ENTRY_OUT), int(mt5.DEAL_ENTRY_OUT_BY)}
    estimates: list[float] = []
    for position_deals in groups.values():
        entries = [
            deal for deal in position_deals
            if int(getattr(deal, "entry", -1)) == entry_in
        ]
        exits = [
            deal for deal in position_deals
            if int(getattr(deal, "entry", -1)) in entry_out
        ]
        entry_volume = sum(float(deal.volume) for deal in entries)
        exit_volume = sum(float(deal.volume) for deal in exits)
        if not entries or not exits or exit_volume + 1e-9 < entry_volume:
            continue
        total_cost = abs(sum(
            float(getattr(deal, "commission", 0))
            + float(getattr(deal, "fee", 0))
            for deal in position_deals
        ))
        estimates.append(total_cost / entry_volume)

    return median(estimates) if estimates else None
