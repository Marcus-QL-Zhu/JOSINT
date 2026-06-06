#!/usr/bin/env python3
"""
Dedup helpers for the Bitable sync layer.

Primary key: `url` (full job link).
Fallback key: `url_hash` (md5 of normalized url), needed because
morgan-philips urls contain a 24h session= parameter that expires
and gets replaced with a new one, but the underlying job is the same.

Decision tree for each inbound job:
  1. Bitable search by url        -> hit  -> UPDATE existing record (refresh last_seen_date)
  2. Bitable search by url_hash   -> hit  -> UPDATE existing record (and also fix url if it changed)
  3. Neither hit                  -> CREATE new record
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .client import BitableClient


log = logging.getLogger(__name__)


# Query params that change per-request but don't represent a different job.
# We strip these before computing url_hash so that two URLs of the same job
# (with different transient params) produce the same hash.
_TRANSIENT_PARAMS = {
    "session",        # morgan-philips 24h session id
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm",            # morgan-philips short form (utm=fb)
    "gclid",
    "fbclid",
    "ref",
    "source",
    "from",
    "lang",
    "locale",
}


def normalize_url(url: str) -> str:
    """Strip transient query params and fragments. Used to compute url_hash.

    Does NOT modify the original url (we keep the freshest one in bitable).
    """
    if not url:
        return ""
    parsed = urlparse(url.strip())
    query_pairs = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRANSIENT_PARAMS
    ]
    cleaned_query = urlencode(query_pairs, doseq=True)
    cleaned = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "/",
        parsed.params,
        cleaned_query,
        "",  # drop fragment
    ))
    return cleaned


def url_hash(url: str) -> str:
    """md5(normalize_url(url)) as 32 hex chars. Stored in url_hash field."""
    return hashlib.md5(normalize_url(url).encode("utf-8")).hexdigest()


@dataclass
class DedupResult:
    """Outcome of a dedup lookup."""
    action: str            # 'new' | 'update_url' | 'update_hash'
    record_id: Optional[str]  # existing record_id (None if action == 'new')
    matched_field: Optional[str]  # 'url' | 'url_hash' | None


class Deduper:
    """Decides what to do with each job during sync.

    On first lookup, fetches a full snapshot of the Bitable and indexes
    by url and url_hash. Subsequent lookups are O(1) dict lookups.
    Snapshot is rebuilt only if `refresh_snapshot()` is called explicitly.
    """

    def __init__(self, client: BitableClient):
        self.client = client
        self._url_index: dict[str, str] = {}      # url -> record_id
        self._hash_index: dict[str, str] = {}     # url_hash -> record_id
        self._loaded = False

    def refresh_snapshot(self) -> None:
        """Reload the in-memory index from the Bitable. Call once per sync run."""
        log.info("Deduper: loading bitable snapshot...")
        records = self.client.list_all_records()
        url_index: dict[str, str] = {}
        hash_index: dict[str, str] = {}
        for record in records:
            fields = record.get("fields", {}) or {}
            rid = record.get("record_id")
            # url is stored as {"link": "...", "text": "..."}
            url_field = fields.get("url")
            if isinstance(url_field, dict):
                url_value = url_field.get("link", "")
            elif isinstance(url_field, str):
                url_value = url_field
            else:
                url_value = ""
            if url_value:
                url_index[url_value] = rid
            hash_value = fields.get("url_hash")
            if isinstance(hash_value, str) and hash_value:
                hash_index[hash_value] = rid
        self._url_index = url_index
        self._hash_index = hash_index
        self._loaded = True
        log.info("Deduper: indexed %d urls, %d hashes from %d records", len(url_index), len(hash_index), len(records))

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.refresh_snapshot()

    def lookup(self, job_url: str) -> DedupResult:
        """Decide create vs update for a single job URL.

        Returns DedupResult with action='new' if no match found.
        """
        if not job_url:
            log.warning("Dedup lookup called with empty url, treating as new")
            return DedupResult(action="new", record_id=None, matched_field=None)

        self._ensure_loaded()

        # 1. Primary: url
        record_id = self._url_index.get(job_url)
        if record_id:
            return DedupResult(
                action="update_url",
                record_id=record_id,
                matched_field="url",
            )

        # 2. Fallback: url_hash
        h = url_hash(job_url)
        record_id = self._hash_index.get(h)
        if record_id:
            log.info("Dedup hit via url_hash for url=%s (hash=%s)", job_url, h)
            return DedupResult(
                action="update_hash",
                record_id=record_id,
                matched_field="url_hash",
            )

        return DedupResult(action="new", record_id=None, matched_field=None)
