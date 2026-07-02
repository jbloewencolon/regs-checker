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

## P1-4 — RLS baseline codified into a migration

Added `25cffe678fbc_rls_baseline_and_public_grant_revoke.py`, which enables RLS on every table, revokes `PUBLIC`/`anon`/`authenticated` grants (the `anon`/`authenticated` statements are wrapped in `pg_roles` existence checks so they're no-ops on non-Supabase Postgres), and installs the `rls_auto_enable` event-trigger backstop — codifying what Phase 0 applied ad hoc directly to Supabase.

**This migration runs on every database in the fleet — local Docker, CI, and Supabase — not just Supabase**, so it had to be proven safe on plain Postgres before being trusted anywhere. Verified against three scratch scenarios before applying live:

1. **Plain Postgres, no `anon`/`authenticated` roles** (simulates local Docker / CI): `alembic upgrade head` succeeds; the owning role (`regs`, matching `docker-compose.yml`'s `POSTGRES_USER`) can still insert/select without any restriction post-migration, since RLS `ENABLE` (not `FORCE`) never restricts the table owner; a fresh `CREATE TABLE` after the migration gets RLS auto-enabled by the event trigger, confirming the backstop actually fires.
2. **Postgres with `anon`/`authenticated` pre-created** (simulates Supabase): `alembic upgrade head` succeeds; `has_schema_privilege` confirms both roles lose schema `USAGE` while the owning role keeps it.
3. Both scenarios: `downgrade -1 && upgrade head` and a second idempotent `upgrade head` both exit 0.

**Caught a self-introduced regression during this verification, not after:** `CREATE FUNCTION` grants `EXECUTE` to `PUBLIC` by default in Postgres. Creating the new `rls_auto_enable()` function therefore re-opened exactly the class of gap Phase 0 had just closed — the live advisor scan (re-run after applying to Supabase) showed 2 new WARNs (`anon`/`authenticated` could call `rls_auto_enable()` via `/rest/v1/rpc/`). Fixed by adding `REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC` immediately after creating it, in both the live database and the migration file, then re-ran all three scratch scenarios plus the live advisor scan to confirm the fix and that nothing else regressed. Final live state: **0 ERROR, 0 WARN** on `wjxlimjpaijdogyrqtxc`.

Applied live to Regs Checker Supabase (`p1_4_rls_baseline_and_event_trigger_backstop` + the follow-up `p1_4_fix_rls_auto_enable_execute_grant`); `alembic_version` updated to `25cffe678fbc`.

## What remains in Phase 1

- **P1-3** (delete `_ensure_*` raw-SQL hacks): the only remaining item, blocked until local Docker Postgres receives the same reconciliation sweep as Supabase did here — deleting `_ensure_failed_attempts_table()` before that would break `alembic upgrade head` against local Docker if it has any equivalent drift.
- **P1-5 local Docker half**: not started, no access this session.
