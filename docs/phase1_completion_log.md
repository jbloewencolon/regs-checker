# Phase 1 Completion Log — Migrations & Schema Truth (partial)

**Applied:** 2026-07-02
**Reference:** [`remediation_plan.md`](remediation_plan.md) Phase 1
**Purpose:** Durable record of what was changed on the live Regs Checker Supabase project (outside git) during P1-5/P1-6, and exactly what verification was performed for P1-1/P1-2/P1-6, so this doesn't need to be re-derived later — and so whoever runs the equivalent sweep against local Docker Postgres knows exactly what to check for.

## P1-1 — Fixed the duplicate Alembic revision

See the commit message on `fix(P1): repair broken Alembic migration history + add CI migration job` for the full account. Summary: `a3b9c5d7e028` was declared by two files; re-keyed `concept_actor_role.py` to `b4c0d6e8f030`, chained after DI-1. Verifying against a real `initdb`'d Postgres 16 instance (not just review) surfaced a second, independent gap — `failed_extraction_attempts` was never created by any migration — closed with a new `bf74ef19697d_create_failed_extraction_attempts.py`. Confirmed: fresh-DB `upgrade head`, idempotent re-run, and `downgrade -1 && upgrade head` all clean with a single head. Confirmed the pre-fix code reproduces the original failure on the same harness.

## P1-2 — CI migration job

Added the `migrations` job to `.github/workflows/ci.yml` (postgres:16-alpine service, three steps: fresh upgrade, downgrade+upgrade reversibility, idempotent re-upgrade). Ran the identical three steps locally against a `regs`/`regs`-credentialed scratch Postgres matching the CI service container's exact configuration before committing.

## P1-5 — Regs Checker Supabase reconciliation (local Docker Postgres NOT done — see below)

**Local Docker Postgres was not reachable from this sandbox session at all** (no `docker` daemon available). Everything below applies only to the live Regs Checker Supabase project (`wjxlimjpaijdogyrqtxc`). **The identical sweep described here still needs to run against local Docker Postgres before P1-3 can safely delete the raw-SQL `_ensure_*` hacks** — do not assume local Docker is in the same state as Supabase; they are independently-provisioned databases and could have diverged differently.

### What was found

`alembic_version` on Supabase reported `b4c0d6e8f029` (the post-P1-1-fix head at the time), which would normally mean "everything up to and including DI-1, concept_actor_role, and extraction_model_agreement has been applied." Before trusting that, every `add_column`/`create_table` statement across all 32 migration files was extracted and checked against the live schema via `information_schema`. Result:

- `compliance_concepts.actor_role` — present.
- `extractions.model_agreement_count` — present.
- `document_families.canonical_key` — **missing.**
- `extractions.agent_name` — **missing.**
- Every other column/table added by any other migration in the entire history — present, zero drift.

This means DI-1 (`a3b9c5d7e028_di1_canonical_key_agent_name.py`) was silently never applied to this database, even though later migrations were. The recorded `alembic_version` was not truthful.

### What was done

Applied DI-1's `upgrade()` logic verbatim (unchanged from the migration file — column adds, backfill UPDATEs, both indexes) directly against Supabase via `apply_migration` (named `p1_5_catchup_di1_canonical_key_agent_name`). Verified afterward:
- `document_families.canonical_key`, `uq_document_families_canonical_key`, `ix_document_families_canonical_key` — all present.
- `extractions.agent_name`, `ix_extractions_agent_name` — present.
- **15,543 / 15,543** extraction rows backfilled `agent_name` (zero left NULL) — the deterministic `extraction_type → agent_name` map covered every row.
- Re-ran the full 32-migration column/table sweep afterward — zero remaining drift anywhere.

`alembic_version` already read `b4c0d6e8f029` and is now genuinely accurate, so no stamp update was needed for that step.

### P1-6 addition to the same database

After adding the `2cf4e0a680ea_extractions_review_confidence_index.py` migration (composite index on `extractions(review_status, confidence_tier)`) and verifying it against a fresh scratch Postgres, applied the equivalent `CREATE INDEX IF NOT EXISTS` directly to Supabase and updated `alembic_version` to `2cf4e0a680ea` (confirmed via `SELECT * FROM alembic_version`).

**Current true state of Regs Checker Supabase: `alembic_version = 2cf4e0a680ea`, matches `alembic heads` exactly, zero schema drift against the full migration history.**

## P1-6 — Deferred schema constraints

- `document_families.canonical_key` unique partial index: **already existed** at the database level (DI-1 created it) — the actual gap was that `src/db/models.py`'s SQLAlchemy declaration only had `index=True` (a plain index), not reflecting the unique constraint. Left undeclared, a future `alembic revision --autogenerate` run would likely have proposed *dropping* the real unique index to match the model. Fixed by adding an explicit `Index(..., unique=True, postgresql_where=...)` to `DocumentFamily.__table_args__` matching the migration exactly.
- `extractions(review_status, confidence_tier)` composite index: genuinely missing. Added as migration `2cf4e0a680ea` and mirrored in `Extraction.__table_args__`. Verified both the migration (fresh scratch Postgres) and the ORM declaration (`Extraction.__table__.indexes`, `DocumentFamily.__table__.indexes` both load without error and list the expected index names) before applying live.

## What remains in Phase 1

- **P1-3** (delete `_ensure_*` raw-SQL hacks): blocked until local Docker Postgres receives the same reconciliation sweep as Supabase did here — deleting `_ensure_failed_attempts_table()` before that would break `alembic upgrade head` against local Docker if it has any equivalent drift.
- **P1-4** (codify P0-2/P0-3's RLS/grant changes into an actual migration): not started. The live RLS/grant lockdown from Phase 0 exists only as ad-hoc `apply_migration` calls against Supabase (see `phase0_completion_log.md`), not as a versioned migration a fresh database would receive.
- **P1-5 local Docker half**: not started, no access this session.
