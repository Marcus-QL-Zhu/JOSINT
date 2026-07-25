from __future__ import annotations

import html
import json
import re
from urllib.parse import quote, urljoin, urlsplit

from ..models import JobRecord
from .base import SourceAdapter, _html_fragment_to_text, _iter_json_ld_items


DEFAULT_KEYWORDS = (
    "半导体总监",
    "机器人总监",
    "具身智能负责人",
    "AI总监",
    "芯片销售总监",
    "智能硬件总监",
    "大模型负责人",
)
BLOCKED_UPSTREAM_HOSTS = ("boss.com", "zhipin.com", "liepin.com")


class AggregatorAdapter(SourceAdapter):
    """Search an aggregator while treating upstream job sites as metadata only."""

    search_template = ""
    allowed_hosts: tuple[str, ...] = ()
    keywords = DEFAULT_KEYWORDS
    max_jobs_per_keyword = 20
    max_pages = 1

    def crawl(self, run_date: str, date_from: str | None = None, date_to: str | None = None) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        seen: set[str] = set()
        for keyword in self.keywords:
            search_url = self.search_template.format(keyword=quote(keyword))
            listing = self.fetch(search_url)
            for href, text, context in self._iter_links(listing):
                url = urljoin(search_url, html.unescape(href))
                if not self._is_aggregator_job_url(url) or url in seen:
                    continue
                seen.add(url)
                job = self._job_from_detail(url, text, context, run_date)
                if job:
                    if (
                        job.published_at
                        and not self._date_in_range(
                            job.published_at, date_from, date_to
                        )
                    ):
                        continue
                    job.raw["search_keyword"] = keyword
                    jobs.append(job)
                if sum(1 for item in jobs if item.raw.get("search_keyword") == keyword) >= self.max_jobs_per_keyword:
                    break
            if len(jobs) >= self.max_jobs:
                break
        return jobs[: self.max_jobs]

    def _is_aggregator_job_url(self, url: str) -> bool:
        host = urlsplit(url).netloc.lower()
        if any(blocked in host for blocked in BLOCKED_UPSTREAM_HOSTS):
            return False
        if self.allowed_hosts and not any(host == allowed or host.endswith(f".{allowed}") for allowed in self.allowed_hosts):
            return False
        return self._looks_like_job_url(url)

    def _job_from_detail(self, url: str, link_text: str, context: str, run_date: str) -> JobRecord | None:
        try:
            detail_html = self.fetch(url)
        except Exception:
            detail_html = ""
        posting = _first_job_posting(detail_html)
        if not posting:
            return None
        title = _text_value(posting.get("title")) or self._clean_title(link_text, url)
        if not title:
            return None
        company = _company_name(posting.get("hiringOrganization")) or _context_company(context)
        location = _location(posting.get("jobLocation")) or self._extract_location(context)
        jd_text = _html_fragment_to_text(str(posting.get("description") or "")) or self._clean_detail_text(detail_html)
        identifier = posting.get("identifier")
        source_job_id = _identifier_value(identifier) or _job_id_from_url(url)
        full_text = f"{context}\n{detail_html}"
        upstream_source, upstream_url, upstream_job_id = _upstream_metadata(full_text)
        return JobRecord(
            source_slug=self.slug,
            source_name=self.name,
            source_job_id=source_job_id,
            title=title,
            raw_title=self._clean_text(link_text) or title,
            url=url,
            canonical_url=url,
            company_name=company,
            location=location,
            salary=_salary(posting),
            experience=_text_value(posting.get("experienceRequirements")),
            education=_text_value(posting.get("educationRequirements")),
            published_at=_date_only(posting.get("datePosted")),
            updated_at=_date_only(posting.get("validThrough")),
            first_seen_at=run_date,
            last_seen_at=run_date,
            list_excerpt=self._clean_text(context)[:500],
            detail_text=jd_text[:8000] if jd_text else None,
            jd_text=jd_text[:8000] if jd_text else None,
            upstream_source=upstream_source,
            upstream_url=upstream_url,
            upstream_job_id=upstream_job_id,
            raw={"source_start_url": self.start_url, "aggregator": True},
        )


class WatchJobsAdapter(AggregatorAdapter):
    slug = "watchjobs"
    name = "WatchJobs"
    start_url = "https://watchjobs.net/"
    search_template = "https://watchjobs.net/?keyword={keyword}"
    allowed_hosts = ("watchjobs.net",)
    include_url_patterns = ("/job/", "/jobs/", "jobid=", "job_id=")


class TalentChinaAdapter(AggregatorAdapter):
    slug = "talent-cn"
    name = "Talent.com China"
    start_url = "https://cn.talent.com/jobs"
    search_template = "https://cn.talent.com/jobs?k={keyword}&l=%E4%B8%AD%E5%9B%BD"
    allowed_hosts = ("talent.com",)
    job_url_regex = r"/view\?id=[a-z0-9]+"


class BeBeeChinaAdapter(AggregatorAdapter):
    slug = "bebee-cn"
    name = "BeBee China"
    start_url = "https://cn.bebee.com/jobs"
    search_template = "https://cn.bebee.com/jobs?term={keyword}"
    allowed_hosts = ("bebee.com",)
    include_url_patterns = ("/job/", "/jobs/")


def _first_job_posting(html_text: str) -> dict:
    pattern = re.compile(
        r"<script\b[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        re.I | re.S,
    )
    for body in pattern.findall(html_text):
        try:
            payload = json.loads(body.strip())
        except json.JSONDecodeError:
            continue
        for item in _iter_json_ld_items(payload):
            item_type = item.get("@type")
            if item_type == "JobPosting" or (isinstance(item_type, list) and "JobPosting" in item_type):
                return item
    return {}


def _text_value(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("value")
    if isinstance(value, list):
        value = ", ".join(filter(None, (_text_value(item) for item in value)))
    text = str(value).strip() if value is not None else ""
    return text or None


def _company_name(value) -> str | None:
    return _text_value(value)


def _context_company(value: str) -> str | None:
    match = re.search(r"(?:company|公司|企业)\s*[:：]\s*([^<|\n]{2,80})", value, re.I)
    return _clean_fragment(match.group(1)) if match else None


def _location(value) -> str | None:
    if isinstance(value, list):
        values = [_location(item) for item in value]
        return ", ".join(item for item in values if item) or None
    if isinstance(value, dict):
        address = value.get("address", value)
        if isinstance(address, dict):
            return " ".join(
                str(address.get(key, "")).strip()
                for key in ("addressRegion", "addressLocality", "streetAddress")
                if address.get(key)
            ) or None
    return _text_value(value)


def _salary(posting: dict) -> str | None:
    value = posting.get("baseSalary")
    if not value:
        return None
    return _text_value(value) or json.dumps(value, ensure_ascii=False)


def _identifier_value(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("value") or value.get("name")
    return _text_value(value)


def _job_id_from_url(url: str) -> str | None:
    matches = re.findall(r"(?:job(?:id|_id)?[=/\-]|/)([a-z0-9_-]{6,})(?:[/?&#]|$)", url, re.I)
    return matches[-1] if matches else None


def _upstream_metadata(text: str) -> tuple[str | None, str | None, str | None]:
    match = re.search(r"https?://[^\s\"'<>]+(?:boss|zhipin|liepin)[^\s\"'<>]*", text, re.I)
    url = html.unescape(match.group(0)).rstrip(").,") if match else None
    lowered = text.lower()
    source = "boss" if "boss直聘" in text or "zhipin" in lowered or "boss.com" in lowered else None
    if "猎聘" in text or "liepin" in lowered:
        source = "liepin"
    return source, url, _job_id_from_url(url) if url else None


def _date_only(value) -> str | None:
    text = _text_value(value)
    match = re.search(r"\d{4}-\d{2}-\d{2}", text or "")
    return match.group(0) if match else None


def _clean_fragment(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()
