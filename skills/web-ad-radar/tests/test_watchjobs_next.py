from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from radar.sources.watchjobs import WatchJobsAdapter


class WatchJobsNextDataTest(unittest.TestCase):
    def test_reads_embedded_jobs_and_never_follows_original_url(self):
        upstream = "https://www.zhipin.com/job_detail/boss123.html"
        state = json.dumps({
            "jobs": [{
                "id": 369479,
                "code": "c12-abc123",
                "title": "半导体研发总监",
                "url": upstream,
                "date_posted": "2026-07-24T00:00:00.000Z",
                "company_description": "A fabless AI accelerator company.",
                "benefits_en": "Long-term incentive plan.",
                "location": "上海市",
                "description_zh": "负责芯片研发团队。",
                "min_qualifications_zh": "十年以上半导体经验。",
                "company": {"name_zh": "示例芯片"},
                "dict_experience": {"name_zh": "十年以上"},
            }],
            "pagination": {"page": 1},
        }, ensure_ascii=False, separators=(",", ":"))
        embedded = json.dumps(f"12:{state}", ensure_ascii=False)
        calls: list[str] = []

        def fetch(url: str) -> str:
            calls.append(url)
            return f"<script>self.__next_f.push([1,{embedded}])</script>"

        adapter = WatchJobsAdapter(fetch=fetch)
        adapter.keywords = ("半导体总监",)
        records = adapter.crawl("2026-07-24")

        self.assertEqual(1, len(records))
        self.assertEqual("示例芯片", records[0].company_name)
        self.assertEqual("boss", records[0].upstream_source)
        self.assertEqual(upstream, records[0].upstream_url)
        self.assertIn("fabless AI accelerator company", records[0].jd_text)
        self.assertIn("Long-term incentive plan", records[0].jd_text)
        self.assertEqual("A fabless AI accelerator company.", records[0].company_description)
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0].startswith("https://watchjobs.net/"))


if __name__ == "__main__":
    unittest.main()

def test_excludes_dated_jobs_outside_requested_window():
        state = json.dumps({
            "jobs": [{
                "id": 1,
                "code": "stale-job",
                "title": "半导体研发总监",
                "date_posted": "2026-07-20T00:00:00.000Z",
                "company": {"name_zh": "示例芯片"},
            }],
            "pagination": {"page": 1},
        }, ensure_ascii=False, separators=(",", ":"))
        embedded = json.dumps(f"12:{state}", ensure_ascii=False)
        adapter = WatchJobsAdapter(
            fetch=lambda _url: f"<script>self.__next_f.push([1,{embedded}])</script>"
        )
        adapter.keywords = ("半导体总监",)

        records = adapter.crawl(
            "2026-07-24", date_from="2026-07-24", date_to="2026-07-24"
        )

        assert records == []
