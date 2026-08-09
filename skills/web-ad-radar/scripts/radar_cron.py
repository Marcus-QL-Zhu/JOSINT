#!/usr/bin/env python3
"""
JOSINT daily cron entry point.

Pipeline:
  1. Run per_source_runner (8 sources sequentially; same as v1)
  2. Sync local DB -> Feishu Bitable (with dedup)
  3. Filter senior technical/business subset and run M3 employer analysis
  4. Write analysis back to Bitable
  5. Send an optional daily digest via Feishu IM

State file: runtime/cron_state.json
  - last_run_date: 'YYYY-MM-DD'
  - last_run_status: 'success' | 'failed'
  - last_run_started_at: ISO timestamp
  - last_run_finished_at: ISO timestamp
  - retry_count: int (used by watchdog to cap retries at 3)
  - last_error: str | None

Exit codes:
  0 = success
  1 = partial failure (some sources failed, but data flowed)
  2 = total failure (could not even crawl; should be retried)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta  # noqa: F401  (kept for legacy import-compat in tests)
from pathlib import Path
from typing import Any

# Make scripts/ importable
SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

from radar.env import load_env
from radar.state_io import atomic_write_json, now_utc_iso, today_business_iso, yesterday_business_iso
from radar.bitable.client import BitableClient
from radar.bitable.dedup import Deduper
from radar.bitable.sync import BitableSyncer
from radar.bitable.analyze import (
    AnalysisResult,
    HIGH_CONFIDENCE_THRESHOLD,
    analyze_subset,
    filter_subset,
    write_analysis_logs_to_bitable,
    write_back_to_bitable,
)
from radar.storage import JobStore
from radar.llm_minimax import MiniMaxClient
from radar.metaso import MetasoClient
from notify_feishu import (
    format_daily_summary,
    format_failure_message,
    send_text,
)


log = logging.getLogger("radar_cron")


# ---------- state ----------


def _state_path(workspace: Path) -> Path:
    return workspace / "runtime" / "cron_state.json"


def load_state(workspace: Path) -> dict:
    p = _state_path(workspace)
    if not p.exists():
        return {
            "last_run_date": None,
            "last_run_status": None,
            "retry_count": 0,
        }
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read cron state: %s; starting fresh", e)
        return {"last_run_date": None, "last_run_status": None, "retry_count": 0}


def save_state(workspace: Path, state: dict) -> None:
    p = _state_path(workspace)
    atomic_write_json(p, state)


# ---------- main pipeline ----------


def run_cron(
    workspace: Path,
    env: dict[str, str],
    m3_client: Any | None = None,
    metaso_client: Any | None = None,
    *,
    skip_crawl: bool = False,
    skip_analyze: bool = False,
) -> int:
    """Run the full daily pipeline. Returns 0/1/2 (see module docstring).

    When m3_client is provided, step 3 runs the v1 inference pipeline
    (analyze_jobs) with optional Metaso candidate generation and evidence verification.
    Pass metaso_client=None to skip evidence (--without-evidence mode).

    CLI flags:
    - skip_crawl: skip step 1 (per_source_runner). Useful for re-running
      sync+analyze on existing data without re-hitting source sites.
    - skip_analyze: skip step 3 (employer inference). Saves ~20 min.
    """
    timezone_name = env.get("JOSINT_TIMEZONE", "Asia/Shanghai")
    today = today_business_iso(timezone_name)
    crawl_run_id = f"cron-{today}-{uuid.uuid4().hex[:6]}"
    state = load_state(workspace)
    state.update({
        "last_run_date": today,
        "last_run_started_at": now_utc_iso(),
        "retry_count": state.get("retry_count", 0),
    })

    # ---- 1. Crawl via per_source_runner ----
    if skip_crawl:
        log.info("Step 1: skipping crawl (--skip-crawl)")
        crawl_exit = 0
    else:
        log.info("Step 1: crawling enabled sources via per_source_runner")
        crawl_exit = _run_per_source_runner(workspace, env)
    if crawl_exit != 0:
        # Catastrophic: the crawl subprocess did not complete. Do not sync
        # stale local DB rows and mark the run as successful.
        _send_failure(env, today, stage="crawl", error=f"per_source_runner exit={crawl_exit}")
        state["last_run_status"] = "failed"
        state["last_error"] = f"crawl exit {crawl_exit}"
        state["last_run_finished_at"] = now_utc_iso()
        save_state(workspace, state)
        return 2

    # ---- 2. Sync to bitable ----
    log.info("Step 2: syncing local DB to bitable")
    app_id = env.get("FEISHU_APP_ID", "")
    app_secret = env.get("FEISHU_APP_SECRET", "")
    app_token = env.get("FEISHU_BITABLE_APP_TOKEN", "")
    table_id = env.get("FEISHU_BITABLE_TABLE_ID", "")
    analysis_log_table_id = env.get("FEISHU_ANALYSIS_LOG_TABLE_ID", "")
    notify_open_id = env.get("FEISHU_NOTIFY_OPEN_ID", "")

    if not all([app_id, app_secret, app_token, table_id]):
        log.error("Missing Feishu bitable env vars; aborting sync")
        _send_failure(env, today, stage="sync_config", error="missing FEISHU_* env vars")
        state["last_run_status"] = "failed"
        state["last_error"] = "missing bitable env vars"
        save_state(workspace, state)
        return 2

    client = BitableClient(app_id, app_secret, app_token, table_id)
    _ensure_canonical_bitable_fields(client)
    deduper = Deduper(client)
    syncer = BitableSyncer(client, deduper, cache_path=workspace / "data" / "bitable_state.json")
    deduper.refresh_snapshot()

    # Read local jobs. per_source_runner is designed to crawl the previous
    # business day because early-morning scheduled runs need a complete
    # snapshot of yesterday's job ads.
    yesterday = yesterday_business_iso(timezone_name)
    store = JobStore(workspace / "data" / "jobs.sqlite")
    # Feishu receives every canonical job observed for the business date so it
    # remains a complete operational overview. Employer inference is still
    # restricted to target jobs that have not already completed analysis.
    refreshed_targets = store.refresh_target_decisions()
    log.info("Refreshed targeting decisions for %d canonical jobs", refreshed_targets)
    all_jobs = store.list_canonical_jobs()
    jobs_today = store.list_canonical_jobs(
        last_seen_at=yesterday
    )
    jobs_to_analyze = _jobs_eligible_for_analysis(jobs_today)

    sync_stats = syncer.sync_many(jobs_today, crawl_run_id=crawl_run_id)
    log.info("Sync done: %s", sync_stats.as_dict())
    # The DB analysis marker is authoritative. A migrated canonical job may
    # update an old Feishu row instead of creating one and still needs analysis.
    jobs_new_in_bitable = jobs_to_analyze
    log.info("Unanalyzed canonical jobs eligible for analysis: %d", len(jobs_new_in_bitable))

    # ---- 3. Subset analysis (v1 inference pipeline + optional Metaso) ----
    if skip_analyze:
        log.info("Step 3: skipping subset analysis (--skip-analyze)")
        subset = filter_subset(jobs_new_in_bitable)
        log.info("Subset size: %d jobs (analysis skipped)", len(subset))
        # Persist success and exit; we do not run steps 4-5 because
        # the user explicitly asked to skip analysis.
        state.update({
            "last_run_status": "success",
            "last_run_finished_at": now_utc_iso(),
            "retry_count": 0,
            "last_error": None,
        })
        save_state(workspace, state)
        log.info("Cron pipeline finished OK (analysis skipped)")
        return 0 if sync_stats.failed == 0 else 1

    log.info("Step 3: subset employer analysis (with_evidence=%s)", metaso_client is not None)
    subset = filter_subset(jobs_new_in_bitable)
    log.info("Subset size: %d jobs", len(subset))

    analysis_results: list[AnalysisResult] = []
    high_conf: list[dict] = []
    low_conf_count = 0
    pending_count = 0
    if m3_client is None:
        log.warning("M3 client not provided; skipping employer analysis (set MINIMAX_API_KEY)")
        pending_count = len(subset)
    else:
        # Delegate to v1 analyze_jobs so the scheduled path stays aligned
        # with full inference + Metaso candidate generation and evidence verification.
        analysis_results = analyze_subset(
            subset, minimax=m3_client, metaso=metaso_client,
        )
        # Build a url -> job map for nicer notification rows.
        url_to_job = {j.url: j for j in subset}
        for r in analysis_results:
            job_ref = url_to_job.get(r.url)
            title = job_ref.title if job_ref else r.job_id
            source = job_ref.source_slug if job_ref else r.job_id.split(":", 1)[0]
            if r.guessed_employer and r.confidence_score >= HIGH_CONFIDENCE_THRESHOLD:
                high_conf.append({
                    "title": title,
                    "source": source,
                    "url": r.url,
                    "employer": r.guessed_employer,
                    "confidence": r.confidence_score,
                    "reasoning": r.reasoning,
                    "review_flags": r.review_flags,
                })
            elif r.guessed_employer:
                low_conf_count += 1
            else:
                pending_count += 1

        analysis_model = getattr(m3_client, "model", None) or env.get("MINIMAX_REASONING_MODEL", "MiniMax-M3")
        written = write_back_to_bitable(
            client,
            analysis_results,
            run_id=crawl_run_id,
            model=analysis_model,
        )
        log.info("Wrote %d/%d analysis results back to bitable", written, len(analysis_results))
        for result in analysis_results:
            if result.is_retryable_failure():
                log.warning("Leaving %s pending after retryable inference failure", result.job_id)
                continue
            store.mark_analysis_completed(
                result.job_id,
                completed_at=now_utc_iso(),
                guessed_employer=result.guessed_employer,
                confidence=result.confidence_score,
                evidence=result.evidence,
                reasoning=result.reasoning,
            )
        if analysis_log_table_id:
            log_client = BitableClient(app_id, app_secret, app_token, analysis_log_table_id)
            log_written = write_analysis_logs_to_bitable(
                log_client,
                analysis_results,
                run_id=crawl_run_id,
                model=analysis_model,
            )
            log.info("Wrote %d/%d analysis log rows", log_written, len(analysis_results))

    # ---- 4. Trend aggregation (today only) ----
    industry_counts = Counter(
        (j.industry_label or j.industry or "Unknown")
        for j in jobs_today
    )
    function_counts = Counter(
        (j.function_label or j.function or "Unknown")
        for j in jobs_today
    )
    top_employers = Counter(h["employer"] for h in high_conf if h.get("employer"))

    # ---- 5. Notify ----
    log.info("Step 5: sending Feishu notification")
    if notify_open_id:
        bitable_url = env.get("FEISHU_BITABLE_URL") or f"https://feishu.cn/base/{app_token}"
        msg = format_daily_summary(
            run_date=today,
            sync_stats=sync_stats.as_dict(),
            total_local_jobs=len(all_jobs),
            high_confidence=high_conf,
            low_confidence_count=low_conf_count,
            pending_count=pending_count,
            industry_counts=industry_counts,
            function_counts=function_counts,
            top_employers=top_employers,
            bitable_url=bitable_url,
        )
        try:
            send_text(notify_open_id, msg, app_id, app_secret)
        except Exception as e:  # noqa: BLE001
            log.error("Failed to send notification: %s", e)

    # ---- mark success ----
    state.update({
        "last_run_status": "success",
        "last_run_finished_at": now_utc_iso(),
        "retry_count": 0,  # reset on success
        "last_error": None,
    })
    save_state(workspace, state)
    log.info("Cron pipeline finished OK")
    return 0 if sync_stats.failed == 0 else 1


def _run_per_source_runner(workspace: Path, env: dict[str, str] | None = None) -> int:
    """Invoke the per_source_runner.py script and return its exit code.

    Note: per_source_runner is designed to crawl the PREVIOUS day
    (run_date = today - 1) because at 6:00 AM there are no jobs
    published "today" yet; we want a complete snapshot of yesterday's
    job ads. The runner sets last_seen_at = run_date inside each
    JobRecord, which is the design intent. v2 sync picks up jobs whose
    last_seen_at matches yesterday.
    """
    import subprocess
    env = env or {}
    source_timeout = int(env.get("JOSINT_SOURCE_TIMEOUT", "180"))
    crawl_timeout = int(env.get("JOSINT_CRAWL_TIMEOUT", "1800"))
    cmd = [
        sys.executable, str(workspace / "scripts" / "per_source_runner.py"),
        "--workspace", ".",
        "--timeout", str(source_timeout),
        "--max-jobs", "30",
    ]
    log.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, timeout=crawl_timeout)
        if result.returncode != 0:
            log.error("per_source_runner failed: %s", result.stderr[-500:])
        else:
            log.info("per_source_runner output: %s", result.stdout[-500:])
        return result.returncode
    except subprocess.TimeoutExpired:
        log.error("per_source_runner timed out after %ds", crawl_timeout)
        return 2


def _jobs_new_in_bitable(jobs: list[Any], sync_stats: Any) -> list[Any]:
    """Return jobs that were newly created in Bitable during this sync run."""
    new_ids = set(getattr(sync_stats, "new_job_ids", []) or [])
    if not new_ids:
        return []
    return [job for job in jobs if getattr(job, "id", None) in new_ids]


def _jobs_eligible_for_analysis(jobs: list[Any]) -> list[Any]:
    """Return target jobs that have not completed hidden-employer analysis."""
    return [
        job for job in jobs
        if getattr(job, "is_target_job", False)
        and not getattr(job, "analysis_completed_at", None)
    ]


def _ensure_canonical_bitable_fields(client: BitableClient) -> None:
    """Idempotently add optional canonical columns before the daily sync."""
    requested = {
        "canonical_job_id": 1,
        "company_name": 1,
        "source_count": 2,
        "source_urls": 1,
        "upstream_sources": 1,
        "seniority": 1,
        "target_reason": 1,
        "is_target_job": 7,
    }
    try:
        existing = client.list_fields(use_cache=False)
        for name, field_type in requested.items():
            if name not in existing:
                client.create_field(name, field_type)
    except Exception as exc:
        # Legacy tables remain usable; BitableSyncer writes only fields that
        # actually exist after this best-effort migration.
        log.warning("Could not ensure canonical Bitable fields: %s", exc)


def _send_failure(env: dict[str, str], today: str, stage: str, error: str) -> None:
    notify_open_id = env.get("FEISHU_NOTIFY_OPEN_ID", "")
    if not notify_open_id:
        return
    msg = format_failure_message(
        run_date=today,
        stage=stage,
        error=error,
        log_path=str(Path(env.get("WORKSPACE", ".")) / "runtime" / "cron.log"),
    )
    try:
        send_text(notify_open_id, msg, env["FEISHU_APP_ID"], env["FEISHU_APP_SECRET"])
    except Exception as e:  # noqa: BLE001
        log.error("Failed to send failure notification: %s", e)


def _build_m3_client(env: dict[str, str]) -> Any | None:
    """Build a MiniMaxClient (custom protocol, same as v1 uses) for M3.

    Returns None if MINIMAX_API_KEY is missing.
    """
    api_key = env.get("MINIMAX_API_KEY") or env.get("MINIMAX_REASONING_API_KEY")
    if not api_key:
        return None
    base_url = env.get("MINIMAX_REASONING_BASE_URL", "https://api.minimaxi.com/v1/chat/completions")
    return MiniMaxClient(
        api_key=api_key,
        base_url=base_url,
        model=env.get("MINIMAX_REASONING_MODEL", "MiniMax-M3"),
        timeout=int(env.get("MINIMAX_REASONING_TIMEOUT", "300")),
        stage="radar_cron",
    )


def _build_metaso_client(env: dict[str, str]) -> Any | None:
    """Build a MetasoClient for candidate generation and evidence verification.

    Returns None if METASO_API_KEY is missing (analysis will skip evidence).
    """
    api_key = env.get("METASO_API_KEY")
    if not api_key:
        return None
    return MetasoClient(
        api_key=api_key,
        base_url=env.get("METASO_BASE_URL", "https://metaso.cn"),
        model=env.get("METASO_MODEL", "fast"),
        timeout=int(env.get("METASO_TIMEOUT", "90")),
        stage="radar_cron",
    )


def main():
    ap = argparse.ArgumentParser(description="JOSINT daily cron")
    ap.add_argument("--workspace", default=".", help="Skill workspace path")
    ap.add_argument("--env", default=".env", help="Env file path (relative to workspace)")
    ap.add_argument("--skip-crawl", action="store_true", help="Skip crawl step (sync + analyze existing data)")
    ap.add_argument("--skip-analyze", action="store_true", help="Skip subset analysis step")
    ap.add_argument(
        "--without-evidence",
        action="store_true",
        help="Skip Metaso candidate generation and evidence verification (M3 only). "
             "Default is WITH evidence (matches v1 analyze_jobs behavior).",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    workspace = Path(args.workspace).resolve()
    env_path = workspace / args.env if not Path(args.env).is_absolute() else Path(args.env)
    env = load_env(env_path)
    env["WORKSPACE"] = str(workspace)

    m3_client = _build_m3_client(env)
    if args.skip_analyze:
        m3_client = None
        metaso_client = None
    else:
        # Default: with evidence (matches v1)
        metaso_client = _build_metaso_client(env)
        if args.without_evidence:
            metaso_client = None
            log.info("--without-evidence set; skipping Metaso evidence search")
        elif metaso_client is None:
            log.warning(
                "METASO_API_KEY not set; running without evidence. "
                "Pass --without-evidence to silence this warning.",
            )

    exit_code = run_cron(
        workspace,
        env,
        m3_client=m3_client,
        metaso_client=metaso_client,
        skip_crawl=args.skip_crawl,
        skip_analyze=args.skip_analyze,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
