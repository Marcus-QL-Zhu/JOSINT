# JOSINT Skill SPEC

JOSINT - the open-source job intelligence system monitors public recruiter job boards, normalizes job records, labels each role with controlled industry/function tags, and can infer likely hidden employers behind recruiter advertisements.

## Runtime Model

- The project runs as a Codex skill from `skills/web-ad-radar`.
- The same scripts run in OpenClaw or any server cron by passing `--workspace`.
- Runtime paths are always relative to the workspace unless an absolute path is explicitly provided.
- Secrets are read from `.env`; `.env` is ignored by git.

## Core Commands

```bash
python skills/web-ad-radar/scripts/run_radar.py --workspace .
python skills/web-ad-radar/scripts/run_radar.py --workspace . --crawl-only
python skills/web-ad-radar/scripts/run_radar.py --workspace . --from 2026-06-01 --to 2026-06-04
python skills/web-ad-radar/scripts/run_radar.py --workspace . --companies hays,morgan-philips
python skills/web-ad-radar/scripts/radar_cron.py --workspace .
```

## Default Crawl Contract

- With no explicit dates, the crawler targets yesterday in `JOSINT_TIMEZONE`, defaulting to `Asia/Shanghai`.
- Date-aware sources should page through results and keep only jobs published in the requested range.
- Sources without reliable publish dates should collect a bounded fallback sample, defaulting to 30 jobs.
- Job title and JD text are stored separately. JD text includes responsibilities, requirements, benefits, company introductions, and similar long-form advert content.

## Outputs

- Markdown report: `reports/josint-YYYY-MM-DD.md`
- SQLite store: `data/jobs.sqlite`
- API usage log: `data/api_usage.jsonl`
- Optional Feishu state cache: `data/bitable_state.json`

The report is Chinese by default and includes source, title, JD, location, publish date, crawl date, URL, labels, employer guess, confidence, evidence, and source errors.

## Labels

Function labels are restricted to:

`VC/PE`, `保险`, `banking`, `销售`, `市场`, `财务`, `人事`, `法务`, `行政`, `研发`, `生产`, `供应链`, `IT`

Industry labels are restricted to:

`机器人`, `半导体`, `软件`, `消费`, `耐消`, `专业服务`, `电商`, `金融`, `化工`, `工业`

The labeler first uses local deterministic rules. MiniMax fallback labeling is only used for jobs that remain low-confidence.

## Employer Inference

Employer inference uses three evidence modes:

- Proprietary terms inside the JD, such as product names, systems, role titles, or internal terminology.
- Company descriptions inside the JD, such as location, sector, ownership, scale, or niche market clues.
- Cross-job clustering across postings from the same recruiter, where multiple JDs reveal complementary clues.

When Metaso is configured, JOSINT searches for evidence before asking MiniMax-M3, and then verifies candidate employers by searching for similar public job advertisements from the guessed employer.

## OpenClaw / Feishu Mode

`scripts/radar_cron.py` runs the daily pipeline:

1. Crawl each enabled source with per-source timeout.
2. Sync local records to Feishu Bitable with URL and normalized URL-hash deduplication.
3. Filter high-interest roles, currently robotics/AI/R&D oriented.
4. Run MiniMax-M3 employer inference with optional Metaso evidence.
5. Write employer guesses and confidence back to Feishu.
6. Send an optional Feishu IM summary if `FEISHU_NOTIFY_OPEN_ID` is configured.

Feishu field names are stable in the sync layer so downstream OpenClaw customizations can map them predictably.
