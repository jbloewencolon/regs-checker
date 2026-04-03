# Regs Checker — Tasks

## Active Tasks

- **Run full extraction on 243 laws** — The pipeline is debugged and ready. Reset extractions, then run seed -> triage -> extract on the full corpus. This is the user's next manual step.
- **Merge feature branch to main** — All work is on `claude/ai-policy-audit-agents-pwle7`. Needs review and merge to `main`.

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
