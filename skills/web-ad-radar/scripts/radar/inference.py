from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

from .models import EmployerGuess, JobRecord


log = logging.getLogger(__name__)


# Per-job wall-clock cap for analyze_jobs. Each job must finish within
# this budget, otherwise it is marked low-confidence and the run moves on.
# This replaces a previous single global timeout that killed the whole run
# after 15 minutes; a single slow job can never block the rest.
PER_JOB_TIMEOUT_S = 300  # 5 minutes


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


def analyze_jobs(
    jobs: list[JobRecord],
    *,
    minimax: Any,
    metaso: Any | None = None,
    per_job_timeout_s: int = PER_JOB_TIMEOUT_S,
) -> dict[str, EmployerGuess]:
    """Run hidden-employer inference on each job with a per-job wall-clock cap.

    Each job gets `per_job_timeout_s` (default 300s) to complete. If a
    job exceeds that, we record a low-confidence "per_job_timeout" guess
    and move on; one slow API call no longer blocks the rest of the subset.
    Sequential, not parallel: the M3
    client and Metaso client are rate-limited, and we want predictable
    load in constrained server environments.
    """
    clusters = cluster_related_jobs(jobs)
    guesses: dict[str, EmployerGuess] = {}
    for job in jobs:
        guesses[job.id] = _analyze_one_job_with_timeout(
            job, clusters, minimax, metaso, per_job_timeout_s
        )
    return guesses


def _analyze_one_job_with_timeout(
    job: JobRecord,
    clusters: dict[str, list[JobRecord]],
    minimax: Any,
    metaso: Any | None,
    timeout_s: int,
) -> EmployerGuess:
    """Run the per-job inference inside a single-shot thread with a hard timeout.

    Why a thread: the previous design ran the whole `analyze_jobs` loop
    with a 15-minute outer cap that killed the
    pipeline if any one job was slow. Now we use `ThreadPoolExecutor`
    per-job so that a stuck HTTP call can be abandoned after `timeout_s`
    without affecting the other jobs. The thread is not joined after a
    timeout (it leaks briefly until the work returns or the process
    exits), but that is acceptable: the alternative (signal.alarm) only
    works in the main thread and would interrupt urllib reads mid-flight.

    Implementation note: ThreadPoolExecutor's `__exit__` calls
    `shutdown(wait=True)` which would block the caller until the slow
    worker finishes. We can't use the `with` context manager. Instead
    we hold a single-thread executor as a local, submit the work, and
    on timeout we explicitly call `shutdown(wait=False)` so the caller
    returns immediately. The orphan thread continues until its HTTP
    call returns (or the process exits); its result is dropped.
    """
    def _work() -> EmployerGuess:
        return _analyze_one_job(job, clusters, minimax, metaso)

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"analyze-{job.id[:8]}")
    already_shutdown = False
    try:
        future = pool.submit(_work)
        try:
            result = future.result(timeout=timeout_s)
            return result
        except FutureTimeout:
            # Stop blocking on the orphan thread; it will be cleaned up
            # when its HTTP call eventually returns (or at process exit).
            pool.shutdown(wait=False)
            already_shutdown = True
            log.warning(
                "analyze_jobs: job %s exceeded per-job timeout (%ds); recording low-confidence guess and moving on",
                job.id, timeout_s,
            )
            return EmployerGuess(
                job_id=job.id,
                guessed_employer=None,
                confidence="low",
                confidence_score=0.0,
                evidence=[{"type": "per_job_timeout", "text": f"per-job timeout {timeout_s}s exceeded"}],
                cross_job_links=[related.id for related in clusters.get(job.id, [])],
                external_sources=[],
                search_queries=[],
                reasoning_summary=f"职位分析超时（{timeout_s}s），已跳过。",
                review_flags=[f"per_job_timeout: exceeded {timeout_s}s"],
            )
        except Exception as exc:  # noqa: BLE001
            pool.shutdown(wait=False)
            already_shutdown = True
            # _analyze_one_job already swallows per-job API errors, but if
            # something escapes (e.g. unexpected data shape) we still want
            # to record a guess and keep going.
            log.error("analyze_jobs: job %s raised %s", job.id, exc, exc_info=True)
            return EmployerGuess(
                job_id=job.id,
                guessed_employer=None,
                confidence="low",
                confidence_score=0.0,
                evidence=[],
                cross_job_links=[related.id for related in clusters.get(job.id, [])],
                external_sources=[],
                search_queries=[],
                reasoning_summary="职位分析异常，已跳过。",
                review_flags=[f"per_job_exception: {type(exc).__name__}: {exc}"],
            )
    finally:
        # On the success path we want the worker fully joined before
        # returning (so future jobs see a clean slate). On the
        # timeout/exception paths we already called shutdown(wait=False)
        # and just need to mark the pool as shut down.
        if not already_shutdown:
            pool.shutdown(wait=True)


def _analyze_one_job(
    job: JobRecord,
    clusters: dict[str, list[JobRecord]],
    minimax: Any,
    metaso: Any | None,
) -> EmployerGuess:
    """The original per-job inference body, lifted out of the for-loop so
    `_analyze_one_job_with_timeout` can wrap it in a timeout thread.

    Defensive short-circuit: if a job has no usable JD or listing excerpt,
    do not burn an M3 call on a title-only record.
    """
    text_blob = (job.jd_text or job.detail_text or job.list_excerpt or "").strip()
    if not text_blob:
        return EmployerGuess(
            job_id=job.id,
            guessed_employer=None,
            confidence="low",
            confidence_score=0.0,
            evidence=[{"type": "short_circuit", "text": "missing jd_text/detail_text/list_excerpt"}],
            cross_job_links=[related.id for related in clusters.get(job.id, [])],
            external_sources=[],
            search_queries=[],
            reasoning_summary="Employer inference skipped: missing JD/listing text content.",
        )
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
        return EmployerGuess(
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
        return EmployerGuess(
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
            "reasoning_summary": "中文，简短说明证据链，以及为什么支持或不支持该雇主猜测",
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
