# Phase 0 Completion Log — Emergency Lockdown

**Applied:** 2026-07-02
**Reference:** [`remediation_plan.md`](remediation_plan.md) Phase 0
**Purpose:** Durable record of what was changed directly on the live Supabase projects (outside git) plus the code-side fixes, so Phase 1 (P1-4, "RLS in migrations") can codify these into the Alembic history without re-deriving them.

## P0-1 — Key rotation (manual action required — not done)

**Not applied.** Rotating the `service_role` and `anon` keys requires the Supabase dashboard (Project Settings → API) and cannot be done via the available MCP tools. Given the service_role key has been living in developer `.env` files behind an unauthenticated dashboard endpoint (`dashboard.py:4043` instructs users to paste it in), **treat it as compromised and rotate both keys** for project `wjxlimjpaijdogyrqtxc` before relying on the RLS/grant lockdown below as a complete fix — a leaked service_role key bypasses RLS entirely. After rotating, update `.env` (`REGS_SUPABASE_KEY`) on every machine that runs `sync_to_supabase.py`.

## P0-2 / P0-3 — Regs Checker Supabase (`wjxlimjpaijdogyrqtxc`) lockdown — done

Two migrations applied directly via the Supabase MCP (`apply_migration`):

1. **`p0_lockdown_rls_and_grants`**
   - `ENABLE ROW LEVEL SECURITY` on the 11 tables that had it disabled: `compliance_concepts`, `concept_extraction_links`, `concept_tracker_links`, `content_blobs`, `extraction_attempts`, `extraction_runs`, `extraction_verification_status`, `pipeline_events`, `sync_cursors`, `verification_run_summaries`, `vocab_review_queue`.
   - `REVOKE ALL PRIVILEGES` on all tables, sequences, and functions in `public` from `anon`, `authenticated`, `PUBLIC`.
   - `ALTER DEFAULT PRIVILEGES` so future tables/sequences/functions aren't auto-exposed.
2. **`p0_lockdown_revoke_public_schema_usage`** (follow-up)
   - Discovered during verification: `REVOKE ALL ... FROM anon, authenticated` alone was insufficient because the `public` schema grants `USAGE` to the implicit `PUBLIC` pseudo-role by default (`nspacl` showed `=U/pg_database_owner`), which `anon`/`authenticated` inherit regardless of per-role revokes.
   - `REVOKE USAGE ON SCHEMA public FROM PUBLIC` — this is the change that actually removes the tables from PostgREST/GraphQL's visible schema for those roles.

**Verified:** `has_schema_privilege('anon','public','USAGE')` and same for `authenticated` → both `false`; `service_role`/`postgres` → both `true` (unaffected). Security advisor scan after the fix: **0 ERROR, 0 WARN** — every remaining finding is `rls_enabled_no_policy` at INFO level, which is the expected/correct end state for a service-role-only project (deny-by-default, service_role bypasses RLS by design).

## P0-4 — Drop `api_keys`/`export_jobs` from the Supabase mirror — done

- Code: `src/scripts/sync_to_supabase.py` `SYNC_TABLES` no longer lists `api_keys` or `export_jobs`.
- Verified both tables were already empty on the cloud project (0 rows each) — no purge of existing data was needed.

## P0-5 — Policy Navigator (`aaxxunfarlhmydvohsrm`) — done, with one item deliberately deferred

Applied via `apply_migration` (`p0_5_authz_gap_and_definer_hardening`):

- **Critical finding beyond the original audit's scope:** `bulk_reject_law_extractions`, `bulk_sync_extractions`, `sync_from_rc_chunk`, and `reset_synced_extractions` are `SECURITY DEFINER` functions grantable to `authenticated` with **no internal authorization check** — any signed-in user, not just admins, could mass-reject a law's extractions, inject fabricated extraction rows, or `TRUNCATE` the entire `synced_extractions`/`extraction_reviews`/`extraction_review_audit` history. Fixed by adding `IF NOT (is_admin() OR ...) THEN RAISE EXCEPTION` guards inside each function body, matching the `is_admin()`/`is_reviewer()` convention already used elsewhere in this schema (e.g. `admin_review_extractions` policy on `synced_extractions`, the `resp_*` table policies). **Verified**: calling each function without admin JWT claims now raises `permission denied` before any data is touched (tested live against all four).
- Revoked `EXECUTE` on `fn_propagate_regulatory_change`, `fn_update_extraction_consensus` (trigger functions), and `rls_auto_enable` (event-trigger function) from `anon`/`authenticated`/`PUBLIC` — these have no legitimate direct-RPC use case.
- `v_state_coverage`: switched to `security_invoker = true`. Confirmed safe first — all three base tables (`fact_laws`, `dim_jurisdictions`, `dim_legislative_statuses`) already grant open `SELECT` to both `authenticated` and `public`, so this is a zero-behavior-change fix that removes an unnecessary privilege escalation.
- `extraction_audit_trail`: replaced the `UPDATE` policy (`USING(true)/WITH CHECK(true)`) with an `is_admin()`-gated version — an audit trail that any signed-in user can freely rewrite isn't an audit trail. Left the `INSERT` policy open (append-only writes from any authenticated action is the normal pattern for this kind of table).
- `law_full_text`: replaced both `INSERT` and `UPDATE` policies with `is_admin()`-gated versions — the existing policy names ("Admins can insert/update law full text") already promised admin-gating that the `true` qual didn't deliver; unambiguous name/implementation mismatch.

**Deliberately left unchanged** (documented in the migration's SQL comments, not silently skipped):
- `public_extractions` / `verified_extractions` — still `SECURITY DEFINER`. Evidence: `synced_extractions` has **no** RLS policy granting `anon` any access at all (only `authenticated` via `synced_extractions_read_all`, `qual=true`); these two views are the *only* way an unauthenticated visitor sees any extraction data, and they already apply their own curation filter (`review_status <> 'rejected'`, `is_excerpt_excluded()`, ambiguity severity filtering). This reads as an intentional "curated public preview" surface, not an accidental RLS bypass — flipping to `security_invoker` would silently return zero rows to anon and could break a real public-facing feature. **Needs product-owner confirmation, not a unilateral change.**
- `activity_log`, `coverage_gaps`, `law_scope_exclusions`, and the `INSERT` policies on `framework_extraction_reviews`/`framework_extractions`/`framework_sources`/`obligation_control_crosswalk` — these use an "Auth ..." naming prefix (vs. the "Admin ..." prefix fixed above), which may indicate deliberately broader access for any signed-in team member rather than a bug. Also left the `UPDATE` policies on the four `framework_*`/`obligation_control_crosswalk` tables unchanged for the same reason. **Recommend a product-owner review pass in Phase 5** to confirm intent for each; if any should be admin-gated, apply the same `is_admin()` pattern used above.
- **Leaked-password protection** — still disabled. This is an Auth *service* setting (Project Settings → Authentication → Policies), not a SQL/DDL change, and isn't exposed by the available Supabase MCP tools. Needs manual dashboard action.
- `extension_in_public` (the `vector` and `http` extensions installed in `public` instead of a dedicated schema) — pre-existing, out of scope for Phase 0, tracked for a later cleanup pass.

## Code changes (this repo)

| File | Change |
|---|---|
| `src/core/config.py` | `api_host` default changed from `"0.0.0.0"` to `"127.0.0.1"` (P0-6) — the dashboard has no authentication yet (that's Phase 3), so binding to all interfaces by default was an unnecessary exposure. |
| `src/scripts/sync_to_supabase.py` | `SYNC_TABLES` no longer includes `api_keys` or `export_jobs` (P0-4). |

## Remaining before Phase 0 is fully closed

1. **Rotate both Supabase keys** for `wjxlimjpaijdogyrqtxc` (manual, dashboard).
2. **Enable leaked-password protection** on `aaxxunfarlhmydvohsrm` (manual, dashboard).
3. Product-owner sign-off on the deferred Policy Navigator policies listed above.
