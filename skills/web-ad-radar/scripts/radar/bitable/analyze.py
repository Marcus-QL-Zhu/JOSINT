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
from dataclasses import dataclass, field
from typing import Any, Iterable

from .client import BitableClient
from .dedup import url_hash
from ..inference import analyze_jobs as v1_analyze_jobs
from ..models import JobRecord, EmployerGuess


log = logging.getLogger(__name__)


# Subset filter keywords for high-interest job intelligence workflows.
SUBSET_INDUSTRIES = ["机器人", "AI", "artificial intelligence", "robotics", "robot"]
SUBSET_FUNCTIONS = ["研发", "开发", "R&D", "research", "engineering", "engineer"]

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
    """True if this job matches the configured high-interest subset."""
    industry = (job.industry_label or job.industry or "").lower()
    function = (job.function_label or job.function or "").lower()
    return (
        any(k.lower() in industry for k in SUBSET_INDUSTRIES)
        or any(k.lower() in function for k in SUBSET_FUNCTIONS)
    )


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


def write_back_to_bitable(client: BitableClient, results: list[AnalysisResult]) -> int:
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
            })
            written += 1
        except Exception as e:  # noqa: BLE001
            log.error("Failed to write analysis for %s: %s", r.url, e)
    return written
