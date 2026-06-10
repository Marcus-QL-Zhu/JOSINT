from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from radar.models import JobRecord  # noqa: E402
from radar.bitable.sync import SyncStats  # noqa: E402
import radar_cron  # noqa: E402
from radar_cron import _jobs_new_in_bitable  # noqa: E402


class RadarCronAnalysisQueueTest(unittest.TestCase):
    def test_jobs_new_in_bitable_filters_out_updated_jobs(self):
        new_job = JobRecord("morgan-philips", "Morgan Philips", "New AI Role", "https://e/new")
        updated_job = JobRecord("morgan-philips", "Morgan Philips", "Old AI Role", "https://e/old")
        stats = SyncStats(new=1, updated=1)
        stats.new_job_ids.append(new_job.id)
        stats.action_by_job_id[updated_job.id] = "updated"

        result = _jobs_new_in_bitable([new_job, updated_job], stats)

        self.assertEqual([job.id for job in result], [new_job.id])


class RadarCronCrawlFailureTest(unittest.TestCase):
    def test_per_source_runner_uses_current_python_executable(self):
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch("subprocess.run", return_value=completed) as run:
            rc = radar_cron._run_per_source_runner(Path("/workspace"))

        self.assertEqual(rc, 0)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], sys.executable)

    def test_crawl_exit_one_aborts_without_syncing_old_database(self):
        env = {
            "JOSINT_TIMEZONE": "Asia/Shanghai",
            "FEISHU_APP_ID": "app",
            "FEISHU_APP_SECRET": "secret",
            "FEISHU_BITABLE_APP_TOKEN": "base",
            "FEISHU_BITABLE_TABLE_ID": "table",
        }
        with mock.patch.object(radar_cron, "_run_per_source_runner", return_value=1), \
             mock.patch.object(radar_cron, "_send_failure") as send_failure, \
             mock.patch.object(radar_cron, "BitableClient") as bitable_client, \
             mock.patch.object(radar_cron, "save_state"):
            with self.subTest("run_cron returns catastrophic failure"):
                with mock.patch.object(radar_cron, "today_business_iso", return_value="2026-06-10"), \
                     mock.patch.object(radar_cron, "now_utc_iso", return_value="2026-06-09T22:00:00+00:00"):
                    with unittest.mock.patch("radar_cron.load_state", return_value={"retry_count": 0}):
                        rc = radar_cron.run_cron(Path("/tmp/workspace"), env)

        self.assertEqual(rc, 2)
        send_failure.assert_called_once()
        bitable_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
