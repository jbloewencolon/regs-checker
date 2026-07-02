# Regs Checker — Production Readiness Review

**Date:** 2026-07-01
**Scope:** Full repository audit — extraction pipeline, data model, Supabase architecture, security, legal/policy data modeling, dashboard UX, and operational readiness.
**Audience:** Technical, compliance, product, and legal-operations stakeholders.
**Evidence base:** Source code on `main` (commit `3d7aad2`), live Supabase security advisors for both projects (`wjxlimjpaijdogyrqtxc` regs-checker, `aaxxunfarlhmydvohsrm` Policy Navigator), committed schema backups, migration history, and the unit test suite.

---

## 1. Executive Summary

**Verdict: NOT READY for production. Strong pipeline engineering, but four launch-blocking defects sit between this system and any external user.**

The repository is an unusually well-architected *research and curation pipeline*. Provenance discipline (content-addressed artifacts, verbatim evidence spans, run versioning with git SHA + prompt-hash pinning), abstention-first prompting, and a deterministic summary layer are genuinely better than most production LLM extraction systems. The team's own docs are honest about fragility, which is a maturity signal.

However, the *serving* side is not production-grade:

1. **Security is the hardest blocker.** The live Regs Checker Supabase project has **11 tables with RLS fully disabled** (ERROR-level advisor findings) and 32 tables SELECT-able by the `anon` role via GraphQL — including `api_keys`. The FastAPI dashboard, review queue, and ~30 destructive pipeline endpoints (`full-reset-seed-ingest`, `sync`, purge/reset) have **no authentication at all**.
2. **Human review is not enforced on the product path.** `sync_extractions.py` copies **every** extraction — pending, rejected, Tier D — into the Policy Navigator `synced_extractions` table. The review queue exists, but the product database does not respect it. `rollup_matrix.py` then aggregates those rows into the law-level flags business users see.
3. **Migrations are broken.** Two Alembic files declare the identical revision ID `a3b9c5d7e028` with the same `down_revision`. Alembic will refuse to run (or behave nondeterministically), which is why the codebase carries `_ensure_*_table()` raw-SQL hacks as a workaround.
4. **Confidence is anchored to a secondary source.** The tier that gates publication is 50% weighted on token-similarity to the Orrick law-firm tracker — a marketing summary whose own text in `fact_laws.csv` is visibly garbled from PDF column interleaving. "Matches Orrick" is a proxy for trust, not a measure of statutory accuracy, and the current weighting lets a bad tracker row cap or inflate the score of a correct extraction.

Fix those four and this system is a credible v1. Ship without them and you have an unauthenticated write path into the exact database a compliance team is supposed to trust.

---

## 2. Key Strengths

These are real and worth preserving through any remediation:

- **Provenance chain is genuinely audit-grade.** `sources → document_families → document_versions → raw_artifacts (SHA-256, content-addressed via content_blobs) → normalized_source_records → extractions` with char offsets, `text_hash`, `source_hash`, `retrieved_at`, `session_year`, `bill_number`. Few legal-data pipelines can answer "exactly which bytes produced this claim" — this one can.
- **Run reproducibility.** `extraction_runs` pins git SHA, per-agent model config, and prompt template versions; extractions carry `prompt_hash`, `template_version`, `model_id`, and token/duration telemetry. `is_serving` gives a serving-run concept most teams never build.
- **Abstention-first prompting.** `detected: false` is a first-class output; evidence spans must be verbatim and are verified with 4-tier Unicode-normalized string matching; broad spans are penalized. This is the correct posture for legal text.
- **Deterministic presentation layer.** `summary_generator.py` produces plain-English summaries from structured payloads via templates — not a second LLM pass — so summaries can never introduce new hallucinated facts. This is exactly the right "lossless extraction, lossy presentation" split.
- **Failure engineering.** Circuit breaker with consecutive + rate thresholds, `extraction_attempts` lifecycle tracking (including abstentions, which naive designs lose), `failed_extraction_attempts` retry queue, stale-job recovery at startup, durable `pipeline_events`, JSON-repair strategies with tests.
- **Controlled vocabulary machinery.** Two-tier vocab (canonical codes + alias tables) across 7 dimensions, `vocab_review_queue` for unrecognized terms with provisional codes, fork-decision docs. This is proper ontology governance, mid-build.
- **Honest documentation.** `architecture.md` has a "Known Hacks / Fragile Areas" section that is accurate. `tasks.md` tracks an open contradiction the team hasn't resolved yet and says so. The `data_dictionary.md` for non-engineers is unusually good.
- **Evaluation harness with gold-standard fixtures** (CO SB205, CA SB1047) built before prompt iteration — the right order.
- **The unit suite is real and green.** 871 unit tests pass in ~34 s (verified during this audit), covering JSON repair, confidence scoring, normalizers, routing, and provider behavior. CI runs them on every push.

---

## 3. Critical Risks and Gaps

Ranked by launch impact.

### CR-1 — Supabase pipeline database is effectively public (SECURITY, BLOCKER)
Live advisor scan of `wjxlimjpaijdogyrqtxc` (regs-checker):
- **ERROR — `rls_disabled_in_public` on 11 tables:** `extraction_runs`, `compliance_concepts`, `concept_extraction_links`, `concept_tracker_links`, `extraction_attempts`, `extraction_verification_status`, `verification_run_summaries`, `content_blobs`, `pipeline_events`, `sync_cursors`, `vocab_review_queue`. Anyone with the project's anon key (public by design) can read *and write* these via PostgREST/GraphQL.
- **WARN — 32 tables exposed to `anon` via GraphQL SELECT**, including `api_keys` (hashes + names + scopes) and both materialized views.
- **INFO — 19 tables have RLS enabled with zero policies** — the sync only works because the service-role key is used, and the dashboard error text explicitly instructs users to paste the **service_role** key into `.env` (`dashboard.py:4043`). One leaked `.env` = full read/write on the pipeline of record.

The newer tables (the 11 ERRORs) were created after the older ones had RLS enabled — i.e., the RLS posture is *regressing* as the schema grows, because migrations don't include RLS statements.

### CR-2 — Unreviewed and rejected extractions reach the product database (LEGAL ACCURACY, BLOCKER)
`sync_extractions.py` selects `WHERE e.id > :cursor` with **no `review_status` filter** and no confidence-tier floor; rejected and Tier-D rows land in `synced_extractions` with `review_status` carried as an inert column. `rollup_matrix.py` then aggregates them into `law_enforcement_details`, `law_obligation_flags`, `law_triggering_thresholds` — again with no review/tier filter. The local materialized views (`served_obligations`, `current_active_obligations`) *do* filter on `review_status = 'approved'`, so the discipline exists — it just doesn't extend to the leg that feeds the actual product. A business user can see "private right of action: true" derived from an extraction a reviewer explicitly rejected. The `confidence_publish_min_tier` setting exists in `config.py` but is not enforced on this path.

### CR-3 — Duplicate Alembic revision `a3b9c5d7e028` (DATA INTEGRITY, BLOCKER)
`a3b9c5d7e028_di1_canonical_key_agent_name.py` and `a3b9c5d7e028_concept_actor_role.py` both declare `revision = "a3b9c5d7e028"`, `down_revision = "z2a8b4c6d027"`. Alembic errors on duplicate revisions; every fresh environment (CI, staging, a colleague's laptop, Supabase) will fail `alembic upgrade head`. This is presumably why the extractor carries `_ensure_extraction_enums()` / `_ensure_triage_table()` / `_ensure_failed_attempts_table()` raw-SQL fallbacks — treating the symptom. Schema drift between local Docker, Regs Checker Supabase, and Policy Navigator is now unauditable.

### CR-4 — Confidence tiers are dominated by a garbled secondary source (LEGAL ACCURACY, HIGH)
- The publication-gating score is 50% Orrick token-Jaccard; evidence grounding — the only signal computed against the *statute itself* — is 35%.
- `fact_laws.csv` Orrick fields are visibly corrupted by two-column PDF extraction (e.g., AZ SB 1359: *"…synthetic media message that the Permanent declaratory relief, Advertising person knows is a deceptive and fraudulent deepfake…"*). Jaccard similarity against interleaved text is noise in both directions.
- The Orrick gate makes tracker *coverage* a ceiling on trust: a perfectly grounded extraction from a law Orrick doesn't summarize is Tier D, while a mediocre extraction that happens to share tokens with a garbled blurb can reach Tier B.
- `--mode enrich-orrick` generates Orrick-*style* summaries with an LLM to "break the gate" — this quietly converts the tracker signal from "law firm validated" to "our own model agreed with itself" without any downstream flag distinguishing the two.

### CR-5 — No authentication on the operational surface (SECURITY, BLOCKER for any non-localhost deploy)
- `/dashboard/*` and `/internal/*` have zero auth. The `auth.py` docstring claims "/internal/ routes use session-based auth" — it does not exist. Approve/reject review decisions can be posted anonymously; `ReviewAction.reviewer` is client-supplied, so the "immutable audit log" has unauthenticated, spoofable identity.
- ~30 unauthenticated POST endpoints perform destructive operations: `full-reset-seed-ingest`, `fetch/reset-all`, `triage/reset-all`, `sync-to-supabase` (writes to cloud with the service-role key), `import-extractions`.
- `/v1/` API-key auth ignores `expires_at`, `scopes`, and `rate_limit_rpm` (all modeled on `ApiKey`, none enforced). The app description advertises "cached, rate-limited" — neither exists.
- Mitigation today is only that `start.py` binds uvicorn to `127.0.0.1` (while `settings.api_host` defaults to `0.0.0.0` — any other launch path is exposed).

### CR-6 — Legal instrument type is not modeled (LEGAL NUANCE, HIGH)
Everything ingested is hardcoded `source_type="state_statute"` (`local_ingest.py:289`) with one `"federal_framework"` path. Executive orders, agency rules/regulations, enacted-but-amendatory acts, resolutions, and guidance are indistinguishable from statutes. For the product's stated purpose — helping businesses distinguish *binding law* from *guidance* from *policy posture* — this is the single largest taxonomy gap (see §4 and the constraint in your own brief: "Distinguish clearly between law, guidance, policy, enforcement activity, and internal compliance recommendation"). Enforcement *activity* (AG actions, consent decrees, settlements) has no home in the model at all; `LegalEventType` covers legislative lifecycle only.

### CR-7 — Committed database backups and quarantine corpus in git (HYGIENE, MEDIUM)
`backups/*.sql` (full schema dumps of both cloud projects, with `search_path` disabled and RLS state visible) and `output/law_texts_quarantine/` (known-garbage source files) are checked into the repository. The backups leak infrastructure detail; the quarantine files risk being re-ingested by path-glob mistakes. Neither belongs in version control.

### CR-8 — Throughput: the pipeline is nearly fully sequential (COST/SPEED, MEDIUM — see §7 for the optimization plan)
`max_concurrent_agents_per_model` defaults to **1** (a single-GPU LM Studio constraint) and still governs the NVIDIA hosted path; passages are processed one at a time per document, documents sequentially. Against a hosted API that tolerates parallelism, a 232-law full run is doing on the order of thousands of serial round-trips at up to 300 s timeout each. There is no structured-output enforcement (`response_format`), so budget is spent on 5-strategy JSON repair; there is no cross-run response cache keyed on `(prompt_hash, passage_hash, model_id)` even though both hashes are already computed and stored.

---

## 4. Data Model Review

### What's right
- **Version-first document model.** `document_families` → `document_versions` (with `predecessor_id`, `temporal_status`, `effective_date`, `sunset_date`) plus append-only `legal_events` is the correct backbone for regulatory versioning. Most competitors model "the law" as one mutable row; this doesn't.
- **Unified `extractions` table** (type discriminator + JSONB payload + GIN index + generated dedup key on `(source_record_id, extraction_type, payload_hash)`) is a defensible choice over 8 narrow tables at this stage, and the Pydantic schemas per payload type restore structure at the edges.
- **Two-altitude extraction** (passage-level `extractions` + bill-level `bill_level_extractions` upserted on `(document_version_id, agent_name)`) matches how statutes actually distribute meaning — penalties in §X attaching to duties in §Y. Good legal-modeling instinct.
- **The compliance-concept layer** (`compliance_concepts` + typed member links + tracker links with three-state `tracker_grounded/conflict/silent`) is the right product unit: a requirement a compliance team can act on, not a raw model output. Denormalized currentness snapshot (`law_status`, `as_of_date`) is a smart dashboard optimization.
- `applicability_conditions` AND/OR/NOT tree and `obligation_dependencies` graph edges anticipate real "does this apply to me?" evaluation.

### Gaps and oversimplifications
1. **No instrument-type dimension** (CR-6). Add `instrument_type` (statute | executive_order | regulation | guidance | resolution | enforcement_action | internal_recommendation) as a controlled vocabulary on `document_families`, not a free string on `sources`. `Source.source_type` conflates publisher type with instrument type.
2. **Temporal status is present but under-maintained.** `TemporalStatus` covers the lifecycle well, but nothing in the pipeline updates it after seeding (status_checker exists but is not on the critical path); `current_active_obligations` filters on `temporal_status = 'active'`, so a stale status silently hides or falsely serves obligations. A law that dies in committee after ingestion keeps serving until someone manually flips it.
3. **`ExtractionType` mixes altitudes.** `obligation`, `definition`, `threshold` are content types; `ambiguity` is a retired meta-type kept "read-only"; enforcement lives both as an extraction type and as embedded `obligation.enforcement` and as a bill-level agent. The enforcement normalizer (`enforcement_normalizer.py` with per-field `_provenance` and orrick > iapp > bill_level > obligation precedence) is a good reconciliation layer — but its output is not itself persisted as a first-class, queryable record; it's recomputed in sync paths.
4. **Jurisdiction is a string pair** (`jurisdiction_code`, `jurisdiction_name`) rather than a dimension table in the pipeline DB (one exists in `data/dim_jurisdictions.csv` and in Policy Navigator). Municipal ordinances (NYC LL144 is in scope for any US AI tracker) don't fit a two-letter state code cleanly.
5. **`ReviewAction.corrections` is a dead end.** Reviewer corrections are stored as JSONB on the action but never produce a corrected extraction row — so a "revise" decision doesn't actually change what's served, and corrected data can't be distinguished from model output.
6. **Numeric legal facts live only inside JSONB.** `max_civil_penalty_usd`, `cure_period_days`, thresholds, etc. are queried with `payload->>'...'` and Python-side casts. At 232 laws this works; the moment you want cross-law analytics ("all laws with PRA and penalty > $10k"), you want generated columns or a typed `obligation_facts` projection. Penalties also lack `penalty_unit` normalization at the rollup level (per violation vs per day materially changes exposure — the field exists at bill level but the rollup takes `MAX(max_civil_penalty_usd)` across heterogeneous units).
7. **Rollup aggregation semantics can overstate.** `ANY(true)` for `private_right_of_action` across all of a law's extractions means one hallucinated or rejected row (per CR-2, rejected rows are present) permanently sets the flag `true` — and the `ON CONFLICT` update uses `COALESCE/GREATEST/LEAST`, which makes flags **ratchet-only**: a corrected re-run can never lower `max_civil_penalty_usd` or unset PRA without manual deletion. For compliance UX, false "you can be sued" flags are the fail-safe direction, but false penalty magnitudes are not.
8. **`canonical_key` on `document_families` is nullable with no uniqueness** — as the "stable join key (DI-1)" it should be unique-when-present and required going forward, or joins will silently fan out.

---

## 5. Supabase and API Architecture Review

### RLS and exposure
Covered in CR-1/CR-5. Summary of required posture:
- Regs Checker (pipeline) project: no table should be readable by `anon`. Enable RLS on all 30+ tables, add **no** anon policies (service-role sync bypasses RLS by design), revoke `anon`/`authenticated` SELECT grants so tables leave the GraphQL schema, and disable the GraphQL/Data-API exposure for this project entirely if the only client is the sync script.
- Policy Navigator project: better baseline (RLS on, policies exist, `rls_auto_enable` event trigger — good pattern worth copying to the pipeline project), but the advisors show **ERROR: three SECURITY DEFINER views** (`public_extractions`, `v_state_coverage`, `verified_extractions`) that bypass querying-user RLS, ~10 `USING (true)` write policies for `authenticated` (any signed-up user can UPDATE `extraction_audit_trail`, `framework_extractions`, `law_full_text` — an *audit trail* writable by any authenticated user is not an audit trail), and anon-executable SECURITY DEFINER RPCs including `bulk_reject_law_extractions` and `sync_from_rc_chunk` for authenticated users. Leaked-password protection is off.

### Migrations
- Duplicate revision (CR-3) plus the `_ensure_*` raw-SQL fallbacks mean the schema's source of truth is ambiguous. The Supabase projects are synced via PostgREST inserts, so their schemas were created out-of-band (`scripts/apply_pending_migrations.sql` exists as a manual artifact) — there is no single migration history covering all three databases. Adopt: one Alembic head, applied to local Docker *and* to Regs Checker Supabase via `supabase migration`/`apply_migration`, with RLS/grants inside the migrations.
- Enum management via `_ensure_extraction_enums()` (psycopg2 autocommit `ALTER TYPE ... ADD VALUE`) at extraction start is a race-prone hack; enum additions belong in migrations (they already exist there — `d1a5f3e7b904` — the hack exists only because migrations can't run; fixing CR-3 removes the need).

### Sync architecture
- The two-leg sync (local → RC Supabase via PostgREST; RC → PN via direct SQL) with `sync_cursors` ID-window pagination and `ON CONFLICT DO NOTHING` is reasonable and idempotent. Concerns:
  - **ID-cursor incremental sync never propagates UPDATEs.** Extractions are mutable (review status changes, CV recompute rewrites `confidence_score`/`tier`) but only new IDs sync; the cloud copy silently diverges from local truth after any verify pass or review action. Add an `updated_at`-based upsert leg (PostgREST `Prefer: resolution=merge-duplicates`) or an explicit re-sync of changed rows.
  - `--clear` deletes cloud tables row-by-row with `id=gte.0` — fine for dev, but it's an unauthenticated dashboard button away (CR-5) and there is no confirmation, dry-run default, or backup gate.
  - Non-incremental mode re-POSTs the entire table every run and counts conflict-skipped rows as "pushed" — sync reports overstate.
  - `sync_to_supabase.py` syncs the `api_keys` table to the cloud project. Even hashed, product API credentials do not belong in the pipeline mirror.
- `/v1/` queries interpolate a view name from a bool and otherwise bind parameters — no injection issue found; pagination is offset-based (fine at this scale). But `/v1/` reads local materialized views that are refreshed... when? No trigger/refresh scheduling was found wired to `review_actions` despite the views.py docstring claiming it — after approvals, `/v1/` serves stale data until someone manually refreshes. Confirm and wire `REFRESH MATERIALIZED VIEW CONCURRENTLY` post-review or on a schedule.

### Performance
- Missing composite index for the dominant dashboard/review pattern `(review_status, confidence_tier)` on `extractions` (there is `(extraction_type, review_status)` only). `pipeline_events` will grow unbounded — add retention or partitioning before it becomes the biggest table. JSONB GIN index on `payload` is broad; if only a few keys are queried, `jsonb_path_ops` or expression indexes are cheaper to maintain.

---

## 6. UX / Dashboard Review

### What works
- The persistent layout banner **"Informational only — not legal advice"** plus disclaimer records embedded in every export (CSV headers and JSONL `_record_type: "disclaimer"` objects) is exactly right, and rare to see done in exports.
- The review UI's confidence explainer ("How confidence scores work"), tier badges, per-component breakdown, and Orrick-gate flag give reviewers honest calibration.
- Evidence-span drill-down to verbatim statutory text with section paths supports the "trust but verify" workflow the product depends on.
- SSE progress, pause/resume/cancel, per-agent stats, and failure attribution by provider make the operator experience genuinely good.

### Where the experience can mislead
1. **Certainty inflation at the product layer.** By the time data reaches Policy Navigator matrix tables, confidence tiers, `orrick_gated`, `grounding_status`, and review status are either dropped or ignored (CR-2). A boolean `bias_testing_required = true` in `law_obligation_flags` reads as fact. Every rolled-up flag should carry (a) the tier of its *weakest contributing extraction*, (b) contributing `extraction_ids` (present — good), and (c) a rendered "based on N approved extractions, Tier B median" caption.
2. **Tier D means two different things** — "reviewed and weak" vs "no tracker coverage." The three-state `orrick_status` (aligned / tracker_silent / gated) exists in `extraction_verification_status` but the tier collapses it. Business users will read D as "wrong" when it often means "Orrick hasn't written this law up." Surface *"unverified — no third-party tracker coverage"* as its own badge, distinct from low-scoring.
3. **Stale-status risk is invisible.** No dashboard surface shows `as_of_date` / last-status-check per law. A compliance user cannot tell whether "active" means "checked last week" or "seeded four months ago." Add a per-law "last verified against source" timestamp to any card or matrix row — for legal content this is as important as the content itself.
4. **The review queue does not enforce reviewer identity** (anonymous approve/reject, CR-5), so the audit trail shown in the UI implies more governance than exists.
5. **Interpretation risks (`interpretation_risks`) don't reach the business surface.** The pipeline's most legally sophisticated output — "the term 'reasonable measures' is undefined, severity: medium" — stays inside payload JSON. That is exactly the nuance a non-lawyer needs ("this obligation exists but its standard is vague"), and it's already extracted. Render it as an "ambiguity note" on the obligation card.
6. **Filters** exist for jurisdiction/type/tier internally, but the product matrix lacks the filters business users will actually start from: sector, actor role (developer/deployer/employer), effective-date window, and "has private right of action."

---

## 7. Recommended Improvements

### Must fix before production
| # | Action | Addresses |
|---|--------|-----------|
| M1 | Enable RLS + deny-by-default policies on **all** Regs Checker Supabase tables; revoke `anon`/`authenticated` grants; remove `api_keys` from `SYNC_TABLES`; rotate the service-role key (treat it as exposed); stop instructing users to put service_role in `.env` for a dashboard-triggered sync — move cloud sync to a server-side job with a scoped key. | CR-1 |
| M2 | Filter both product-bound sync legs to `review_status = 'approved'` AND tier ≥ `confidence_publish_min_tier`; make `rollup_matrix.py` recompute from scratch per run (drop the COALESCE/GREATEST ratchet) and purge already-synced rejected rows from `synced_extractions`. | CR-2, §4.7 |
| M3 | Re-key one of the two `a3b9c5d7e028` migrations, chain it properly, verify `alembic upgrade head` from empty DB in CI, then delete the `_ensure_*` raw-SQL fallbacks. Add a CI job that runs migrations against a scratch Postgres. | CR-3 |
| M4 | Add authentication: any SSO/basic-auth gate on `/dashboard` + `/internal`, authenticated reviewer identity recorded server-side, and enforcement of `expires_at`/`scopes` in `verify_api_key`. | CR-5 |
| M5 | Fix Policy Navigator advisor ERRORs: convert the 3 SECURITY DEFINER views to `security_invoker = true`, replace `USING (true)` write policies with role checks (especially `extraction_audit_trail`, `law_full_text`), revoke anon EXECUTE on SECURITY DEFINER RPCs. | §5 |
| M6 | Re-extract or repair the Orrick metadata (the PDF column-interleaving corrupts the 50%-weight signal at its source); until then, cap Orrick alignment's weight or treat obviously-garbled rows as `tracker_silent`. Tag `enrich-orrick` LLM-generated summaries so they can never masquerade as law-firm validation in scoring or UI. | CR-4 |

### Should fix soon
| # | Action |
|---|--------|
| S1 | **Throughput/cost overhaul (LLM optimization):** (a) split concurrency config — keep `1` for local GPU, allow 8–16 parallel passages against NVIDIA with a semaphore per model; parallelize at the *passage* level, not just agents-within-passage; (b) request structured output (`response_format: json_object` / guided JSON where the NVIDIA endpoint supports it) to cut JSON-repair retries; (c) add a response cache keyed on `(prompt_hash, text_hash, model_id, template_version)` — both hashes already exist — so re-runs and recovery passes stop re-paying for identical calls; (d) merge `triage` into a single cheap batched call per document (many passages per prompt) instead of one call per passage; (e) revisit `definition_actor` on llama-3.1-8b at temperature 0.2 — definitions are the substrate for downstream actor normalization, and 8B + nonzero temperature is the wrong place to economize; (f) consider collapsing the 6 clause agents to 3 for short passages (obligation+rights, definitions+thresholds, mechanisms+preemption) — routing already proves the signal exists. |
| S2 | Propagate UPDATEs in incremental sync (upsert on `updated_at`, `Prefer: resolution=merge-duplicates`) so review decisions and CV recomputes reach the cloud. |
| S3 | Wire materialized-view refresh to review actions (or a scheduler) and expose "views last refreshed" in `/health`. |
| S4 | Wire `status_checker` into a scheduled job; surface per-law `last_verified_at` in the matrix; alert on laws whose `effective_date` passed while `temporal_status` ≠ active. |
| S5 | Turn reviewer `corrections` into a real mechanism: a correction produces a superseding extraction row (provenance: `human_correction`, link to original), and serving views prefer it. |
| S6 | Add `instrument_type` controlled vocabulary + `is_binding` flag to `document_families`; backfill the 232 rows (this is a day of analyst work with the CSV already in hand). |
| S7 | Remove `backups/` and `output/law_texts_quarantine/` from git (history rewrite optional; at minimum stop tracking). Add secret scanning (gitleaks) to CI. |
| S8 | Expand CI: run the migration job (M3), enforce the full ruff ruleset (currently advisory-only `|| true`), and add tests for the untested surface architecture.md itself lists (bill-level agents are now partially covered; signal routing, retag, JSON repair 3–5 still thin). Add a nightly gold-standard eval run with F1 regression gates. |
| S9 | Persist the enforcement normalizer's reconciled per-law record (with `_provenance`) as a table; make rollups read it instead of re-deriving. |
| S10 | Split `dashboard.py` (6,348 lines) and `extractor.py` (3,196 lines) along their existing seams before more features land — both files are past the point where change risk compounds. |

### Nice to have
- Generated columns (or a typed projection table) for `max_civil_penalty_usd`, `cure_period_days`, `private_right_of_action`, `effective_date` to enable cross-law analytics without JSONB gymnastics.
- Jurisdiction dimension table in the pipeline DB with support for municipal level.
- `pipeline_events` retention policy / monthly partitions.
- Embedding-based Orrick/IAPP similarity to replace token Jaccard once the tracker text is clean (pgvector is already installed in Policy Navigator).
- Keyset pagination on `/v1/` and actual response caching to match the API's self-description.
- An "enforcement activity" ingestion track (AG actions, settlements) as a new instrument type — it is the highest-value signal for business risk ranking and currently absent.

---

## 8. Suggested Data / Schema Changes

Concrete DDL-level recommendations (aligned with the priorities above):

1. **`document_families.instrument_type`** — `TEXT NOT NULL DEFAULT 'statute' CHECK (instrument_type IN ('statute','executive_order','regulation','guidance','resolution','enforcement_action','internal_recommendation'))` + `is_binding BOOLEAN`. Backfill from analyst review; expose in every product surface. *(Distinguishes law vs guidance vs policy vs enforcement — the core constraint of this product.)*
2. **`document_families.canonical_key`** — `UNIQUE` partial index where not null; make required for new rows.
3. **`synced_extractions` (Policy Navigator)** — add `CHECK (review_status = 'approved')` once M2 lands, so the invariant is enforced by the database, not the script.
4. **Rollup tables** — add `derived_from_tier_floor CHAR(1)`, `contributing_extraction_count INT`, `computed_at`, `run_id`; recompute-from-scratch semantics per run (drop ratchet upserts).
5. **`law_enforcement_details.max_civil_penalty_usd`** — pair with `penalty_unit TEXT` and never aggregate across units; if units differ across extractions, store per-unit maxima.
6. **`extractions`** — composite index `(review_status, confidence_tier)`; consider `provenance TEXT NOT NULL DEFAULT 'llm' CHECK (provenance IN ('llm','human_correction','tracker_backfill'))` to support S5 and honest UI labeling.
7. **`extraction_verification_status.orrick_status`** → promote `tracker_silent` to the product layer as `verification_badge` so "unverified (no tracker coverage)" is distinguishable from "low confidence."
8. **`sources`** — rename `source_type` → `publisher_type`; jurisdiction dimension table (`jurisdiction_id`, `level: federal|state|municipal`, `code`, `name`) referenced by `sources`.
9. **New table `enforcement_actions`** *(nice-to-have track)* — `(id, jurisdiction_id, instrument_ref, actor, action_type: investigation|settlement|consent_decree|litigation, date, summary, source_url, document_version_id NULL)` — first-class home for enforcement activity signals.
10. **RLS in migrations** — every new table migration includes `ALTER TABLE ... ENABLE ROW LEVEL SECURITY;` plus the deny-all baseline; copy Policy Navigator's `rls_auto_enable` event trigger to the pipeline project as a backstop.

---

## 9. Final Verdict

**Not ready for production.** Ready to *become* production-ready quickly — the hard, differentiating work (provenance, versioned runs, abstention discipline, concept layer, vocab governance) is done or well underway, and the blocking defects are days-to-weeks of focused effort, not redesigns.

**Top three actions before any deployment:**

1. **Lock down the data plane** — RLS + grant revocation on the Regs Checker Supabase project, service-role key rotation and removal from the dashboard path, authentication on the FastAPI app, and Policy Navigator's SECURITY DEFINER / `USING (true)` policy fixes. (M1, M4, M5)
2. **Make human review binding** — filter both sync legs and all rollups to approved extractions at or above the publish tier, purge already-synced unapproved rows, and enforce it with a database CHECK. Until this lands, nothing downstream can honestly be called "reviewed." (M2)
3. **Restore a single, runnable migration history** — fix the duplicate `a3b9c5d7e028` revision, prove `alembic upgrade head` from scratch in CI, delete the raw-SQL table-creation hacks, and fold RLS into migrations so the security posture stops regressing with each new table. (M3)

With those three closed, the remaining items (Orrick signal repair, throughput overhaul, instrument-type taxonomy, staleness surfacing) become fast-follow quality work on a system whose foundations are sound.

---

*This review is an engineering and data-governance assessment. It is not legal advice, and the pipeline it reviews is — as its own banner correctly states — informational only.*
