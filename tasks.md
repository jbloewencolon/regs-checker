# Regs Checker — Tasks

## Active Tasks

- **DATA ALIGNMENT: CSV deduplicated, DB cleanup still needed** — Orrick/IAPP/CSV are 3 views of the same laws. **DONE**:
  - Merged 4 confirmed IAPP→Orrick duplicates (CA AB 2013, CA SB 53, CO SB 205, TX HB 149)
  - Added `iapp_scope` and `iapp_section` columns to fact_laws.csv
  - Recovered 87 bill numbers from old corrupted titles
  - CSV: 241 rows (187 Orrick + 53 IAPP-only + 1 other), down from 244
  - The 53 IAPP-only rows are mostly ACTIVE BILLS (pending legislation) not tracked by Orrick
  - **Still needed**: (1) Delete 186 legacy orphan families from local DB, (2) Re-seed from fixed CSV, (3) Re-extract families with zero extractions, (4) Sync to Supabase
- **Re-sync local → Supabase (fresh)** — All Supabase tables were truncated on 2026-04-04. DO NOT sync until data alignment is resolved.
- **Merge feature branch to main** — All work is on `claude/ai-policy-audit-agents-pwle7`. Needs review and merge to `main`.

## Bugs / Issues (post-extraction run)

### BUG-1: Laws missing Orrick data → auto Tier D extractions — REASSESSED
**Post-dedup status**: 241 rows. 187 Orrick-sourced (185 have key_requirements or enforcement_penalties). 53 IAPP-only (0 have Orrick data — they're active bills Orrick doesn't track). 1 other.
**Net impact**: Only 2 Orrick laws + 53 IAPP active bills lack Orrick data. The 53 IAPP bills are pending legislation — the Orrick gate legitimately flags them since there's no firm analysis to validate against.
**Recommendation**: Accept Tier D for IAPP-only active bills. Focus on getting the 2 missing Orrick laws' data.
**Affected files**: `data/fact_laws.csv`, `src/core/confidence.py` (Orrick gate logic).

### BUG-2: Failed extractions cannot be retried from "Generate Summaries" step — FIXED
**Root cause**: The "Generate Summaries" button is a no-op because summaries are auto-generated at extraction time (`extractor.py:1004-1012`). The real issue is that failed agent calls (stored in `FailedExtractionAttempt` table) need the **"Retry Failed"** workflow (`dashboard.py:2208`, `POST /api/run/retry-failed`), not the summary step.
**Fix applied**: Added a `Retry Failed` button + badge to the Extract step (Step 3) in `templates/dashboard.html`. It polls `GET /api/failed-extractions-count` every 10s and shows the count + button when failures exist.
**Affected files**: `templates/dashboard.html`.

### BUG-3: Supabase sync says "not configured" — FIXED
**Root cause**: Two problems:
  1. **Format mismatch**: `.env` had `REGS_SUPABASE_URL=postgresql://...` (Postgres connection string) but the sync code uses the REST API and needs `https://wjxlimjpaijdogyrqtxc.supabase.co`.
  2. **Missing API key**: Needed `REGS_SUPABASE_ANON_KEY` (a JWT service_role key), not a Postgres password.
**Fix applied**: Updated `.env` with REST URL and service_role JWT key. Added diagnostic error detection in `dashboard.py` for postgres:// URLs and non-JWT keys. Sync confirmed working (200 responses from Supabase).
**Affected files**: `.env` (user config, not committed), `src/api/routes/dashboard.py`.

## Next Tasks

- **Run verification pass (cross-validation + gap detection)** — After extraction completes, run the verification pass from the dashboard to populate cross-validation scores.
- **Generate summaries** — After extraction, run "Generate Summaries" from dashboard Step 4.5.
- **Sync local -> Regs Checker Supabase** — Dashboard Step 5. Supabase truncated 2026-04-04; needs fresh re-sync via `python -m src.scripts.sync_to_supabase`.
- **Sync Regs Checker -> Policy Navigator** — Dashboard Step 6. Requires `REGS_POLICY_NAVIGATOR_URL` in `.env`.
- **Run rollup matrix** — After sync, run `python -m src.scripts.rollup_matrix` to aggregate into the 4 matrix detail tables.
- **Review test coverage (IN PROGRESS)** — 403 pass, 13 fail. Added 76 new tests; fixed 7 Orrick gate failures. Remaining: 7 DB-required (need Docker), 5 stale mock targets (`fetch_document` removed), 1 stale module ref. 4 stale test files still to delete. See `agents/test-coverage/` for details.
- **Write handoff document (HANDOFF_DOCUMENT.md)** — Comprehensive walkthrough for CS undergrad audience. Started but not completed.

## Blocked Tasks
- **Cross-validation scoring in confidence model** — The `cross_validation` weight (25%) is redistributed to other components when not available. Needs a full verification pass run to populate.

## Questions / Clarifications Needed

- **RESOLVED**: Orrick gate applies to all laws — IAPP rows will get Orrick data once merged with their Orrick counterparts.
- What is the target extraction count? Previous run produced ~28k extractions from ~9k passages. New run with 7 agents may produce more.
- Should the sync to Policy Navigator include all extraction types, or filter to approved-only?
- Is the MinIO/S3 storage layer actually needed? The pipeline works without it (raw artifacts stored but not retrieved during extraction).
