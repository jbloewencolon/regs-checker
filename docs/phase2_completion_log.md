# Phase 2 Completion Log — Review-Binding Data Path

**Applied:** 2026-07-02
**Reference:** [`remediation_plan.md`](remediation_plan.md) Phase 2
**Purpose:** Durable record of the live Policy Navigator changes, the two product decisions confirmed mid-implementation, and the pre-existing bugs discovered while building and end-to-end-testing this phase's code — none of this is inferable from the plan document alone.

## Two design questions resolved mid-implementation

The original plan assumed `synced_extractions.review_status` was a static mirror of Regs Checker's approval decision. Live schema inspection during implementation showed this is wrong: Policy Navigator has its own independent, post-sync review workflow (`extraction_reviews` table + `fn_update_extraction_consensus` trigger) that overwrites the same column to `pending`/`flagged`/`verified`/`rejected` the moment a PN reviewer votes — it never writes `'approved'`, which is exclusively RC's vocabulary. Filtering on `review_status = 'approved'` alone would have silently excluded every row PN's own reviewers had verified, and a `CHECK (review_status = 'approved')` constraint would have broken the trigger's own legitimate writes.

Confirmed with the product owner:
1. **Review gate design**: RC approval is the baseline gate (a row must be RC-approved to sync at all); PN's own review is a backup veto layer, not a required blessing. A row is eligible if `review_status IN ('approved', 'verified')`.
2. **Purge scope**: 100% of the 13,488 rows in `synced_extractions` were `review_status = 'pending'` at the time of the purge (zero ever approved or verified — `extraction_reviews` had 0 rows, confirming PN's review workflow had never been used). This meant the purge criterion removed the entire table, not "some bad rows." Confirmed as acceptable: "it is ok to purge, because we're working with stale data. In the future, we will only want to sync new extractions."

## P2-1 — Publish gate

`sync_extractions.py`: added `review_status = 'approved' AND confidence_tier IN (eligible tiers)` to the actual row-fetching query that drives inserts (not just the reporting/dry-run queries — see the bug note below, this was initially missed and caught by end-to-end testing before it shipped).

`rollup_matrix.py`: added the same filter, using the `('approved', 'verified')` allow-list per the product decision above.

## P2-2 — Purge

Full account:
1. Snapshotted baseline counts: `synced_extractions` 13,488, `law_enforcement_details` 0, `law_obligation_flags` 56, `law_triggering_thresholds` 28, `jurisdictional_conflicts` 0, `extraction_reviews` 0.
2. Created `*_pre_p2_purge_backup` tables (`CREATE TABLE ... AS SELECT *, now() AS backed_up_at FROM ...`) for all 5 tables before deleting anything; verified backup row counts matched the baseline exactly.
3. Deleted every `synced_extractions` row failing `review_status = ANY(ARRAY['approved','verified']) AND confidence_tier = ANY(ARRAY['A','B','C'])` — given the 100%-pending baseline, this removed all 13,488 rows.
4. `TRUNCATE`d the four matrix detail tables — they're entirely computed from `synced_extractions` and had no supporting source rows left; `rollup_matrix.py`'s upsert logic has no "delete if no longer supported by any source row" path, so leaving them populated would mean presenting fully orphaned computed values with zero real source data behind them.
5. Verified: all 5 tables at 0 rows; `MAX(system_a_extraction_id)` (the sync cursor) correctly reset to 0, so the next `sync_extractions.py` run cleanly re-evaluates every RC extraction under the new gate.

**Security finding during backup:** the new backup tables and the P2-3 view (below) inherited Policy Navigator's default-privilege auto-grants to `anon`/`authenticated` on creation (full INSERT/UPDATE/DELETE/TRUNCATE/SELECT). All 5 backup tables had RLS auto-enabled with zero policies (confirmed via PN's pre-existing `rls_auto_enable` event trigger), so they were not actually exploitable — RLS-enabled-with-no-policy is default-deny regardless of the underlying grants — but the grants were still narrowed as defense in depth (`REVOKE ALL ... FROM anon, authenticated` on all 5 backup tables; the view was narrowed to `authenticated`-read-only, `anon` fully revoked). Verified via a full advisor re-scan: zero new ERROR/WARN findings from this session's work.

## P2-3 — "DB-enforced invariant" redesigned as a view, not a CHECK constraint

A raw `CHECK (review_status = 'approved')` was rejected once the PN review workflow was understood (see above — it would break `fn_update_extraction_consensus`'s legitimate writes of `'rejected'`/`'flagged'`). Instead, created `rollup_eligible_extractions` — `CREATE OR REPLACE VIEW ... WITH (security_invoker = true) AS SELECT * FROM synced_extractions WHERE review_status = ANY(ARRAY['approved','verified'])` — matching this schema's existing `public_extractions`/`verified_extractions` pattern. `rollup_matrix.py` now reads from this view instead of the raw table; the tier filter stays in Python since it's a runtime-configurable floor (`--min-tier`), not something a static view can encode.

## P2-4 / P2-5 — De-ratchet and penalty-unit honesty

Every `ON CONFLICT` clause across the three merge-aggregating rollup functions (`rollup_enforcement`, `rollup_obligation_flags`, `rollup_thresholds`) now does a plain `EXCLUDED.col` overwrite instead of `GREATEST`/`LEAST`/`COALESCE`/`OR` merging against the prior row. Added `contributing_extraction_count` and `derived_from_tier_floor` columns to all three tables (migration `p2_4_rollup_provenance_columns`).

`max_civil_penalty_usd` remains a `MAX()` — a full per-unit fix isn't possible without syncing `bill_level_extractions` (not currently synced to Policy Navigator at all) and preferring `enforcement_agent`'s properly `penalty_per`-tagged per-law record over this passage-level scatter-gather. That's a real architectural gap, larger than this phase's scope — see "New findings" below. Within the current constraint, `penalty_notes` now carries a caveat whenever more than one differently-worded penalty mention contributed to the ceiling.

All four rewritten rollup functions were verified end-to-end against a scratch Postgres schema mirroring the live one (approved/verified/pending/rejected/flagged rows across multiple tiers, a deliberately-seeded stale row to prove de-ratcheting, and a second scratch run after the P2-3 view refactor) before being applied live.

## P2-6 — Update-propagation leg

Added `sync_updates()` to `sync_extractions.py`. The existing id-cursor leg (`sync_extractions()`) discovers brand-new rows via `MAX(id)`, which by construction never looks backward — once a higher id has synced, a lower id that was ineligible at the time becomes permanently unreachable to that leg even after later approval. `sync_updates()` closes this gap by re-checking rows whose `updated_at` has advanced past a durable watermark, reusing the existing `sync_cursors` table (RR6e, already used by `sync_to_supabase.py`) with `table_name='extractions'`, `destination='policy_navigator_updates'`. Unlike the id cursor, this watermark is safe to advance past a still-ineligible row: `Extraction.updated_at`'s `onupdate=func.now()` guarantees any future change produces a fresh timestamp exceeding the watermark, so it's naturally re-checked later.

Design decision (matches "RC leads, PN backs up"): once a row is published, this leg refreshes its content (payload, evidence_spans, confidence_score, confidence_tier, section_reference, source_text_excerpt) on every RC-side change, but never touches `review_status`/`consensus_status`/PN's own review columns — those become Policy Navigator's domain once a row exists there. A row isn't retroactively un-published by a later RC-side status change; only PN's own review workflow can do that going forward.

Wired into `main()` — runs automatically after the id-cursor leg on every invocation; `--skip-updates` opts out.

### Bugs found and fixed while building this (all pre-existing, not introduced by this phase)

Building `sync_updates()` required an end-to-end scratch-Postgres test spanning both a source (RC) and target (PN) database with realistic schemas. That test immediately failed against the **existing, unmodified** `sync_extractions()` insert path, surfacing three column mismatches between the code and the real live `synced_extractions` schema:

1. `section_path`/`passage_text` (the dict keys `_insert_batch()` uses to build the INSERT's column list) — the real columns are `section_reference`/`source_text_excerpt`.
2. `source_created_at` — the real column is `system_a_created_at`.
3. `model_id` — genuinely didn't exist on the table at all; added via migration (`p2_6_add_model_id_column`) rather than dropped, since the code's clear intent (selecting `e.model_id` from the source and trying to write it) was to capture real provenance data, not dead code.

**This means `sync_extractions()`'s direct INSERT path had never actually succeeded against the live schema.** The 13,488 rows purged in P2-2 were almost certainly populated via the `bulk_sync_extractions`/`sync_from_rc_chunk` RPC functions (found and fixed for a missing authorization check during Phase 0 — see `phase0_completion_log.md`), which use different, correct column mappings, not via this script. Fixed all three mismatches in both `sync_extractions()` and the new `sync_updates()`.

A second bug, in code written earlier in this same phase: the P2-1 eligibility filter had been added to the reporting/dry-run queries but was missing from the actual row-fetching query that drives what gets inserted — meaning the core publish gate wasn't actually wired into the real data path. Caught by the same end-to-end test (observed "Source pending: 1" but "Synced: 2") before it was ever applied to a live database.

### Verification performed

Two-database scratch Postgres setup (separate RC-shaped and PN-shaped schemas, including `law_document_bridge` and `sync_cursors`) proving:
- A row that is `pending` at initial sync time is correctly excluded by `sync_extractions()`.
- The id-cursor leg alone provably cannot see that row's later approval (`WHERE id > cursor` finds nothing once cursor has advanced past it).
- `sync_updates()` correctly finds and syncs it by `updated_at`, independent of id position.
- A content correction (payload change) on an already-published row propagates correctly via `sync_updates()`.
- Two consecutive `sync_updates()` runs: second run finds and applies zero rows (idempotent, correct watermark advancement).
- Confirmed by code inspection (`review_routes.py`/`internal.py`: `item.extraction.review_status = status`) that the real production review-approval path uses SQLAlchemy ORM attribute assignment, which does trigger `onupdate=func.now()` — the design's core assumption holds in production, even though a raw `psql` UPDATE in the test itself (bypassing the ORM) does not.

## Known gap not fixed in this phase

`jurisdiction_code` exists on `synced_extractions` but neither sync leg currently populates it (it stays NULL for every row synced via `sync_extractions.py`/`sync_updates()`, as opposed to the RPC-function path which does resolve it via `dim_jurisdictions`/`fact_laws`). Doesn't break anything — no code currently filters or joins on it from these two functions — but is a real data-completeness gap worth closing in a follow-up: would need `sources.jurisdiction_code` joined through `document_families`/`document_versions` on the RC side (two more joins in both queries).

## P2-7 — Materialized-view refresh wiring

`src/db/views.py` already contained `SERVED_MATRIX_CELLS_VIEW`, `REFRESH_TRIGGER_FUNCTION`, `REFRESH_TRIGGER`, and unique indexes on the two obligation matviews — all wired into the single existing migration `a3f7b2c8d901_add_materialized_views.py` via `ALL_VIEW_DEFINITIONS`. Since that migration pulls the SQL from `views.py` at execution time (not a frozen snapshot), the code looked complete. Live inspection of Regs Checker Supabase (`wjxlimjpaijdogyrqtxc`) — whose `alembic_version` already claimed to be past this migration — showed otherwise: this is the same class of drift found repeatedly elsewhere in this audit, where a migration's *current* code is correct but the *historical* apply against a specific database silently diverged.

### What was found

- `served_matrix_cells` matview: missing (only `served_obligations` and `current_active_obligations` existed).
- `refresh_served_views()` function: did not exist.
- `trg_refresh_on_review` trigger on `review_actions`: did not exist.
- `served_obligations` and `current_active_obligations`: each had non-unique, differently-named indexes (`idx_served_obligations_jurisdiction`, `idx_served_obligations_type`) instead of the unique `ix_so_extraction_id`/`ix_cao_extraction_id` that `REFRESH MATERIALIZED VIEW CONCURRENTLY` requires — `current_active_obligations` had no indexes at all. Confirmed by attempting `REFRESH ... CONCURRENTLY`, which failed with "cannot refresh materialized view concurrently... Create a unique index with no WHERE clause" before the fix, and succeeded after.

### What was done

1. `p2_7_catchup_materialized_view_refresh_trigger` — added the missing `served_matrix_cells` view, `refresh_served_views()` function, and `trg_refresh_on_review` trigger.
2. `p2_7_add_missing_matview_unique_indexes` — added `ix_cao_extraction_id` and `ix_so_extraction_id` (both unique, on `extraction_id`).
3. Verified `REFRESH MATERIALIZED VIEW CONCURRENTLY` succeeds for both views, and that the trigger fires end-to-end (inserted a real row into `review_actions`, confirmed no error, cleaned up the test row).

### Freshness tracking for `/health`

Postgres doesn't record a matview's last-refresh timestamp anywhere queryable (`pg_stat_all_tables`'s vacuum/analyze columns don't track `REFRESH`). Added a `view_refresh_log` table (`view_name TEXT PRIMARY KEY, refreshed_at TIMESTAMPTZ`), seeded with all 3 view names at creation time, and had `refresh_served_views()` write fresh timestamps for `served_obligations`/`current_active_obligations` on every trigger fire (`ON CONFLICT (view_name) DO UPDATE`). `served_matrix_cells` is deliberately never updated by the trigger — its refresh was always meant to be "less frequently via scheduled job" per the original code comment, but no such job exists anywhere in the codebase (no scheduler infrastructure at all — no cron, no APScheduler). Building an actual periodic-refresh mechanism for it is a real gap but out of scope for this phase (the plan's acceptance test only covers `served_obligations`/`current_active_obligations` appearing in `/v1/obligations`); `/health` reports its freshness honestly rather than papering over the gap — its `views_last_refreshed` timestamp will show its last manual/creation-time refresh and visibly go stale, which is the correct signal until a scheduled refresh is built.

`src/api/app.py`'s `/health` endpoint now queries `view_refresh_log` and returns:
```json
{"status": "healthy", "version": "...", "views_last_refreshed": {"served_obligations": "...", "current_active_obligations": "...", "served_matrix_cells": "..."}}
```
Wrapped in try/except so a DB outage degrades to `"views_last_refreshed": null` rather than a 500 — `/health` itself must stay up to be useful. Verified against a scratch Postgres with a real `view_refresh_log` table (endpoint returns real ISO timestamps) and confirmed the pre-existing `test_health_endpoint` integration test (which runs without a live DB) still passes.

### Bug found and fixed while touching this function

`refresh_served_views()` had no `SET search_path`, unlike the `rls_auto_enable()` function added in P1-4 (which does pin `search_path=pg_catalog`, an established convention for this codebase). This tripped the advisor's `function_search_path_mutable` WARN. Fixed by adding `SET search_path = public, pg_catalog` and, matching the same P1-4 precedent, an explicit `REVOKE EXECUTE ON FUNCTION public.refresh_served_views() FROM PUBLIC` (Postgres grants `EXECUTE` to `PUBLIC` by default on `CREATE FUNCTION`). Verified the revoke doesn't break the trigger: created an unprivileged scratch-Postgres role with only `INSERT` on `review_actions` (deliberately no `EXECUTE` grant on the function) and confirmed the trigger still fires — Postgres trigger invocation isn't gated by the invoking session's `EXECUTE` privilege on the trigger function, the same behavior already relied on for `rls_auto_enable()`. Re-ran the live advisor scan afterward: the WARN is gone, only the expected `INFO`-level "RLS enabled, no policy" findings remain (unchanged from before this phase, present on every table by design).

## Live Policy Navigator changes (all via `apply_migration`, in order)

1. `p2_4_rollup_provenance_columns` — added `contributing_extraction_count`, `derived_from_tier_floor` to `law_enforcement_details`, `law_obligation_flags`, `law_triggering_thresholds`.
2. `p2_2_backup_before_purge` — created the 5 `*_pre_p2_purge_backup` tables.
3. `p2_2_execute_purge` — deleted all `synced_extractions` rows, truncated the 4 matrix detail tables.
4. `p2_3_rollup_eligible_extractions_view` — created the view.
5. `p2_2_lock_down_new_backup_objects` — revoked excess `anon`/`authenticated` grants on the 5 backup tables and the view.
6. `p2_6_add_model_id_column` — added the missing `model_id` column.

## Live Regs Checker Supabase changes this phase (all via `apply_migration`)

1. `p2_7_catchup_materialized_view_refresh_trigger` — added `served_matrix_cells`, `refresh_served_views()`, `trg_refresh_on_review`.
2. `p2_7_add_missing_matview_unique_indexes` — added `ix_cao_extraction_id`, `ix_so_extraction_id`.
3. `p2_7_view_refresh_log` — created `view_refresh_log`, updated `refresh_served_views()` to write to it.
4. `p2_7_pin_refresh_fn_search_path` — pinned `search_path`, revoked `PUBLIC` `EXECUTE` on `refresh_served_views()`.

`sync_cursors` already existed from Phase 1, confirmed reusable as-is for the new `policy_navigator_updates` destination — no PN schema change was needed for P2-6's cursor storage.
