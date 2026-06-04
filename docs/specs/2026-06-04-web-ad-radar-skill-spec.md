# Web Ad Radar Skill SPEC

Date: 2026-06-04
Updated: 2026-06-05 for OpenClaw daily deployment, pagination, and AI/robotics recruiter expansion.

## Goal

Create a portable Codex skill named `web-ad-radar` that monitors public job advertisements from Michael Page competitors in Mainland China, stores crawl artifacts, and outputs a Markdown report containing:

- job title
- source recruitment firm
- job URL
- publish/update date when available
- employer guess
- confidence level
- evidence and reasoning summary
- review flags when the guess is weak or ambiguous

The skill must run independently after installation. It should call configured APIs itself and should not depend on absolute machine-specific paths.

## Installation And Portability

The skill will be installed under the Codex skills directory:

```text
<CODEX_HOME or user home>/.codex/skills/web-ad-radar
```

All files inside the skill must refer to bundled scripts and references relative to the skill root. No implementation file should hard-code paths such as `C:\Users\wande\...`.

Runtime paths must be supplied by CLI flags or inferred from the current working directory:

- `--workspace .` defaults to the directory where the skill runner is invoked.
- `--env .env` defaults to `<workspace>/.env`.
- `--output-dir reports` defaults to `<workspace>/reports`.
- `--data-dir data` defaults to `<workspace>/data`.

For the current project, the intended workspace is:

```text
C:\Users\wande\Documents\Web_ad_radar
```

This path is operational context only, not a value to bake into the skill.

## Modes

The skill runner should support these modes:

```bash
python scripts/run_radar.py
python scripts/run_radar.py --crawl-only
python scripts/run_radar.py --from 2026-06-01 --to 2026-06-04
python scripts/run_radar.py --companies robert-walters,randstad,morgan-mckinley
python scripts/run_radar.py --companies randstad --from 2026-06-01 --to 2026-06-04 --crawl-only
```

Default behavior:

- Use yesterday's date in the Asia/Shanghai business timezone when no `--from` or `--to` is supplied. This is designed for a daily early-morning OpenClaw run that captures the previous complete calendar day.
- Crawl all enabled competitors.
- Analyze all newly discovered or updated jobs in scope.
- Write one Markdown report.
- Keep raw crawl data and normalized job records for future diffing.

`--crawl-only` behavior:

- Crawl and normalize jobs.
- Update local storage.
- Skip employer inference.
- Produce a lightweight Markdown report listing titles and URLs only.

Date-range behavior:

- For sources that expose a reliable publication/update date on the list page or detail page, crawl paginated results and include only jobs whose `published_at` or `updated_at` falls in the requested inclusive date range.
- Date-aware adapters should continue checking pages until they either exhaust pagination or see enough older dated pages to prove no additional in-range jobs remain.
- For sources that do not expose reliable publication/update dates, do not pretend visible jobs were published on the crawl date. Instead, crawl a deterministic fallback sample, currently 30 jobs per source, by following pagination from the first page.
- Hays is currently treated as a no-date source and should crawl the first 3 pages / 30 jobs by default.
- `first_seen_at` and `last_seen_at` are crawler observation dates, not publication dates. Reports and structured exports must keep these separate from `published_at`.

Company filter behavior:

- Accept canonical slugs and common aliases.
- Unknown company slugs should fail fast with available choices.

## Competitor Sources

Initial enabled sources:

| Slug | Company | Primary job source |
|---|---|---|
| `robert-half` | Robert Half China | `https://www.roberthalf.cn/cn/en/find-jobs` |
| `morgan-philips` | Morgan Philips Mainland China | `https://jobs.morganphilips.cn/en-cn` |
| `morgan-mckinley` | Morgan McKinley Mainland China | `https://www.morganmckinley.com.cn/en/jobs` |
| `hays` | Hays China | `https://www.hays-china.cn/en/jobs/` |
| `randstad` | Randstad China | `https://www.randstad.cn/en/jobs/` |
| `rgf` | RGF Professional Recruitment China | `https://www.rgf-professional.com.cn/zh/jobs` |
| `imatch` | imatch talent | Official job board if publicly available |
| `intellipro` | IntelliPro / 英特利普 | Official job board if publicly available |
| `cgl` | CGL Consulting / 猎聘系 CGL | Official job board if publicly available |
| `vip-hunter` | VIP-HUNTER | Official job board if publicly available |
| `risfond` | 锐仕方达 / Risfond | Official job board if publicly available |
| `bo-le` | Bó Lè Associates / 伯乐 | Official job board if publicly available |

Disabled or skipped sources:

- `robert-walters`: currently returns HTTP 403 from the China jobs entry.
- `persolkelly`: current feed includes non-job placeholder posts and needs separate cleanup.
- Any new requested source that lacks an official public job board, requires login, is blocked by anti-crawl/verification, or has no stable job-detail URL may be skipped with a documented reason in `references/source-registry.md` and `最终实施报告.md`.

Each source adapter should define:

- base URL
- list page URL pattern
- pagination strategy
- job detail URL extraction
- date extraction strategy
- no-date fallback target count, if reliable dates are unavailable
- fields available on list pages
- fields requiring detail-page fetch
- rate limit and retry policy
- known anti-bot or JavaScript rendering notes

## Architecture

The skill should include:

```text
web-ad-radar/
  SKILL.md
  agents/openai.yaml
  scripts/
    run_radar.py
    radar/
      __init__.py
      cli.py
      config.py
      env.py
      models.py
      storage.py
      report.py
      llm_minimax.py
      metaso.py
      inference.py
      sources/
        base.py
        robert_walters.py
        robert_half.py
        morgan_philips.py
        morgan_mckinley.py
        hays.py
        randstad.py
        rgf.py
        persolkelly.py
  references/
    source-registry.md
    employer-inference-playbook.md
```

`SKILL.md` should stay concise and instruct Codex to run the bundled script rather than recreate crawler logic.

`source-registry.md` should hold source-specific notes, selectors, and adapter status.

`employer-inference-playbook.md` should hold the reasoning rules for company identification.

## Data Model

Normalized job record:

```json
{
  "id": "stable hash of source_slug + canonical_url",
  "source_slug": "randstad",
  "source_name": "Randstad China",
  "title": "Finance Director",
  "url": "https://...",
  "canonical_url": "https://...",
  "location": "Shanghai",
  "industry": "Chemicals",
  "function": "Finance",
  "salary": "RMB 800k - 1.2m",
  "job_type": "Permanent",
  "published_at": "2026-06-04",
  "updated_at": null,
  "first_seen_at": "2026-06-04",
  "last_seen_at": "2026-06-04",
  "language": "zh-CN",
  "list_excerpt": "...",
  "detail_text": "...",
  "raw": {}
}
```

Employer inference record:

```json
{
  "job_id": "...",
  "guessed_employer": "Honeywell",
  "confidence": "high",
  "confidence_score": 0.86,
  "evidence": [
    {
      "type": "proprietary_term",
      "text": "HOS",
      "interpretation": "Likely Honeywell Operating System"
    }
  ],
  "cross_job_links": ["job_id_1", "job_id_2"],
  "search_queries": ["\"HOS\" \"operation system\" Honeywell"],
  "external_sources": [
    {
      "title": "...",
      "url": "https://...",
      "summary": "..."
    }
  ],
  "reasoning_summary": "...",
  "review_flags": []
}
```

## Storage

Use local files so the skill is portable and easy to inspect.

Default layout under `<workspace>`:

```text
data/
  jobs.sqlite
  raw/
    <source_slug>/
      YYYY-MM-DD/
        list-page-*.html
        detail-<hash>.html
reports/
  web-ad-radar-YYYY-MM-DD.md
```

SQLite should store:

- sources
- jobs
- crawl_runs
- job_snapshots
- inference_runs
- employer_guesses

Raw HTML/text artifacts should be optional but enabled by default for auditability. A future flag can disable raw storage if needed.

## API Configuration

Read `.env` from the workspace by default. Never commit `.env`.

Existing variables:

```text
MINIMAX_API_KEY
MINIMAX_TEXT_MODEL=MiniMax-M2.7-highspeed
MINIMAX_TEXT_BASE_URL=https://api.minimaxi.com/v1/text/chatcompletion_v2
SILICONFLOW_API_KEY
SILICONFLOW_PREVIEW_MODEL
SILICONFLOW_PREVIEW_BASE_URL
METASO_API_KEY
```

Required additions for MiniMax-M3 reasoning:

```text
MINIMAX_REASONING_MODEL=MiniMax-M3
MINIMAX_REASONING_BASE_URL=https://api.minimaxi.com/v1/chat/completions
MINIMAX_REASONING_ENABLED=true
MINIMAX_REASONING_THINKING_TYPE=adaptive
MINIMAX_REASONING_SPLIT=true
```

MiniMax-M3 notes:

- Use OpenAI-compatible chat completions for new code.
- Use `thinking`, not `reasoning`, to control reasoning behavior.
- `thinking: {"type": "disabled"}` disables thinking output for smoke tests.
- `thinking: {"type": "adaptive"}` keeps reasoning enabled for stage 2 inference.
- `reasoning_split: true` separates reasoning fields from final content where supported.

## LLM Usage

Stage 1 crawl/normalize:

- Prefer deterministic parsing with HTML selectors and structured adapters.
- Use `MiniMax-M2.7-highspeed` only when extraction from messy detail pages needs cleanup.
- Prompt for strict JSON output and validate with schema before accepting.

Stage 2 employer inference:

- Use `MiniMax-M3` only for deeper reasoning tasks.
- Provide the model with:
  - one target JD
  - neighboring jobs from the same source that appear related by industry, location, wording, salary band, recruiter, or repeated company description
  - proprietary-term hits
  - Metaso search snippets/sources
- Require structured JSON output with confidence and evidence.
- Treat unsupported guesses as low confidence.

## Metaso Usage

Use Metaso for live evidence gathering in stage 2:

1. Search: query proprietary terms, unusual phrases, product names, system names, company descriptions.
2. Read webpage: fetch promising search result pages as Markdown for evidence extraction.
3. Q&A: ask focused questions only after search/read evidence is available.

Metaso results should be cached by query and URL to avoid repeated calls.

Confirmed Metaso configuration:

```text
METASO_BASE_URL=https://metaso.cn
METASO_SEARCH_URL=https://metaso.cn/api/v1/search
METASO_READER_URL=https://metaso.cn/api/v1/reader
METASO_CHAT_URL=https://metaso.cn/api/v1/chat/completions
METASO_MODEL=fast
```

Common headers:

```http
Authorization: Bearer $METASO_API_KEY
Content-Type: application/json
Accept: application/json
```

Search minimum request:

```json
{
  "q": "OpenAI"
}
```

Search enriched request:

```json
{
  "q": "Honeywell HOS operation system",
  "scope": "webpage",
  "includeSummary": true,
  "conciseSnippet": true
}
```

Do not send `page` or `size` in the initial implementation. Local smoke tests showed those fields can return HTTP 200 with a business error: `errCode=1000`.

Reader minimum request:

```json
{
  "url": "https://example.com"
}
```

Use `Accept: application/json` for `{title, url, author, date, markdown, credits}`. Use `Accept: text/plain` when raw Markdown text is enough.

Q&A simple request:

```json
{
  "q": "Use one sentence to identify what HOS means in an operations-management context.",
  "model": "fast",
  "format": "simple"
}
```

Q&A chat-completions request:

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Use one sentence to identify what HOS means in an operations-management context."
    }
  ],
  "model": "fast",
  "stream": false
}
```

The implementation should wrap Metaso in `MetasoClient` with `search`, `read`, `ask_simple`, and `chat` methods. It must check both HTTP status and body-level `errCode`/`errMsg`.

## Employer Inference Playbook

The inference logic should combine three evidence types.

### 1. Proprietary Terms

Detect company-specific terms inside the JD:

- product names or model names
- internal systems
- operating systems or business frameworks
- role names or internal abbreviations
- named platforms, technical stacks, certifications, or process labels

Examples:

- `HOS` may indicate Honeywell Operating System.
- `FDE` may indicate Palantir Forward Deployed Engineer.

These hits should trigger Metaso searches and MiniMax-M3 reasoning.

### 2. Company Description Narrowing

Use structured clues:

- location
- industry
- ownership type
- funding or listing status
- country of origin
- product category
- factory/R&D footprint
- business model
- reporting line
- market rank claims

Example:

- "embodied AI company in Shanghai Pudong" sharply narrows the candidate set and should trigger targeted search queries.

### 3. Cross-Job Joint Analysis

Cluster jobs from the same recruitment firm and date range when they share:

- similar anonymous company description
- same industry and location
- repeated salary bands
- repeated team structure
- same product clues
- same recruiter/consultant where available

Run joint inference when multiple jobs appear to describe the same hidden employer. The final report should mention when a guess is strengthened by other jobs and list those job titles.

## Report Format

Default report filename:

```text
reports/web-ad-radar-YYYY-MM-DD.md
```

Report structure:

```markdown
# Web Ad Radar Report - 2026-06-04

Scope: all enabled competitors
Mode: crawl + analysis

## Executive Summary

- Jobs crawled:
- New jobs:
- Jobs analyzed:
- High-confidence employer guesses:
- Needs review:

## Jobs

| Source | Job Title | Location | Date | URL | Employer Guess | Confidence |
|---|---|---|---|---|---|---|
| Randstad | Finance Director | Shanghai | 2026-06-04 | ... | BASF | Medium |

## Employer Guess Details

### Finance Director

- Source: Randstad
- URL: ...
- Employer guess: BASF
- Confidence: Medium
- Evidence:
  - German chemical company description
  - Shanghai location
  - Cross-job match with two other roles
- External checks:
  - ...
- Review flags:
  - Several German chemical companies remain plausible
```

## Error Handling

Crawler errors:

- Retry transient network failures.
- Continue other sources when one source fails.
- Record source-level failures in the report.

Parser errors:

- Store raw page.
- Mark job as `parse_failed`.
- Include source/page in diagnostics.

LLM/API errors:

- Retry rate limits with backoff.
- Fall back from stage 2 analysis to "not analyzed" rather than blocking the entire report.
- Never fabricate employer guesses when API calls fail.

## Validation

Minimum validation before delivery:

1. `python scripts/run_radar.py --help`
2. `python scripts/run_radar.py --companies randstad --crawl-only`
3. MiniMax-M2.7-highspeed smoke test for JSON extraction
4. MiniMax-M3 smoke test for employer inference
5. Metaso search/read/Q&A smoke tests
6. Generate a sample Markdown report

Optional validation:

- Run a one-day all-source crawl.
- Manually inspect 5 random job URLs and compare normalized records.
- Manually inspect 5 employer guesses for evidence quality.

## Security

- Never print API keys.
- Redact keys in logs.
- Keep `.env` outside the skill directory and git-ignored.
- Do not commit raw crawl data unless explicitly requested.
- Respect robots.txt and rate limits where practical.

## Open Items

- Confirm whether hidden source adapters such as Hudson, Korn Ferry, Adecco, Manpower, and Antal should be enabled after stable crawling is proven.
