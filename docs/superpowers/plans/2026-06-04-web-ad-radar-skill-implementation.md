# Web Ad Radar Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a portable `web-ad-radar` Codex skill that crawls competitor China job ads, optionally analyzes likely hidden employers, and writes Markdown reports.

**Architecture:** Install a skill under the Codex skills directory with a Python runner and package under `scripts/`. Runtime data lives under a caller-supplied workspace, while bundled skill code and references are resolved relative to the skill root.

**Tech Stack:** Python 3 standard library plus `requests` and `beautifulsoup4` when available; SQLite for local storage; MiniMax for extraction/reasoning; Metaso for live search evidence.

---

### Task 1: Skill Skeleton And Metadata

**Files:**
- Create: `C:\Users\wande\.codex\skills\web-ad-radar\SKILL.md`
- Create: `C:\Users\wande\.codex\skills\web-ad-radar\agents\openai.yaml`
- Create: `C:\Users\wande\.codex\skills\web-ad-radar\scripts\run_radar.py`
- Create: `C:\Users\wande\.codex\skills\web-ad-radar\scripts\radar\__init__.py`

- [ ] Initialize the skill using `skill-creator` helper.
- [ ] Replace template text with concise invocation instructions.
- [ ] Add a runner placeholder that imports `radar.cli.main`.
- [ ] Validate skill frontmatter with `quick_validate.py`.

### Task 2: Core Config And Models

**Files:**
- Create: `scripts/radar/config.py`
- Create: `scripts/radar/env.py`
- Create: `scripts/radar/models.py`
- Test: `tests/test_config_models.py`

- [ ] Write failing tests for relative path resolution, company filtering, and required env loading.
- [ ] Implement dataclasses for `JobRecord`, `EmployerGuess`, `SourceConfig`, and `RunConfig`.
- [ ] Implement `.env` parsing without printing secrets.
- [ ] Run tests until green.

### Task 3: API Clients

**Files:**
- Create: `scripts/radar/llm_minimax.py`
- Create: `scripts/radar/metaso.py`
- Test: `tests/test_api_clients.py`

- [ ] Write failing tests with fake HTTP transports for MiniMax request bodies.
- [ ] Write failing tests with fake HTTP transports for Metaso search/read/chat and body-level errors.
- [ ] Implement clients with dependency-injected HTTP function.
- [ ] Verify no API key appears in exceptions or logs.

### Task 4: Storage And Report

**Files:**
- Create: `scripts/radar/storage.py`
- Create: `scripts/radar/report.py`
- Test: `tests/test_storage_report.py`

- [ ] Write failing tests for SQLite upsert and first/last seen dates.
- [ ] Write failing tests for crawl-only and analyzed Markdown output.
- [ ] Implement schema creation and upsert operations.
- [ ] Implement report rendering.

### Task 5: Source Adapters

**Files:**
- Create: `scripts/radar/sources/base.py`
- Create: one adapter per enabled source under `scripts/radar/sources/`
- Test: `tests/test_sources.py`

- [ ] Write failing parser tests using saved HTML snippets.
- [ ] Implement resilient list/detail extraction.
- [ ] Add timeout, retry, and per-source failure reporting.
- [ ] Ensure a failing source does not stop the run.

### Task 6: Inference Pipeline

**Files:**
- Create: `scripts/radar/inference.py`
- Test: `tests/test_inference.py`

- [ ] Write failing tests for proprietary-term detection.
- [ ] Write failing tests for cross-job clustering.
- [ ] Write failing tests for low-confidence fallback when APIs fail.
- [ ] Implement MiniMax-M3 plus Metaso evidence flow.

### Task 7: CLI And End-To-End Verification

**Files:**
- Create: `scripts/radar/cli.py`
- Modify: project `.env` to add non-secret MiniMax-M3 and Metaso defaults.
- Test: `tests/test_cli.py`

- [ ] Write failing tests for `--help`, `--crawl-only`, date range, and company filters.
- [ ] Implement CLI orchestration.
- [ ] Run local smoke tests against at least one source.
- [ ] Generate a sample Markdown report.
