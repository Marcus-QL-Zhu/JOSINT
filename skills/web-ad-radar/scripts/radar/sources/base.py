from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from dataclasses import dataclass, field
from typing import Callable
from urllib import request
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from ..models import JobRecord


def default_fetch(url: str, timeout: int = 30) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
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
    max_pages = 1
    target_job_count_without_dates = 30
    date_aware = False
    prefer_title_from_url = False
    detail_xpaths: tuple[str, ...] = ()

    def __init__(self, fetch: Callable[[str], str] | None = None):
        self.fetch = fetch or default_fetch

    def crawl(self, run_date: str, date_from: str | None = None, date_to: str | None = None) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        seen: set[str] = set()
        for page_number in range(1, self.max_pages + 1):
            page_url = self.page_url(page_number)
            try:
                html_text = self.fetch(page_url)
            except Exception:
                if page_number == 1:
                    raise
                break
            page_jobs = self.extract_jobs(html_text, page_url, run_date, date_from=date_from, date_to=date_to)
            new_jobs = [job for job in page_jobs if job.url not in seen]
            for job in new_jobs:
                seen.add(job.url)
            jobs.extend(new_jobs)
            if self.date_aware and self._page_is_older_than_range(page_jobs, date_from):
                break
            if not self.date_aware and len(jobs) >= self.target_job_count_without_dates:
                break
            if not page_jobs:
                break
            if len(jobs) >= self.max_jobs:
                break
        return jobs[: self.max_jobs]

    def page_url(self, page_number: int) -> str:
        if page_number == 1:
            return self.start_url
        separator = "&" if "?" in self.start_url else "?"
        return f"{self.start_url}{separator}page={page_number}"

    def extract_jobs(self, html_text: str, base_url: str, run_date: str, date_from: str | None = None, date_to: str | None = None) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        seen: set[str] = set()
        for href, text, context in self._iter_links(html_text):
            if not self._looks_like_job_url(href):
                continue
            raw_title = self._clean_text(text)
            title = self._clean_title(text, href)
            if not title or len(title) < 2:
                continue
            url = urljoin(base_url, html.unescape(href))
            if url in seen:
                continue
            seen.add(url)
            published_at = self._extract_date(context)
            if self.date_aware and not self._date_in_range(published_at, date_from, date_to):
                continue
            jd_text = self._fetch_detail_text(url) or self._fallback_listing_detail(text, context)
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
                    detail_text=jd_text,
                    jd_text=jd_text,
                    raw_title=raw_title or title,
                    raw={"source_start_url": self.start_url, "raw_title": raw_title},
                )
            )
            if len(jobs) >= self.max_jobs:
                break
        return jobs

    def _date_in_range(self, value: str | None, date_from: str | None, date_to: str | None) -> bool:
        if not value:
            return False
        if date_from and value < date_from:
            return False
        if date_to and value > date_to:
            return False
        return True

    def _page_is_older_than_range(self, jobs: list[JobRecord], date_from: str | None) -> bool:
        if not date_from or not jobs:
            return False
        dated = [job.published_at for job in jobs if job.published_at]
        return bool(dated) and all(value < date_from for value in dated)

    def _iter_links(self, html_text: str):
        link_re = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
        for match in link_re.finditer(html_text):
            attrs = match.group("attrs")
            href_match = re.search(r"""href\s*=\s*["'](?P<href>[^"']+)["']""", attrs, re.IGNORECASE)
            if not href_match:
                continue
            yield href_match.group("href"), self._strip_tags(match.group("body")), self._link_context(html_text, match.start(), match.end())
        data_page_re = re.compile(r"""data-page\s*=\s*["'](?P<href>[^"']+)["']""", re.IGNORECASE)
        for match in data_page_re.finditer(html_text):
            href = match.group("href")
            yield href, self._title_from_url(href) or "", self._link_context(html_text, match.start(), match.end())

    def _link_context(self, html_text: str, start_index: int, end_index: int) -> str:
        for tag in ("article", "li", "tr"):
            open_start = html_text.rfind(f"<{tag}", 0, start_index)
            close_end = html_text.find(f"</{tag}>", end_index)
            if open_start >= 0 and close_end >= 0:
                return html_text[open_start : close_end + len(tag) + 3]
        start = max(0, start_index - 700)
        end = min(len(html_text), end_index + 700)
        return html_text[start:end]

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
        cleaned = re.sub(r"<(script|style|noscript|svg|head)\b[^>]*>.*?</\1>", " ", value, flags=re.IGNORECASE | re.DOTALL)
        return re.sub(r"<[^>]+>", " ", cleaned)

    def _clean_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(self._strip_tags(value))).strip()

    def _clean_title(self, value: str, href: str) -> str:
        title = self._clean_text(value)
        url_title = self._title_from_url(href)
        if self.prefer_title_from_url and url_title:
            return url_title
        if self._looks_mojibake(title):
            if url_title:
                return url_title
        return title

    def _fetch_detail_text(self, url: str) -> str | None:
        try:
            detail_html = self.fetch(_quote_url(url))
        except Exception:
            return None
        text = self._clean_detail_text(detail_html)
        return text[:8000] if text else None

    def _fallback_listing_detail(self, link_text: str, context: str) -> str | None:
        return None

    def _clean_detail_text(self, html_text: str) -> str:
        candidates = [self._clean_text(value) for value in self._extract_detail_candidates(html_text)]
        candidates.append(self._clean_text(html_text))
        scored = [(self._score_detail_candidate(self._trim_detail_text(candidate)), self._trim_detail_text(candidate)) for candidate in candidates if candidate]
        if not scored:
            return ""
        return max(scored, key=lambda item: item[0])[1]

    def _extract_detail_candidates(self, html_text: str) -> list[str]:
        candidates: list[str] = []
        candidates.extend(_extract_json_ld_job_descriptions(html_text))
        for xpath in self.detail_xpaths:
            value = _extract_xpath_html(html_text, xpath)
            if value:
                candidates.append(value)
        return candidates

    def _trim_detail_text(self, text: str) -> str:
        for marker in ("职位描述", "岗位职责", "任职要求", "职位名称", "关于我们", "你将会做什么", "Job description", "Job Description", "Responsibilities", "Role description", "Requirements"):
            index = text.find(marker)
            if index >= 0:
                text = text[index:]
                break
        for marker in ("立即投递 分享", "邀请好友", "隐私条款", "Powered by", "Privacy Policy", "CONTACT US", "services Talent Acquisition"):
            index = text.find(marker)
            if index >= 0:
                text = text[:index]
        return text.strip()

    def _score_detail_candidate(self, text: str) -> tuple[int, int]:
        anchors = ("职位描述", "岗位职责", "任职要求", "职位名称", "关于我们", "你将会做什么", "Job description", "Job Description", "Responsibilities", "Role description", "Requirements")
        noise = ("CONTACT US", "Talent Acquisition", "Toggle navigation", "window.dataLayer", "self.__next_s")
        return (
            sum(20 for anchor in anchors if anchor in text) - sum(15 for marker in noise if marker in text),
            -len(text),
        )

    def _looks_mojibake(self, value: str) -> bool:
        markers = ("�", "Ã", "Â", "æ", "å", "ç", "娴", "璐", "閿", "涓", "鍛", "缁", "鐞", "姹")
        return any(marker in value for marker in markers)

    def _title_from_url(self, href: str) -> str | None:
        path = href.split("?", 1)[0].strip("/")
        slug = path.rsplit("/", 1)[-1]
        if slug.isdigit() and "/" in path:
            slug = path.rsplit("/", 2)[-2]
        slug = unquote(slug)
        slug = re.sub(r"-\d+$", "", slug, flags=re.IGNORECASE)
        slug = re.sub(r"_(?:20)?\d{4,}.*$", "", slug, flags=re.IGNORECASE)
        slug = re.sub(r"_[a-z-]+$", "", slug, flags=re.IGNORECASE)
        slug = slug.replace("-", " ").replace("_", " ")
        slug = re.sub(r"\s+", " ", slug).strip()
        return slug or None


def crawl_sources(adapters: list[SourceAdapter], *, run_date: str, date_from: str | None = None, date_to: str | None = None) -> CrawlResult:
    result = CrawlResult()
    for adapter in adapters:
        try:
            result.jobs.extend(adapter.crawl(run_date, date_from=date_from, date_to=date_to))
        except Exception as exc:  # keep one broken site from stopping the whole radar
            result.errors.append(f"{adapter.slug}: {exc}")
    return result


def _quote_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe="/%:@"),
            quote(parts.query, safe="=&%:@/?"),
            quote(parts.fragment, safe="=&%:@/?"),
        )
    )


@dataclass
class _HtmlNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_HtmlNode"] = field(default_factory=list)
    text: list[str] = field(default_factory=list)


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = _HtmlNode("_root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs):
        node = _HtmlNode(tag.lower(), dict(attrs))
        self.stack[-1].children.append(node)
        self.stack.append(node)

    def handle_endtag(self, tag: str):
        lowered = tag.lower()
        while len(self.stack) > 1:
            node = self.stack.pop()
            if node.tag == lowered:
                break

    def handle_data(self, data: str):
        if data.strip():
            self.stack[-1].text.append(data)


def _extract_xpath_html(html_text: str, xpath: str) -> str | None:
    builder = _TreeBuilder()
    builder.feed(html_text)
    node: _HtmlNode | None = builder.root
    for tag, index in _parse_simple_xpath(xpath):
        matches = [child for child in (node.children if node else []) if child.tag == tag]
        if len(matches) < index:
            return None
        node = matches[index - 1]
    return _node_text(node) if node else None


def _parse_simple_xpath(xpath: str) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for part in xpath.strip("/").split("/"):
        match = re.fullmatch(r"([a-zA-Z0-9]+)(?:\[(\d+)\])?", part)
        if not match:
            return []
        result.append((match.group(1).lower(), int(match.group(2) or "1")))
    return result


def _node_text(node: _HtmlNode) -> str:
    parts = list(node.text)
    for child in node.children:
        parts.append(_node_text(child))
    return " ".join(part.strip() for part in parts if part.strip())


def _extract_json_ld_job_descriptions(html_text: str) -> list[str]:
    descriptions: list[str] = []
    script_re = re.compile(
        r"<script\b[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(?P<body>.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in script_re.finditer(html_text):
        body = match.group("body").strip()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            fallback = _extract_json_ld_description_field(body)
            if fallback:
                descriptions.append(_html_fragment_to_text(fallback))
            continue
        for item in _iter_json_ld_items(payload):
            if item.get("@type") == "JobPosting" and item.get("description"):
                descriptions.append(_html_fragment_to_text(str(item["description"])))
    return descriptions


def _extract_json_ld_description_field(body: str) -> str | None:
    match = re.search(r'"description"\s*:\s*"(?P<description>.*?)"\s*,\s*"datePosted"', body, re.DOTALL)
    if not match:
        return None
    value = match.group("description")
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value.replace(r"\/", "/").replace(r"\"", '"')


def _iter_json_ld_items(payload):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_json_ld_items(item)
    elif isinstance(payload, dict):
        graph = payload.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _iter_json_ld_items(item)
        yield payload


def _html_fragment_to_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()
