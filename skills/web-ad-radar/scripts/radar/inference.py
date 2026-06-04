from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .models import EmployerGuess, JobRecord


PROPRIETARY_TERMS = {
    "HOS": "Likely Honeywell Operating System; check Honeywell as candidate employer.",
    "FDE": "Likely Forward Deployed Engineer; check Palantir as candidate employer.",
}

CLUSTER_TOKENS = (
    "german chemical company",
    "embodied ai",
    "fortune 500",
    "european industrial",
    "global medical device",
    "semiconductor",
    "德国化工",
    "具身智能",
)


def detect_proprietary_terms(job: JobRecord) -> list[dict[str, str]]:
    text = f"{job.title}\n{job.list_excerpt or ''}\n{job.jd_text or job.detail_text or ''}"
    evidence = []
    for term, interpretation in PROPRIETARY_TERMS.items():
        if re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
            evidence.append({"type": "proprietary_term", "text": term, "interpretation": interpretation})
    return evidence


def cluster_related_jobs(jobs: list[JobRecord]) -> dict[str, list[JobRecord]]:
    result: dict[str, list[JobRecord]] = defaultdict(list)
    lowered = {job.id: f"{job.title} {job.location or ''} {job.list_excerpt or ''} {job.jd_text or job.detail_text or ''}".lower() for job in jobs}
    for job in jobs:
        for other in jobs:
            if job.id == other.id or job.source_slug != other.source_slug:
                continue
            if job.location and other.location and job.location != other.location:
                continue
            if any(token in lowered[job.id] and token in lowered[other.id] for token in CLUSTER_TOKENS):
                result[job.id].append(other)
        result.setdefault(job.id, [])
    return dict(result)


def analyze_jobs(jobs: list[JobRecord], *, minimax: Any, metaso: Any | None = None) -> dict[str, EmployerGuess]:
    clusters = cluster_related_jobs(jobs)
    guesses: dict[str, EmployerGuess] = {}
    for job in jobs:
        evidence = detect_proprietary_terms(job)
        external_sources: list[dict[str, Any]] = []
        search_queries: list[str] = []
        if metaso:
            for query in _build_search_queries(job, evidence)[:5]:
                search_queries.append(query)
                try:
                    for page in metaso.search(query).get("webpages", [])[:3]:
                        external_sources.append(
                            {
                                "title": page.get("title", ""),
                                "url": page.get("link", ""),
                                "summary": page.get("summary", ""),
                            }
                        )
                except Exception as exc:
                    evidence.append({"type": "search_error", "text": str(exc)})
        try:
            payload = _call_reasoner(job, clusters.get(job.id, []), evidence, external_sources, minimax)
            payload = _verify_candidate_with_public_jds(
                job=job,
                payload=payload,
                related_jobs=clusters.get(job.id, []),
                evidence=evidence,
                external_sources=external_sources,
                search_queries=search_queries,
                minimax=minimax,
                metaso=metaso,
            )
            guesses[job.id] = EmployerGuess(
                job_id=job.id,
                guessed_employer=payload.get("guessed_employer"),
                confidence=payload.get("confidence", "low"),
                confidence_score=float(payload.get("confidence_score", 0.0)),
                evidence=evidence,
                cross_job_links=[related.id for related in clusters.get(job.id, [])],
                search_queries=search_queries,
                external_sources=external_sources,
                reasoning_summary=payload.get("reasoning_summary", ""),
                review_flags=payload.get("review_flags", []),
            )
        except Exception as exc:
            guesses[job.id] = EmployerGuess(
                job_id=job.id,
                guessed_employer=None,
                confidence="low",
                confidence_score=0.0,
                evidence=evidence,
                cross_job_links=[related.id for related in clusters.get(job.id, [])],
                search_queries=search_queries,
                external_sources=external_sources,
                reasoning_summary="Employer inference was not completed.",
                review_flags=[f"Inference API failed: {exc}"],
            )
    return guesses


def _verify_candidate_with_public_jds(
    *,
    job: JobRecord,
    payload: dict[str, Any],
    related_jobs: list[JobRecord],
    evidence: list[dict[str, Any]],
    external_sources: list[dict[str, Any]],
    search_queries: list[str],
    minimax: Any,
    metaso: Any | None,
) -> dict[str, Any]:
    employer = payload.get("guessed_employer")
    if not employer or not metaso:
        return payload
    query = _candidate_jd_query(employer, job)
    search_queries.append(query)
    try:
        pages = metaso.search(query).get("webpages", [])[:5]
    except Exception as exc:
        evidence.append({"type": "candidate_jd_check_error", "text": str(exc)})
        return payload
    evidence.append(
        {
            "type": "candidate_jd_check",
            "text": query,
            "summary": f"Checked whether {employer} has similar public job ads for this role.",
        }
    )
    for page in pages:
        external_sources.append(
            {
                "title": page.get("title", ""),
                "url": page.get("link", ""),
                "summary": page.get("summary", ""),
            }
        )
    return _call_reasoner(job, related_jobs, evidence, external_sources, minimax)


def _candidate_jd_query(employer: str, job: JobRecord) -> str:
    parts = [employer, job.title]
    if job.location:
        parts.append(job.location)
    parts.extend(["job", "JD"])
    return " ".join(part for part in parts if part)


def _build_search_queries(job: JobRecord, evidence: list[dict[str, Any]]) -> list[str]:
    queries = [f"{item['text']} employer company clue" for item in evidence[:3]]
    text = f"{job.title} {job.location or ''} {job.list_excerpt or ''} {job.jd_text or job.detail_text or ''}".lower()
    location = job.location or ""
    if "german chemical company" in text:
        queries.append(f"German chemical company {location} employer".strip())
    if "embodied ai" in text or "具身智能" in text:
        queries.append(f"embodied AI company {location} employer".strip())
    if "semiconductor" in text:
        queries.append(f"semiconductor company {location} employer".strip())
    deduped: list[str] = []
    for query in queries:
        if query and query not in deduped:
            deduped.append(query)
    return deduped


def _call_reasoner(
    job: JobRecord,
    related_jobs: list[JobRecord],
    evidence: list[dict[str, Any]],
    external_sources: list[dict[str, Any]],
    minimax: Any,
) -> dict[str, Any]:
    prompt = {
        "task": "推断这条猎头职位广告背后的隐藏雇主公司。只返回严格 JSON，不要返回 Markdown。",
        "language_requirement": "所有自然语言字段必须使用简体中文；JSON key 保持英文。reasoning_summary 和 review_flags 必须是中文。",
        "job": {
            "title": job.title,
            "location": job.location,
            "url": job.url,
            "jd_text": job.jd_text or job.detail_text or "",
            "list_excerpt": job.list_excerpt or "",
            "company_description": job.company_description or "",
        },
        "related_jobs": [
            {"title": related.title, "location": related.location, "jd_text": related.jd_text or related.detail_text or "", "list_excerpt": related.list_excerpt or ""}
            for related in related_jobs[:5]
        ],
        "evidence": evidence,
        "external_sources": external_sources,
        "schema": {
            "guessed_employer": "string or null",
            "confidence": "high|medium|low",
            "confidence_score": "number between 0 and 1",
            "reasoning_summary": "中文，简短说明证据链和为什么支持/不支持该雇主猜测",
            "review_flags": ["中文，列出仍需人工复核的疑点"],
        },
    }
    content = minimax.chat(
        [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        thinking_type="adaptive",
        reasoning_split=True,
        usage_context={"job_ids": [job.id], "batch_size": 1},
    )
    return _parse_json_object(content)


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
