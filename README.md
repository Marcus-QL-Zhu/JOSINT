# JOSINT - the open-source job intelligence system

JOSINT monitors public recruiter job boards, normalizes job advertisements, assigns controlled industry/function labels, and can infer likely hidden employers behind recruiter postings.

It can run locally as a Codex skill or on a server/OpenClaw schedule. Runtime data, reports, state, and secrets are kept outside the source code and resolved from relative workspace paths.

## What It Does

- Crawls enabled recruiter job boards for a date range or the previous business day.
- Stores clean job records in SQLite.
- Keeps job title and JD text in separate fields.
- Labels jobs using fixed industry and function enums.
- Uses MiniMax-M3 plus optional Metaso search to infer likely hidden employers.
- Generates Chinese Markdown reports.
- Optionally syncs results to Feishu Bitable and sends Feishu IM summaries.
- In scheduled Feishu mode, only jobs newly created in Bitable are sent to employer inference; re-seen or updated jobs are deduplicated without re-analysis.

## Quick Start

```bash
cp skills/web-ad-radar/.env.example .env
python skills/web-ad-radar/scripts/run_radar.py --workspace . --crawl-only
python skills/web-ad-radar/scripts/run_radar.py --workspace . --offline-sample --companies hays --crawl-only
```

Scheduled/OpenClaw-style run:

```bash
python skills/web-ad-radar/scripts/radar_cron.py --workspace .
```

## Configuration

Copy `skills/web-ad-radar/.env.example` to `.env` and fill only the integrations you need.

- MiniMax text model is used for extraction/fallback labeling where configured.
- MiniMax-M3 is used for hidden-employer reasoning.
- Metaso is used for evidence search and candidate-employer verification.
- Feishu variables are only required for Bitable/IM sync mode.
- `FEISHU_ANALYSIS_LOG_TABLE_ID` is optional. When configured, each inference result is also appended to a separate analysis log table with reasoning, searches, evidence, and review flags.

## Outputs

- `reports/josint-YYYY-MM-DD.md`
- `data/jobs.sqlite`
- `data/api_usage.jsonl`
- `data/bitable_state.json` when Feishu sync is enabled

## Feishu Data Contract

The main Bitable keeps one canonical row per job advertisement, deduplicated by URL and normalized `url_hash`.

- `month` is set only when a row is first created and is intended for monthly views.
- `last_seen_month` is refreshed whenever a row is seen again.
- `analysis_*`, `reasoning_summary`, and `*_json` fields store the latest hidden-employer inference state.
- A separate optional analysis log table can be enabled with `FEISHU_ANALYSIS_LOG_TABLE_ID` to preserve the inference trail for every run.

## Documentation

- [Skill SPEC](docs/specs/josint-skill-spec.md)
- [Source Registry](skills/web-ad-radar/references/source-registry.md)
- [Employer Inference Playbook](skills/web-ad-radar/references/employer-inference-playbook.md)

## Tests

```bash
cd skills/web-ad-radar/scripts
python -m unittest discover -s ../tests -v
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
