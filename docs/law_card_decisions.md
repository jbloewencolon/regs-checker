# Law Card Decision Record (D-1…D-7)

**Status:** Working decisions adopted for implementation. **D-1, D-4, D-6 need
product-owner confirmation** — no product owner was present in the implementation
session; the recommendation from `docs/law_card_dashboard_plan.md` §2.5 was adopted
provisionally so LC-1 (which is otherwise decision-independent of D-1) could proceed.
If product sign-off changes any decision below, only LC-4 (gated on D-1) and the
`human_review_state`/precedence wiring in LC-1e/LC-3 (D-4) need rework — LC-1a/b/c/d
are unaffected by D-1/D-6 and only lightly coupled to D-4.

| # | Decision | Adopted resolution | Confirmed by | Rework blast radius if reversed |
|---|---|---|---|---|
| D-1 | Run retention: purge vs retain N runs | **Retain last 3 runs** in DB; serving-run scoping on product queries. Purge becomes `prune_runs(keep=3)`. | ⏳ pending product owner | LC-4 only (comparison needs retained runs; LC-0..LC-3 don't touch the purge path) |
| D-2 | Frontend stack: React island vs Jinja2/HTMX port | **Jinja2/HTMX port.** No JS build stack exists in this repo; the bundle doesn't run as shipped anyway (broken imports — see LC-0b findings); HTMX is the existing interaction idiom. | ✅ engineering (rationale is technical, not product) | Would require introducing a build pipeline; high cost, not reversible cheaply — treat as settled |
| D-3 | Edit storage: in-place mutation vs immutable base + overlay | **Immutable base + `ExtractionFieldEdit` rows + materialized `effective_payload`.** Fixes the active G-1 destructive-edit defect. | ✅ engineering (data-integrity requirement, not a product tradeoff) | Foundational — reversing this un-fixes G-1; not revisited |
| D-4 | Does a human edit change `confidence_tier`? | **No.** Tier keeps meaning "model+pipeline confidence." A separate `human_review_state` (`unedited`/`edited`/`verified`) tracks review state. Publish/sync precedence: human edit wins over tier-derived value at sync time (mirrors `enforcement_normalizer` precedence order), stamped `edited_by_analyst`. | ⏳ pending product owner | LC-1e's sync-adapter precedence line + LC-3's `human_review_state` surfacing; assembler/API JSON shape unaffected (already models tier and review state as separate JSON keys) |
| D-5 | Edit survival across runs | **Key edits to `(canonical_key, extraction_identity)`, not raw `extraction_id`.** On new run: `payload_hash` match → auto-carry; changed → `status=orphaned` + review item. Never silent-drop, never silent-apply-to-changed-text. | ✅ engineering | LC-4c only (carry-forward runs at run-finalize time, which doesn't exist until LC-4a's retention lands) |
| D-6 | Editor identity for MVP | **Interim: required "reviewer name" session field + CSRF token on mutating routes.** Full authn/z stays Run-1 Phase 6a (separate, larger workstream). | ⏳ pending product owner (acceptable-risk call for legally-audited edits) | LC-3b only; LC-1's `ReviewAction.reviewer` / new `editor` columns already accept a free-text string, so swapping in real auth later is additive, not a schema break |
| D-7 | Bill-level payload editing in MVP? | **No — read-only in MVP.** Bill-level payloads (`enforcement_agent`, `applicability_agent`, `compliance_timeline_agent`) lack per-field verified evidence spans until EA5-1/EAR-2-3 land, so field-level validation (the whole point of LC-3) can't be done honestly for them yet. | ✅ engineering (blocked on a separate, already-tracked plan item) | LC-3's scope only; assembler already renders bill-level as a read-only panel per LC-2 |

## What "adopted provisionally" means for this implementation pass

- **D-1** is NOT implemented in this pass — `run_extraction(purge=True)` is untouched.
  LC-1's `ExtractionFieldEdit`/`effective_payload` design does not assume retention;
  it works today against the single serving run and simply carries forward the same
  data-integrity guarantee once D-1 lands (D-5's carry-forward logic is additive).
- **D-4** IS partially implemented: `ExtractionFieldEdit` and the assembler JSON model
  `human_review_state` as its own field, never blended into `confidence_score`/
  `confidence_tier`. The sync-precedence half (human edit wins at sync time) is
  scoped to LC-1e and implemented per the adopted resolution; if product overturns
  D-4, only that precedence line and its tests need to change.
- **D-6** IS partially implemented: `ExtractionFieldEdit.editor` and the API's
  `editor` request field are free-text strings today (no session/auth check wired
  in this pass — that's LC-3b, a UI-layer concern). The data model already has a
  place for identity so nothing here blocks future auth wiring.

## Non-decisions confirmed unaffected by pending sign-off

D-2, D-3, D-5, D-7 are pure engineering calls (data integrity, stack fit, or blocked
on another tracked plan) and are treated as final for this implementation.
