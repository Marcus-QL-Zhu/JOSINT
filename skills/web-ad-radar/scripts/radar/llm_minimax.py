from __future__ import annotations

import json as json_module
import time
from typing import Any, Callable
from urllib import request


class MiniMaxError(RuntimeError):
    pass


def _default_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):
    data = json_module.dumps(json).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    with request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return _SimpleResponse(resp.status, body)


class _SimpleResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json_module.loads(self.text)


class MiniMaxClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        post: Callable[..., Any] | None = None,
        timeout: int = 60,
        usage_logger: Any | None = None,
        stage: str = "minimax",
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.post = post or _default_post
        self.timeout = timeout
        self.usage_logger = usage_logger
        self.stage = stage

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        thinking_type: str | None = None,
        reasoning_split: bool = False,
        response_format: dict[str, Any] | None = None,
        max_completion_tokens: int | None = None,
        usage_context: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if thinking_type:
            payload["thinking"] = {"type": thinking_type}
        if reasoning_split:
            payload["reasoning_split"] = True
        if response_format:
            payload["response_format"] = response_format
        if max_completion_tokens:
            payload["max_completion_tokens"] = max_completion_tokens
        start = time.perf_counter()
        try:
            response = self.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                raise MiniMaxError(f"MiniMax HTTP error {response.status_code}")
            body = response.json()
            base_resp = body.get("base_resp") or {}
            if base_resp.get("status_code") not in (None, 0):
                raise MiniMaxError(f"MiniMax error {base_resp.get('status_code')}: {base_resp.get('status_msg', 'request failed')}")
            try:
                content = body["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise MiniMaxError("MiniMax response did not include message content") from exc
            self._record_usage(True, start, usage_context)
            return content
        except Exception as exc:
            self._record_usage(False, start, usage_context, error=str(exc))
            raise

    def _record_usage(self, success: bool, start: float, usage_context: dict[str, Any] | None, error: str | None = None) -> None:
        if not self.usage_logger:
            return
        self.usage_logger.record(
            provider="minimax",
            stage=self.stage,
            model=self.model,
            success=success,
            latency_ms=int((time.perf_counter() - start) * 1000),
            error=error,
            **(usage_context or {}),
        )
