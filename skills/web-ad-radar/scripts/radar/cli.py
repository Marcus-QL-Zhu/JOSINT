from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import build_run_config
from .env import load_env
from .inference import analyze_jobs
from .labeling import label_jobs
from .llm_minimax import MiniMaxClient
from .metaso import MetasoClient
from .models import JobRecord
from .report import render_report
from .sources import build_adapters, crawl_sources
from .storage import JobStore
from .usage import ApiUsageLogger


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = build_run_config(
        workspace=Path(args.workspace),
        env_path=Path(args.env),
        output_dir=Path(args.output_dir),
        data_dir=Path(args.data_dir),
        companies=args.companies,
        crawl_only=args.crawl_only,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    env = load_env(cfg.env_path)
    usage_logger = ApiUsageLogger(cfg.data_dir / "api_usage.jsonl")

    if args.offline_sample:
        jobs = _sample_jobs(cfg.companies, cfg.date_to)
        source_errors: list[str] = []
    else:
        result = crawl_sources(build_adapters(cfg.companies), run_date=cfg.date_to, date_from=cfg.date_from, date_to=cfg.date_to)
        jobs = result.jobs
        source_errors = result.errors

    store = JobStore(cfg.data_dir / "jobs.sqlite")
    label_client = None if args.label_local_only else _build_label_client(env, usage_logger=usage_logger)
    label_jobs(jobs, minimax=label_client)
    for job in jobs:
        store.upsert_job(job)

    guesses = {}
    if not cfg.crawl_only and jobs:
        jobs_to_analyze = jobs[: args.analysis_limit] if args.analysis_limit else jobs
        guesses = analyze_jobs(
            jobs_to_analyze,
            minimax=_build_reasoning_client(env, usage_logger=usage_logger),
            metaso=_build_metaso_client(env, usage_logger=usage_logger),
        )

    report = render_report(
        report_date=cfg.date_to,
        scope=",".join(cfg.companies),
        mode="crawl only" if cfg.crawl_only else "crawl + analysis",
        jobs=jobs,
        guesses=guesses,
        source_errors=source_errors,
    )
    output_path = cfg.output_dir / f"web-ad-radar-{cfg.date_to}.md"
    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote report: {output_path}")
    if source_errors:
        print(f"Completed with {len(source_errors)} source errors.", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawl competitor China job ads and infer hidden employers.")
    parser.add_argument("--workspace", default=".", help="Runtime workspace. Defaults to current directory.")
    parser.add_argument("--env", default=".env", help="Env file path relative to workspace unless absolute.")
    parser.add_argument("--output-dir", default="reports", help="Report output directory relative to workspace unless absolute.")
    parser.add_argument("--data-dir", default="data", help="Data directory relative to workspace unless absolute.")
    parser.add_argument("--companies", default=None, help="Comma-separated competitor slugs or aliases.")
    parser.add_argument("--crawl-only", action="store_true", help="Skip employer inference.")
    parser.add_argument("--from", dest="date_from", default=None, help="Inclusive date from YYYY-MM-DD.")
    parser.add_argument("--to", dest="date_to", default=None, help="Inclusive date to YYYY-MM-DD.")
    parser.add_argument("--offline-sample", action="store_true", help="Use deterministic sample jobs for local smoke tests.")
    parser.add_argument("--analysis-limit", type=int, default=None, help="Analyze only the first N crawled jobs; useful for smoke tests and batches.")
    parser.add_argument("--label-local-only", action="store_true", help="Skip MiniMax Layer 4 label fallback and use local labels only.")
    return parser


def _build_label_client(env: dict[str, str], usage_logger: ApiUsageLogger | None = None) -> MiniMaxClient | None:
    key = env.get("MINIMAX_API_KEY")
    if not key:
        return None
    return MiniMaxClient(
        api_key=key,
        base_url=env.get("MINIMAX_TEXT_BASE_URL", "https://api.minimaxi.com/v1/chat/completions"),
        model=env.get("MINIMAX_TEXT_MODEL", "MiniMax-M2.7-highspeed"),
        usage_logger=usage_logger,
        stage="label",
    )


def _build_reasoning_client(env: dict[str, str], usage_logger: ApiUsageLogger | None = None) -> MiniMaxClient:
    return MiniMaxClient(
        api_key=env["MINIMAX_API_KEY"],
        base_url=env.get("MINIMAX_REASONING_BASE_URL", "https://api.minimaxi.com/v1/chat/completions"),
        model=env.get("MINIMAX_REASONING_MODEL", "MiniMax-M3"),
        timeout=int(env.get("MINIMAX_REASONING_TIMEOUT", "180")),
        usage_logger=usage_logger,
        stage="employer_inference",
    )


def _build_metaso_client(env: dict[str, str], usage_logger: ApiUsageLogger | None = None) -> MetasoClient | None:
    key = env.get("METASO_API_KEY")
    if not key:
        return None
    return MetasoClient(api_key=key, base_url=env.get("METASO_BASE_URL", "https://metaso.cn"), model=env.get("METASO_MODEL", "fast"), usage_logger=usage_logger, stage="metaso_search")


def _sample_jobs(companies: list[str], run_date: str) -> list[JobRecord]:
    jobs = []
    for slug in companies:
        jobs.append(
            JobRecord(
                source_slug=slug,
                source_name=slug.replace("-", " ").title(),
                title="Sample Finance Director",
                url=f"https://example.com/{slug}/sample-finance-director",
                location="Shanghai",
                first_seen_at=run_date,
                last_seen_at=run_date,
                jd_text="German chemical company in Shanghai seeking finance leadership.",
            )
        )
    return jobs


if __name__ == "__main__":
    raise SystemExit(main())
