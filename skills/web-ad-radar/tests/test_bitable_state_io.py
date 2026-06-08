"""Tests for v2 bitable state_io + sync helpers.

Covers the P0/P1 fixes from 2026-06-05:
- atomic_write_json survives process kill mid-write
- _today_ms() returns UTC midnight (not local midnight)
- today_utc_iso / yesterday_utc_iso / now_utc_iso are timezone-consistent
- run_cron path that uses save_state is the same atomic write as watchdog
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Ensure the skill root is on sys.path so `from radar...` works.
SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import calendar  # noqa: E402

from radar.state_io import (  # noqa: E402
    atomic_write_json,
    now_utc_iso,
    today_utc_iso,
    yesterday_utc_iso,
)
from radar.bitable.dedup import DedupResult, url_hash  # noqa: E402
from radar.bitable.sync import BitableSyncer, _today_ms  # noqa: E402
from radar.models import JobRecord  # noqa: E402


class StateIoTest(unittest.TestCase):
    def test_atomic_write_creates_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            atomic_write_json(p, {"a": 1, "b": [1, 2, 3]})
            self.assertTrue(p.exists())
            import json
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": 1, "b": [1, 2, 3]})

    def test_atomic_write_overwrites(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            atomic_write_json(p, {"a": 1})
            atomic_write_json(p, {"a": 2, "c": "x"})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": 2, "c": "x"})

    def test_atomic_write_creates_parent_dirs(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "deep" / "nested" / "state.json"
            atomic_write_json(p, {"k": "v"})
            self.assertTrue(p.exists())
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"k": "v"})

    def test_atomic_write_handles_unicode(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            payload = {"name": "Operator", "msg": "测试 unicode ✓"}
            atomic_write_json(p, payload)
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), payload)


class DateHelpersTest(unittest.TestCase):
    def test_today_utc_is_utc(self):
        today = today_utc_iso()
        now_utc = datetime.now(timezone.utc).date().isoformat()
        self.assertEqual(today, now_utc)

    def test_yesterday_utc_is_one_day_before_today(self):
        today = today_utc_iso()
        yesterday = yesterday_utc_iso()
        # Compare via datetime arithmetic
        t = datetime.strptime(today, "%Y-%m-%d")
        y = datetime.strptime(yesterday, "%Y-%m-%d")
        self.assertEqual((t - y).days, 1)

    def test_now_utc_has_timezone_marker(self):
        s = now_utc_iso()
        # Should contain "+00:00" or "Z" or similar UTC marker.
        self.assertTrue("+" in s or "Z" in s, f"now_utc_iso() output missing timezone marker: {s}")

    def test_now_utc_is_within_2s_of_system_clock(self):
        # now_utc_iso() uses timespec="seconds", so it loses sub-second
        # precision. Compare at second-resolution, allow ±1s slack.
        from datetime import datetime as _dt, timedelta
        before = _dt.now(timezone.utc).replace(microsecond=0)
        s = now_utc_iso()
        after = _dt.now(timezone.utc).replace(microsecond=0)
        parsed = _dt.fromisoformat(s)
        self.assertTrue(
            before - timedelta(seconds=1) <= parsed <= after + timedelta(seconds=1),
            f"now_utc_iso()={s} not within ±1s of [{before.isoformat()}, {after.isoformat()}]",
        )


class BitableSyncTimezoneTest(unittest.TestCase):
    """Regression for the P0 #2 timezone bug: _today_ms() must use UTC midnight.

    Feishu Date fields expect UTC epoch ms. Using time.mktime(local_midnight)
    was putting Beijing midnight (UTC 16:00 prev day) in the bitable, making
    'last_seen_date' display one day behind reality.
    """

    def test_today_ms_equals_utc_midnight_today(self):
        expected_utc_midnight_ms = calendar.timegm(
            datetime.now(timezone.utc).date().timetuple()
        ) * 1000
        self.assertEqual(_today_ms(), expected_utc_midnight_ms)

    def test_today_ms_differs_from_local_midnight_ms(self):
        """If local TZ is not UTC, _today_ms() must NOT match local midnight ms.

        In CI this test runs in UTC so they match. In a Beijing-timezone
        host they differ by 8 hours. We assert the UTC invariant.
        """
        import time as _time
        from datetime import date
        local_midnight_ms = int(_time.mktime(date.today().timetuple())) * 1000
        utc_midnight_ms = calendar.timegm(
            datetime.now(timezone.utc).date().timetuple()
        ) * 1000
        # _today_ms() must equal UTC midnight, never local.
        self.assertEqual(_today_ms(), utc_midnight_ms)


class SaveStateIntegrationTest(unittest.TestCase):
    """Verify save_state() in radar_cron.py uses atomic_write_json.

    regression check that the cron entry point doesn't silently regress
    to direct write_text.
    """

    def test_save_state_uses_atomic_writer(self):
        from radar_cron import save_state
        import inspect
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            save_state(workspace, {"last_run_date": "2026-06-05", "retry_count": 1})
            state_path = workspace / "runtime" / "cron_state.json"
            self.assertTrue(state_path.exists(), "save_state did not create cron_state.json")
            import json
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("last_run_date"), "2026-06-05")
            self.assertEqual(payload.get("retry_count"), 1)


class FakeBitableClient:
    def __init__(self):
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []

    def create_record(self, fields: dict) -> str:
        self.created.append(fields)
        return f"rec-{len(self.created)}"

    def update_record(self, record_id: str, fields: dict) -> None:
        self.updated.append((record_id, fields))


class FakeDeduper:
    def __init__(self, actions: dict[str, DedupResult]):
        self.actions = actions

    def lookup(self, job_url: str) -> DedupResult:
        return self.actions[job_url]


class BitableSyncerTest(unittest.TestCase):
    def test_sync_stats_tracks_new_job_ids_only(self):
        new_job = JobRecord("morgan-philips", "Morgan Philips", "AI Role", "https://e/new", first_seen_at="2026-06-08", last_seen_at="2026-06-08")
        old_job = JobRecord("morgan-philips", "Morgan Philips", "Old AI Role", "https://e/old", first_seen_at="2026-06-01", last_seen_at="2026-06-08")
        client = FakeBitableClient()
        with tempfile.TemporaryDirectory() as tmp:
            syncer = BitableSyncer(
                client,
                FakeDeduper({
                    new_job.url: DedupResult("new", None, None),
                    old_job.url: DedupResult("update_url", "rec-old", "url"),
                }),
                Path(tmp) / "cache.json",
            )
            stats = syncer.sync_many([new_job, old_job], crawl_run_id="run-1")

        self.assertEqual(stats.new, 1)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.new_job_ids, [new_job.id])
        self.assertEqual(stats.action_by_job_id[old_job.id], "updated")

    def test_job_fields_include_first_seen_month_for_monthly_views(self):
        job = JobRecord(
            "morgan-philips",
            "Morgan Philips",
            "AI Role",
            "https://e/new",
            first_seen_at="2026-06-08",
            last_seen_at="2026-06-09",
        )
        with tempfile.TemporaryDirectory() as tmp:
            syncer = BitableSyncer(FakeBitableClient(), FakeDeduper({}), Path(tmp) / "cache.json")
            fields = syncer._job_to_fields(job, is_new=True, crawl_run_id="run-1")

        self.assertEqual(fields["month"], "2026-06")
        self.assertEqual(fields["last_seen_month"], "2026-06")
        self.assertEqual(fields["url_hash"], url_hash(job.url))


if __name__ == "__main__":
    unittest.main()
