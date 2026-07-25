from __future__ import annotations

import html
import json
import re
from urllib.parse import quote, urlsplit

from ..models import JobRecord
from .aggregators import AggregatorAdapter, DEFAULT_KEYWORDS, _date_only, _job_id_from_url, _text_value


class WatchJobsAdapter(AggregatorAdapter):
    """WatchJobs adapter for its Next.js embedded public job dataset."""

    slug = "watchjobs"
    name = "WatchJobs"
    start_url = "https://watchjobs.net/"
    search_template = "https://watchjobs.net/?keyword={keyword}"
    allowed_hosts = ("watchjobs.net",)
    keywords = DEFAULT_KEYWORDS
    max_jobs_per_keyword = 20

    def crawl(self, run_date: str, date_from: str | None = None, date_to: str | None = None) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        seen: set[str] = set()
        for keyword in self.keywords:
            search_url = self.search_template.format(keyword=quote(keyword))
            try:
                page = self.fetch(search_url)
            except Exception:
                continue
            for item in _extract_embedded_jobs(page):
                source_job_id = str(item.get("code") or item.get("id") or "").strip()
                if not source_job_id or source_job_id in seen:
                    continue
                seen.add(source_job_id)
                title = _text_value(item.get("title"))
                if not title:
                    continue
                upstream_url = _text_value(item.get("url"))
                company = item.get("company") or {}
                company_name = (
                    _text_value(company.get("name_zh") if isinstance(company, dict) else company)
                    or _text_value(company.get("name_en") if isinstance(company, dict) else None)
                    or _text_value(item.get("company_name"))
                )
                description = "\n".join(filter(None, (
                    _text_value(item.get("description_zh")),
                    _text_value(item.get("min_qualifications_zh")),
                    _text_value(item.get("description_en")),
                    _text_value(item.get("min_qualifications_en")),
                )))
                published_at = _date_only(item.get("date_posted"))
                if (
                    published_at
                    and not self._date_in_range(
                        published_at, date_from, date_to
                    )
                ):
                    continue
                source_url = f"{search_url}#home-job-card-{source_job_id}"
                jobs.append(JobRecord(
                    source_slug=self.slug,
                    source_name=self.name,
                    source_job_id=source_job_id,
                    title=title,
                    url=source_url,
                    canonical_url=source_url,
                    company_name=company_name,
                    location=_text_value(item.get("location")),
                    salary=_salary(item),
                    experience=_nested_name(item.get("dict_experience")),
                    education=_nested_name(item.get("dict_education")),
                    published_at=published_at,
                    updated_at=_date_only(item.get("updated_at")),
                    first_seen_at=run_date,
                    last_seen_at=run_date,
                    list_excerpt=_text_value(item.get("highlight")) or description[:500],
                    detail_text=description[:8000] or None,
                    jd_text=description[:8000] or None,
                    upstream_source=_source_name(upstream_url),
                    upstream_url=upstream_url,
                    upstream_job_id=_job_id_from_url(upstream_url) if upstream_url else None,
                    raw={"search_keyword": keyword, "watchjobs_id": item.get("id")},
                ))
                if len([job for job in jobs if job.raw.get("search_keyword") == keyword]) >= self.max_jobs_per_keyword:
                    break
            if len(jobs) >= self.max_jobs:
                break
        return jobs[: self.max_jobs]


def _extract_embedded_jobs(html_text: str) -> list[dict]:
    results = _extract_next_push_jobs(html_text)
    if results:
        return results
    results = []
    pattern = re.compile(r'\\"jobs\\":(?P<jobs>\[\{.*?\}\]),\\"pagination\\"', re.S)
    for match in pattern.finditer(html_text):
        escaped = match.group("jobs")
        try:
            decoded = json.loads(f'"{escaped}"')
            payload = json.loads(decoded)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, list):
            results.extend(item for item in payload if isinstance(item, dict))
    if results:
        return results
    # Some Next.js versions emit unescaped application state.
    pattern_plain = re.compile(r'"jobs":(?P<jobs>\[\{.*?\}\]),"pagination"', re.S)
    for match in pattern_plain.finditer(html_text):
        try:
            payload = json.loads(html.unescape(match.group("jobs")))
        except json.JSONDecodeError:
            continue
        results.extend(item for item in payload if isinstance(item, dict))
    return results


def _nested_name(value) -> str | None:
    if not isinstance(value, dict):
        return _text_value(value)
    return _text_value(value.get("name_zh")) or _text_value(value.get("name_en"))


def _salary(item: dict) -> str | None:
    minimum, maximum = item.get("min_salary"), item.get("max_salary")
    if minimum is None and maximum is None:
        return None
    unit = "/月" if int(item.get("salary_month") or 0) else ""
    return f"{minimum or '?'}-{maximum or '?'}{unit}"


def _source_name(url: str | None) -> str | None:
    host = urlsplit(url or "").netloc.lower()
    if "zhipin.com" in host or "boss.com" in host:
        return "boss"
    if "liepin.com" in host:
        return "liepin"
    return host.removeprefix("www.") or None


def _extract_next_push_jobs(html_text: str) -> list[dict]:
    pattern = re.compile(
        r'<script>self\.__next_f\.push\(\[1,(?P<payload>".*?")\]\)</script>',
        re.S,
    )
    for match in pattern.finditer(html_text):
        try:
            decoded = json.loads(match.group("payload"))
        except json.JSONDecodeError:
            continue
        if '"jobs"' not in decoded:
            continue
        _, separator, body = decoded.partition(":")
        if not separator:
            continue
        try:
            tree = json.loads(body)
        except json.JSONDecodeError:
            continue
        jobs = _find_jobs(tree)
        if jobs:
            return jobs
    return []


def _find_jobs(value) -> list[dict]:
    if isinstance(value, dict):
        jobs = value.get("jobs")
        if isinstance(jobs, list) and all(isinstance(item, dict) for item in jobs):
            return jobs
        for child in value.values():
            found = _find_jobs(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_jobs(child)
            if found:
                return found
    return []
