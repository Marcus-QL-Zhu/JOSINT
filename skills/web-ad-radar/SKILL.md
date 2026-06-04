---
name: web-ad-radar
description: Crawl competitor recruitment-agency job advertisements in Mainland China and generate Markdown reports with job titles, URLs, and likely hidden-employer guesses. Use when asked to monitor Michael Page competitors, run daily job-ad radar, crawl Robert Walters/Robert Half/Morgan Philips/Morgan McKinley/Hays/Randstad/RGF/PERSOLKELLY China jobs, or infer the end client behind recruiter job ads.
---

# Web Ad Radar

## Quick Start

Run the bundled Python runner from the project workspace that contains `.env`:

```bash
python <skill-root>/scripts/run_radar.py --workspace .
```

Common modes:

```bash
python <skill-root>/scripts/run_radar.py --workspace . --crawl-only
python <skill-root>/scripts/run_radar.py --workspace . --from 2026-06-01 --to 2026-06-04
python <skill-root>/scripts/run_radar.py --workspace . --companies randstad,morgan-mckinley
python <skill-root>/scripts/run_radar.py --workspace . --companies randstad --crawl-only
python <skill-root>/scripts/run_radar.py --workspace . --companies randstad --analysis-limit 3
```

Use `--offline-sample` only for local smoke tests that should not touch real websites or APIs.
Use `--analysis-limit N` for small real-analysis batches; omit it for full scheduled analysis.

## Runtime Paths

Do not hard-code user-specific absolute paths in edits. The runner resolves paths from CLI flags:

- `--workspace .`
- `--env .env`
- `--output-dir reports`
- `--data-dir data`

The skill's own scripts and references are relative to this skill directory. Runtime data belongs under the workspace.

## Outputs

The runner writes:

- `reports/web-ad-radar-YYYY-MM-DD.md`
- `data/jobs.sqlite`

The Markdown report is written in Chinese. It lists source, job title, location, date, URL, employer guess, confidence, evidence, and source errors.
It also includes function labels, industry labels, and label confidence. Labels are restricted to the configured enum sets.

## APIs

Read API keys from the workspace `.env`. Never print keys.

Stage 1 extraction and Layer 4 job-label fallback use `MINIMAX_TEXT_MODEL`, defaulting to `MiniMax-M2.7-highspeed` where needed.
The labeler first applies local rules. Only non-high-confidence jobs are sent to MiniMax, batched at up to 10 jobs per request.
Use `--label-local-only` to skip MiniMax label fallback.

Stage 2 employer inference uses:

```text
MINIMAX_REASONING_MODEL=MiniMax-M3
MINIMAX_REASONING_BASE_URL=https://api.minimaxi.com/v1/chat/completions
MINIMAX_REASONING_THINKING_TYPE=adaptive
MINIMAX_REASONING_SPLIT=true
```

Metaso live search uses:

```text
METASO_BASE_URL=https://metaso.cn
METASO_MODEL=fast
```

During employer inference, the runner first gathers Metaso evidence from proprietary terms and company-description clues, then asks MiniMax-M3 for a candidate employer. If MiniMax-M3 returns a candidate employer, the runner searches Metaso again for `candidate employer + job title + location + job JD` to check whether the employer has a similar public job advertisement online, then sends that evidence back to MiniMax-M3 for a final confidence update.

## References

- Read `references/source-registry.md` when adding or debugging source adapters.
- Read `references/employer-inference-playbook.md` when changing hidden-employer reasoning.
