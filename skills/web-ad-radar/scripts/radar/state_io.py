"""Shared atomic JSON state writer and date helpers."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically via temp file + os.replace.

    Readers see either the old or new payload, never a half-truncated
    file. Survives process kill mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as tf:
        tf.write(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp_path = tf.name
    os.replace(tmp_path, path)


def today_utc_iso() -> str:
    """Today's date in UTC (YYYY-MM-DD). Use for state file dates."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def yesterday_utc_iso() -> str:
    """Yesterday's date in UTC (YYYY-MM-DD)."""
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def today_business_iso(tz_name: str = "Asia/Shanghai") -> str:
    """Today's date in the configured business timezone."""
    return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def yesterday_business_iso(tz_name: str = "Asia/Shanghai") -> str:
    """Yesterday's date in the configured business timezone."""
    return (datetime.now(ZoneInfo(tz_name)) - timedelta(days=1)).strftime("%Y-%m-%d")


def now_utc_iso() -> str:
    """Current UTC timestamp ISO string with seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
