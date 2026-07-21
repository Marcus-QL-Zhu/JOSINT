#!/usr/bin/env python3
"""Subset employer analysis for Feishu/OpenClaw runs.

Filters local jobs to a configurable high-interest subset and runs the
full hidden-employer inference pipeline using MiniMax M3 plus optional
Metaso evidence search and post-verification.

This module is a thin wrapper that:
1. Filters jobs via subset rules.
2. Calls v1 `analyze_jobs(jobs, *, minimax, metaso)` for full inference.
3. Maps the v1 EmployerGuess to the AnalysisResult dataclass.
4. Writes employer_guess + confidence back to the Feishu Bitable.

Confidence threshold follows the existing daily digest threshold: 0.7.
"""
from __future__ import annotations

import logging
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .client import BitableClient
from .dedup import url_hash
from ..inference import analyze_jobs as v1_analyze_jobs
from ..models import JobRecord, EmployerGuess


log = logging.getLogger(__name__)


# Subset filter keywords for high-interest job intelligence workflows.
# Scheduled employer inference is intentionally narrower than full crawling:
# senior/management role AND (target technical industry + business-line function,
# OR explicit AI adoption/AI leadership role in any industry).
TARGET_INDUSTRY_KEYWORDS = [
    "robot", "robotics", "humanoid", "embodied ai", "automation robot",
    "\u673a\u5668\u4eba", "\u5177\u8eab\u667a\u80fd", "\u4eba\u5f62\u673a\u5668\u4eba", "\u534f\u4f5c\u673a\u5668\u4eba",
    "semiconductor", "wafer", "chip", "eda", "ic design", "foundry",
    "\u534a\u5bfc\u4f53", "\u82af\u7247", "\u6676\u5706", "\u5c01\u6d4b", "\u96c6\u6210\u7535\u8def", "\u529f\u7387\u5668\u4ef6",
    "artificial intelligence", " genai", " generative ai", "machine learning", "large language model", " llm",
    "\u4eba\u5de5\u667a\u80fd", "\u5927\u6a21\u578b", "\u751f\u6210\u5f0fai", "\u751f\u6210\u5f0f AI", "\u673a\u5668\u5b66\u4e60", "\u7b97\u6cd5",
    "consumer electronics", "smart hardware", "wearable", "iot device", "home appliance electronics",
    "\u6d88\u8d39\u7535\u5b50", "\u667a\u80fd\u786c\u4ef6", "\u53ef\u7a7f\u6234", "\u667a\u80fd\u7a7f\u6234", "\u5bb6\u7535\u7535\u5b50",
]
TARGET_INDUSTRY_LABELS = {"\u673a\u5668\u4eba", "\u534a\u5bfc\u4f53"}

BUSINESS_FUNCTION_KEYWORDS = [
    "r&d", "research", "development", "engineering", "algorithm", "software", "embedded", "product development",
    "\u7814\u53d1", "\u5f00\u53d1", "\u7b97\u6cd5", "\u5d4c\u5165\u5f0f", "\u8f6f\u4ef6", "\u4ea7\u54c1\u7814\u53d1",
    "sales", "business development", " bd ", "commercial", "key account", "go-to-market", "gtm",
    "\u9500\u552e", "\u5546\u52a1\u62d3\u5c55", "\u5ba2\u6237\u5f00\u53d1", "\u5927\u5ba2\u6237", "\u5546\u4e1a\u5316",
    "marketing", "brand", "market", "growth", "product marketing",
    "\u5e02\u573a", "\u54c1\u724c", "\u589e\u957f", "\u4ea7\u54c1\u5e02\u573a",
    "operations", "business operations", "commercial operations", "product operations", "user operations", "strategy operations",
    "\u8fd0\u8425", "\u4e1a\u52a1\u8fd0\u8425", "\u5546\u4e1a\u8fd0\u8425", "\u4ea7\u54c1\u8fd0\u8425", "\u7528\u6237\u8fd0\u8425", "\u6218\u7565\u8fd0\u8425",
]
BUSINESS_FUNCTION_LABELS = {"\u7814\u53d1", "\u9500\u552e", "\u5e02\u573a"}

SUPPORT_FUNCTION_KEYWORDS = [
    "hr", "human resources", "talent acquisition", "recruiter", "c&b", "compensation", "benefits", "hrbp",
    "\u4eba\u4e8b", "\u62db\u8058", "\u85aa\u916c", "\u7ec4\u7ec7\u53d1\u5c55",
    "finance", "accounting", "fp&a", "tax", "audit", "treasury", "controller",
    "\u8d22\u52a1", "\u4f1a\u8ba1", "\u7a0e\u52a1", "\u5ba1\u8ba1", "\u8d44\u91d1",
    "legal", "compliance", "risk", "intellectual property", " ip ",
    "\u6cd5\u52a1", "\u5408\u89c4", "\u98ce\u63a7", "\u77e5\u8bc6\u4ea7\u6743",
    "admin", "administration", "office manager", "secretary", "assistant", "facility",
    "supply chain", "procurement", "sourcing", "quality", "logistics", "manufacturing", "production", "plant", "ehs",
    "\u884c\u653f", "\u529e\u516c\u5ba4", "\u79d8\u4e66", "\u52a9\u7406", "\u8bbe\u65bd",
    "\u4f9b\u5e94\u94fe", "\u91c7\u8d2d", "\u5bfb\u6e90", "\u8d28\u91cf", "\u7269\u6d41", "\u5236\u9020", "\u751f\u4ea7", "\u5de5\u5382",
]
SUPPORT_FUNCTION_LABELS = {"\u4eba\u4e8b", "\u8d22\u52a1", "\u6cd5\u52a1", "\u884c\u653f", "\u4f9b\u5e94\u94fe", "\u751f\u4ea7"}

SENIORITY_INCLUDE_KEYWORDS = [
    "manager", "senior manager", "director", "head of", "vp", "vice president", "general manager", "gm ",
    "managing director", "country manager", "regional manager", "chief", "cxo", "ceo", "cto", "coo", "cmo",
    "\u8d1f\u8d23\u4eba", "\u7ecf\u7406", "\u9ad8\u7ea7\u7ecf\u7406", "\u603b\u76d1", "\u526f\u603b\u88c1", "\u603b\u7ecf\u7406", "\u9996\u5e2d", "\u533a\u57df\u8d1f\u8d23\u4eba", "\u56fd\u5bb6\u7ecf\u7406",
]
ENTRY_LEVEL_EXCLUDE_KEYWORDS = [
    "intern", "trainee", "graduate", "entry level", "junior", "assistant", "associate", "specialist",
    "\u5b9e\u4e60", "\u7ba1\u57f9", "\u5e94\u5c4a", "\u521d\u7ea7", "\u52a9\u7406", "\u4e13\u5458",
]

AI_LEADERSHIP_KEYWORDS = [
    "head of ai", "ai head", "ai leader", "ai director", "ai manager", "chief ai", "ai transformation",
    "ai strategy", "ai implementation", "ai adoption", "enterprise ai", "genai lead", "genai director",
    "\u4eba\u5de5\u667a\u80fd\u8d1f\u8d23\u4eba", "ai\u8d1f\u8d23\u4eba", "AI\u8d1f\u8d23\u4eba", "AI \u8d1f\u8d23\u4eba", "ai \u8d1f\u8d23\u4eba", "ai\u843d\u5730", "AI\u843d\u5730", "AI \u843d\u5730",
    "ai\u8f6c\u578b", "AI\u8f6c\u578b", "AI \u8f6c\u578b", "\u667a\u80fd\u5316\u8d1f\u8d23\u4eba", "\u5927\u6a21\u578b\u8d1f\u8d23\u4eba", "\u751f\u6210\u5f0fai\u8d1f\u8d23\u4eba", "\u751f\u6210\u5f0fAI\u8d1f\u8d23\u4eba",
]

# Confidence threshold for "high confidence" in the daily digest
HIGH_CONFIDENCE_THRESHOLD = 0.7


@dataclass
class AnalysisResult:
    job_id: str
    url: str
    industry: str
    function: str
    guessed_employer: str | None
    confidence: str  # "high" | "medium" | "low" (label, v1-style)
    confidence_score: float  # 0.0 - 1.0 (v1-style)
    reasoning: str  # reasoning_summary from v1
    review_flags: list[str] = field(default_factory=list)
    external_sources: list[dict] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    cross_job_links: list[str] = field(default_factory=list)
    is_high_confidence: bool = False

    @classmethod
    def from_v1_guess(cls, guess: EmployerGuess, job: JobRecord) -> "AnalysisResult":
        score = float(guess.confidence_score or 0.0)
        score = max(0.0, min(1.0, score))
        return cls(
            job_id=guess.job_id,
            url=job.url,
            industry=job.industry_label or job.industry or "",
            function=job.function_label or job.function or "",
            guessed_employer=guess.guessed_employer,
            confidence=guess.confidence or "low",
            confidence_score=score,
            reasoning=guess.reasoning_summary or "",
            review_flags=list(guess.review_flags or []),
            external_sources=list(guess.external_sources or []),
            search_queries=list(guess.search_queries or []),
            cross_job_links=list(guess.cross_job_links or []),
            is_high_confidence=score >= HIGH_CONFIDENCE_THRESHOLD,
        )


def is_subset(job: JobRecord) -> bool:
    """True if this job should receive scheduled hidden-employer inference."""
    title_text = _title_text(job)
    all_text = _job_text(job)
    if not _has_senior_management_level(title_text):
        return False
    if _has_support_function(job, _function_text(job)):
        return False
    if _has_ai_leadership_role(_function_text(job)):
        return True
    return _has_target_industry(job, all_text) and _has_business_function(job, all_text)


def _title_text(job: JobRecord) -> str:
    return f"{job.title or ''}\n{job.raw_title or ''}".lower()


def _job_text(job: JobRecord) -> str:
    return "\n".join([
        job.title or "",
        job.raw_title or "",
        job.industry_label or "",
        job.industry or "",
        job.function_label or "",
        job.function or "",
        job.list_excerpt or "",
        job.jd_text or job.detail_text or "",
        job.company_description or "",
    ]).lower()


def _function_text(job: JobRecord) -> str:
    return "\n".join([
        job.title or "",
        job.raw_title or "",
        job.function_label or "",
        job.function or "",
        job.list_excerpt or "",
    ]).lower()


def _has_senior_management_level(title_text: str) -> bool:
    if _contains_any(title_text, ENTRY_LEVEL_EXCLUDE_KEYWORDS):
        return False
    return _contains_any(title_text, SENIORITY_INCLUDE_KEYWORDS)


def _has_target_industry(job: JobRecord, text: str) -> bool:
    label_text = f"{job.industry_label or ''}\n{job.industry or ''}"
    return any(label in label_text for label in TARGET_INDUSTRY_LABELS) or _contains_any(text, TARGET_INDUSTRY_KEYWORDS)


def _has_business_function(job: JobRecord, text: str) -> bool:
    label_text = f"{job.function_label or ''}\n{job.function or ''}"
    return any(label in label_text for label in BUSINESS_FUNCTION_LABELS) or _contains_any(text, BUSINESS_FUNCTION_KEYWORDS)


def _has_support_function(job: JobRecord, text: str) -> bool:
    label_text = f"{job.function_label or ''}\n{job.function or ''}"
    return any(label in label_text for label in SUPPORT_FUNCTION_LABELS) or _contains_any(text, SUPPORT_FUNCTION_KEYWORDS)


def _has_ai_leadership_role(text: str) -> bool:
    return _contains_any(text, AI_LEADERSHIP_KEYWORDS)


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(_keyword_in_text(text, keyword.lower()) for keyword in keywords)


def _keyword_in_text(text: str, keyword: str) -> bool:
    normalized = keyword.strip().lower()
    if not normalized:
        return False
    if any("\u4e00" <= char <= "\u9fff" for char in normalized):
        return normalized in text
    if re.fullmatch(r"[a-z0-9&/+-]+", normalized):
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text) is not None
    return normalized in text

def filter_subset(jobs: Iterable[JobRecord]) -> list[JobRecord]:
    return [j for j in jobs if is_subset(j)]


def analyze_subset(
    jobs: list[JobRecord],
    *,
    minimax: Any,
    metaso: Any | None = None,
) -> list[AnalysisResult]:
    """Run the FULL v1 inference pipeline (with optional Metaso evidence)
    on the subset of jobs. Returns v2 AnalysisResult list.

    Args:
        jobs: subset jobs (caller should pre-filter via filter_subset)
        minimax: MiniMaxClient-compatible object used by v1 inference.
        metaso: MetasoClient for evidence search + post-verification.
            Pass None to skip evidence (--without-evidence mode).
    """
    if not jobs:
        return []
    log.info(
        "analyze_subset: %d jobs, with_evidence=%s",
        len(jobs), metaso is not None,
    )
    v1_guesses = v1_analyze_jobs(jobs, minimax=minimax, metaso=metaso)
    results: list[AnalysisResult] = []
    for job in jobs:
        guess = v1_guesses.get(job.id)
        if not guess:
            # v1 dropped the job (shouldn't happen, but defensive)
            results.append(AnalysisResult(
                job_id=job.id, url=job.url,
                industry=job.industry_label or job.industry or "",
                function=job.function_label or job.function or "",
                guessed_employer=None,
                confidence="low", confidence_score=0.0,
                reasoning="v1 analyze_jobs returned no guess",
            ))
            continue
        results.append(AnalysisResult.from_v1_guess(guess, job))
    return results


def write_back_to_bitable(
    client: BitableClient,
    results: list[AnalysisResult],
    *,
    run_id: str = "",
    model: str = "MiniMax-M3",
) -> int:
    """Write employer_guess + confidence back to the Bitable.

    Uses the url_hash index in the Bitable to find the record_id.
    Returns the number of successful writes.
    """
    written = 0
    if not results:
        return 0
    # Build a hash -> record_id index from a single paginated read
    all_records = client.list_all_records()
    hash_to_rid: dict[str, str] = {}
    for record in all_records:
        fields = record.get("fields", {}) or {}
        h = fields.get("url_hash")
        # Feishu returns text fields as either a bare string OR a
        # list of {'text': ..., 'type': 'text'} wrappers (varies by
        # endpoint / encoding). Handle both.
        if isinstance(h, list) and h:
            h = h[0].get("text", "") if isinstance(h[0], dict) else str(h[0])
        if isinstance(h, str) and h:
            hash_to_rid[h] = record.get("record_id", "")

    for r in results:
        rid = hash_to_rid.get(url_hash(r.url))
        if not rid:
            log.warning("Analysis write-back: no bitable record for url=%s", r.url)
            continue
        try:
            # confidence field type in Bitable is numeric.
            # employer_guess is text; write the name or "Unknown".
            client.update_record(rid, {
                "employer_guess": r.guessed_employer or "Unknown",
                "confidence": r.confidence_score,
                "analysis_status": "success" if r.guessed_employer else "no_guess",
                "analysis_run_id": run_id,
                "analysis_model": model,
                "analyzed_at": _now_ms(),
                "reasoning_summary": _truncate_text(r.reasoning),
                "review_flags_json": _json_field(r.review_flags),
                "evidence_json": _json_field({
                    "external_sources": r.external_sources,
                    "search_queries": r.search_queries,
                    "cross_job_links": r.cross_job_links,
                }),
                "external_sources_json": _json_field(r.external_sources),
                "search_queries_json": _json_field(r.search_queries),
                "cross_job_links_json": _json_field(r.cross_job_links),
            })
            written += 1
        except Exception as e:  # noqa: BLE001
            log.error("Failed to write analysis for %s: %s", r.url, e)
    return written


def write_analysis_logs_to_bitable(
    client: BitableClient,
    results: list[AnalysisResult],
    *,
    run_id: str = "",
    model: str = "MiniMax-M3",
) -> int:
    """Append full-ish analysis process rows to an optional analysis log table."""
    written = 0
    for r in results:
        try:
            client.create_record({
                "analysis_run_id": run_id,
                "analysis_model": model,
                "analyzed_at": _now_ms(),
                "job_id": r.job_id,
                "url": {"link": r.url, "text": r.job_id[:60] or r.url},
                "url_hash": url_hash(r.url),
                "employer_guess": r.guessed_employer or "Unknown",
                "confidence": r.confidence_score,
                "analysis_status": "success" if r.guessed_employer else "no_guess",
                "reasoning_summary": _truncate_text(r.reasoning, limit=4500),
                "review_flags_json": _json_field(r.review_flags),
                "external_sources_json": _json_field(r.external_sources),
                "search_queries_json": _json_field(r.search_queries),
                "cross_job_links_json": _json_field(r.cross_job_links),
            })
            written += 1
        except Exception as e:  # noqa: BLE001
            log.error("Failed to write analysis log for %s: %s", r.url, e)
    return written


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _json_field(value: Any, *, limit: int = 4500) -> str:
    return _truncate_text(json.dumps(value, ensure_ascii=False), limit=limit)


def _truncate_text(value: str, *, limit: int = 4500) -> str:
    return (value or "")[:limit]
