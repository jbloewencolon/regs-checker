# Regs Checker — Tasks

## Active Tasks

- **Run full extraction on 243 laws** — The pipeline is debugged and ready. Reset extractions, then run seed -> triage -> extract on the full corpus. This is the user's next manual step.
- **Merge feature branch to main** — All work is on `claude/ai-policy-audit-agents-pwle7`. Needs review and merge to `main`.

## Bugs / Issues (post-extraction run)

### BUG-1: 59 laws missing Orrick data → auto Tier D extractions — FLAGGED
**Root cause**: `data/fact_laws.csv` has 244 laws. Only 185 have `key_requirements_raw` or `enforcement_penalties` populated. 59 laws have neither, meaning ALL extractions from those laws get auto-Tier D by the Orrick gate.
**Impact**: The 2046 Tier D items in the review queue include extractions that may be perfectly good but can never score above D because their source law has no Orrick reference data.
**Action needed**: Either (a) add Orrick data to those 59 laws in the CSV and re-seed, or (b) flag them for manual review, or (c) exclude them from extraction. User preference: they should not be included unless Orrick data is added.
**Affected files**: `data/fact_laws.csv`, possibly `src/ingestion/local_ingest.py` (seeding).

### BUG-2: Failed extractions cannot be retried from "Generate Summaries" step — FIXED
**Root cause**: The "Generate Summaries" button is a no-op because summaries are auto-generated at extraction time (`extractor.py:1004-1012`). The real issue is that failed agent calls (stored in `FailedExtractionAttempt` table) need the **"Retry Failed"** workflow (`dashboard.py:2208`, `POST /api/run/retry-failed`), not the summary step.
**Fix applied**: Added a `Retry Failed` button + badge to the Extract step (Step 3) in `templates/dashboard.html`. It polls `GET /api/failed-extractions-count` every 10s and shows the count + button when failures exist.
**Affected files**: `templates/dashboard.html`.

### BUG-3: Supabase sync says "not configured" — wrong URL format in .env
**Root cause**: Two problems:
  1. **Format mismatch**: `.env` has `REGS_SUPABASE_URL=postgresql://postgres:...@db.wjxlimjpaijdogyrqtxc.supabase.co:5432/postgres` (a direct Postgres connection string). The sync code uses the **Supabase REST API** (`{base_url}/rest/v1/{table}`) via httpx, so it needs the REST URL: `https://wjxlimjpaijdogyrqtxc.supabase.co`.
  2. **Missing API key**: The dashboard also needs `REGS_SUPABASE_KEY` (a Supabase anon or service_role JWT key), not a Postgres password. This key is found in Supabase Dashboard > Settings > API.
**Fix**: Update `.env` to have the REST URL and API key:
  ```
  REGS_SUPABASE_URL=https://wjxlimjpaijdogyrqtxc.supabase.co
  REGS_SUPABASE_KEY=eyJ...  (from Supabase dashboard > Settings > API > service_role key)
  ```
  The existing Postgres connection string can stay for direct DB access if needed, under a different name.
**Affected files**: `.env` (user config, not committed).

## Next Tasks

- **Run verification pass (cross-validation + gap detection)** — After extraction completes, run the verification pass from the dashboard to populate cross-validation scores.
- **Generate summaries** — After extraction, run "Generate Summaries" from dashboard Step 4.5.
- **Sync local -> Regs Checker Supabase** — Dashboard Step 5. Requires `REGS_SUPABASE_URL` and `REGS_SUPABASE_KEY` in `.env`.
- **Sync Regs Checker -> Policy Navigator** — Dashboard Step 6. Requires `REGS_POLICY_NAVIGATOR_URL` in `.env`.
- **Run rollup matrix** — After sync, run `python -m src.scripts.rollup_matrix` to aggregate into the 4 matrix detail tables.
- **Review test coverage (IN PROGRESS)** — 403 pass, 13 fail. Added 76 new tests; fixed 7 Orrick gate failures. Remaining: 7 DB-required (need Docker), 5 stale mock targets (`fetch_document` removed), 1 stale module ref. 4 stale test files still to delete. See `agents/test-coverage/` for details.
- **Write handoff document (HANDOFF_DOCUMENT.md)** — Comprehensive walkthrough for CS undergrad audience. Started but not completed.

## Blocked Tasks

- **Supabase sync testing** — Supabase projects may be paused. Cannot verify sync until they're active. Test with dry-run first.
- **Cross-validation scoring in confidence model** — The `cross_validation` weight (25%) is redistributed to other components when not available. Needs a full verification pass run to populate.

## Questions / Clarifications Needed

- Should the Orrick gate (auto-Tier D without Orrick data) apply to all laws, or only Orrick-sourced laws? Currently applies to all.
- What is the target extraction count? Previous run produced ~28k extractions from ~9k passages. New run with 7 agents may produce more.
- Should the sync to Policy Navigator include all extraction types, or filter to approved-only?
- Is the MinIO/S3 storage layer actually needed? The pipeline works without it (raw artifacts stored but not retrieved during extraction).
