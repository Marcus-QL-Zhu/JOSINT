import tempfile
import unittest
from pathlib import Path

from radar.models import EmployerGuess, JobRecord
from radar.report import render_report
from radar.storage import JobStore


class StorageReportTest(unittest.TestCase):
    def test_job_store_upsert_preserves_first_seen_and_updates_last_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite")
            job = JobRecord(
                source_slug="randstad",
                source_name="Randstad China",
                title="Finance Director",
                url="https://example.com/job-1",
                first_seen_at="2026-06-01",
                last_seen_at="2026-06-01",
            )

            store.upsert_job(job)
            changed = JobRecord(
                source_slug="randstad",
                source_name="Randstad China",
                title="Finance Director Updated",
                url="https://example.com/job-1",
                first_seen_at="2026-06-04",
                last_seen_at="2026-06-04",
            )
            store.upsert_job(changed)
            rows = store.list_jobs()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].title, "Finance Director Updated")
            self.assertEqual(rows[0].first_seen_at, "2026-06-01")
            self.assertEqual(rows[0].last_seen_at, "2026-06-04")

    def test_render_report_includes_jobs_and_employer_guess_details(self):
        job = JobRecord(
            source_slug="randstad",
            source_name="Randstad China",
            title="Finance Director",
            url="https://example.com/job-1",
            location="Shanghai",
            first_seen_at="2026-06-04",
        )
        guess = EmployerGuess(
            job_id=job.id,
            guessed_employer="BASF",
            confidence="medium",
            confidence_score=0.62,
            evidence=[{"type": "description", "text": "German chemical company"}],
            reasoning_summary="German chemical company and Shanghai footprint narrow the field.",
            review_flags=["Several German chemical companies remain plausible"],
        )

        markdown = render_report(
            report_date="2026-06-04",
            scope="randstad",
            mode="crawl + analysis",
            jobs=[job],
            guesses={job.id: guess},
            source_errors=[],
        )

        self.assertIn("# 职位广告雷达报告 - 2026-06-04", markdown)
        self.assertIn("## 执行摘要", markdown)
        self.assertIn("| 发布方 | 职位名称 | 地点 | 日期 | URL | 雇主猜测 | 置信度 |", markdown)
        self.assertIn("| Randstad China | Finance Director | Shanghai | 2026-06-04 | https://example.com/job-1 | BASF | medium |", markdown)
        self.assertIn("### Finance Director", markdown)
        self.assertIn("German chemical company", markdown)
        self.assertIn("Several German chemical companies remain plausible", markdown)

    def test_render_report_uses_chinese_source_error_section(self):
        markdown = render_report(
            report_date="2026-06-04",
            scope="all",
            mode="crawl only",
            jobs=[],
            guesses={},
            source_errors=["robert-walters: HTTP Error 403: Forbidden"],
        )

        self.assertIn("## 爬取失败公司", markdown)
        self.assertIn("- robert-walters: HTTP Error 403: Forbidden", markdown)


if __name__ == "__main__":
    unittest.main()
