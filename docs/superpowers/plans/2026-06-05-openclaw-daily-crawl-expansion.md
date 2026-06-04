# Openclaw Daily Crawl Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand `web-ad-radar` so OpenClaw can run it daily on a server, crawl yesterday's China recruiter job ads with pagination/date-aware rules, expose stable structured fields for Feishu, include additional AI/robotics-focused recruiters where feasible, deploy to `139.224.164.156`, and produce a Chinese final implementation report.

**Architecture:** Keep deterministic source adapters as the primary extraction layer. Add generic pagination/date filtering behavior to `SourceAdapter`, then override per source only where URL patterns or fallback rules differ. Treat sources with no reliable publication date as sampling sources that crawl up to a stable fallback count, currently 30 jobs.

**Tech Stack:** Python standard library crawler, SQLite storage, Markdown reports, MiniMax/Metaso API clients already present, PowerShell/SSH/SCP for local verification and deployment.

---

## Requirements Snapshot

- Default run date: if no `--from/--to` is provided, use yesterday in `Asia/Shanghai` terms.
- Date-aware sources: crawl paginated listing pages, keep only jobs whose `published_at` falls in the requested date range, and stop when pages clearly move older than the range.
- Date-unknown sources: crawl paginated listing pages until at least 30 jobs are collected or no more pages exist.
- Hays: crawl first 3 pages / 30 jobs when no publication date is available.
- Existing enabled sources to verify and update: `robert-half`, `morgan-philips`, `morgan-mckinley`, `hays`, `randstad`, `rgf`.
- Existing disabled sources remain disabled: `robert-walters`, `persolkelly`.
- New sources to investigate and enable if feasible: `imatch`, `intellipro`, `cgl`, `vip-hunter`, `risfond`, `bo-le`.
- Sources with anti-crawl, no official job board, login-only jobs, or no stable job URLs may be skipped with documented reason.
- Structured output fields must stay stable for OpenClaw/Feishu downstream development: source, title, JD, URL, location, published date, crawl dates, labels, employer guess, evidence/status fields.
- Deploy final skill to `admin@139.224.164.156:/home/admin/.openclaw/workspace/skills/`.
- Create Chinese `最终实施报告.md` with implemented behavior, enabled/skipped sources, verification results, and deployment evidence.

## File Map

- Modify: `docs/specs/2026-06-04-web-ad-radar-skill-spec.md` for the updated daily crawl contract.
- Modify: `skills/web-ad-radar/SKILL.md` for OpenClaw run behavior and enabled source list.
- Modify: `skills/web-ad-radar/references/source-registry.md` for source status and skip reasons.
- Modify: `skills/web-ad-radar/scripts/radar/config.py` for default yesterday and source registry.
- Modify: `skills/web-ad-radar/scripts/radar/sources/base.py` for pagination/date-aware crawl behavior and stable URL quoting.
- Modify: `skills/web-ad-radar/scripts/radar/sources/registry.py` for existing and new adapters.
- Modify: `skills/web-ad-radar/scripts/radar/report.py` and/or storage if structured fields need explicit report exposure.
- Test: `skills/web-ad-radar/tests/test_config_models.py`.
- Test: `skills/web-ad-radar/tests/test_sources.py`.
- Test: `skills/web-ad-radar/tests/test_storage_report.py`.
- Create: `最终实施报告.md`.

## Tasks

### Task 1: Lock Spec and Current Partial Work

- [x] Record the full objective in this plan.
- [ ] Update the skill SPEC with daily default/yesterday, pagination, date-aware filtering, fallback count, and skip policy.
- [ ] Keep current uncommitted Hays/Morgan McKinley fixes unless contradicted by tests.

### Task 2: Add Date and Pagination Tests

- [ ] Add a failing config test proving default `date_from` and `date_to` are yesterday when no date flags are given.
- [ ] Add a failing source test proving a paginated no-date source returns 30 jobs across 3 pages.
- [ ] Add a failing source test proving a date-aware source keeps only target-date jobs and follows next pages.
- [ ] Add a failing source test proving Hays uses page URLs and returns 30 jobs when dates are absent.

### Task 3: Implement Generic Crawl Policy

- [ ] Add source attributes such as `target_job_count_without_dates`, `max_pages`, and `date_aware`.
- [ ] Add a generic page URL generator with source-local override.
- [ ] Update `crawl()` to collect page by page, de-duplicate by URL, fetch details, and apply date filtering.
- [ ] Preserve existing title/JD split and list fallback behavior.

### Task 4: Verify Existing Sources

- [ ] Run crawl-only checks for `hays`, `morgan-mckinley`, `morgan-philips`, `randstad`, `rgf`, `robert-half`.
- [ ] For each source, record jobs found, pages attempted, empty JD count, and date coverage.
- [ ] Add or adjust source-local pagination patterns as needed.
- [ ] Update source registry with verified status.

### Task 5: Investigate New Sources

- [ ] Find official job board or careers pages for `imatch`, `intellipro`, `cgl`, `vip-hunter`, `risfond`, and `bo-le`.
- [ ] For each source, decide enabled/skipped using official public job-board feasibility.
- [ ] For enabled sources, add adapter, tests, URL patterns, pagination, title/JD extraction, and cleaning.
- [ ] For skipped sources, document reason in source registry and final report.

### Task 6: Stable Output and Reports

- [ ] Ensure Markdown and SQLite retain separate `title`, `jd_text`, URL, location, `published_at`, `first_seen_at`, `last_seen_at`, labels, employer guess, and evidence fields.
- [ ] If needed, add report columns or a JSON/CSV export only if it helps OpenClaw consume stable data without Feishu-specific code.
- [ ] Keep Markdown report Chinese.

### Task 7: Verification

- [ ] Run full unit tests from repo skill.
- [ ] Sync to installed local skill and run full unit tests there.
- [ ] Run real crawl-only smoke tests for all enabled sources.
- [ ] Confirm no enabled source has systematic empty JD or broken URL extraction without a documented skip/error.

### Task 8: Deploy and Final Report

- [ ] Copy final skill to `admin@139.224.164.156:/home/admin/.openclaw/workspace/skills/web-ad-radar`.
- [ ] Verify remote files exist and remote Python can at least load/run the skill smoke test or show a clear environment limitation.
- [ ] Create `最终实施报告.md` in Chinese.
- [ ] Include deployment evidence and known limitations.

## Completion Criteria

- Unit tests pass in repo and local installed skill.
- Default run scope excludes disabled Robert Walters/PERSOLKELLY and includes feasible new sources.
- Hays no-date crawl returns 30 jobs from paginated listings in a real or sufficiently representative test.
- Date-aware behavior is covered by tests.
- Source registry states enabled/skipped status for every requested company.
- Skill is present under the OpenClaw server target path.
- `最终实施报告.md` exists and is written in Chinese.
