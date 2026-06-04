from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Callable
from urllib import request
from urllib.parse import urljoin

from ..models import JobRecord


def default_fetch(url: str, timeout: int = 30) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; WebAdRadar/1.0; +https://openai.com)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")


@dataclass
class CrawlResult:
    jobs: list[JobRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class SourceAdapter:
    slug = ""
    name = ""
    start_url = ""
    include_url_patterns: tuple[str, ...] = ()
    exclude_url_patterns: tuple[str, ...] = ("#", "javascript:", "mailto:")
    job_url_regex: str | None = None
    max_jobs = 60

    def __init__(self, fetch: Callable[[str], str] | None = None):
        self.fetch = fetch or default_fetch

    def crawl(self, run_date: str) -> list[JobRecord]:
        html_text = self.fetch(self.start_url)
        return self.extract_jobs(html_text, self.start_url, run_date)

    def extract_jobs(self, html_text: str, base_url: str, run_date: str) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        seen: set[str] = set()
        for href, text, context in self._iter_links(html_text):
            if not self._looks_like_job_url(href):
                continue
            title = self._clean_title(text, href)
            if not title or len(title) < 2:
                continue
            url = urljoin(base_url, html.unescape(href))
            if url in seen:
                continue
            seen.add(url)
            published_at = self._extract_date(context)
            detail_text = self._fetch_detail_text(url)
            jobs.append(
                JobRecord(
                    source_slug=self.slug,
                    source_name=self.name,
                    title=title,
                    url=url,
                    location=self._extract_location(context),
                    published_at=published_at,
                    first_seen_at=run_date,
                    last_seen_at=run_date,
                    list_excerpt=self._clean_text(context)[:500],
                    detail_text=detail_text,
                    raw={"source_start_url": self.start_url},
                )
            )
            if len(jobs) >= self.max_jobs:
                break
        return jobs

    def _iter_links(self, html_text: str):
        link_re = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
        for match in link_re.finditer(html_text):
            attrs = match.group("attrs")
            href_match = re.search(r"""href\s*=\s*["'](?P<href>[^"']+)["']""", attrs, re.IGNORECASE)
            if not href_match:
                continue
            start = max(0, match.start() - 700)
            end = min(len(html_text), match.end() + 700)
            yield href_match.group("href"), self._strip_tags(match.group("body")), html_text[start:end]
        data_page_re = re.compile(r"""data-page\s*=\s*["'](?P<href>[^"']+)["']""", re.IGNORECASE)
        for match in data_page_re.finditer(html_text):
            start = max(0, match.start() - 700)
            end = min(len(html_text), match.end() + 700)
            href = match.group("href")
            yield href, self._title_from_url(href) or "", html_text[start:end]

    def _looks_like_job_url(self, href: str) -> bool:
        lowered = href.lower()
        if any(pattern.lower() in lowered for pattern in self.exclude_url_patterns):
            return False
        if self.job_url_regex:
            return re.search(self.job_url_regex, href, re.IGNORECASE) is not None
        if self.include_url_patterns:
            return any(pattern.lower() in lowered for pattern in self.include_url_patterns)
        return "job" in lowered or "position" in lowered

    def _extract_date(self, context: str) -> str | None:
        datetime_match = re.search(r"""datetime\s*=\s*["'](\d{4}-\d{2}-\d{2})""", context, re.IGNORECASE)
        if datetime_match:
            return datetime_match.group(1)
        text_match = re.search(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b", context)
        return text_match.group(1).replace("/", "-") if text_match else None

    def _extract_location(self, context: str) -> str | None:
        cleaned = self._clean_text(context)
        for location in ("Shanghai", "Beijing", "Shenzhen", "Suzhou", "Guangzhou", "Hangzhou", "Chengdu", "Wuhan", "Hong Kong", "上海", "北京", "深圳", "苏州", "广州", "杭州", "成都", "武汉"):
            if location in cleaned:
                return location
        return None

    def _strip_tags(self, value: str) -> str:
        return re.sub(r"<[^>]+>", " ", value)

    def _clean_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(self._strip_tags(value))).strip()

    def _clean_title(self, value: str, href: str) -> str:
        title = self._clean_text(value)
        if self._looks_mojibake(title):
            fallback = self._title_from_url(href)
            if fallback:
                return fallback
        return title

    def _fetch_detail_text(self, url: str) -> str | None:
        try:
            detail_html = self.fetch(url)
        except Exception:
            return None
        text = self._clean_text(detail_html)
        return text[:8000] if text else None

    def _looks_mojibake(self, value: str) -> bool:
        markers = ("�", "Ã", "Â", "æ", "å", "ç", "娴", "璐", "閿", "涓", "鍛", "缁", "鐞", "姹")
        return any(marker in value for marker in markers)

    def _title_from_url(self, href: str) -> str | None:
        path = href.split("?", 1)[0].strip("/")
        slug = path.rsplit("/", 1)[-1]
        if slug.isdigit() and "/" in path:
            slug = path.rsplit("/", 2)[-2]
        slug = re.sub(r"_(?:20)?\d{4,}.*$", "", slug, flags=re.IGNORECASE)
        slug = re.sub(r"_[a-z-]+$", "", slug, flags=re.IGNORECASE)
        slug = slug.replace("-", " ").replace("_", " ")
        slug = re.sub(r"\s+", " ", slug).strip()
        return slug or None


def crawl_sources(adapters: list[SourceAdapter], *, run_date: str) -> CrawlResult:
    result = CrawlResult()
    for adapter in adapters:
        try:
            result.jobs.extend(adapter.crawl(run_date))
        except Exception as exc:  # keep one broken site from stopping the whole radar
            result.errors.append(f"{adapter.slug}: {exc}")
    return result
