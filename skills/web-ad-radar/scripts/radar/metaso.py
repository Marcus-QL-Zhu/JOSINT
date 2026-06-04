from __future__ import annotations

import json as json_module
from typing import Any, Callable
from urllib import request


class MetasoError(RuntimeError):
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


class MetasoClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://metaso.cn",
        model: str = "fast",
        post: Callable[..., Any] | None = None,
        timeout: int = 60,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.post = post or _default_post
        self.timeout = timeout

    def search(self, q: str) -> dict[str, Any]:
        return self._post(
            "/api/v1/search",
            {"q": q, "scope": "webpage", "includeSummary": True, "conciseSnippet": True},
        )

    def read(self, url: str) -> dict[str, Any]:
        return self._post("/api/v1/reader", {"url": url})

    def ask_simple(self, q: str) -> dict[str, Any]:
        return self._post("/api/v1/chat/completions", {"q": q, "model": self.model, "format": "simple"})

    def chat(self, messages: list[dict[str, str]], *, stream: bool = False) -> dict[str, Any]:
        return self._post("/api/v1/chat/completions", {"messages": messages, "model": self.model, "stream": stream})

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.post(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise MetasoError(f"Metaso HTTP error {response.status_code}")
        body = response.json()
        if isinstance(body, dict) and body.get("errCode"):
            raise MetasoError(f"Metaso error {body.get('errCode')}: {body.get('errMsg', 'request failed')}")
        return body
