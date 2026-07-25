from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from radar.models import JobRecord
from radar.storage import JobStore


class CanonicalAnalysisMarkerTest(unittest.TestCase):
    def test_marker_uses_canonical_id_and_prevents_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite")
            store.upsert_job(JobRecord(
                source_slug="watchjobs",
                source_name="WatchJobs",
                source_job_id="job-a",
                title="半导体研发总监",
                url="https://watchjobs.net/?keyword=chip#home-job-card-job-a",
                company_name="示例芯片公司",
                location="上海",
                jd_text="负责半导体芯片研发团队、软件和硬件产品开发。",
                first_seen_at="2026-07-24",
                last_seen_at="2026-07-24",
            ))

            target = store.list_canonical_jobs(target_only=True, unanalyzed_only=True)[0]
            self.assertEqual(target.canonical_job_id, target.id)
            store.mark_analysis_completed(target.id, completed_at="2026-07-25T01:00:00Z")
            self.assertEqual([], store.list_canonical_jobs(target_only=True, unanalyzed_only=True))


if __name__ == "__main__":
    unittest.main()
