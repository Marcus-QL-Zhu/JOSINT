from __future__ import annotations

import json
import re
from typing import Callable
from urllib import parse, request

from .base import SourceAdapter
from ..models import JobRecord


class RobertWaltersAdapter(SourceAdapter):
    slug = "robert-walters"
    name = "Robert Walters China"
    start_url = "https://www.robertwalters.cn/jobs.html"
    include_url_patterns = ("/jobs/", "/job/")


class RobertHalfAdapter(SourceAdapter):
    slug = "robert-half"
    name = "Robert Half China"
    start_url = "https://www.roberthalf.cn/cn/en/jobs"
    include_url_patterns = ("/cn/en/job/", "/jobs/")
    max_pages = 3


class MorganPhilipsAdapter(SourceAdapter):
    slug = "morgan-philips"
    name = "Morgan Philips Mainland China"
    start_url = "https://jobs.morganphilips.cn/en-cn/jobs-in-shanghai"
    include_url_patterns = ("/en-cn/", "/zh-cn/")
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs-in-", "/shortlist", "?")
    job_url_regex = r"/(?:en-cn|zh-cn)/[^/?#]+-\d+/?$"
    detail_xpaths = ("/html/body/div[2]/div[1]/div[2]/div[2]/div[2]/div[1]/div/div/div/div/div",)
    max_pages = 3


class MorganMcKinleyAdapter(SourceAdapter):
    slug = "morgan-mckinley"
    name = "Morgan McKinley Mainland China"
    start_url = "https://www.morganmckinley.com.cn/en/jobs"
    include_url_patterns = ("/en/job/", "/zh/job/", "/en/jobs/", "/zh-hans/jobs/")
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs/discipline/", "/jobs/send-us-your-cv")
    job_url_regex = r"/(?:en|zh-hans)/jobs/[^/]+/[^/]+/\d+/?$|/(?:en|zh)/job/[^/?#]+/?$"
    max_pages = 3


class HaysAdapter(SourceAdapter):
    slug = "hays"
    name = "Hays China"
    start_url = "https://careers.hays-china.cn/jobs"
    include_url_patterns = ("/jobs/",)
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs?page=", "/zh-cn/jobs")
    job_url_regex = r"(?:^\.?/jobs/|/jobs/)[^?/#]+-\d+/?$"
    max_pages = 3

    def _clean_title(self, value: str, href: str) -> str:
        url_title = self._title_from_url(href)
        if url_title:
            if re.fullmatch(r"[a-z0-9/]+", url_title, flags=re.IGNORECASE):
                return url_title.upper().replace("PMSPM", "PM/SPM")
            if re.fullmatch(r"[a-z][a-z -]+", url_title, flags=re.IGNORECASE):
                return url_title.title()
            return url_title
        return super()._clean_title(value, href)

    def _fallback_listing_detail(self, link_text: str, context: str) -> str | None:
        return self._clean_text(link_text) or None


class RandstadAdapter(SourceAdapter):
    slug = "randstad"
    name = "Randstad China"
    start_url = "https://www.randstad.cn/en/jobs/"
    include_url_patterns = ("/jobs/",)
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs/s-", "/jobs/q-")
    job_url_regex = r"/jobs/.+_\d+_(?:en|cn)/?$|/jobs/.+_\d+/?$"
    max_pages = 3


class RgfAdapter(SourceAdapter):
    slug = "rgf"
    name = "RGF Professional Recruitment China"
    start_url = "https://www.rgf-professional.com.cn/zh/jobs"
    include_url_patterns = ("/zh/jobs/", "/en/jobs/", "/job/")
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs/location/", "/jobs/industry/", "/jobs/function/")
    max_pages = 3


class IntelliProAdapter(SourceAdapter):
    slug = "intellipro"
    name = "IntelliPro / 英特利普"
    start_url = "https://intellipro.applytojob.com/"
    include_url_patterns = ("/apply/",)
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("#job-description",)
    job_url_regex = r"/apply/[A-Za-z0-9]+/[^/?#]+/?$"
    max_pages = 1
    max_jobs = 30


class RisfondAdapter(SourceAdapter):
    slug = "risfond"
    name = "Risfond / 锐仕方达"
    start_url = "https://www.risfond.com/job/"
    date_aware = True
    max_pages = 12
    max_jobs = 60

    def crawl(self, run_date: str, date_from: str | None = None, date_to: str | None = None) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for page_number in range(1, self.max_pages + 1):
            page = self._fetch_job_list(page_number)
            items = page.get("Data") or []
            if not items:
                break
            page_had_in_range = False
            page_dates: list[str] = []
            for item in items:
                published_at = str(item.get("LastUpdatedStr") or "")[:10] or None
                if published_at:
                    page_dates.append(published_at)
                if not self._date_in_range(published_at, date_from, date_to):
                    continue
                page_had_in_range = True
                job_id = str(item.get("JobId") or "").strip()
                if not job_id:
                    continue
                url = f"https://www.risfond.com/job/job-{job_id}"
                jd_text = self._fetch_detail_text(url)
                jobs.append(
                    JobRecord(
                        source_slug=self.slug,
                        source_name=self.name,
                        title=self._clean_text(str(item.get("Title") or "")),
                        url=url,
                        location=self._clean_text(str(item.get("LocationsStr") or "")) or None,
                        salary=self._clean_text(str(item.get("SalaryStr") or "")) or None,
                        published_at=published_at,
                        first_seen_at=run_date,
                        last_seen_at=run_date,
                        list_excerpt=self._clean_text(str(item.get("ClientIntroduction") or ""))[:500],
                        detail_text=jd_text,
                        jd_text=jd_text,
                        raw_title=self._clean_text(str(item.get("Title") or "")),
                        raw={"source_start_url": self.start_url, "raw": item},
                    )
                )
            if page_dates and all(value < (date_from or "") for value in page_dates) and not page_had_in_range:
                break
            if len(jobs) >= self.max_jobs:
                break
        return jobs[: self.max_jobs]

    def _fetch_job_list(self, page_number: int) -> dict:
        data = parse.urlencode(
            {
                "s": 10,
                "p": page_number,
                "industry": "",
                "locationname": "",
                "ssalary": "",
                "esalary": "",
                "edulevel": "",
                "occupation": "",
                "slife": "",
                "elife": "",
                "enterprise": "",
                "releasetime": "",
                "keywords": "",
                "order": 0,
            }
        ).encode("utf-8")
        req = request.Request(
            "https://www.risfond.com/Services/BaseData.ashx?action=getjoblist",
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": self.start_url,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))


class PersolkellyAdapter(SourceAdapter):
    slug = "persolkelly"
    name = "PERSOLKELLY China"
    start_url = "https://persolchina.career.gllue.com/jobs"
    include_url_patterns = ("/job/", "/jobs/", "/zh-hans/job/")
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs?page=", "/zh-cn/jobs")
    job_url_regex = r"(?:^\.?/jobs/|/jobs/)[^?/#]+-\d+/?$"
    prefer_title_from_url = True


ADAPTERS: dict[str, type[SourceAdapter]] = {
    cls.slug: cls
    for cls in (
        RobertWaltersAdapter,
        RobertHalfAdapter,
        MorganPhilipsAdapter,
        MorganMcKinleyAdapter,
        HaysAdapter,
        RandstadAdapter,
        RgfAdapter,
        IntelliProAdapter,
        RisfondAdapter,
    )
}


def build_adapters(slugs: list[str], fetch: Callable[[str], str] | None = None) -> list[SourceAdapter]:
    return [ADAPTERS[slug](fetch=fetch) for slug in slugs if slug in ADAPTERS]
