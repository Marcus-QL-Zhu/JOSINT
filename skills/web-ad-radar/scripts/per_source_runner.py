"""Per-source runner for JOSINT.

Wraps the existing CLI by importing adapters, store, and report modules.
Adds:
  - hard per-source timeout (180s default)
  - incremental DB writes (one source finished -> immediately upsert + write report)
  - per-source error capture (one slow/broken source does not block the rest)

Does NOT modify radar/cli.py or any business code in radar/.
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import pickle
import sys
import tempfile
import traceback
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from radar.config import SOURCES
from radar.labeling import label_jobs
from radar.models import JobRecord
from radar.report import render_report
from radar.sources import build_adapters
from radar.storage import JobStore


def _crawl_source_worker(
    slug: str,
    max_jobs: int,
    target_job_count_without_dates: int,
    run_date: str,
    date_from: str,
    date_to: str,
    result_path: str,
) -> None:
    """Run one registered adapter in an isolated process and persist its result."""
    try:
        adapter = build_adapters([slug])[0]
        adapter.max_jobs = max_jobs
        adapter.target_job_count_without_dates = target_job_count_without_dates
        payload = (
            "ok",
            adapter.crawl(run_date, date_from=date_from, date_to=date_to),
        )
    except Exception as exc:  # noqa: BLE001
        payload = (
            "error",
            f"EXCEPTION: {type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        )
    with open(result_path, "wb") as result_file:
        pickle.dump(payload, result_file)


def crawl_with_timeout(adapter, run_date: str, date_from: str, date_to: str, timeout_s: int):
    """Run adapter.crawl in a killable child process. Returns (jobs, error_str)."""
    result_file = tempfile.NamedTemporaryFile(prefix="josint-crawl-", suffix=".pkl", delete=False)
    result_path = result_file.name
    result_file.close()
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crawl_source_worker,
        args=(
            adapter.slug,
            adapter.max_jobs,
            adapter.target_job_count_without_dates,
            run_date,
            date_from,
            date_to,
            result_path,
        ),
    )
    try:
        process.start()
        process.join(timeout_s)
        if process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(5)
            return [], f"TIMEOUT after {timeout_s}s"
        if process.exitcode != 0:
            return [], f"WORKER EXIT {process.exitcode}"
        with open(result_path, "rb") as result_stream:
            status, payload = pickle.load(result_stream)
        return (payload, None) if status == "ok" else ([], payload)
    except Exception as exc:  # noqa: BLE001
        return [], f"EXCEPTION: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        try:
            os.unlink(result_path)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-source JOSINT runner with timeouts.")
    parser.add_argument("--workspace", default=".", help="Runtime workspace.")
    parser.add_argument("--data-dir", default="data", help="Data dir relative to workspace.")
    parser.add_argument("--output-dir", default="reports", help="Output dir relative to workspace.")
    parser.add_argument("--timeout", type=int, default=180, help="Hard per-source timeout in seconds.")
    parser.add_argument("--max-jobs", type=int, default=30, help="Max jobs per source.")
    parser.add_argument("--companies", default=None, help="Comma-separated slugs; default = all enabled.")
    parser.add_argument("--report-prefix", default=os.environ.get("JOSINT_REPORT_PREFIX", "josint"), help="Markdown report filename prefix.")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    data_dir = (workspace / args.data_dir).resolve()
    output_dir = (workspace / args.output_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    default_day = (date.today() - timedelta(days=1)).isoformat()
    run_date = default_day
    date_from = default_day
    date_to = default_day

    # Per-source override: cap jobs at args.max_jobs to keep runs bounded.
    slugs = [s.strip() for s in args.companies.split(",")] if args.companies else [s for s, source in SOURCES.items() if source.enabled]
    adapters = build_adapters(slugs)
    for adapter in adapters:
        adapter.max_jobs = min(adapter.max_jobs, args.max_jobs)
        adapter.target_job_count_without_dates = min(adapter.target_job_count_without_dates, args.max_jobs)

    store = JobStore(data_dir / "jobs.sqlite")
    all_jobs: list[JobRecord] = []
    source_errors: list[str] = []
    per_source_summaries: list[dict] = []

    print(f"=== Per-source runner | timeout={args.timeout}s | max_jobs={args.max_jobs} | sources={len(adapters)} ===")
    print(f"    data -> {data_dir / 'jobs.sqlite'}")
    print(f"    output -> {output_dir}")
    print()

    for adapter in adapters:
        print(f"[{adapter.slug}] crawling (max {adapter.max_jobs} jobs, timeout {args.timeout}s)...", flush=True)
        jobs, err = crawl_with_timeout(adapter, run_date, date_from, date_to, args.timeout)
        if err:
            source_errors.append(f"{adapter.name}: {err}")
            print(f"[{adapter.slug}] FAILED: {err[:200]}", flush=True)
            per_source_summaries.append({"slug": adapter.slug, "name": adapter.name, "jobs": 0, "error": err[:200]})
            continue

        try:
            label_jobs(jobs, minimax=None)  # local labels only
        except Exception as exc:
            print(f"[{adapter.slug}] labeling failed (non-fatal): {exc}", flush=True)

        for job in jobs:
            try:
                store.upsert_job(job)
            except Exception as exc:
                print(f"[{adapter.slug}] upsert failed for {job.url}: {exc}", flush=True)

        all_jobs.extend(jobs)
        per_source_summaries.append({"slug": adapter.slug, "name": adapter.name, "jobs": len(jobs), "error": None})
        print(f"[{adapter.slug}] OK: {len(jobs)} jobs", flush=True)

    # Render combined report
    report = render_report(
        report_date=date_to,
        scope=",".join(s["slug"] for s in per_source_summaries),
        mode="crawl only",
        jobs=all_jobs,
        guesses={},  # analysis disabled
        source_errors=source_errors,
    )
    report_prefix = (args.report_prefix or "josint").strip() or "josint"
    combined_path = output_dir / f"{report_prefix}-{date_to}.md"
    combined_path.write_text(report, encoding="utf-8")

    # Render a short per-source summary file
    summary_lines = [f"# Per-source summary - {date_to}", ""]
    for s in per_source_summaries:
        status = "FAILED" if s["error"] else "OK"
        summary_lines.append(f"- {s['slug']}: {status} ({s['jobs']} jobs)")
        if s["error"]:
            summary_lines.append(f"    error: {s['error']}")
    summary_path = output_dir / f"{report_prefix}-{date_to}-summary.md"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print()
    print("=== Summary ===")
    for s in per_source_summaries:
        status = "FAILED" if s["error"] else "OK"
        print(f"  {s['slug']:<20} {status:<8} {s['jobs']} jobs")
    print()
    print(f"Total jobs: {len(all_jobs)}")
    print(f"Source errors: {len(source_errors)}")
    print(f"Combined report: {combined_path}")
    print(f"Per-source summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
