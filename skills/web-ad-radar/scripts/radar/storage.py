from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .canonicalization import (
    content_hash,
    fingerprint,
    normalize_company,
    normalize_location,
    normalize_title,
    normalize_url,
    stable_id,
    text_similarity,
)
from .models import JobRecord
from .targeting import evaluate_target


class JobStore:
    """SQLite store with a backward-compatible raw table and canonical job layer."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, source_slug TEXT NOT NULL, source_name TEXT NOT NULL,
                    title TEXT NOT NULL, url TEXT NOT NULL, canonical_url TEXT NOT NULL,
                    location TEXT, industry TEXT, function TEXT, salary TEXT, job_type TEXT,
                    published_at TEXT, updated_at TEXT, first_seen_at TEXT, last_seen_at TEXT,
                    language TEXT, list_excerpt TEXT, detail_text TEXT, jd_text TEXT,
                    company_description TEXT, raw_title TEXT, function_label TEXT,
                    industry_label TEXT, label_confidence TEXT, label_evidence_json TEXT,
                    raw_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS source_observations (
                    observation_id TEXT PRIMARY KEY,
                    canonical_job_id TEXT NOT NULL,
                    source_slug TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    normalized_source_url TEXT NOT NULL,
                    source_job_id TEXT,
                    upstream_source TEXT,
                    upstream_url TEXT,
                    normalized_upstream_url TEXT,
                    upstream_job_id TEXT,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    company_name TEXT,
                    normalized_company TEXT,
                    location TEXT,
                    normalized_location TEXT,
                    salary TEXT,
                    experience TEXT,
                    education TEXT,
                    published_at TEXT,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    crawled_at TEXT,
                    raw_html_hash TEXT,
                    content_hash TEXT,
                    list_excerpt TEXT,
                    jd_text TEXT,
                    raw_payload_json TEXT NOT NULL,
                    duplicate_confidence TEXT NOT NULL DEFAULT 'strong',
                    duplicate_group_id TEXT
                );
                CREATE TABLE IF NOT EXISTS canonical_jobs (
                    canonical_job_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    company_name TEXT,
                    normalized_company TEXT,
                    location TEXT,
                    normalized_location TEXT,
                    jd_text TEXT,
                    content_hash TEXT,
                    fingerprint TEXT NOT NULL,
                    industry_label TEXT,
                    function_label TEXT,
                    seniority_label TEXT,
                    is_target_job INTEGER NOT NULL DEFAULT 0,
                    target_reason TEXT,
                    guessed_employer TEXT,
                    employer_confidence REAL,
                    employer_evidence_json TEXT NOT NULL DEFAULT '[]',
                    employer_reasoning TEXT,
                    analysis_completed_at TEXT,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    observation_count INTEGER NOT NULL DEFAULT 1,
                    source_urls_json TEXT NOT NULL DEFAULT '[]',
                    upstream_sources_json TEXT NOT NULL DEFAULT '[]',
                    feishu_record_id TEXT,
                    feishu_synced_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_obs_canonical ON source_observations(canonical_job_id);
                CREATE INDEX IF NOT EXISTS idx_obs_upstream_id ON source_observations(upstream_source, upstream_job_id);
                CREATE INDEX IF NOT EXISTS idx_obs_upstream_url ON source_observations(normalized_upstream_url);
                CREATE INDEX IF NOT EXISTS idx_canonical_fingerprint ON canonical_jobs(fingerprint);
                CREATE INDEX IF NOT EXISTS idx_canonical_target_seen ON canonical_jobs(is_target_job, last_seen_at);
                """
            )
            conn.commit()
        self._migrate_legacy_columns()
        self._migrate_legacy_rows_once()

    def _migrate_legacy_columns(self) -> None:
        required = {
            "function_label": "TEXT", "industry_label": "TEXT", "label_confidence": "TEXT",
            "label_evidence_json": "TEXT", "jd_text": "TEXT", "company_description": "TEXT",
            "raw_title": "TEXT",
        }
        with closing(self._connect()) as conn:
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
            for column, column_type in required.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {column_type}")
            conn.commit()

    def _migrate_legacy_rows_once(self) -> None:
        marker = "canonical-v1"
        with closing(self._connect()) as conn:
            if conn.execute("SELECT 1 FROM schema_migrations WHERE name=?", (marker,)).fetchone():
                return
            rows = conn.execute(f"SELECT {self._select_columns()} FROM jobs").fetchall()
        for row in rows:
            self._ingest_canonical(self._row_to_job(row))
        with closing(self._connect()) as conn:
            conn.execute("INSERT OR IGNORE INTO schema_migrations(name) VALUES (?)", (marker,))
            conn.commit()

    def upsert_job(self, job: JobRecord) -> None:
        existing = self.get_job(job.id)
        first_seen = existing.first_seen_at if existing and existing.first_seen_at else job.first_seen_at
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, source_slug, source_name, title, url, canonical_url, location, industry,
                    function, salary, job_type, published_at, updated_at, first_seen_at,
                    last_seen_at, language, list_excerpt, detail_text, jd_text,
                    company_description, raw_title, function_label, industry_label,
                    label_confidence, label_evidence_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_name=excluded.source_name, title=excluded.title, url=excluded.url,
                    canonical_url=excluded.canonical_url, location=excluded.location,
                    industry=excluded.industry, function=excluded.function, salary=excluded.salary,
                    job_type=excluded.job_type, published_at=excluded.published_at,
                    updated_at=excluded.updated_at, first_seen_at=jobs.first_seen_at,
                    last_seen_at=excluded.last_seen_at, language=excluded.language,
                    list_excerpt=excluded.list_excerpt, detail_text=excluded.detail_text,
                    jd_text=excluded.jd_text, company_description=excluded.company_description,
                    raw_title=excluded.raw_title, function_label=excluded.function_label,
                    industry_label=excluded.industry_label, label_confidence=excluded.label_confidence,
                    label_evidence_json=excluded.label_evidence_json, raw_json=excluded.raw_json
                """,
                self._job_values(job, first_seen),
            )
            conn.commit()
        self._ingest_canonical(job)

    def _ingest_canonical(self, job: JobRecord) -> str:
        source_url = normalize_url(job.canonical_url or job.url)
        upstream_url = normalize_url(job.upstream_url)
        company = job.company_name or _raw_value(job, "company_name") or _raw_value(job, "company")
        source_job_id = job.source_job_id or _raw_value(job, "source_job_id")
        upstream_source = (job.upstream_source or _raw_value(job, "upstream_source") or "").lower() or None
        upstream_job_id = job.upstream_job_id or _raw_value(job, "upstream_job_id")
        title_norm = normalize_title(job.title)
        company_norm = normalize_company(company)
        location_norm = normalize_location(job.location)
        jd_hash = content_hash(job.jd_text)
        fp = fingerprint(company, job.title, job.location, jd_hash)
        observation_id = stable_id("obs", job.source_slug, source_job_id or source_url)
        decision = evaluate_target(job)

        with closing(self._connect()) as conn:
            existing_obs = conn.execute(
                "SELECT canonical_job_id FROM source_observations WHERE observation_id=?",
                (observation_id,),
            ).fetchone()
            canonical_id, confidence, group_id = self._match_canonical(
                conn,
                upstream_source=upstream_source,
                upstream_job_id=upstream_job_id,
                upstream_url=upstream_url,
                company=company_norm,
                title=title_norm,
                location=location_norm,
                jd_text=job.jd_text,
                salary=job.salary,
                experience=job.experience,
                education=job.education,
                fingerprint_value=fp,
            )
            if existing_obs:
                canonical_id = existing_obs["canonical_job_id"]
            if not canonical_id:
                canonical_id = stable_id(
                    "job",
                    upstream_source,
                    upstream_job_id or upstream_url,
                    fp if company_norm else observation_id,
                )

            conn.execute(
                """
                INSERT INTO source_observations (
                    observation_id, canonical_job_id, source_slug, source_name, source_url,
                    normalized_source_url, source_job_id, upstream_source, upstream_url,
                    normalized_upstream_url, upstream_job_id, title, normalized_title,
                    company_name, normalized_company, location, normalized_location,
                    salary, experience, education, published_at, first_seen_at, last_seen_at,
                    crawled_at, raw_html_hash, content_hash, list_excerpt, jd_text,
                    raw_payload_json, duplicate_confidence, duplicate_group_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    canonical_job_id=excluded.canonical_job_id, source_url=excluded.source_url,
                    normalized_source_url=excluded.normalized_source_url,
                    upstream_source=excluded.upstream_source, upstream_url=excluded.upstream_url,
                    normalized_upstream_url=excluded.normalized_upstream_url,
                    upstream_job_id=excluded.upstream_job_id, title=excluded.title,
                    normalized_title=excluded.normalized_title, company_name=excluded.company_name,
                    normalized_company=excluded.normalized_company, location=excluded.location,
                    normalized_location=excluded.normalized_location, salary=excluded.salary,
                    experience=excluded.experience, education=excluded.education,
                    published_at=excluded.published_at,
                    first_seen_at=COALESCE(source_observations.first_seen_at, excluded.first_seen_at),
                    last_seen_at=excluded.last_seen_at, crawled_at=excluded.crawled_at,
                    content_hash=excluded.content_hash, list_excerpt=excluded.list_excerpt,
                    jd_text=excluded.jd_text, raw_payload_json=excluded.raw_payload_json,
                    duplicate_confidence=excluded.duplicate_confidence,
                    duplicate_group_id=excluded.duplicate_group_id
                """,
                (
                    observation_id, canonical_id, job.source_slug, job.source_name, job.url,
                    source_url, source_job_id, upstream_source, job.upstream_url, upstream_url,
                    upstream_job_id, job.title, title_norm, company, company_norm, job.location,
                    location_norm, job.salary, job.experience, job.education, job.published_at,
                    job.first_seen_at, job.last_seen_at, job.updated_at or job.last_seen_at,
                    _raw_value(job, "raw_html_hash"), jd_hash, job.list_excerpt, job.jd_text,
                    json.dumps(job.raw, ensure_ascii=False), confidence, group_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO canonical_jobs (
                    canonical_job_id, title, normalized_title, company_name, normalized_company,
                    location, normalized_location, jd_text, content_hash, fingerprint,
                    industry_label, function_label, seniority_label, is_target_job, target_reason,
                    first_seen_at, last_seen_at, observation_count, source_urls_json,
                    upstream_sources_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
                ON CONFLICT(canonical_job_id) DO UPDATE SET
                    title=CASE WHEN length(excluded.title)>length(canonical_jobs.title)
                               THEN excluded.title ELSE canonical_jobs.title END,
                    company_name=COALESCE(canonical_jobs.company_name, excluded.company_name),
                    normalized_company=COALESCE(NULLIF(canonical_jobs.normalized_company,''), excluded.normalized_company),
                    location=COALESCE(canonical_jobs.location, excluded.location),
                    normalized_location=COALESCE(NULLIF(canonical_jobs.normalized_location,''), excluded.normalized_location),
                    jd_text=CASE WHEN length(COALESCE(excluded.jd_text,''))>length(COALESCE(canonical_jobs.jd_text,''))
                                 THEN excluded.jd_text ELSE canonical_jobs.jd_text END,
                    content_hash=CASE WHEN length(COALESCE(excluded.jd_text,''))>length(COALESCE(canonical_jobs.jd_text,''))
                                      THEN excluded.content_hash ELSE canonical_jobs.content_hash END,
                    industry_label=COALESCE(excluded.industry_label, canonical_jobs.industry_label),
                    function_label=COALESCE(excluded.function_label, canonical_jobs.function_label),
                    seniority_label=excluded.seniority_label,
                    is_target_job=excluded.is_target_job,
                    target_reason=excluded.target_reason,
                    first_seen_at=COALESCE(canonical_jobs.first_seen_at, excluded.first_seen_at),
                    last_seen_at=MAX(COALESCE(canonical_jobs.last_seen_at,''), COALESCE(excluded.last_seen_at,'')),
                    observation_count=(SELECT count(*) FROM source_observations
                                       WHERE canonical_job_id=excluded.canonical_job_id)
                """,
                (
                    canonical_id, job.title, title_norm, company, company_norm, job.location,
                    location_norm, job.jd_text, jd_hash, fp, job.industry_label or job.industry,
                    job.function_label or job.function, decision.seniority_label,
                    int(decision.is_target), decision.reason, job.first_seen_at, job.last_seen_at,
                    json.dumps([job.url], ensure_ascii=False),
                    json.dumps([upstream_source] if upstream_source else [], ensure_ascii=False),
                ),
            )
            self._refresh_canonical_aggregates(conn, canonical_id)
            self._refresh_target_decision(conn, canonical_id)
            conn.commit()
        return canonical_id

    def _match_canonical(
        self,
        conn: sqlite3.Connection,
        *,
        upstream_source: str | None,
        upstream_job_id: str | None,
        upstream_url: str,
        company: str,
        title: str,
        location: str,
        jd_text: str | None,
        salary: str | None,
        experience: str | None,
        education: str | None,
        fingerprint_value: str,
    ) -> tuple[str | None, str, str | None]:
        if upstream_source and upstream_job_id:
            row = conn.execute(
                "SELECT canonical_job_id FROM source_observations WHERE upstream_source=? AND upstream_job_id=? LIMIT 1",
                (upstream_source, upstream_job_id),
            ).fetchone()
            if row:
                return row["canonical_job_id"], "strong", None
        if upstream_url:
            row = conn.execute(
                "SELECT canonical_job_id FROM source_observations WHERE normalized_upstream_url=? LIMIT 1",
                (upstream_url,),
            ).fetchone()
            if row:
                return row["canonical_job_id"], "strong", None
        row = conn.execute(
            "SELECT canonical_job_id FROM canonical_jobs WHERE fingerprint=? LIMIT 1",
            (fingerprint_value,),
        ).fetchone()
        if row and company:
            return row["canonical_job_id"], "strong", None

        candidates = conn.execute(
            """
            SELECT c.*, o.salary, o.experience, o.education
            FROM canonical_jobs c
            LEFT JOIN source_observations o ON o.canonical_job_id=c.canonical_job_id
            WHERE c.normalized_company=? AND c.normalized_location=?
            GROUP BY c.canonical_job_id
            """,
            (company, location),
        ).fetchall() if company and location else []
        weak: sqlite3.Row | None = None
        for candidate in candidates:
            title_score = text_similarity(title, candidate["normalized_title"])
            jd_score = text_similarity(jd_text, candidate["jd_text"])
            compatible = all(
                not left or not candidate[right] or left == candidate[right]
                for left, right in ((salary, "salary"), (experience, "experience"), (education, "education"))
            )
            if title_score >= 0.88 and jd_score >= 0.82 and compatible:
                return candidate["canonical_job_id"], "medium", None
            if title_score >= 0.72:
                weak = candidate
        if weak:
            return None, "low", stable_id("suspect", weak["canonical_job_id"], company, title, location)
        return None, "strong", None

    def _refresh_canonical_aggregates(self, conn: sqlite3.Connection, canonical_id: str) -> None:
        rows = conn.execute(
            "SELECT source_url, upstream_source FROM source_observations WHERE canonical_job_id=?",
            (canonical_id,),
        ).fetchall()
        source_urls = sorted({row["source_url"] for row in rows if row["source_url"]})
        upstream_sources = sorted({row["upstream_source"] for row in rows if row["upstream_source"]})
        conn.execute(
            """
            UPDATE canonical_jobs SET observation_count=?, source_urls_json=?,
                upstream_sources_json=? WHERE canonical_job_id=?
            """,
            (
                len(rows), json.dumps(source_urls, ensure_ascii=False),
                json.dumps(upstream_sources, ensure_ascii=False), canonical_id,
            ),
        )

    def _refresh_target_decision(self, conn: sqlite3.Connection, canonical_id: str) -> None:
        row = conn.execute(
            "SELECT * FROM canonical_jobs WHERE canonical_job_id=?",
            (canonical_id,),
        ).fetchone()
        if not row:
            return
        decision = evaluate_target(self._canonical_row_to_job(row))
        conn.execute(
            """
            UPDATE canonical_jobs
            SET is_target_job=?, target_reason=?, seniority_label=?
            WHERE canonical_job_id=?
            """,
            (
                int(decision.is_target),
                decision.reason,
                decision.seniority_label,
                canonical_id,
            ),
        )

    def refresh_target_decisions(self) -> int:
        """Re-evaluate all canonical jobs against the current targeting rules."""
        with closing(self._connect()) as conn:
            ids = [
                row["canonical_job_id"]
                for row in conn.execute("SELECT canonical_job_id FROM canonical_jobs").fetchall()
            ]
            for canonical_id in ids:
                self._refresh_target_decision(conn, canonical_id)
            conn.commit()
        return len(ids)

    def get_job(self, job_id: str) -> JobRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute(f"SELECT {self._select_columns()} FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self) -> list[JobRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT {self._select_columns()} FROM jobs ORDER BY source_slug,title").fetchall()
        return [self._row_to_job(row) for row in rows]

    def list_canonical_jobs(
        self,
        *,
        target_only: bool = False,
        unanalyzed_only: bool = False,
        last_seen_at: str | None = None,
    ) -> list[JobRecord]:
        clauses, values = [], []
        if target_only:
            clauses.append("is_target_job=1")
        if unanalyzed_only:
            clauses.append("analysis_completed_at IS NULL")
        if last_seen_at:
            clauses.append("last_seen_at LIKE ?")
            values.append(f"{last_seen_at}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT * FROM canonical_jobs {where} ORDER BY last_seen_at DESC,title", values).fetchall()
        return [self._canonical_row_to_job(row) for row in rows]

    def mark_analysis_completed(
        self,
        canonical_job_id: str,
        *,
        completed_at: str,
        guessed_employer: str | None = None,
        confidence: float | None = None,
        evidence: list[dict[str, Any]] | None = None,
        reasoning: str | None = None,
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE canonical_jobs SET analysis_completed_at=?, guessed_employer=?,
                    employer_confidence=?, employer_evidence_json=?, employer_reasoning=?
                WHERE canonical_job_id=?
                """,
                (
                    completed_at, guessed_employer, confidence,
                    json.dumps(evidence or [], ensure_ascii=False), reasoning, canonical_job_id,
                ),
            )
            conn.commit()

    def _select_columns(self) -> str:
        return (
            "id,source_slug,source_name,title,url,canonical_url,location,industry,function,"
            "salary,job_type,published_at,updated_at,first_seen_at,last_seen_at,language,"
            "list_excerpt,detail_text,jd_text,company_description,raw_title,function_label,"
            "industry_label,label_confidence,label_evidence_json,raw_json"
        )

    def _job_values(self, job: JobRecord, first_seen: str | None) -> tuple[Any, ...]:
        return (
            job.id, job.source_slug, job.source_name, job.title, job.url, job.canonical_url,
            job.location, job.industry, job.function, job.salary, job.job_type, job.published_at,
            job.updated_at, first_seen, job.last_seen_at, job.language, job.list_excerpt,
            job.detail_text, job.jd_text, job.company_description, job.raw_title,
            job.function_label, job.industry_label, job.label_confidence,
            json.dumps(job.label_evidence, ensure_ascii=False),
            json.dumps(job.raw, ensure_ascii=False),
        )

    def _row_to_job(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            source_slug=row["source_slug"], source_name=row["source_name"], title=row["title"],
            url=row["url"], canonical_url=row["canonical_url"], location=row["location"],
            industry=row["industry"], function=row["function"], salary=row["salary"],
            job_type=row["job_type"], published_at=row["published_at"], updated_at=row["updated_at"],
            first_seen_at=row["first_seen_at"], last_seen_at=row["last_seen_at"],
            language=row["language"], list_excerpt=row["list_excerpt"],
            detail_text=row["detail_text"], jd_text=row["jd_text"] or row["detail_text"],
            company_description=row["company_description"], raw_title=row["raw_title"] or row["title"],
            function_label=row["function_label"], industry_label=row["industry_label"],
            label_confidence=row["label_confidence"],
            label_evidence=json.loads(row["label_evidence_json"] or "[]"),
            raw=json.loads(row["raw_json"] or "{}"),
        )

    def _canonical_row_to_job(self, row: sqlite3.Row) -> JobRecord:
        urls = json.loads(row["source_urls_json"] or "[]")
        return JobRecord(
            source_slug="josint-canonical",
            source_name="JOSINT Canonical",
            title=row["title"],
            url=urls[0] if urls else f"josint://{row['canonical_job_id']}",
            canonical_url=urls[0] if urls else f"josint://{row['canonical_job_id']}",
            location=row["location"],
            jd_text=row["jd_text"],
            detail_text=row["jd_text"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            company_name=row["company_name"],
            industry_label=row["industry_label"],
            function_label=row["function_label"],
            seniority_label=row["seniority_label"],
            canonical_job_id=row["canonical_job_id"],
            is_target_job=bool(row["is_target_job"]),
            target_reason=row["target_reason"],
            observation_count=row["observation_count"],
            source_urls=urls,
            upstream_sources=json.loads(row["upstream_sources_json"] or "[]"),
            guessed_employer=row["guessed_employer"],
            employer_confidence=row["employer_confidence"],
            employer_evidence=json.loads(row["employer_evidence_json"] or "[]"),
            employer_reasoning=row["employer_reasoning"],
            analysis_completed_at=row["analysis_completed_at"],
        )


def _raw_value(job: JobRecord, key: str) -> str | None:
    value = (job.raw or {}).get(key)
    return str(value).strip() if value is not None and str(value).strip() else None
