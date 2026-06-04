from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class JobRecord:
    source_slug: str
    source_name: str
    title: str
    url: str
    canonical_url: str | None = None
    location: str | None = None
    industry: str | None = None
    function: str | None = None
    salary: str | None = None
    job_type: str | None = None
    published_at: str | None = None
    updated_at: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    language: str | None = None
    list_excerpt: str | None = None
    detail_text: str | None = None
    jd_text: str | None = None
    company_description: str | None = None
    raw_title: str | None = None
    function_label: str | None = None
    industry_label: str | None = None
    label_confidence: str | None = None
    label_evidence: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    id: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.canonical_url:
            self.canonical_url = self.url
        if self.jd_text is None:
            self.jd_text = self.detail_text
        if self.detail_text is None:
            self.detail_text = self.jd_text
        if self.raw_title is None:
            self.raw_title = self.title
        digest = hashlib.sha256(f"{self.source_slug}|{self.canonical_url}".encode("utf-8")).hexdigest()[:12]
        self.id = f"{self.source_slug}:{digest}"


@dataclass(slots=True)
class EmployerGuess:
    job_id: str
    guessed_employer: str | None
    confidence: str
    confidence_score: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    cross_job_links: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    external_sources: list[dict[str, Any]] = field(default_factory=list)
    reasoning_summary: str = ""
    review_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SourceConfig:
    slug: str
    name: str
    url: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class RunConfig:
    workspace: Any
    env_path: Any
    output_dir: Any
    data_dir: Any
    companies: list[str]
    crawl_only: bool
    date_from: str
    date_to: str
