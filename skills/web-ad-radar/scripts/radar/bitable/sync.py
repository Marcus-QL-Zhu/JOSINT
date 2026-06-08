#!/usr/bin/env python3
"""
Local DB -> Feishu Bitable sync.

Strategy (incremental):
- For each local JobRecord, look up Bitable by url then by url_hash.
- new:        create record, populate first_seen_date
- update_*:   update record, refresh last_seen_date
- The local DB is source of truth; Bitable is mirror + dedup index.

This module writes to a small local cache `bitable_state.json` mapping
url -> record_id so we can skip the round-trip on warm lookups. The cache
is best-effort: if a record_id is missing or stale, we re-query Bitable.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .client import BitableClient, FeishuApiError
from .dedup import DedupResult, Deduper, url_hash
from ..models import JobRecord


log = logging.getLogger(__name__)


@dataclass
class SyncStats:
    scanned: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    new_job_ids: list[str] = field(default_factory=list)
    new_url_hashes: list[str] = field(default_factory=list)
    action_by_job_id: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "new": self.new,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
        }


class BitableSyncer:
    """Sync local JobStore rows to a Feishu Bitable table."""

    def __init__(
        self,
        client: BitableClient,
        deduper: Deduper,
        cache_path: Path,
    ):
        self.client = client
        self.deduper = deduper
        self.cache_path = cache_path
        self._cache: dict[str, str] = self._load_cache()

    # ---------- cache ----------

    def _load_cache(self) -> dict[str, str]:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load bitable cache (%s); starting empty", e)
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---------- field mapping ----------

    def _job_to_fields(self, job: JobRecord, is_new: bool, crawl_run_id: str = "") -> dict[str, Any]:
        """Map a JobRecord to a Bitable fields payload.

        - title, source, url, url_hash, location, salary are written always.
        - first_seen_date only when is_new (preserves historical first-seen).
        - last_seen_date is refreshed on every sync (job was seen in this run).
        - crawl_run_id is set to a per-run UUID (caller passes it via sync_one).
        - url field is a Link type, needs {"link": ..., "text": ...} format.
        - date fields need epoch-ms (int), not ISO strings.
        - jd_text carries the cleaned job description (truncated to 4500
          chars because Feishu text fields reject > 5000).
        """
        today_ms = _today_ms()
        jd = (job.jd_text or job.detail_text or "") or ""
        jd = jd[:4500]
        fields: dict[str, Any] = {
            "title": job.title,
            "source": job.source_slug,
            "source_job_id": job.id,
            "location": job.location or "",
            "salary": job.salary or "",
            "url": {"link": job.url, "text": job.title[:60] or job.url},
            "url_hash": url_hash(job.url),
            "last_seen_date": _iso_or_today_ms(job.last_seen_at, default=today_ms),
            "last_seen_month": _job_month(job.last_seen_at) or _job_month(job.first_seen_at) or _current_month(),
            "crawl_run_id": crawl_run_id,
            "jd_text": jd,
        }
        if is_new:
            fields["first_seen_date"] = _iso_or_today_ms(job.first_seen_at, default=today_ms)
            fields["month"] = _job_month(job.first_seen_at) or _job_month(job.last_seen_at) or _current_month()
        if job.industry_label:
            fields["industry"] = job.industry_label
        elif job.industry:
            fields["industry"] = job.industry
        if job.function_label:
            fields["function"] = job.function_label
        elif job.function:
            fields["function"] = job.function
        return fields

    # ---------- per-job ----------

    def sync_one(self, job: JobRecord, crawl_run_id: str = "") -> str:
        """Sync a single job. Returns action taken: 'new'|'updated'|'skipped'."""
        url = job.url
        cached_id = self._cache.get(url)
        if cached_id:
            # Warm path: trust the cache, refresh last_seen_date and
            # overwrite jd_text so the table keeps the freshest JD content.
            try:
                jd = (job.jd_text or job.detail_text or "") or ""
                self.client.update_record(cached_id, {
                    "last_seen_date": _today_ms(),
                    "last_seen_month": _job_month(job.last_seen_at) or _current_month(),
                    "crawl_run_id": crawl_run_id,
                    "jd_text": jd[:4500],
                })
                return "updated"
            except FeishuApiError as e:
                log.warning("Cached record %s update failed (%s); falling back to lookup", cached_id, e)
                self._cache.pop(url, None)

        result = self.deduper.lookup(url)
        fields = self._job_to_fields(job, is_new=(result.action == "new"), crawl_run_id=crawl_run_id)

        if result.action == "new":
            record_id = self.client.create_record(fields)
            self._cache[url] = record_id
            return "new"

        # update_url or update_hash
        # If matched by hash, also refresh the url field to the new one
        # (morgan-philips session= param rotated, but the job is the same).
        if result.action == "update_hash" and result.record_id:
            self.client.update_record(result.record_id, {
                "url": {"link": job.url, "text": job.title[:60] or job.url},
                "url_hash": url_hash(job.url),
            })
        # On every update, refresh last_seen_date + crawl_run_id and
        # overwrite jd_text with the latest crawl's JD content.

        jd = (job.jd_text or job.detail_text or "") or ""
        self.client.update_record(result.record_id, {
            "last_seen_date": _today_ms(),
            "last_seen_month": _job_month(job.last_seen_at) or _current_month(),
            "crawl_run_id": crawl_run_id,
            "jd_text": jd[:4500],
        })
        self._cache[url] = result.record_id
        return "updated"

    # ---------- batch ----------

    def sync_many(self, jobs: Iterable[JobRecord], crawl_run_id: str = "") -> SyncStats:
        stats = SyncStats()
        for job in jobs:
            stats.scanned += 1
            try:
                action = self.sync_one(job, crawl_run_id=crawl_run_id)
                stats.action_by_job_id[job.id] = action
                if action == "new":
                    stats.new += 1
                    stats.new_job_ids.append(job.id)
                    stats.new_url_hashes.append(url_hash(job.url))
                elif action == "updated":
                    stats.updated += 1
                else:
                    stats.skipped += 1
            except FeishuApiError as e:
                stats.failed += 1
                log.error("Failed to sync job %s: %s", getattr(job, "id", "?"), e)
            except Exception as e:  # noqa: BLE001
                stats.failed += 1
                log.exception("Unexpected error syncing job %s: %s", getattr(job, "id", "?"), e)
        self._save_cache()
        return stats

    # ---------- source DB helpers ----------

    @staticmethod
    def iter_local_jobs(db_path: Path, since_iso: str | None = None) -> Iterable[JobRecord]:
        """Yield JobRecord rows from the local sqlite store.

        If since_iso is provided (e.g. '2026-06-05'), only yield rows whose
        last_seen_at is on or after that date. Useful for the daily cron that
        only cares about the most recent crawl.
        """
        # We don't import storage.JobStore to avoid a hard dep on the v1
        # schema. Read columns directly and reconstruct the dataclass.
        from ..models import JobRecord  # local import to avoid cycle
        from ..storage import JobStore  # noqa: F401  (kept for side effects)

        store = JobStore(db_path)
        # list_jobs returns all rows; filter in Python so we can keep
        # JobStore's row-to-dataclass logic untouched.
        for job in store.list_jobs():
            if since_iso and job.last_seen_at and job.last_seen_at[:10] < since_iso:
                continue
            yield job


def _today_ms() -> int:
    """Epoch milliseconds at UTC midnight today (Bitable date field format).

    Feishu Date fields expect UTC epoch ms. We compute UTC midnight today
    explicitly to avoid the local-midnight-as-UTC confusion that
    time.mktime(local_midnight) causes (Beijing midnight is 16:00 UTC
    the previous day).
    """
    import calendar as _cal
    today_utc = datetime.now(timezone.utc).date()
    return int(_cal.timegm(today_utc.timetuple())) * 1000


def _job_month(value: str | int | None) -> str | None:
    if value is None or value == "":
        return None
    s = str(value)
    if len(s) >= 7 and s[4] in "-/" and s[7:8] in {"-", "/", ""}:
        return s[:7].replace("/", "-")
    return None


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _iso_or_today_ms(value: str | int | None, default: int) -> int:
    """Convert 'YYYY-MM-DD HH:MM:SS' to epoch ms; if unparseable, return default."""
    if value is None or value == "":
        return default
    s = str(value)
    try:
        if len(s) >= 10 and s[4] in "-/" and s[7] in "-/":
            from datetime import datetime
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            return int(dt.timestamp() * 1000)
        # Already ms?
        return int(s)
    except (ValueError, TypeError):
        return default
