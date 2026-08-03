from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from .config import TradingConfig
from .models import AccountConfig, ExecutionResult, Signal


def _run_account(
    account: AccountConfig,
    signal: Signal,
    trading: TradingConfig,
    timing: dict[str, int] | None = None,
) -> ExecutionResult:
    if not account.supports_symbol(signal.symbol):
        return ExecutionResult(
            account.name,
            "skipped_unsupported_symbol",
            f"{signal.symbol} is not enabled for this account",
        )
    payload = {
        "account": account.to_dict(),
        "signal": signal.to_dict(),
        "trading": asdict(trading),
    }
    if timing is not None:
        payload["timing"] = timing
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "aurum_bot.mt5_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ExecutionResult(account.name, "failed", "MT5 worker timed out")

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return ExecutionResult(
            account.name, "failed", f"MT5 worker exited {completed.returncode}: {detail}"
        )
    try:
        raw = json.loads(completed.stdout.strip())
        return ExecutionResult(**raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return ExecutionResult(
            account.name,
            "failed",
            f"invalid MT5 worker response: {exc}; {completed.stdout!r}",
        )


def execute_for_accounts(
    signal: Signal,
    accounts: tuple[AccountConfig, ...],
    trading: TradingConfig,
    timing: dict[str, int] | None = None,
) -> list[ExecutionResult]:
    enabled = [account for account in accounts if account.enabled]
    if not enabled:
        return []

    results: list[ExecutionResult] = []
    with ThreadPoolExecutor(max_workers=len(enabled)) as pool:
        futures = {
            pool.submit(_run_account, account, signal, trading, timing): account.name
            for account in enabled
        }
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item.account)
