from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from radar.models import JobRecord
from radar.sources.aggregators import TalentChinaAdapter, WatchJobsAdapter
from radar.storage import JobStore
from radar.targeting import evaluate_target


def job(source: str, url: str, **kwargs) -> JobRecord:
    defaults = {
        "source_slug": source,
        "source_name": source,
        "title": "半导体研发总监",
        "url": url,
        "company_name": "示例芯片科技有限公司",
        "location": "上海市",
        "jd_text": "负责芯片研发团队、产品开发与工程交付，要求十年以上半导体经验。",
        "first_seen_at": "2026-07-24",
        "last_seen_at": "2026-07-24",
    }
    defaults.update(kwargs)
    return JobRecord(**defaults)


class CanonicalStoreTest(unittest.TestCase):
    def test_cross_source_upstream_id_is_strong_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite")
            store.upsert_job(job(
                "watchjobs", "https://www.watchjobs.net/job/a",
                upstream_source="boss", upstream_job_id="boss-123",
            ))
            store.upsert_job(job(
                "talent-cn", "https://cn.talent.com/view?id=b",
                upstream_source="boss", upstream_job_id="boss-123",
            ))
            canonical = store.list_canonical_jobs()
            self.assertEqual(1, len(canonical))
            self.assertEqual(2, canonical[0].observation_count)
            self.assertEqual(["boss"], canonical[0].upstream_sources)

    def test_medium_similarity_merges_but_weak_only_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite")
            store.upsert_job(job("watchjobs", "https://www.watchjobs.net/job/a"))
            store.upsert_job(job(
                "talent-cn", "https://cn.talent.com/view?id=b",
                jd_text="负责芯片研发团队、产品开发以及工程交付，要求十年以上半导体经验。",
            ))
            self.assertEqual(1, len(store.list_canonical_jobs()))
            store.upsert_job(job(
                "bebee-cn", "https://cn.bebee.com/job/c",
                title="半导体市场总监",
                jd_text="负责品牌、公关和市场活动。",
            ))
            self.assertEqual(2, len(store.list_canonical_jobs()))

    def test_only_director_plus_target_business_jobs_are_selected(self):
        director = job("watchjobs", "https://www.watchjobs.net/job/a")
        manager = job("watchjobs", "https://www.watchjobs.net/job/b", title="半导体研发经理")
        hr = job("watchjobs", "https://www.watchjobs.net/job/c", title="半导体人力资源总监")
        self.assertTrue(evaluate_target(director).is_target)
        self.assertFalse(evaluate_target(manager).is_target)
        self.assertFalse(evaluate_target(hr).is_target)


class AggregatorSafetyTest(unittest.TestCase):
    def test_aggregator_never_fetches_boss_or_liepin_upstream_urls(self):
        listing_url = "https://watchjobs.net/?keyword="
        detail_url = "https://watchjobs.net/job/abc123"
        upstream_url = "https://www.zhipin.com/job_detail/boss123.html"
        calls: list[str] = []

        def fetch(url: str) -> str:
            calls.append(url)
            if url.startswith(listing_url):
                return (
                    f'<a href="{detail_url}">半导体研发总监</a>'
                    f'<a href="{upstream_url}">原始职位</a>'
                )
            return (
                '<script type="application/ld+json">'
                + json.dumps({
                    "@type": "JobPosting",
                    "title": "半导体研发总监",
                    "description": "负责半导体芯片研发与工程团队。",
                    "hiringOrganization": {"name": "测试芯片有限公司"},
                    "jobLocation": {"address": {"addressLocality": "上海"}},
                    "identifier": {"value": "abc123"},
                }, ensure_ascii=False)
                + f"</script> 来源 BOSS直聘 {upstream_url}"
            )

        adapter = WatchJobsAdapter(fetch=fetch)
        adapter.keywords = ("半导体总监",)
        jobs = adapter.crawl("2026-07-24")
        self.assertEqual(1, len(jobs))
        self.assertEqual("boss", jobs[0].upstream_source)
        self.assertNotIn(upstream_url, calls)

    def test_talent_search_url_is_first_page_in_china(self):
        adapter = TalentChinaAdapter(fetch=lambda _: "")
        url = adapter.search_template.format(keyword="%E8%8A%AF%E7%89%87")
        self.assertIn("cn.talent.com/jobs", url)
        self.assertIn("l=%E4%B8%AD%E5%9B%BD", url)


if __name__ == "__main__":
    unittest.main()

class TargetBoundaryRegressionTest(unittest.TestCase):
    def test_plain_owner_title_is_not_automatically_director_plus(self):
        vague = job(
            "watchjobs", "https://watchjobs.net/job/owner",
            title="具身智能项目负责人",
            jd_text="负责具身智能项目推进。",
        )
        self.assertFalse(evaluate_target(vague).is_target)

    def test_support_word_in_jd_does_not_reclassify_business_director(self):
        director = job(
            "watchjobs", "https://watchjobs.net/job/director",
            title="半导体研发总监",
            jd_text="负责芯片研发团队，并与法务合规及采购团队协作。",
        )
        self.assertTrue(evaluate_target(director).is_target)


class CanonicalBitableRegressionTest(unittest.TestCase):
    def test_warm_cache_update_backfills_canonical_fields(self):
        from radar.bitable.sync import BitableSyncer

        class Client:
            def __init__(self):
                self.updated = []

            def list_fields(self):
                return {
                    "canonical_job_id": "f1", "company_name": "f2",
                    "source_count": "f3", "source_urls": "f4",
                    "upstream_sources": "f5", "seniority": "f6",
                    "target_reason": "f7", "is_target_job": "f8",
                }

            def update_record(self, record_id, fields):
                self.updated.append((record_id, fields))

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            cache.write_text(json.dumps({"job:canonical": "rec-1"}))
            client = Client()
            syncer = BitableSyncer(client, object(), cache)
            target = job("josint-canonical", "https://talent.com/view/2")
            target.canonical_job_id = "job:canonical"
            target.is_target_job = True
            target.seniority_label = "director+"
            target.observation_count = 2
            target.source_urls = [
                "https://watchjobs.net/job/1", "https://talent.com/view/2"
            ]

            self.assertEqual("updated", syncer.sync_one(target, "run-1"))

            fields = client.updated[0][1]
            self.assertEqual("job:canonical", fields["canonical_job_id"])
            self.assertEqual(2, fields["source_count"])
            self.assertTrue(fields["is_target_job"])
