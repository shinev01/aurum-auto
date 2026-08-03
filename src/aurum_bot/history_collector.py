from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.tl.types import PeerChannel

from .config import AppConfig
from .history_signals import (
    HistoricalSignal,
    load_signals,
    merge_signals,
    message_has_image,
    parse_historical_signal,
    save_signals,
)


LOGGER = logging.getLogger("aurum_bot.history")


@dataclass(frozen=True)
class CollectionResult:
    signals: tuple[HistoricalSignal, ...]
    scanned_messages: int
    rejected_entry_like_messages: int


async def _resolve_channel(client: TelegramClient, config: AppConfig) -> Any:
    expected_id = abs(config.telegram.channel_id)
    try:
        entity = await client.get_entity(PeerChannel(expected_id))
        entity_title = str(getattr(entity, "title", ""))
        if entity_title != config.telegram.channel_title:
            raise RuntimeError(
                f"Channel ID {expected_id} has title {entity_title!r}, "
                f"expected {config.telegram.channel_title!r}"
            )
        return entity
    except ValueError:
        # Fall back to one sequential dialog scan when the access hash is not
        # yet cached in the local Telethon session.
        pass

    async for dialog in client.iter_dialogs():
        entity_id = abs(int(getattr(dialog.entity, "id", 0)))
        if entity_id != expected_id:
            continue
        if dialog.name != config.telegram.channel_title:
            raise RuntimeError(
                f"Channel ID {expected_id} has title {dialog.name!r}, "
                f"expected {config.telegram.channel_title!r}"
            )
        return dialog.entity
    raise RuntimeError(
        f"Channel {config.telegram.channel_title!r} ({expected_id}) is unavailable"
    )


async def collect_history(
    config: AppConfig,
    start_utc: datetime,
    end_utc: datetime,
    json_path: Path,
    csv_path: Path,
) -> CollectionResult:
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise ValueError("History range must use timezone-aware datetimes")
    start_utc = start_utc.astimezone(timezone.utc)
    end_utc = end_utc.astimezone(timezone.utc)
    if end_utc <= start_utc:
        raise ValueError("History end must be after start")

    client = TelegramClient(
        str(config.telegram.session_file),
        config.telegram.api_id,
        config.telegram.api_hash,
        receive_updates=False,
        catch_up=False,
        request_retries=3,
        connection_retries=3,
        # Respect server-requested cooldowns instead of retrying aggressively.
        flood_sleep_threshold=300,
    )
    await client.start(phone=config.telegram.phone)
    scanned = 0
    rejected_entry_like = 0
    signals: list[HistoricalSignal] = []
    try:
        channel = await _resolve_channel(client, config)
        async for message in client.iter_messages(
            channel,
            offset_date=end_utc,
            # One gentle sequential history request cadence; no parallel fetches.
            wait_time=2,
        ):
            message_date = message.date.astimezone(timezone.utc)
            if message_date < start_utc:
                break
            if message_date >= end_utc:
                continue
            scanned += 1
            signal = parse_historical_signal(
                int(message.id),
                message.raw_text,
                message_date,
                has_image=message_has_image(message),
            )
            if signal is not None:
                signals.append(signal)
            elif message.raw_text and (
                "#XAUUSD" in message.raw_text.upper()
                or "#GOLD" in message.raw_text.upper()
                or "#XAGUSD" in message.raw_text.upper()
                or "#SILVER" in message.raw_text.upper()
                or "#DE40" in message.raw_text.upper()
                or "#GERMANY40" in message.raw_text.upper()
                or "#US100" in message.raw_text.upper()
                or "#USNDAQ100" in message.raw_text.upper()
            ) and "ВХОД" in message.raw_text.upper():
                rejected_entry_like += 1
                LOGGER.warning(
                    "Entry-like message %s was rejected by strict parser", message.id
                )
    finally:
        await client.disconnect()

    signals.sort(key=lambda item: (item.timestamp_utc, item.message_id))
    existing = load_signals(json_path) if json_path.exists() else []
    merged = merge_signals(existing, signals)
    save_signals(merged, json_path, csv_path)
    return CollectionResult(
        signals=tuple(merged),
        scanned_messages=scanned,
        rejected_entry_like_messages=rejected_entry_like,
    )
