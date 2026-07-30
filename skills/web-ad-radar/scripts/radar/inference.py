from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import EmployerGuess, JobRecord


log = logging.getLogger(__name__)


# Per-job wall-clock cap for analyze_jobs. Each job must finish within
# this budget, otherwise it is marked low-confidence and the run moves on.
# This replaces a previous single global timeout that killed the whole run
# after 15 minutes; a single slow job can never block the rest.
PER_JOB_TIMEOUT_S = 720  # Covers candidate generation, up to 3 checks, M3, and one repair.
MAX_METASO_CANDIDATES = 3
LOW_CONFIDENCE_MAX = 0.49
HIGH_CONFIDENCE_MIN = 0.80

M3_EMPLOYER_SYSTEM_PROMPT = """你是招聘情报中的隐藏雇主核验专家。你的任务是根据完整职位信息、Metaso 无偏候选、公开职位证据和关联岗位，判断最可能的实际雇主。

必须遵守：
1. 先比较全部候选的支持证据和冲突证据，再给结论；不要围绕单一候选自证。
2. 公司介绍、产品、专有技术、系统、型号、品牌、地点和高度相似公开 JD 的权重高于行业知名度。
3. 深圳、3C、DTC、Shopify 等通用特征只能形成低置信度线索，不能单独支持知名公司猜测。
4. 候选定向搜索只有在内容与职位或公司描述实质匹配时才算证据，搜索结果出现公司名本身不算验证。
5. 证据不足时 guessed_employer 必须为 null；不要为了给答案而猜测。
6. 只输出严格 JSON，所有自然语言字段使用简体中文。"""


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
    evidence: list[dict[str, Any]] = []
    external_sources: list[dict[str, Any]] = []
    search_queries: list[str] = []
    metaso_candidates: list[dict[str, Any]] = []
    if metaso:
        try:
            metaso_candidates, candidate_answer, candidate_sources = _generate_metaso_candidates(
                job, clusters.get(job.id, []), metaso,
            )
            evidence.append({
                "type": "metaso_candidate_generation",
                "text": candidate_answer[:12000],
                "candidates": metaso_candidates,
            })
            external_sources.extend(candidate_sources)
            _verify_metaso_candidates(
                job=job,
                candidates=metaso_candidates,
                metaso=metaso,
                evidence=evidence,
                external_sources=external_sources,
                search_queries=search_queries,
            )
        except Exception as exc:  # noqa: BLE001
            evidence.append({"type": "metaso_candidate_generation_error", "text": str(exc)})
    try:
        payload = _call_reasoner(
            job, clusters.get(job.id, []), evidence, external_sources, minimax,
            stage="final_comparison",
            metaso_candidates=metaso_candidates,
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


def _generate_metaso_candidates(
    job: JobRecord,
    related_jobs: list[JobRecord],
    metaso: Any,
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    messages = [
        {
            "role": "system",
            "content": (
                "你是隐藏雇主候选生成器。根据完整招聘广告和公开网络信息，无偏识别可能的实际雇主。"
                "返回 0 至 3 个有证据支持的候选；证据只支持几个就返回几个，允许空数组，绝不能为凑满三个而添加弱候选。"
                "不要因为公司知名、地点和行业相符就优先猜测。只返回严格 JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "分析这个岗位是给哪家公司招人，并提供不超过三个候选。",
                    "job": _job_prompt_payload(job),
                    "related_jobs": [_job_prompt_payload(item) for item in related_jobs[:3]],
                    "schema": {
                        "candidates": [
                            {
                                "name": "具体公司名称",
                                "supporting_clues": ["支持证据"],
                                "conflicting_clues": ["冲突或疑点"],
                                "evidence_urls": ["公开证据链接"],
                            }
                        ],
                        "summary": "简短说明；没有可靠候选时说明证据不足",
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]
    body = metaso.chat(messages, stream=False)
    answer = _metaso_answer_text(body)
    candidates = _parse_metaso_candidates(answer)
    return candidates[:MAX_METASO_CANDIDATES], answer, _metaso_response_sources(body)


def _verify_metaso_candidates(
    *,
    job: JobRecord,
    candidates: list[dict[str, Any]],
    metaso: Any,
    evidence: list[dict[str, Any]],
    external_sources: list[dict[str, Any]],
    search_queries: list[str],
) -> None:
    for candidate in candidates[:MAX_METASO_CANDIDATES]:
        employer = str(candidate.get("name") or "").strip()
        if not employer:
            continue
        query = _candidate_jd_query(employer, job)
        search_queries.append(query)
        try:
            pages = metaso.search(query).get("webpages", [])[:5]
        except Exception as exc:
            evidence.append({
                "type": "candidate_jd_check_error",
                "candidate": employer,
                "text": str(exc),
            })
            continue
        evidence.append({
            "type": "candidate_jd_check",
            "candidate": employer,
            "text": query,
            "summary": "核验候选公司是否存在相似公开职位或与匿名描述一致的公开信息。",
        })
        for page in pages:
            external_sources.append({
                "candidate": employer,
                "title": page.get("title", ""),
                "url": page.get("link", ""),
                "summary": page.get("summary", ""),
            })


def _candidate_jd_query(employer: str, job: JobRecord) -> str:
    parts = [employer, job.title]
    if job.location:
        parts.append(job.location)
    clue = _distinctive_search_clue(job)
    if clue:
        parts.append(f'"{clue[:80]}"')
    parts.extend(["招聘", "职位"])
    return " ".join(part for part in parts if part)


def _distinctive_search_clue(job: JobRecord) -> str | None:
    preferred = job.company_description or ""
    candidates = re.split(r"[。；;\n]", preferred)
    if not any(12 <= len(item.strip()) <= 120 for item in candidates):
        text = job.jd_text or job.detail_text or ""
        candidates.extend(re.split(r"[。；;\n]", text))
    markers = (
        "总部", "专注", "闻名", "领先", "旗下", "核心技术", "产品型号", "自主品牌",
        "headquartered", "specializes", "known for", "leading", "proprietary", "brand",
    )
    scored = []
    for item in candidates:
        cleaned = re.sub(r"\s+", " ", item).strip(" ：:")
        if 12 <= len(cleaned) <= 120:
            score = sum(3 for marker in markers if marker.lower() in cleaned.lower())
            scored.append((score, len(cleaned), cleaned))
    if not scored:
        return None
    return max(scored, key=lambda item: (item[0], item[1]))[2]


def _job_prompt_payload(job: JobRecord) -> dict[str, Any]:
    return {
        "title": job.title,
        "location": job.location,
        "url": job.url,
        "company_description": job.company_description or "",
        "jd_text": job.jd_text or job.detail_text or "",
        "list_excerpt": job.list_excerpt or "",
    }


def _metaso_answer_text(body: Any) -> str:
    if isinstance(body, str):
        return body.strip()
    if not isinstance(body, dict):
        return ""
    if body.get("answer"):
        return str(body["answer"]).strip()
    choices = body.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or choices[0].get("delta") or {}
        if isinstance(message, dict) and message.get("content"):
            return str(message["content"]).strip()
    return ""


def _parse_metaso_candidates(answer: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    try:
        payload = _parse_json_object(answer)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    raw_candidates = payload.get("candidates") if isinstance(payload, dict) else []
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("company") or "").strip()
            if name:
                candidates.append({
                    "name": name,
                    "supporting_clues": item.get("supporting_clues") or [],
                    "conflicting_clues": item.get("conflicting_clues") or [],
                    "evidence_urls": item.get("evidence_urls") or [],
                })
    if not candidates and answer:
        patterns = (
            r"(?:最可能(?:的)?(?:公司|雇主)|实际雇主|公司名称)\s*[:：]\s*\**([^*\n，。；]{2,80})",
            r"(?:职位|岗位).{0,12}?是为\s*\*\*([^*\n]{2,80})\*\*",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, answer, re.IGNORECASE):
                name = match.group(1).strip(" *：:（）()")
                if name and all(name != item["name"] for item in candidates):
                    candidates.append({
                        "name": name,
                        "supporting_clues": [],
                        "conflicting_clues": [],
                        "evidence_urls": [],
                    })
                if len(candidates) >= MAX_METASO_CANDIDATES:
                    break
    return candidates[:MAX_METASO_CANDIDATES]


def _metaso_response_sources(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    raw_sources = body.get("sources") or body.get("webpages") or body.get("references") or []
    if not isinstance(raw_sources, list):
        return []
    sources = []
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        sources.append({
            "title": item.get("title", ""),
            "url": item.get("url") or item.get("link") or "",
            "summary": item.get("summary") or item.get("snippet") or "",
        })
    return sources[:10]


def _call_reasoner(
    job: JobRecord,
    related_jobs: list[JobRecord],
    evidence: list[dict[str, Any]],
    external_sources: list[dict[str, Any]],
    minimax: Any,
    *,
    stage: str,
    metaso_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prompt = _build_reasoner_prompt(
        job,
        related_jobs,
        evidence,
        external_sources,
        metaso_candidates=metaso_candidates,
    )
    content = minimax.chat(
        [
            {"role": "system", "content": M3_EMPLOYER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        thinking_type="adaptive",
        reasoning_split=True,
        usage_context={"job_ids": [job.id], "batch_size": 1},
    )
    try:
        return _normalize_reasoner_payload(_parse_json_object(content))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        _write_inference_debug(
            job=job,
            stage=stage,
            prompt_payload=prompt,
            raw_response=content,
            parse_error=exc,
        )
        repaired = _repair_reasoner_json(
            job=job,
            stage=stage,
            prompt_payload=prompt,
            raw_response=content,
            parse_error=exc,
            minimax=minimax,
        )
        return _normalize_reasoner_payload(repaired)


def _build_reasoner_prompt(
    job: JobRecord,
    related_jobs: list[JobRecord],
    evidence: list[dict[str, Any]],
    external_sources: list[dict[str, Any]],
    *,
    metaso_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "task": "比较全部候选及证据，推断这条猎头职位广告背后的隐藏雇主公司。只返回严格 JSON，不要返回 Markdown。",
        "language_requirement": "所有自然语言字段必须使用简体中文；JSON key 保持英文。reasoning_summary 和 review_flags 必须是中文。",
        "job": _job_prompt_payload(job),
        "related_jobs": [
            _job_prompt_payload(related)
            for related in related_jobs[:5]
        ],
        "metaso_candidates": metaso_candidates or [],
        "evidence": evidence,
        "external_sources": external_sources,
        "decision_method": [
            "先比较所有候选的支持证据与冲突证据，不得围绕单一候选自证。",
            "公司介绍、产品、专有技术或型号、品牌和高度相似公开 JD 属于强证据。",
            "候选定向搜索只有在搜索结果内容与本职位实质相似时才构成验证。",
            "证据不足时返回 null；Metaso 没有候选不代表必须猜测。",
        ],
        "confidence_rules": {
            "high": "0.80-1.00：公司介绍或独特产品/技术直接吻合，并有独立公开证据或高度相似公开 JD 交叉验证。",
            "medium": "0.50-0.79：有多个相互独立的具体线索指向同一公司，但仍存在合理替代候选或公开验证不完整。",
            "low": "0.00-0.49：仅有地点、宽泛行业、渠道或常见技术栈等通用画像，或者证据不足、冲突明显。",
            "generic_signal_cap": "仅凭深圳 + 3C + DTC/Shopify 等通用线索时，confidence_basis 必须为 generic_profile_only，confidence_score 不得超过 0.49。",
        },
        "schema": {
            "guessed_employer": "string or null",
            "confidence": "high|medium|low",
            "confidence_score": "number between 0 and 1",
            "confidence_basis": "direct_description_match|near_duplicate_public_jd|multiple_specific_clues|generic_profile_only|insufficient_evidence",
            "reasoning_summary": "中文，简短说明证据链，以及为什么支持或不支持该雇主猜测",
            "review_flags": ["中文，列出仍需人工复核的疑点"],
        },
    }


def _repair_reasoner_json(
    *,
    job: JobRecord,
    stage: str,
    prompt_payload: dict[str, Any],
    raw_response: str,
    parse_error: Exception,
    minimax: Any,
) -> dict[str, Any]:
    repair_prompt = {
        "task": "修复上一轮 MiniMax-M3 返回的不合法 JSON。",
        "instruction": (
            "不要重新推理，不要改变事实，不要增加新证据。"
            "优先保留 raw_response 中已有判断，只把它修复为符合 schema 的严格 JSON。"
            "如果 raw_response 完全无法修复，则基于 original_prompt_payload 输出同 schema 的 JSON。"
            "只返回 JSON，不要 Markdown，不要解释。"
        ),
        "stage": stage,
        "parse_error": str(parse_error),
        "raw_response": raw_response,
        "original_prompt_payload": prompt_payload,
        "schema": prompt_payload["schema"],
    }
    content = minimax.chat(
        [{"role": "user", "content": json.dumps(repair_prompt, ensure_ascii=False)}],
        thinking_type="adaptive",
        reasoning_split=True,
        usage_context={"job_ids": [job.id], "batch_size": 1, "repair": True, "stage": stage},
    )
    try:
        return _parse_json_object(content)
    except (json.JSONDecodeError, TypeError, ValueError) as repair_error:
        _write_inference_debug(
            job=job,
            stage=f"{stage}_repair_failed",
            prompt_payload=repair_prompt,
            raw_response=content,
            parse_error=repair_error,
        )
        raise ValueError(f"JSON repair failed: {repair_error}; original parse error: {parse_error}") from repair_error


def _parse_json_object(content: str) -> dict[str, Any]:
    content = _strip_json_fence(content.strip())
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        candidate = _extract_balanced_json_object(content)
        if candidate is None:
            raise
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise TypeError(f"Reasoner response must be a JSON object, got {type(payload).__name__}")
    return payload


def _strip_json_fence(content: str) -> str:
    match = re.fullmatch(r"```(?:json)?\s*(?P<body>.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
    return match.group("body").strip() if match else content


def _extract_balanced_json_object(content: str) -> str | None:
    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(content)):
        ch = content[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start:idx + 1]
    return None


def _normalize_reasoner_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    try:
        score = float(normalized.get("confidence_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    raw_basis = normalized.get("confidence_basis")
    basis = str(raw_basis) if raw_basis else "legacy_unspecified"
    allowed_bases = {
        "direct_description_match",
        "near_duplicate_public_jd",
        "multiple_specific_clues",
        "generic_profile_only",
        "insufficient_evidence",
        "legacy_unspecified",
    }
    if basis not in allowed_bases:
        basis = "legacy_unspecified"
    employer = normalized.get("guessed_employer")
    if not employer:
        employer = None
        basis = "insufficient_evidence"
        score = 0.0
    elif basis in {"generic_profile_only", "insufficient_evidence"}:
        score = min(score, LOW_CONFIDENCE_MAX)
    normalized["guessed_employer"] = employer
    normalized["confidence_basis"] = basis
    normalized["confidence_score"] = score
    if score >= HIGH_CONFIDENCE_MIN:
        normalized["confidence"] = "high"
    elif score > LOW_CONFIDENCE_MAX:
        normalized["confidence"] = "medium"
    else:
        normalized["confidence"] = "low"
    flags = normalized.get("review_flags") or []
    if isinstance(flags, str):
        flags = [flags]
    elif not isinstance(flags, list):
        flags = [str(flags)]
    normalized["review_flags"] = [str(flag) for flag in flags]
    normalized["reasoning_summary"] = str(normalized.get("reasoning_summary") or "")
    return normalized


def _write_inference_debug(
    *,
    job: JobRecord,
    stage: str,
    prompt_payload: dict[str, Any],
    raw_response: str,
    parse_error: Exception,
) -> None:
    debug_dir = Path(os.environ.get("JOSINT_INFERENCE_DEBUG_DIR", "runtime/debug/inference"))
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        safe_job_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", job.id)[:80]
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        payload = {
            "timestamp": timestamp,
            "job_id": job.id,
            "title": job.title,
            "url": job.url,
            "stage": stage,
            "parse_error": str(parse_error),
            "raw_response": raw_response,
            "prompt_payload": prompt_payload,
        }
        path = debug_dir / f"{timestamp}-{safe_job_id}-{stage}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to write inference debug artifact for job %s: %s", job.id, exc)
