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
        self.assertIn("German chemical company in Shanghai", jobs[0].jd_text)
        self.assertEqual(jobs[0].raw_title, "Finance Director")

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

    def test_morgan_philips_uses_xpath_container_for_chinese_jd(self):
        from radar.sources.registry import MorganPhilipsAdapter

        listing = '<a href="/zh-cn/ceo-ea-shanghai-153409/">CEO EA（战略与运营方向）</a>'
        detail = """
        <html><body>
          <div>site banner navigation services Talent Acquisition noisy menu</div>
          <div>
            <div>
              <div></div>
              <div>
                <div></div>
                <div>
                  <div>
                    <div><div><div><div><div>
                      <h2>职位描述</h2>
                      <p>负责CEO办公室战略项目、经营分析和跨部门运营推进。</p>
                      <h2>任职要求</h2>
                      <p>具备战略咨询或业务运营经验。</p>
                    </div></div></div></div></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <footer>CONTACT US services Talent Acquisition noisy footer</footer>
        </body></html>
        """

        adapter = MorganPhilipsAdapter(fetch=lambda url: listing if url.endswith("jobs-in-shanghai") else detail)

        jobs = adapter.crawl("2026-06-04")

        self.assertIn("负责CEO办公室战略项目", jobs[0].jd_text)
        self.assertNotIn("Talent Acquisition noisy", jobs[0].jd_text)

    def test_morgan_philips_prefers_json_ld_jobposting_description(self):
        from radar.sources.registry import MorganPhilipsAdapter

        listing = '<a href="/zh-cn/ceo-ea-shanghai-153409/">CEO EA（战略与运营方向）</a>'
        detail = """
        <html><body>
          <div><div>en zh</div></div>
          <script type="application/ld+json">
          {
            "@context": "https://schema.org/",
            "@type": "JobPosting",
            "title": "CEO EA（战略与运营方向）",
            "description": "&lt;br /&gt;我们是一家AI公司。&lt;br /&gt;岗位职责：协助CEO推进战略项目和经营分析。&lt;br /&gt;Requirements&lt;br /&gt;熟悉AI赛道。"
          }
          </script>
          <footer>CONTACT US services Talent Acquisition noisy footer</footer>
        </body></html>
        """

        adapter = MorganPhilipsAdapter(fetch=lambda url: listing if url.endswith("jobs-in-shanghai") else detail)

        jobs = adapter.crawl("2026-06-04")

        self.assertIn("协助CEO推进战略项目", jobs[0].jd_text)
        self.assertNotIn("CONTACT US", jobs[0].jd_text)
        self.assertNotEqual(jobs[0].jd_text, "en zh")

    def test_morgan_philips_anchor_fallback_for_english_jd(self):
        from radar.sources.registry import MorganPhilipsAdapter

        listing = '<a href="/en-cn/china-sales-director-shanghai-153390/">China Sales Director</a>'
        detail = """
        <html><body>
          <nav>CONTACT US services Talent Acquisition noisy menu</nav>
          <main>
            <h2>Responsibilities</h2>
            <p>Develop and execute the regional push-sales strategy for China.</p>
            <h2>Requirements</h2>
            <p>Experience leading commercial teams.</p>
          </main>
          <footer>Apply now privacy policy</footer>
        </body></html>
        """

        adapter = MorganPhilipsAdapter(fetch=lambda url: listing if url.endswith("jobs-in-shanghai") else detail)

        jobs = adapter.crawl("2026-06-04")

        self.assertTrue(jobs[0].jd_text.startswith("Responsibilities"))
        self.assertIn("regional push-sales strategy", jobs[0].jd_text)
        self.assertNotIn("CONTACT US services", jobs[0].jd_text)

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

    def test_gllue_title_prefers_url_slug_and_keeps_card_text_as_excerpt(self):
        from radar.sources.registry import PersolkellyAdapter

        html = """
        <a href="./jobs/%E9%87%87%E8%B4%AD%E4%B8%93%E5%91%98-6488">
          某500强企业 采购专员 面议 汽车及零配件 计算机/互联网/通讯 上海 5-10年 本科
        </a>
        """

        def fetch(url):
            if url.endswith("/jobs"):
                return html
            return "<main><h1>采购专员</h1><p>负责供应商管理、采购执行和采购制度体系搭建。</p><section>公司介绍：某500强汽车零配件企业。</section></main>"

        adapter = PersolkellyAdapter(fetch=fetch)

        jobs = adapter.crawl("2026-06-04")

        self.assertEqual(jobs[0].title, "采购专员")
        self.assertIn("某500强企业", jobs[0].raw_title)
        self.assertIn("汽车及零配件", jobs[0].list_excerpt)
        self.assertIn("供应商管理", jobs[0].jd_text)
        self.assertIn("公司介绍", jobs[0].jd_text)

    def test_detail_text_removes_script_and_head_boilerplate(self):
        def fetch(url):
            if url.endswith("/jobs"):
                return '<a href="/job/mechanical-engineer">Mechanical Engineer</a>'
            return """
            <html>
              <head><title>Mechanical Engineer | Job site</title></head>
              <body>
                <script>window.dataLayer = []; self.__next_s = ["noisy script"];</script>
                <main><h1>Mechanical Engineer</h1><p>Design precision motion equipment and validation plans.</p></main>
              </body>
            </html>
            """

        adapter = DummyAdapter(fetch=fetch)

        jobs = adapter.crawl("2026-06-04")

        self.assertIn("Design precision motion equipment", jobs[0].jd_text)
        self.assertNotIn("window.dataLayer", jobs[0].jd_text)
        self.assertNotIn("Job site", jobs[0].jd_text)

    def test_detail_text_prefers_job_description_section_and_removes_footer(self):
        def fetch(url):
            if url.endswith("/jobs"):
                return '<a href="/job/buyer">Buyer</a>'
            return """
            <html><body>
              工作机会 首页 Toggle navigation menu 职位列表 职位详情 某500强企业 Buyer
              职位描述 负责供应商管理、采购执行和制度体系搭建。
              职位要求 5年以上采购经验。
              立即投递 分享 立即投递 邀请好友 隐私条款 Powered by
            </body></html>
            """

        adapter = DummyAdapter(fetch=fetch)

        jobs = adapter.crawl("2026-06-04")

        self.assertTrue(jobs[0].jd_text.startswith("职位描述"))
        self.assertIn("供应商管理", jobs[0].jd_text)
        self.assertNotIn("工作机会 首页", jobs[0].jd_text)
        self.assertNotIn("隐私条款", jobs[0].jd_text)


if __name__ == "__main__":
    unittest.main()
