# Regs Checker — Tasks

## Active Tasks

- **DATA ALIGNMENT: CSV titles fixed, DB cleanup still needed** — DB audit on 2026-04-04 revealed 430 document families (186 legacy + 244 CSV-seeded). Root cause: CSV `title` column was corrupted during PDF extraction. **FIXED**:
  - Corrected 187 Orrick law titles in `fact_laws.csv` using legacy family data from Supabase
  - Enriched 17 IAPP rows with scope/status from `iapp_law_tracker.csv`
  - Added 38 new IAPP entries to `static/iapp_law_tracker.csv` (from 65 → 103 rows)
  - Identified 15 jurisdiction mismatches between CSV and IAPP tracker (bill number collisions across states)
  - **Still needed**: (1) Delete 186 legacy orphan families from local DB, (2) Re-seed from fixed CSV, (3) Re-extract 163 families with zero extractions
- **Re-sync local → Supabase (fresh)** — All Supabase tables were truncated on 2026-04-04. DO NOT sync until data alignment is resolved.
- **Merge feature branch to main** — All work is on `claude/ai-policy-audit-agents-pwle7`. Needs review and merge to `main`.

## Bugs / Issues (post-extraction run)

### BUG-1: 59 laws missing Orrick data → auto Tier D extractions — FLAGGED
**Root cause**: `data/fact_laws.csv` has 244 laws. 188 are Orrick-sourced, 56 are IAPP-sourced. Of the 188 Orrick laws, 185 have `key_requirements_raw` or `enforcement_penalties` populated. 2 Orrick + all 56 IAPP = 58 laws have neither, meaning ALL extractions from those laws get auto-Tier D by the Orrick gate.
**Update (2026-04-04)**: Legacy families (ID 1-186) have `key_requirements` for all 186 laws. The 2 missing Orrick laws may be recoverable. The 56 IAPP laws inherently lack Orrick data — the Orrick gate should either be modified for IAPP laws or IAPP laws should have equivalent reference data added.
**Affected files**: `data/fact_laws.csv`, `data/flagged_missing_orrick.md`, `src/core/confidence.py` (Orrick gate logic).

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

- Should the Orrick gate (auto-Tier D without Orrick data) apply to all laws, or only Orrick-sourced laws? Currently applies to all.
- What is the target extraction count? Previous run produced ~28k extractions from ~9k passages. New run with 7 agents may produce more.
- Should the sync to Policy Navigator include all extraction types, or filter to approved-only?
- Is the MinIO/S3 storage layer actually needed? The pipeline works without it (raw artifacts stored but not retrieved during extraction).
