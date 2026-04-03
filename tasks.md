# Regs Checker — Tasks

## Active Tasks

- **Run full extraction on 243 laws** — The pipeline is debugged and ready. Reset extractions, then run seed -> triage -> extract on the full corpus. This is the user's next manual step.
- **Merge feature branch to main** — All work is on `claude/ai-policy-audit-agents-pwle7`. Needs review and merge to `main`.

## Bugs / Issues (post-extraction run)

### BUG-1: Review tier counts may misrepresent quality (investigate)
Dashboard shows 130 A, 3752 B, 2112 C, 2046 D (8040 total). Need to verify whether the Orrick gate is working as expected here — 2046 Tier D items (25%) is significant but may be correct if only ~75% of laws have Orrick metadata. The 0/8040 approved count is expected (no review has happened yet). **Action**: Verify Orrick coverage across the 243-law corpus; confirm tier distribution is expected.

### BUG-2: "Generate Summaries" shows all summaries exist — cannot regenerate errors
**Root cause**: Summaries are auto-generated at extraction time (`extractor.py:1004-1012`). Every successful extraction already has `plain_summary` in its metadata. The batch function filters for missing summaries, finds none. Failed extractions live in a separate `FailedExtractionAttempt` table and have no Extraction record — so the summary step can't see them.
**Two sub-issues**:
  1. The "Generate Missing Summaries" button does nothing because all successful extractions already have summaries.
  2. Failed extractions cannot be re-extracted from this step — they need the separate "Retry Failed" workflow.
**Affected files**: `src/core/summary_generator.py` (batch query), `src/api/routes/dashboard.py` (endpoint), `templates/dashboard.html` (UI).

### BUG-3: Supabase sync says "not configured" despite .env having the URL
**Root cause**: Environment variable name mismatch. The dashboard (`dashboard.py:2375`) reads `REGS_SUPABASE_URL`. The standalone script (`sync_to_supabase.py:217`) reads `REGS_SUPABASE_PROJECT_URL`. If user set `REGS_SUPABASE_PROJECT_URL` in `.env`, the dashboard won't find it.
**Fix**: Either (a) add `REGS_SUPABASE_URL` to `.env` alongside the existing var, or (b) update the dashboard to also check `REGS_SUPABASE_PROJECT_URL` as fallback. Similarly for the key: dashboard checks `REGS_SUPABASE_KEY` first, then `REGS_SUPABASE_ANON_KEY`.
**Affected files**: `src/api/routes/dashboard.py:2375-2376`, `src/scripts/sync_to_supabase.py:217-218`, `src/core/config.py`.

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
