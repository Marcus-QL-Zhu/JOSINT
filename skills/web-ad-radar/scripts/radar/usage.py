from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class ApiUsageLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        provider: str,
        stage: str,
        model: str | None,
        success: bool,
        latency_ms: int,
        error: str | None = None,
        **context: Any,
    ) -> None:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "provider": provider,
            "stage": stage,
            "model": model,
            "success": success,
            "latency_ms": latency_ms,
            **{key: value for key, value in context.items() if value is not None},
        }
        if error:
            payload["error"] = error
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
