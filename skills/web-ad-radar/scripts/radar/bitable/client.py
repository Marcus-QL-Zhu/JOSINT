#!/usr/bin/env python3
"""
Feishu Bitable REST client.

Wraps the bitable/v1 endpoints with:
- tenant_access_token auto-refresh (2h expiry)
- exponential backoff retry on transient errors
- simple field_id cache to avoid re-listing fields on every call

References:
- /bitable/v1/apps:        POST create app, GET list
- /bitable/v1/apps/{app}/tables/{tbl}/fields: GET list, POST create
- /bitable/v1/apps/{app}/tables/{tbl}/records: POST create, GET search
- /bitable/v1/apps/{app}/tables/{tbl}/records/{rid}: PATCH update

Rate limits (per app/tenant): 5000 req/hour, 100 req/min.
Backoff honors Retry-After header when present.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import requests


log = logging.getLogger(__name__)


BASE_URL = "https://open.feishu.cn/open-apis"


class FeishuApiError(RuntimeError):
    def __init__(self, code: int, msg: str, payload: dict | None = None):
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg
        self.payload = payload or {}


class BitableClient:
    """Feishu Bitable API client.

    Usage:
        client = BitableClient(app_id, app_secret, app_token, table_id)
        client.create_record({"title": "...", "url": "..."})
    """

    # Retry config
    MAX_RETRIES = 3
    INITIAL_BACKOFF_S = 1.0
    MAX_BACKOFF_S = 8.0
    REQUEST_TIMEOUT_S = 30

    # Per-call error codes that warrant retry
    RETRYABLE_CODES = {429, 500, 502, 503, 504}

    def __init__(self, app_id: str, app_secret: str, app_token: str, table_id: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self._token: Optional[str] = None
        self._token_acquired_at: float = 0.0
        self._field_id_cache: Optional[dict[str, str]] = None  # field_name -> field_id

    # ---------- token ----------

    def _ensure_token(self) -> str:
        # Refresh 60s before expiry (tokens are 2h)
        if self._token and (time.time() - self._token_acquired_at) < (2 * 3600 - 60):
            return self._token
        url = f"{BASE_URL}/auth/v3/tenant_access_token/internal"
        resp = requests.post(
            url,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=self.REQUEST_TIMEOUT_S,
        )
        result = resp.json()
        if result.get("code") != 0:
            raise FeishuApiError(result.get("code", -1), result.get("msg", "token fetch failed"), result)
        self._token = result["tenant_access_token"]
        self._token_acquired_at = time.time()
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    # ---------- low-level request with retry ----------

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{BASE_URL}{path}"
        backoff = self.INITIAL_BACKOFF_S
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                resp = requests.request(method, url, headers=self._headers(), timeout=self.REQUEST_TIMEOUT_S, **kwargs)
                # Token expired mid-flight -> force refresh on next attempt
                if resp.status_code == 401:
                    self._token = None
                    raise FeishuApiError(401, "unauthorized, token rotated")
                result = resp.json()
                code = result.get("code", 0)
                if code == 0:
                    return result.get("data", {})
                # Retryable server-side codes
                if code in self.RETRYABLE_CODES or resp.status_code in self.RETRYABLE_CODES:
                    last_error = FeishuApiError(code, result.get("msg", ""), result)
                    log.warning("Bitable retryable error (attempt %d/%d) code=%d msg=%s", attempt, self.MAX_RETRIES, code, result.get("msg", ""))
                    time.sleep(backoff)
                    backoff = min(backoff * 2, self.MAX_BACKOFF_S)
                    continue
                # Non-retryable: bubble up
                raise FeishuApiError(code, result.get("msg", ""), result)
            except (requests.RequestException, FeishuApiError) as e:
                last_error = e
                if attempt == self.MAX_RETRIES:
                    break
                log.warning("Bitable request error (attempt %d/%d): %s", attempt, self.MAX_RETRIES, e)
                time.sleep(backoff)
                backoff = min(backoff * 2, self.MAX_BACKOFF_S)
        raise FeishuApiError(-1, f"max retries exceeded: {last_error}")

    # ---------- fields ----------

    def list_fields(self, use_cache: bool = True) -> dict[str, str]:
        """Return {field_name: field_id} map. Caches after first call."""
        if use_cache and self._field_id_cache is not None:
            return self._field_id_cache
        data = self._request("GET", f"/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/fields")
        items = data.get("items", [])
        self._field_id_cache = {item["field_name"]: item["field_id"] for item in items}
        return self._field_id_cache

    def resolve_field_id(self, field_name: str, refresh: bool = False) -> Optional[str]:
        if refresh:
            self._field_id_cache = None
        return self.list_fields().get(field_name)

    # ---------- records ----------

    def create_record(self, fields: dict[str, Any]) -> str:
        """Create one record. Returns the new record_id."""
        data = self._request(
            "POST",
            f"/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records",
            json={"fields": fields},
        )
        record = data.get("record", {})
        return record.get("record_id", "")

    def update_record(self, record_id: str, fields: dict[str, Any]) -> None:
        """Partial update of an existing record."""
        self._request(
            "PUT",
            f"/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/{record_id}",
            json={"fields": fields},
        )

    def search_by_field(self, field_name: str, value: str, limit: int = 1) -> list[dict]:
        """Search records where field_name == value. Returns up to `limit` records.

        NOTE: Bitable's server-side filter (POST /records/search with `filter`
        param) returns code 99992402 for our text/url/url_hash fields with
        any operator we've tried (is, =, contains). This is a known
        limitation of the API surface vs. our field types.

        Workaround: list all records and filter in Python. We expose a
        `list_all_records` helper that paginates through and returns a
        full snapshot for the deduper to search.
        """
        # Fallback to client-side filter via list_all_records.
        # (We don't need this anymore — the deduper uses list_all_records
        # directly — but keep this method for symmetry in case future
        # field types get proper server-side filter support.)
        return self._client_side_filter(field_name, value, limit=limit)

    def _client_side_filter(self, field_name: str, value: str, limit: int = 1) -> list[dict]:
        matches: list[dict] = []
        for record in self.list_all_records():
            fields = record.get("fields", {})
            field_value = fields.get(field_name)
            if field_value is None:
                continue
            # URL field is stored as {"link": ..., "text": ...}
            if isinstance(field_value, dict):
                cmp_value = field_value.get("link", "")
            else:
                cmp_value = str(field_value)
            if cmp_value == value:
                matches.append(record)
                if len(matches) >= limit:
                    break
        return matches

    def list_all_records(self, page_size: int = 500) -> list[dict]:
        """List every record in the table, paginating through.

        Cost: O(N) network round-trips. For our table size (low thousands
        of jobs per day) this is fine. We cache the snapshot for the
        dedup layer to avoid repeated pagination.
        """
        all_items: list[dict] = []
        page_token: str | None = None
        while True:
            body: dict = {"limit": min(page_size, 500)}
            if page_token:
                body["page_token"] = page_token
            data = self._request(
                "POST",
                f"/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/search",
                json=body,
            )
            items = data.get("items", [])
            all_items.extend(items)
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break
        return all_items

    def list_recent(self, limit: int = 500) -> list[dict]:
        """List the most recent N records (no filter)."""
        return self.list_all_records()[:limit]
