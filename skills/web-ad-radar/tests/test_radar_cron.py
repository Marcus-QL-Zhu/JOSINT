from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from radar.models import JobRecord  # noqa: E402
from radar.bitable.sync import SyncStats  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
