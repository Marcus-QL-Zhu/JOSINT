import unittest

from radar.sources.base import CrawlResult, SourceAdapter, crawl_sources


class DummyAdapter(SourceAdapter):
    slug = "dummy"
    name = "Dummy Source"
    start_url = "https://example.com/jobs"
    include_url_patterns = ("/job/",)


class FailingAdapter(SourceAdapter):
    slug = "bad"
    name = "Bad Source"
    start_url = "https://bad.example/jobs"

    def crawl(self, run_date):
        raise RuntimeError("site unavailable")


class SourceAdapterTest(unittest.TestCase):
    def test_crawl_fetches_detail_text_for_each_job(self):
        def fetch(url):
            if url.endswith("/jobs"):
                return '<a href="/job/finance-director">Finance Director</a>'
            return '<main><h1>Finance Director</h1><p>German chemical company in Shanghai.</p></main>'

        adapter = DummyAdapter(fetch=fetch)

        jobs = adapter.crawl("2026-06-04")

        self.assertIn("German chemical company in Shanghai", jobs[0].detail_text)

    def test_extract_jobs_from_links_and_cards(self):
        html = """
        <html><body>
          <article>
            <a href="/job/finance-director">Finance Director</a>
            <span>Shanghai</span>
            <time datetime="2026-06-04"></time>
          </article>
          <a href="https://example.com/job/legal-counsel">Legal Counsel</a>
        </body></html>
        """
        adapter = DummyAdapter(fetch=lambda url: html)

        jobs = adapter.crawl("2026-06-04")

        self.assertEqual([job.title for job in jobs], ["Finance Director", "Legal Counsel"])
        self.assertEqual(jobs[0].url, "https://example.com/job/finance-director")
        self.assertEqual(jobs[0].location, "Shanghai")
        self.assertEqual(jobs[0].published_at, "2026-06-04")
        self.assertEqual(jobs[0].first_seen_at, "2026-06-04")

    def test_crawl_sources_records_errors_and_continues(self):
        ok = DummyAdapter(fetch=lambda url: '<a href="/job/a">Role A</a>')
        result = crawl_sources([FailingAdapter(), ok], run_date="2026-06-04")

        self.assertIsInstance(result, CrawlResult)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0].title, "Role A")
        self.assertEqual(len(result.errors), 1)
        self.assertIn("bad", result.errors[0])

    def test_randstad_adapter_excludes_category_links(self):
        from radar.sources.registry import RandstadAdapter

        html = """
        <a href="/en/jobs/s-accounting-finance">accounting and finance</a>
        <a href="/en/jobs/page-2/">2</a>
        <a href="/en/jobs/permanent/">Permanent</a>
        <a href="/en/jobs/finance-director_shanghai_12345/">Finance Director</a>
        """
        adapter = RandstadAdapter(fetch=lambda url: html)

        jobs = adapter.crawl("2026-06-04")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Finance Director")

    def test_mojibake_title_falls_back_to_url_slug(self):
        from radar.sources.registry import RandstadAdapter

        html = '<a href="/en/jobs/hai-wai-xiao-shou-zhi-chi_shanghai_12345_CN/">娴峰閿€鍞敮鎸?</a>'
        adapter = RandstadAdapter(fetch=lambda url: html)

        jobs = adapter.crawl("2026-06-04")

        self.assertEqual(jobs[0].title, "hai wai xiao shou zhi chi")

    def test_morgan_mckinley_adapter_excludes_discipline_links(self):
        from radar.sources.registry import MorganMcKinleyAdapter

        html = """
        <a href="/en/jobs/discipline/technology-jobs">Technology Jobs</a>
        <a href="/en/job/senior-finance-manager-shanghai">Senior Finance Manager</a>
        <div data-page="/en/jobs/shanghai/principal-software-engineer/1070756"></div>
        """
        adapter = MorganMcKinleyAdapter(fetch=lambda url: html)

        jobs = adapter.crawl("2026-06-04")

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].title, "Senior Finance Manager")
        self.assertEqual(jobs[1].title, "principal software engineer")

    def test_rgf_adapter_excludes_location_links(self):
        from radar.sources.registry import RgfAdapter

        html = """
        <a href="/zh/jobs/location/beijing">北京</a>
        <a href="/zh/jobs/12345-finance-manager">Finance Manager</a>
        """
        adapter = RgfAdapter(fetch=lambda url: html)

        jobs = adapter.crawl("2026-06-04")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Finance Manager")

    def test_morgan_philips_accepts_numeric_detail_urls(self):
        from radar.sources.registry import MorganPhilipsAdapter

        html = """
        <a href="/en-cn/jobs-in-shanghai">Shanghai</a>
        <a href="/en-cn/china-sales-director-shanghai-153390/">China Sales Director</a>
        <a href="/en-cn/china-sales-director-shanghai-153390/">View job and apply</a>
        """
        adapter = MorganPhilipsAdapter(fetch=lambda url: html)

        jobs = adapter.crawl("2026-06-04")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "China Sales Director")

    def test_hays_gllue_accepts_relative_job_urls(self):
        from radar.sources.registry import HaysAdapter

        html = """
        <a href="/zh-CN/jobs?page=1">1</a>
        <a href="./jobs/brand-manager-3180">Brand Manager</a>
        """
        adapter = HaysAdapter(fetch=lambda url: html)

        jobs = adapter.crawl("2026-06-04")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Brand Manager")

    def test_persolkelly_gllue_accepts_relative_job_urls(self):
        from radar.sources.registry import PersolkellyAdapter

        html = """
        <a href="/zh-CN/jobs?page=1">1</a>
        <a href="./jobs/confidential-tmkt-6628">confidential tmkt</a>
        """
        adapter = PersolkellyAdapter(fetch=lambda url: html)

        jobs = adapter.crawl("2026-06-04")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "confidential tmkt")


if __name__ == "__main__":
    unittest.main()
