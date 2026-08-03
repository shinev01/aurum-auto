from __future__ import annotations

import json
import sys
from typing import Any


def monitor(payload: dict[str, Any]) -> dict[str, Any]:
    """Compatibility no-op: pending orders are intentionally kept GTC."""
    account = payload.get("account") or {}
    return {
        "account": str(account.get("name", "unknown")),
        "status": "ok",
        "events": [],
    }


def main() -> None:
    try:
        result = monitor(json.loads(sys.stdin.read()))
    except Exception as exc:
        result = {
            "account": "unknown",
            "status": "failed",
            "detail": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
