"""Dev-repair command: apply runtime DDL patches for development environments (RR6c).

These patches handle enum/table additions that couldn't easily be folded into
a migration at the time (or where the migration wasn't applied). In production,
run `alembic upgrade head` instead. This script is ONLY for development
recovery — it should never be called automatically by the pipeline.

Usage:
    python -m src.scripts.dev_repair [--check] [--force]

Options:
    --check   Print what would be patched without making changes.
    --force   Apply patches even if Alembic shows the schema is current.
"""

from __future__ import annotations

import argparse
import sys

import structlog

logger = structlog.get_logger()


def _patch_extraction_enums(db, dry_run: bool = False) -> list[str]:
    """Ensure all ExtractionType enum values exist in the postgres enum."""
    from sqlalchemy import text

    new_values = [
        "rights_protection",
        "compliance_mechanism",
        "preemption_signal",
    ]

    bind = db.get_bind()
    with bind.connect() as conn:
        result = conn.execute(text(
            "SELECT enumlabel FROM pg_enum "
            "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
            "WHERE pg_type.typname = 'extractiontype'"
        ))
        existing = {row[0] for row in result}

    if not existing:
        return ["extractiontype enum not found — run alembic upgrade head first"]

    missing = [v for v in new_values if v not in existing]
    if not missing:
        return []

    actions = [f"ADD VALUE '{v}' TO extractiontype" for v in missing]
    if dry_run:
        return actions

    raw_conn = bind.raw_connection()
    try:
        raw_conn.autocommit = True
        cursor = raw_conn.cursor()
        for val in missing:
            cursor.execute(f"ALTER TYPE extractiontype ADD VALUE IF NOT EXISTS '{val}'")
        cursor.close()
    finally:
        raw_conn.autocommit = False
        raw_conn.close()

    return actions


def _patch_failed_attempts_table(db, dry_run: bool = False) -> list[str]:
    """Create failed_extraction_attempts table if missing."""
    from sqlalchemy import inspect as sa_inspect, text

    bind = db.get_bind()
    if sa_inspect(bind).has_table("failed_extraction_attempts"):
        return []

    actions = ["CREATE TABLE failed_extraction_attempts"]
    if dry_run:
        return actions

    with bind.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS failed_extraction_attempts (
                id SERIAL PRIMARY KEY,
                source_record_id INTEGER NOT NULL REFERENCES normalized_source_records(id),
                agent_name VARCHAR(100) NOT NULL,
                error_type VARCHAR(50) NOT NULL,
                error_message TEXT NOT NULL,
                extraction_job_id INTEGER REFERENCES extraction_jobs(id),
                retried BOOLEAN NOT NULL DEFAULT FALSE,
                retry_succeeded BOOLEAN,
                created_at TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_failed_attempts_source "
            "ON failed_extraction_attempts (source_record_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_failed_attempts_retry "
            "ON failed_extraction_attempts (retried, agent_name)"
        ))
    return actions


def _patch_triage_table(db, dry_run: bool = False) -> list[str]:
    """Create section_triage_results table and its enum types if missing."""
    from sqlalchemy import inspect as sa_inspect, text

    bind = db.get_bind()
    if sa_inspect(bind).has_table("section_triage_results"):
        return []

    actions = ["CREATE TABLE section_triage_results"]
    if dry_run:
        return actions

    with bind.begin() as conn:
        conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE triagedecision AS ENUM ('relevant', 'not_relevant', 'uncertain');
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE triagemethod AS ENUM (
                    'keyword', 'orrick_cross_check', 'llm_generic',
                    'quality_fail', 'passthrough', 'manual_review'
                );
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS section_triage_results (
                id SERIAL PRIMARY KEY,
                source_record_id INTEGER NOT NULL UNIQUE REFERENCES normalized_source_records(id),
                decision triagedecision NOT NULL,
                method triagemethod NOT NULL,
                confidence FLOAT NOT NULL DEFAULT 0.0,
                matched_keywords JSONB DEFAULT '[]'::jsonb,
                orrick_terms_checked JSONB DEFAULT '[]'::jsonb,
                llm_reasoning TEXT,
                pdf_quality_score FLOAT,
                quality_flags JSONB DEFAULT '[]'::jsonb,
                model_id VARCHAR(100),
                created_at TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_triage_source_record "
            "ON section_triage_results (source_record_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_triage_decision "
            "ON section_triage_results (decision)"
        ))
    return actions


def run(dry_run: bool = False) -> None:
    from src.db.engine import SessionLocal

    db = SessionLocal()
    try:
        total_actions: list[str] = []

        total_actions += _patch_extraction_enums(db, dry_run)
        total_actions += _patch_failed_attempts_table(db, dry_run)
        total_actions += _patch_triage_table(db, dry_run)

        if total_actions:
            prefix = "[DRY RUN] " if dry_run else ""
            for action in total_actions:
                print(f"  {prefix}{action}")
        else:
            print("  All patches already applied — schema is current.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Dry-run: print actions without applying")
    args = parser.parse_args()
    run(dry_run=args.check)


if __name__ == "__main__":
    main()
