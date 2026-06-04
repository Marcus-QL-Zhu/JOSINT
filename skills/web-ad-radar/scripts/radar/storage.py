from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .models import JobRecord


class JobStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    source_slug TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    location TEXT,
                    industry TEXT,
                    function TEXT,
                    salary TEXT,
                    job_type TEXT,
                    published_at TEXT,
                    updated_at TEXT,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    language TEXT,
                    list_excerpt TEXT,
                    detail_text TEXT,
                    jd_text TEXT,
                    company_description TEXT,
                    raw_title TEXT,
                    function_label TEXT,
                    industry_label TEXT,
                    label_confidence TEXT,
                    label_evidence_json TEXT,
                    raw_json TEXT NOT NULL
                )
                """
            )
            conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        required = {
            "function_label": "TEXT",
            "industry_label": "TEXT",
            "label_confidence": "TEXT",
            "label_evidence_json": "TEXT",
            "jd_text": "TEXT",
            "company_description": "TEXT",
            "raw_title": "TEXT",
        }
        with closing(self._connect()) as conn:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            for column, column_type in required.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {column_type}")
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
                    source_name=excluded.source_name,
                    title=excluded.title,
                    url=excluded.url,
                    canonical_url=excluded.canonical_url,
                    location=excluded.location,
                    industry=excluded.industry,
                    function=excluded.function,
                    salary=excluded.salary,
                    job_type=excluded.job_type,
                    published_at=excluded.published_at,
                    updated_at=excluded.updated_at,
                    first_seen_at=jobs.first_seen_at,
                    last_seen_at=excluded.last_seen_at,
                    language=excluded.language,
                    list_excerpt=excluded.list_excerpt,
                    detail_text=excluded.detail_text,
                    jd_text=excluded.jd_text,
                    company_description=excluded.company_description,
                    raw_title=excluded.raw_title,
                    function_label=excluded.function_label,
                    industry_label=excluded.industry_label,
                    label_confidence=excluded.label_confidence,
                    label_evidence_json=excluded.label_evidence_json,
                    raw_json=excluded.raw_json
                """,
                self._job_values(job, first_seen),
            )
            conn.commit()

    def get_job(self, job_id: str) -> JobRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute(f"SELECT {self._select_columns()} FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self) -> list[JobRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT {self._select_columns()} FROM jobs ORDER BY source_slug, title").fetchall()
        return [self._row_to_job(row) for row in rows]

    def _select_columns(self) -> str:
        return (
            "id, source_slug, source_name, title, url, canonical_url, location, industry, "
            "function, salary, job_type, published_at, updated_at, first_seen_at, "
            "last_seen_at, language, list_excerpt, detail_text, jd_text, "
            "company_description, raw_title, function_label, industry_label, "
            "label_confidence, label_evidence_json, raw_json"
        )

    def _job_values(self, job: JobRecord, first_seen: str | None) -> tuple[Any, ...]:
        return (
            job.id,
            job.source_slug,
            job.source_name,
            job.title,
            job.url,
            job.canonical_url,
            job.location,
            job.industry,
            job.function,
            job.salary,
            job.job_type,
            job.published_at,
            job.updated_at,
            first_seen,
            job.last_seen_at,
            job.language,
            job.list_excerpt,
            job.detail_text,
            job.jd_text,
            job.company_description,
            job.raw_title,
            job.function_label,
            job.industry_label,
            job.label_confidence,
            json.dumps(job.label_evidence, ensure_ascii=False),
            json.dumps(job.raw, ensure_ascii=False),
        )

    def _row_to_job(self, row: tuple[Any, ...]) -> JobRecord:
        return JobRecord(
            source_slug=row[1],
            source_name=row[2],
            title=row[3],
            url=row[4],
            canonical_url=row[5],
            location=row[6],
            industry=row[7],
            function=row[8],
            salary=row[9],
            job_type=row[10],
            published_at=row[11],
            updated_at=row[12],
            first_seen_at=row[13],
            last_seen_at=row[14],
            language=row[15],
            list_excerpt=row[16],
            detail_text=row[17],
            jd_text=row[18] or row[17],
            company_description=row[19],
            raw_title=row[20] or row[3],
            function_label=row[21],
            industry_label=row[22],
            label_confidence=row[23],
            label_evidence=json.loads(row[24] or "[]"),
            raw=json.loads(row[25] or "{}"),
        )
