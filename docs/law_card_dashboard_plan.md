# Law Card Dashboard Plan (LC) — Editable Per-Law Cards over the Extraction Pipeline

**Date:** 2026-07-19
**Status:** Plan only — no implementation started. Tracked as Phase LC-0 … LC-6 in `tasks.md`.
**Inputs reviewed:** the `Law Card Copy/` bundle (all 13 files), the full extraction
lifecycle (`local_ingest.py` → `parser.py` → `section_triage.py` → `extractor.py` →
`verification_runner.py` → `review_routes.py` → `sync_extractions.py`), the DB schema
(`src/db/models.py`), the dashboard stack (`templates/`, `dashboard.py`,
`review_routes.py`), and the standing plans in `tasks.md` (Run-1, EA, EAR, P3, SFH).

---

## Part 1 — Review of the `Law Card Copy/` bundle

### 1.1 What the bundle is

A point-in-time snapshot (generated 2026-07-19) of the **LawCard** component system
from the *ai-ethics-evaluator* project: React 18 JSX, lucide-react icons, CSS custom
properties. ~2,700 LOC across components, services, engines, data constants, fixtures,
and design tokens.

| File | Role | LOC |
|---|---|---|
| `components/LawCard.jsx` | 4-variant card (compact / browse / full / stub), paced disclosure, role filtering | 1,512 |
| `components/PolicyBadges.jsx` | Badge library (DataGap, Threshold, SourceAuthority, SourceProvenance, AuthorityType, Freshness…) | 287 |
| `components/lawSourceQuotes.js` | Passage collection + lexical obligation↔quote matching, `verbatim` flag | 111 |
| `services/extractionLoader.js` | Lazy per-jurisdiction JSON chunk loader (Supabase Storage / static, manifest freshness) | 124 |
| `services/normalize-extract.js` | Tag normalization + enforcement-actor routing | 42 |
| `services/textSanitize.js` | PDF boilerplate stripping, column-interleaving detection | ~150 |
| `engines/priority.js` | Explainable triage tiers with `reasons[]`, penalty-severity classifier | 208 |
| `data/constants-extract.js` | Status taxonomy, domain tags, obligation categories, triage labels | 119 |
| `css/lawcard-tokens.css` | `--lc-*` design tokens (ink scale, signal/match/triage colors) | ~150 |
| `fixtures/refLaws.js` | 4 reference laws spanning the design matrix (REF_CO/CT/NM/NY) | 201 |

### 1.2 Components and interface patterns

- **Four variants**: `compact` (list row, expand inline), `browse` (paced disclosure
  L0 → L1 → L3), `full` (audit dossier with `renderExpanded` extraction tabs), `stub`
  (dashed border, "On Our Radar", auto-routed via `is_stub`).
- **Paced disclosure**: headline → summary/obligations → enforcement panel, each layer
  behind an explicit user action (`aria-expanded` toggles).
- **Role-based filtering**: active-role obligations prominent; other roles collapse
  into "Also regulates…" disclosures.
- **Honest-unknown rendering** (the bundle's most valuable convention): null renders
  as absent, never defaulted; "Not yet triaged" is a hollow ring, never a low-priority
  fill; missing data gets explicit `DataGapBadge`s; enforcement chips are gated on
  enacted status and suppressed for "TBD" notes.
- **Verbatim honesty**: `lawSourceQuotes` marks each passage `verbatim: true|false` so
  a paraphrase is never presented as a quote, and its matching is explicitly framed as
  "related passages," not authoritative citations.
- **Explainable triage**: `priority.js` emits `tier` + `reasons[]`; a law with no
  signal gets `tier: null`, never a guess.

### 1.3 Data structures and field definitions

Two shapes:

1. **Law object** (curated tracker row): `identifier`, `canonical_law_id`, `name`,
   `level`, `jurisdiction`, `stateCode`, `status`, `priority`, `is_stub`,
   `relevance_score`, `effectiveDate`, `enforcementNote`, `coveredEntities[]`,
   `domainTags[]`, `description`, `fullSummary`, `sourceUrl`,
   `obligations[{id, type, requirement_type, description, actorRole, actorTags,
   isMandatory, policyId, enforcementAuthority?}]`.
2. **Extraction chunk** (lazy, per jurisdiction):
   `{jurisdiction, code, lawCount, extractionCount, byLaw: {lawId: {obligations,
   complianceMechanisms, rightsProtections, enforcements, definitions, ambiguities}}}`.

Neither matches regs-checker's shapes. Regs-checker's obligation is
`ObligationPayload` (subject / modality / action / object / condition / timeline /
enforcement / safe_harbor / consent_requirements / evidence_spans /
interpretation_risks); the bundle's is a flat curated row. The chunk contract still
carries `ambiguities`, which regs-checker retired (interpretation_risks are embedded).
**An adapter is required in every integration scenario.**

### 1.4 Editing and comparison workflows

**None exist.** The bundle is read-only display. There is no edit control, no input
validation, no versioning, no run comparison, no dirty-state handling anywhere in it.
Every editing/validation/comparison requirement of the product goal must be built new.

### 1.5 Validation in the bundle

Display-side only: enforcement gating rules, TBD detection, `looksInterleaved()`
fallback, `verbatim` flagging, status-set membership. No input validation of any kind.

### 1.6 Accessibility posture

Partial and untested-here: `aria-expanded` on disclosure toggles, `role="img"` +
`aria-label` on triage dots, `aria-hidden` on decorative icons, real `<button>`s.
Missing/unknown: focus management after expand/collapse, keyboard traversal of nested
disclosures, contrast verification of the oklch token palette, live-region feedback.
The README references `e2e/lawcard-a11y.spec.js` and visual baselines — **neither is
in the bundle**, so its a11y claims are unverifiable from what was shipped.

### 1.7 Integration friction (verified, concrete)

1. **Stack mismatch (the big one):** regs-checker has **no JS build stack** — no
   `package.json`, no bundler; the dashboard is FastAPI + Jinja2 + HTMX (CDN script in
   `templates/layout.html`). The bundle needs React 18, JSX compilation, lucide-react,
   and Vite-style `import.meta.env`.
2. **The bundle does not run as shipped**, even in a React host: `LawCard.jsx` imports
   `../data/constants` (actual file: `data/constants-extract.js`),
   `../services/normalize` (actual: `services/normalize-extract.js`),
   `./CoverageCard` (absent — README says severable), and `extractionLoader.js`
   imports `./supabase` (absent — README lists it as a stub that was never included).
   `priority.js` additionally imports `PENDING_STATUSES` / `DISCUSSION_STATUSES` /
   `TERMINAL_STATUSES`, which `constants-extract.js` does not export.
3. **Data-shape mismatch** per §1.3.
4. **Serving model mismatch:** `extractionLoader` expects pre-built per-jurisdiction
   JSON chunks in Supabase Storage / static files; regs-checker's data lives in
   Postgres behind FastAPI.

### 1.8 Reuse verdict

**Adopt as-is (copy the artifact):**
- `css/lawcard-tokens.css` — plain CSS custom properties, host-agnostic.
- `fixtures/refLaws.js` — convert to JSON; excellent smoke-test matrix
  (enacted/effective/withdrawn × curated/stub × 1–3 actor roles × enforcement
  present/null/TBD).
- The **design rules as a spec**: honest-unknown rules, enforcement gating,
  verbatim flagging, paced disclosure, data-gap badges. Write them into the new
  templates' acceptance criteria.

**Adopt with modification (port the logic, not the file):**
- Status taxonomy + `POLICY_STATUSES` labels/colors and `TRIAGE_LABELS`
  (`constants-extract.js`) → Python constants module; regs-checker's
  `TemporalStatus` enum is coarser and needs a mapping decision.
- Badge components (`PolicyBadges.jsx`) → Jinja2 macros with the same visual/ARIA
  contract.
- Disclosure/interaction patterns → HTMX partial swaps + a few lines of vanilla JS
  for `aria-expanded` toggling.
- `priority.js` → port only if/when a triage surface is wanted (post-MVP; regs-checker
  already has review priority + confidence tiers, and a second "priority" vocabulary
  would confuse non-specialists).

**Do not port (regs-checker already has stronger equivalents):**
- `textSanitize.js` — parser-side cleaning (EA2-4 strikethrough handling, revisor
  artifact stripping in `text_grounding.py`) is deeper and runs at ingest, where it
  belongs.
- `lawSourceQuotes.js` lexical matching — regs-checker has *verified* evidence spans
  with `match_tier` and raw-offset provenance (EA2-2); lexical similarity would be a
  downgrade. Keep only its `verbatim` display convention (already satisfied by
  `verified` + `loose_match`).
- `normalize-extract.js` enforcement-actor routing — already implemented server-side
  (`_discriminate_extraction_type`, `actor_normalizer`).
- `extractionLoader.js` — wrong serving model.

**Rebuild from scratch (bundle has nothing):** everything editable — field editors,
validation UX, edit versioning/audit, run comparison, change visualization.

**Architectural recommendation:** port the design system into the existing
Jinja2/HTMX stack; do **not** introduce a React island. Rationale: (a) no JS build
stack exists and the bundle wouldn't run without repair anyway; (b) all state is
server-side Postgres and validation must be server-authoritative (Pydantic schemas
already exist); (c) HTMX is already the interaction idiom for review actions;
(d) a React island would create a second frontend paradigm, second test stack, and a
build step for exactly one page. The bundle's transferable value is its **design
contract and fixtures**, not its JSX.

---

## Part 2 — Current-State Assessment

### 2.1 Branch alignment

- Development branch: `claude/legal-extraction-architecture-suw6pm` (this plan's
  branch; per session instructions all LC work lands here unless re-scoped).
- Related standing constraints from `tasks.md`:
  - The Run-1 plan **explicitly deferred** the "law-card data model" ("Deferred
    (confirmed): Law-card data model, applicability product, API…"). This plan
    reactivates it; the concept layer (Run-1 Phase 5) remains the PN hand-off
    boundary and is untouched.
  - `dashboard.py` split is deferred but the file is 6,424 lines — LC adds **new
    route modules**, never grows `dashboard.py`.
  - P3 (tier-only publish gate) means anything LC writes to `review_status` /
    payloads has direct product-sync consequences.
  - EAR-0-4 (audit stamps) and EA 6a (analyst auth identity) overlap LC needs —
    coordinate, don't duplicate.

### 2.2 Relevant architecture (extraction lifecycle trace)

```
fact_laws.csv + output/law_texts/
  └─ local_ingest.py      → Source → DocumentFamily (canonical_key, DI-1)
                             → DocumentVersion (predecessor chain, source_hash)
  └─ parser.py            → NormalizedSourceRecord (passages: section_path, ordinal,
                             parse_quality, amendment-markup flags)
  └─ section_triage.py    → SectionTriageResult (relevant / not_relevant / uncertain)
  └─ extractor.py         → ExtractionRun (git SHA, prompt versions, model config,
                             is_serving) ── run_id FK on:
                             • Extraction (payload JSONB, evidence_spans w/ verified +
                               match_tier + char offsets, confidence_score/tier,
                               payload_hash, agent_name, metadata_)  [clause level]
                             • BillLevelExtraction (3 agents × law)   [bill level]
                             + ReviewQueueItem per extraction
  └─ verification_runner  → CV / gap / citation → ExtractionVerificationStatus,
                             confidence recompute, review-priority sync
  └─ review_routes.py     → approve / reject / edit / retag (+ ReviewAction audit)
  └─ concept_grouping.py  → ComplianceConcept (+ links)
  └─ sync_extractions.py  → Policy Navigator (Supabase), gate: tier A/B/C AND
                             review_status != 'rejected'
```

**The "law" entity** = `DocumentFamily`, stably keyed by `canonical_key` (DI-1,
unique-indexed). A law card aggregates: family + current DocumentVersion + that
version's NormalizedSourceRecords + their Extractions (serving run) + the law's 3
BillLevelExtractions + tracker metadata (`metadata_.orrick_*`, `iapp_*`) + review
state. Everything needed for a read-only card **already exists in the DB** — no new
extraction-side work is required to render.

### 2.3 Existing reusable components (regs-checker side)

- **Review UI** (`review.html`, 596 lines): already has per-field editing with typed
  widgets — selects for `modality`, `severity`, `threshold_type`, `right_type`,
  `mechanism_type`, normalized-actor fields; booleans as Yes/No/Unknown; JSON
  textareas for nested objects; `<label for=…>` wiring. This is the seed of the
  field-editor library — extraction-centric today, law-centric under LC.
- **Audit trail**: `ReviewAction` (action, reviewer, comment, `corrections` JSONB —
  the corrections column exists and is unused by the edit path today).
- **Run versioning**: `ExtractionRun` with `run_id` FK + `is_serving` flag — the
  skeleton comparison needs.
- **Validation machinery** (server-side, deterministic, already tested):
  `EXTRACTION_TYPE_SCHEMAS` Pydantic models, `verify_evidence_spans()` (4-tier,
  offsets), `check_numeric_grounding()`, `normalize_date()`, `actor_normalizer`,
  vocab loader + `VocabReviewQueueItem`.
- **Plain-language surface**: `summary_generator.generate_summary()` already writes
  `plain_summary` into extraction metadata.
- **Auth**: API-key auth on `/v1/`, session auth on `/internal/` (`middleware/auth.py`,
  `docs/auth_posture.md`); dashboard/review routes are effectively open and reviewer
  identity is hardcoded `"dashboard"`.

### 2.4 Gaps and risks

**G-1 (Critical, data integrity) — destructive edits.**
`POST /api/review/{queue_id}/edit` merges user input straight into
`extraction.payload`. The model's original output is gone after the first edit; the
only trace is a `ReviewAction.comment` naming the fields. Also: `payload_hash` is not
recomputed (desyncs the `uq_extractions_dedup` unique index and QA-4 dedup),
evidence spans are not re-verified (an edit can silently invalidate the "verbatim"
promise), confidence is not recomputed or flagged, and no schema validation runs
(free-typed JSON can produce a payload Pydantic would reject). This endpoint is the
first thing LC must replace.

**G-2 (Critical, comparison blocker) — full runs purge extractions.**
`run_extraction(purge=True)` deletes all Extraction rows; "Full runs purge all
extractions first." Cross-run comparison in the DB is therefore impossible today, and
**a purge also deletes any human edits stored on extraction rows**. `run_id` +
`is_serving` exist precisely to enable retention ("query-filter refactor deferred" —
Run-1 1b), but the refactor never happened. This is the single largest architectural
decision LC forces (Decision D-1).

**G-3 (High, security/audit) — no editor identity.** Reviewer is hardcoded
`"dashboard"`; dashboard routes have no auth gate; HTMX POSTs have no CSRF
protection. An edit trail without identity is not an audit trail. Overlaps Run-1 6a.

**G-4 (High, usability) — the current editor is specialist-only.** Raw schema keys as
labels (`subject_normalized`), JSON textareas for nested structures
(timeline/enforcement), no help text, no validation messages beyond "Invalid JSON
payload", no confirmation of what changed.

**G-5 (Medium, a11y) — current templates**: inline styles, color-only severity
encoding in several chips, no live-region announcements for HTMX swap results, no
skip links; contrast unaudited.

**G-6 (Medium) — no law-centric page exists.** The dashboard is pipeline-centric
(runs, triage, review queue); nothing lists laws or aggregates per law except
CSV exports and the concepts page.

**G-7 (Medium, concurrency)** — no optimistic locking anywhere in the review path;
two tabs can silently clobber each other's edits.

### 2.5 Decisions that must be resolved (blocking, with recommendations)

| # | Decision | Recommendation |
|---|---|---|
| D-1 | **Run retention.** Keep purge (comparison from disk archives only), or retain N runs in DB filtered by `run_id`/`is_serving`? | Retain **last K runs (start K=3)** in DB; serving-run filter on all product queries (finishes Run-1 1b's deferred refactor). Purge becomes "prune runs older than K." Comparison and edit survival both need this. |
| D-2 | **Frontend stack.** React island vs Jinja2/HTMX port. | Jinja2/HTMX port (rationale §1.8). Revisit only if a second rich page ships. |
| D-3 | **Edit storage semantics.** In-place mutation vs immutable base + overlay. | Immutable base + `FieldEdit` rows + materialized `effective_payload`. Original model output is never mutated again (§3.3). |
| D-4 | **Edited-data confidence.** Does a human edit change confidence_tier? | No. Edits set a separate `human_review_state` (`unedited / edited / verified`); tier keeps meaning "model+pipeline confidence." Publishing precedence (edit wins) is applied at sync/export, mirroring `enforcement_normalizer` precedence. Aligns with SFH-3a's separate-axis direction. |
| D-5 | **Edit survival across runs.** When a new run re-extracts a law, what happens to edits? | Edits key to `(canonical_key, extraction identity)`, not just extraction_id. On new run: auto-carry-forward when the new base payload is unchanged (payload_hash match); queue "re-apply?" review item when it changed. Never silently drop, never silently apply to changed text. |
| D-6 | **Editor identity.** Full auth (Run-1 6a) or interim named-reviewer? | Interim: required "reviewer name" session field + server-side session id logged on every write; CSRF token on mutating routes. Full authn/z remains 6a. MVP must not ship anonymous edits. |
| D-7 | **Bill-level edits in MVP?** | Read-only display of bill-level payloads in MVP; editing them follows in LC-3b after clause-level editing proves the pattern (their payloads lack per-field spans until EA5-1/EAR-2-3). |

---

## Part 3 — Proposed Architecture

### 3.1 Frontend (Jinja2 + HTMX, ported design system)

New templates (never touching `dashboard.py`):

```
templates/
  laws.html                 # law list: search/filter by state, status, tier mix
  law_card.html             # the card page (three tabs: Overview | Extractions | Runs)
  partials/
    lc_badges.html          # Jinja2 macros ported from PolicyBadges.jsx
    lc_field_editor.html    # one field: label, widget, help, evidence, validation slot
    lc_extraction_panel.html# one extraction: fields + evidence + edit state
    lc_diff_row.html        # comparison row: old/new/changed state
static/lawcard-tokens.css   # copied from bundle (+ dark-scheme audit)
static/lawcard.js           # ~100 lines vanilla: aria-expanded toggles, dirty guard
```

Ported patterns (from the bundle, as spec): paced disclosure; honest-unknown (null →
"Not extracted" gap badge, never a fake default); status taxonomy labels; verbatim
semantics (Tier-1/2 spans render as quotes with highlight offsets; Tier-3/4 render
with a "near match" marker; unverified spans render with an explicit warning, never
as a quote); enforcement gating rules.

### 3.2 Backend services and APIs

New modules:

```
src/api/routes/law_card_routes.py    # pages + HTMX fragments
src/api/routes/law_card_api.py       # JSON API (also serves future consumers)
src/core/law_card_assembler.py       # read-model builder (pure, unit-testable)
src/core/edit_service.py             # validate-then-apply edit engine
src/core/run_comparison.py           # cross-run matching + diff (pure)
```

Endpoints (HTMX fragments mirror the JSON API):

```
GET  /laws                                     # list page
GET  /laws/{canonical_key}                     # card page (serving run default)
GET  /api/laws?state=&status=&q=&page=         # list JSON
GET  /api/laws/{key}/card?run_id=              # assembled card JSON
POST /api/laws/{key}/extractions/{id}/edits    # propose edit {field_path, new_value, reason}
POST /api/edits/{edit_id}/validate             # dry-run validation → messages
POST /api/edits/{edit_id}/apply                # apply (server re-validates; optimistic-lock token)
POST /api/edits/{edit_id}/revert
GET  /api/laws/{key}/compare?base_run=&target_run=   # diff JSON
```

`LawCardAssembler` output contract (the "card JSON"):

```jsonc
{
  "law": {"canonical_key", "title", "short_cite", "jurisdiction", "status",
           "effective_date", "source_urls": {"primary", "orrick", "iapp"}},
  "run":  {"id", "started_at", "is_serving", "git_sha"},
  "bill_level": {"enforcement": {...}, "applicability": {...}, "timeline": {...},
                  "_input_truncated": bool},
  "extractions": [{
      "id", "type", "agent", "section_path", "confidence": {"tier","score","breakdown"},
      "review": {"status", "priority", "human_review_state"},
      "fields": [{"path", "label", "value", "original_value", "edited": bool,
                   "widget", "help", "evidence": [{"text","verified","match_tier",
                   "char_start","char_end"}]}],
      "flags": {"truncated","was_repaired","numeric_mismatch","ungrounded_fields"}
  }],
  "gaps": ["no_preemption_extractions", "tracker_silent", ...]   // honest-unknown
}
```

Field labels/help come from a new `src/core/field_catalog.py`: one registry mapping
every payload field → plain-language label, description, widget type, allowed values
(sourced from the Pydantic schemas + ratified vocab), and "specialist term?" glossary
entry. This is also where EAR-5-1's alias tables plug in when they land.

### 3.3 Data model and versioning

**New tables (one Alembic migration per phase that needs it):**

```python
class ExtractionFieldEdit(Base):          # LC-1
    """One proposed-or-applied human edit to one field of one extraction.
    The base Extraction.payload is IMMUTABLE after creation (G-1 fix)."""
    id, extraction_id (FK, index)
    canonical_key       # law identity — survives run purges (D-5)
    extraction_identity # (extraction_type, agent_name, payload_hash-at-edit-time)
    field_path          # dotted path, e.g. "enforcement.max_civil_penalty_usd"
    old_value / new_value (JSONB)
    reason (Text)       # required, shown in audit trail
    status              # proposed | applied | reverted | superseded | orphaned
    validation_report (JSONB)   # schema/grounding/vocab results at apply time
    editor (String)     # D-6 identity
    lock_token          # optimistic-lock: extraction.updated_at seen by the editor
    created_at / applied_at

class LawCardState(Base):                 # LC-1
    """Per-(law, run) rollup so list pages don't assemble 232 cards per request."""
    id, canonical_key (index), run_id (FK)
    extraction_count, edited_count, tier_counts (JSONB)
    human_review_state   # none | in_progress | complete
    card_cache (JSONB)   # assembled card, invalidated on edit/verify events
    updated_at
```

**Modified (LC-1):** `Extraction` gains `effective_payload` (JSONB, nullable) —
materialized base⊕edits overlay, recomputed by `edit_service` on apply/revert;
`NULL` means "no edits, read `payload`." Base `payload` becomes write-once (enforced
in `edit_service` + a guard assertion; the old edit endpoint is removed). Consumers
(sync, rollup, concepts) read `COALESCE(effective_payload, payload)` — one
helper, `Extraction.current_payload` property, so the change is one review surface.

**Run versioning (D-1, LC-4):** stop deleting on full runs; new runs write under
their own `run_id`; `is_serving` promotion is already implemented. Add
`prune_runs(keep=K)` and switch the four query sites that assume "all extractions
are current" (dashboard stats, review queue, concepts, sync) to serving-run scope.

**Edit lifecycle:**

```
proposed ── validate (dry-run: Pydantic field-level + vocab + numeric/date
 │           normalization + span re-verification if field is span-bearing)
 ├─ validation fails → editor sees per-field messages; nothing persisted to payload
 └─ apply → FieldEdit.status=applied, effective_payload recomputed,
            ReviewAction row written, LawCardState invalidated,
            review_status stays pending until explicit approve
New run arrives (D-5):
  base unchanged (same payload_hash)  → edits auto-carry to the new row
  base changed                        → edits → status=orphaned + review item
                                        "law text/extraction changed — re-apply?"
```

### 3.4 Extraction-pipeline integration points

- **Create:** no pipeline change — cards are a read model over what
  `extract_single_record` / `_run_bill_level_agents` already write. `LawCardState`
  rows are built by a post-run hook in `run_extraction` finalize (and backfillable
  by script).
- **Verification:** CV/gap/citation results already land in extraction metadata +
  `ExtractionVerificationStatus`; the assembler surfaces them as card flags.
- **Sync:** `sync_extractions.py` switches to `current_payload` and gains one gate
  input: `human_review_state` (edited-and-approved rows sync with an
  `edited_by_analyst` provenance stamp — coordinates with the P3 gate and
  `enforcement_normalizer` precedence, where human edit ranks **above** orrick).
- **EAR coordination:** EAR-0-4's sampling-param stamps and EAR-2-1's
  `ungrounded_fields` land in extraction metadata — the card renders both; do not
  duplicate that work here.

### 3.5 Edit, validation, and comparison workflows (user-facing)

**Review:** law list → card → Extractions tab → extraction panel shows each field
with plain label, value, evidence quote (highlighted via Tier-1/2 offsets), and
gap badges for absent fields.

**Edit:** click field → widget per `field_catalog` (select for vocab fields, date
picker for ISO dates, number+unit for numerics, textarea for prose; nested objects
become grouped sub-forms — never raw JSON for MVP fields) → "Check" runs dry-run
validation → messages in plain language ("This date isn't in a format we recognize.
Try 2026-07-01." / "This amount doesn't appear in the quoted law text — double-check
the source.") → "Save" applies with reason (required) → panel shows *edited* chip +
"view original / revert."

**Compare:** Runs tab → pick base/target run → `run_comparison` matches extractions
across runs (match key: `extraction_type` + canonicalized material fields, reusing
`_payload_hash` canonicalization and the QA-4 similarity machinery; unmatched =
added/removed) → diff view: per-law summary counts, then per-extraction rows in three
states — **Added** / **Removed** / **Changed** (field-level old→new, changed fields
listed by plain label). Text deltas render as side-by-side values, not inline
character diffs (legal prose diffs read poorly inline at non-specialist level).

---

## Part 4 — Phased Roadmap

> Effort labels: S (≤1 session), M (1–3 sessions), L (3+ sessions/operator-coupled).
> Every phase ships behind the `law_cards_enabled` settings flag until LC-6 rollout.

### Phase LC-0 — Repository alignment & technical discovery (S)

- **Objective:** Ratify the blocking decisions; make the bundle's value extractable;
  establish baselines.
- **Scope:** No product code. Decision record + bundle triage + a11y/access baseline.
- **Technical tasks:**
  1. Decision record `docs/law_card_decisions.md` resolving D-1…D-7 (owner sign-off
     on D-1, D-4, D-6 — product; rest engineering).
  2. Move `Law Card Copy/` → `reference/law_card_bundle/` (space-free path, marked
     reference-only, excluded from lint/test globs); extract `lawcard-tokens.css` →
     `static/`; convert `refLaws.js` fixtures → `tests/fixtures/law_cards/*.json`.
  3. Write the **design-rules spec** (`docs/law_card_design_rules.md`): honest-unknown
     rules, enforcement gating, verbatim semantics, disclosure levels — as testable
     statements (these become template acceptance tests).
  4. Spike (throwaway): assemble one law's card JSON by hand from the DB for a real
     law (CO SB205) to validate the §3.2 contract against actual data.
  5. Confirm run-retention mechanics: measure `extractions` table growth per run ×
     K=3 (sizing evidence for D-1).
- **Dependencies:** none. **Files:** docs, `reference/`, `static/`, fixtures.
- **Accessibility:** record the WCAG 2.2 AA target + audit checklist now; contrast-
  check the `--lc-*` palette (light) and define dark-scheme equivalents (the current
  dashboard is dark-themed — the bundle palette is light-only; decide per-page scheme).
- **Testing:** none beyond the spike script.
- **Acceptance:** decision record merged; bundle relocated with no broken repo
  tooling; card-JSON spike produces a complete card for CO SB205 with zero guessed
  fields.
- **Risks:** D-1 stalls on product owner → mitigation: LC-1…LC-3 are D-1-independent
  (single-run MVP); only LC-4 blocks.

### Phase LC-1 — Law-card data model & API foundation (M)

- **Objective:** Non-destructive edit storage + the read-model API; kill G-1.
- **Scope:** Migration, assembler, edit service, JSON endpoints. No UI.
- **Technical tasks:**
  1. Migration: `extraction_field_edits`, `law_card_states`,
     `extractions.effective_payload`.
  2. `field_catalog.py` — registry for all `ObligationPayload` +
     `DefinitionActorPayload` + `ThresholdExceptionPayload` + rights/compliance/
     preemption fields (label, help, widget, vocab source, material-field flag).
  3. `law_card_assembler.py` (+ `LawCardState` cache write/invalidate).
  4. `edit_service.py`: propose → dry-run validate (Pydantic field validation via
     the payload schema; vocab check; `normalize_date`; `check_numeric_grounding`
     against verified spans when the field is numeric; span re-verification when the
     edit touches a span-bearing field) → apply (recompute `effective_payload`,
     write `ReviewAction`, optimistic-lock check) → revert.
  5. `law_card_api.py` endpoints (§3.2), API-key-gated via existing `verify_api_key`
     for `/v1`-style access + session for dashboard fragments.
  6. **Remove** `POST /api/review/{queue_id}/edit`'s in-place mutation — reimplement
     it on `edit_service` so the existing review page immediately inherits
     non-destructive semantics (same form, new engine).
  7. `current_payload` property + switch `sync_extractions.py` / `rollup_matrix.py`
     / `concept_grouping.py` reads to it.
- **Dependencies:** LC-0 decisions D-3…D-6.
- **Files:** `src/db/models.py`, new migration, `src/core/{field_catalog,
  law_card_assembler,edit_service}.py`, `src/api/routes/law_card_api.py`,
  `review_routes.py`, `sync_extractions.py`, `rollup_matrix.py`,
  `concept_grouping.py`.
- **Accessibility:** n/a (API), but validation messages authored here must already be
  plain-language (they surface verbatim in LC-3 UI).
- **Testing:** unit — assembler against the LC-0 fixtures + a synthetic
  law with every gap type; edit lifecycle (propose/validate-fail/apply/revert/
  lock-conflict/orphan); property test: `effective_payload` == base when no applied
  edits; regression: sync/rollup/concepts read edited values; migration up/down.
- **Acceptance:** an edit round-trips through the API with original preserved,
  validation report stored, audit row written; the old destructive path no longer
  exists; full suite green.
- **Risks:** consumers of `payload` missed in the sweep → mitigation: grep-audit
  `\.payload\b` across `src/`, and a temporary assertion in `edit_service` that
  base payload hash never changes post-creation.

### Phase LC-2 — Read-only law-card dashboard (M)

- **Objective:** Every extracted law gets a browsable card. Kill G-6.
- **Scope:** List + card pages, ported design system, no editing.
- **Technical tasks:**
  1. `law_card_routes.py`: `/laws` (search/filter/pagination via `LawCardState`),
     `/laws/{canonical_key}` (Overview | Extractions | Runs-placeholder tabs).
  2. Jinja2 partials + `lc_badges` macros implementing the design-rules spec:
     status chips (ported taxonomy), tier chips, data-gap badges, truncation/repair
     flags, tracker-status line, provenance line (agent, model, run, template
     version — from existing columns + EAR-0-4 stamps when they land).
  3. Evidence rendering: Tier-1/2 spans as highlighted quotes in source context
     (offsets exist); Tier-3/4 as "near match"; unverified as flagged text.
  4. Bill-level panel (read-only, D-7) incl. `_input_truncated` warning.
  5. `lawcard.js` (vanilla): disclosure toggles with `aria-expanded` + focus return.
  6. Nav entry in `layout.html`.
- **Dependencies:** LC-1 (assembler/API).
- **Files:** `templates/laws.html`, `templates/law_card.html`,
  `templates/partials/lc_*.html`, `static/lawcard-tokens.css`, `static/lawcard.js`,
  `src/api/routes/law_card_routes.py`, `layout.html`.
- **Accessibility:** semantic landmarks + heading hierarchy; all disclosures
  keyboard-operable with visible focus; status/severity never color-only (icon or
  text always paired); contrast ≥ 4.5:1 verified for the adapted palette; page works
  at 200% zoom and 320px width.
- **Testing:** unit — route/fragment tests (the `get_extraction_monitor` direct-call
  pattern; this stack has no browser in-sandbox); template render tests against the
  four ported reference fixtures + real CO SB205 data; design-rules spec assertions
  (null field → gap badge present, withdrawn → enforcement suppressed, unverified
  span → never rendered as quote); axe-style static checks where feasible, manual
  a11y checklist for the rest (operator).
- **Acceptance:** all 232 laws browsable; every card renders with zero guessed
  values; design-rules tests green; keyboard-only walkthrough completes.
- **Risks:** assembler N+1 queries on the list page → `LawCardState` rollup is the
  mitigation, verify with query-count test.

### Phase LC-3 — Field-level editing & validation (M/L)

- **Objective:** Non-specialists can correct any clause-level field safely. Kill G-4.
- **Scope:** Edit UI on the card page, wired to `edit_service`. Clause-level only
  (D-7).
- **Technical tasks:**
  1. `lc_field_editor.html`: widget per `field_catalog` (vocab select with "other →
     sends to vocab review", date input, number+unit, textarea; nested
     timeline/enforcement as grouped sub-forms). No raw-JSON editing for cataloged
     fields; an "advanced" JSON escape hatch stays admin-flagged only.
  2. HTMX flows: Check (dry-run) → inline per-field messages; Save → edited chip,
     original preserved behind "view original / revert"; reason field required.
  3. Optimistic locking UX: stale-lock response renders "someone else changed this —
     review their change" with a refresh affordance (G-7).
  4. Editor identity (D-6): reviewer-name session prompt + CSRF token on all
     mutating fragment routes; `editor` recorded on every FieldEdit/ReviewAction.
  5. Edit-state surfaces: card header shows `edited_count`; review queue shows
     "edited" filter; approve action on an edited extraction records that approval
     covers the edited state.
  6. Guardrails from validation machinery: numeric edit contradicting verified spans
     → warning (not block) with the quote shown; date normalize-on-blur; vocab
     unknowns → pass-through + vocab-review enqueue (mirrors EAR-5-1 semantics).
- **Dependencies:** LC-1, LC-2.
- **Files:** partials, `law_card_routes.py`, `edit_service.py` (messages),
  `field_catalog.py` (help text pass), `review.html` (edited filter).
- **Accessibility:** every input labeled + described (`aria-describedby` for help
  and errors); errors announced via `aria-live=polite` region; focus moves to first
  invalid field on failed Check; all flows keyboard-complete; no timed UI.
- **Testing:** unit — every widget type round-trips; validation-message snapshot
  tests (plain-language review by a non-engineer); lock-conflict flow; CSRF
  negative tests; e2e-ish route sequence test (propose→check→apply→revert) per
  extraction type; regression — approving an edited extraction syncs
  `current_payload` with `edited_by_analyst` stamp.
- **Acceptance:** a non-specialist (test: someone outside the project following only
  on-screen guidance) corrects a penalty amount, a date, a modality, and a nested
  enforcement field on real data without touching JSON; originals recoverable;
  audit trail complete with identity.
- **Risks:** field catalog drift vs schemas → mitigation: unit test that every
  schema field has a catalog entry (fails CI when a schema adds a field).

### Phase LC-4 — Phased-run comparison & change visualization (M/L, gated on D-1)

- **Objective:** "What changed between run N and run M for this law?" answerable by
  a non-specialist. Kill G-2's product consequence.
- **Scope:** Retention refactor + comparison service + Runs tab UI.
- **Technical tasks:**
  1. D-1 implementation: full runs stop purging; serving-run scoping on the four
     query sites; `prune_runs(keep=K)`; migration note for operators.
  2. `run_comparison.py`: cross-run extraction matching (type + canonical material
     fields; reuse `_payload_hash` canonicalization + QA-4 similarity for fuzzy
     matches), producing added/removed/changed sets with field-level deltas;
     compares **base payloads** (model vs model) with edits overlaid as a separate
     annotation layer, so "the model changed" and "we edited" are never conflated.
  3. Edit carry-forward job (D-5) hooked into run finalize: hash-match → carry;
     changed → orphan + review item.
  4. Runs tab UI: run picker (labeled with date, model config summary, serving
     badge), summary counts, per-extraction diff rows (`lc_diff_row.html`) in three
     states with plain-language field labels; "only changes" default view.
  5. `LawCardState` per (law, run) — already keyed that way from LC-1.
- **Dependencies:** LC-2 (UI home), D-1 ratified, and coordination with the next
  operator run (retention flips behavior on the *next* full run).
- **Files:** `extractor.py` (purge path), `run_comparison.py`, routes/partials,
  `sync/rollup/concepts` serving-run filters, ops docs.
- **Accessibility:** diff states encoded by icon + text + color (never color-only);
  change summaries readable linearly by screen reader ("Penalty amount changed from
  $10,000 to $20,000"); side-by-side collapses to stacked at narrow widths.
- **Testing:** matching unit tests incl. adversarial cases (split obligation 1→2,
  merged 2→1, re-worded same obligation, type retag); retention regression (two
  synthetic runs coexist; serving filters hold everywhere — query-count and
  row-leak tests); carry-forward matrix (unchanged/changed/removed base); diff
  render tests on fixtures.
- **Acceptance:** two real runs retained side-by-side; CO SB205 diff renders
  added/removed/changed correctly against hand-verified expectations; no consumer
  (dashboard stats, review, sync, concepts) double-counts across runs.
- **Risks:** *highest-risk phase.* Retention flips a core invariant ("all rows are
  current") — mitigation: land behind `multi_run_retention` flag, ship the
  serving-filter sweep with an audit script that diffs pre/post counts on every
  consumer query; matching quality on fuzzy cases → surface match confidence in the
  UI ("possibly the same requirement, reworded") instead of overclaiming.

### Phase LC-5 — Accessibility & non-specialist usability hardening (M)

- **Objective:** WCAG 2.2 AA across the LC surfaces; language a non-specialist can
  act on. (A11y is built-in per phase; this phase is the audit + polish pass.)
- **Scope:** LC pages + the legacy review page where LC components replaced it.
- **Technical tasks:**
  1. Full manual audit against the LC-0 checklist (keyboard, SR walkthrough with
     NVDA/VoiceOver, zoom/reflow, contrast) — operator/RPR assisted; fix list.
  2. Glossary layer: every specialist term (`modality`, `preemption`,
     `safe harbor`, tier letters…) gets a hover/expand definition from
     `field_catalog`; "What am I looking at?" intro panel per tab.
  3. Plain-language pass over every label/help/validation/empty state (external
     read-through, same reviewer standard as LC-3 acceptance).
  4. `prefers-reduced-motion` respected (disclosure animations off);
     `prefers-color-scheme` resolved per LC-0 palette decision.
  5. Error-recovery affordances: undo toast after apply (calls revert), unsaved-edit
     navigation guard.
- **Dependencies:** LC-2/3 shipped (4 optional).
- **Files:** templates/partials, `static/`, `field_catalog.py`.
- **Testing:** audit checklist re-run recorded in docs; template tests asserting
  ARIA contract details (live regions present, describedby wiring); no color-only
  assertions extended repo-wide for LC partials.
- **Acceptance:** audit checklist passes with zero AA blockers; glossary coverage =
  100% of catalog terms flagged specialist.
- **Risks:** dark/light palette conflicts with existing dashboard theme →
  resolved in LC-0, verified here.

### Phase LC-6 — Testing, migration, rollout, monitoring (M)

- **Objective:** Safe path from feature-flag to default-on; operational visibility.
- **Scope:** Backfill, flag removal, monitoring, docs.
- **Technical tasks:**
  1. Backfill script: `LawCardState` for the serving run (all 232 laws); idempotent.
  2. Rollout: flag on in operator environment → analyst bake period on real review
     work → default-on; legacy review edit form officially redirected to LC editor.
  3. Monitoring: edits/day, validation-failure rate by field (a rising rate on one
     field = catalog or model problem), orphaned-edit count after each run,
     card-assembly latency, lock-conflict count — into the existing dashboard
     stats pattern + `run_summary.json` where run-coupled.
  4. Data-integrity sweeps as tests: no extraction with `effective_payload` and
     zero applied edits; no applied edit whose extraction lacks the overlay; edit
     rows always resolvable to a law by `canonical_key`.
  5. Docs: analyst guide (non-specialist voice), operator runbook (retention,
     prune, backfill), `architecture.md` section.
- **Dependencies:** LC-1…LC-3 (MVP rollout) / LC-4,5 for full rollout.
- **Files:** `src/scripts/backfill_law_cards.py`, monitoring hooks, docs.
- **Testing:** backfill idempotency; flag-off renders nothing (no route leakage);
  integrity sweeps wired into CI against test DB.
- **Acceptance:** analyst completes a full real review of ≥3 laws entirely in LC UI;
  monitoring visible; integrity sweeps green for two consecutive runs.
- **Risks:** bake reveals workflow mismatch with the reviewer's actual habits →
  mitigation: bake period is scoped for iteration, flag stays until sign-off.

---

## Part 4a — LC-0d spike findings (2026-07-19, implementation session)

Hand-assembled CO SB205's card JSON from real committed data — `data/fact_laws.csv`
row 48 + 11 `tests/fixtures/gold_standard/co_sb205_*.json` fixtures (real extracted
obligation/definition/threshold_exception/enforcement payloads) — since no live
Postgres is reachable in this sandbox. 12 extraction entries assembled (6
obligation, 4 definition, 2 threshold_exception) across all 11 CO SB205 gold
fixtures. Confirms the §3.2 card-JSON contract is assemblable from real data
shapes; three concrete findings feed directly into LC-1c:

1. **`status_id` is blank for real, in-force laws** — confirmed via `csv.DictReader`
   on the actual row (an initial eyeballed `grep` misread the row because
   `key_requirements_raw` contains embedded commas inside quotes — a reminder that
   `law_card_assembler.py` must never hand-parse this CSV; it reads the DB's typed
   columns). CO SB205's `status_id` is empty, `effective_date` is populated. This
   is exactly the gap the bundle's own `isEnacted()` heuristic
   (`LawCard.jsx:76-84`) was built for: "the snapshot leaves status blank for most
   in-force laws... treat those as enacted [when they carry] an effective date."
   **Action for LC-1c:** port this inference (blank/missing status + effective
   date present → treat as enacted) rather than trusting a raw status column
   alone — directly informs how `DocumentVersion.temporal_status` should be
   read for the card's status chip (Design Rule 5).
2. **`iapp_scope`/`iapp_section` are separate, populated fields** (`"D"` /
   `"LAWS SIGNED"` for this law) not previously highlighted in §3.2's law object
   sketch — confirms these belong in the card's tracker-status surface alongside
   `orrick_source` (already in `_build_context()`), not folded into a generic
   metadata blob.
3. **The clause-level "obligation with an embedded enforcement sub-object" shape
   (`co_sb205_sec7_enforcement.json`) is structurally distinct from the
   bill-level `enforcement_agent`'s payload shape.** The gold fixtures only
   cover the former. This confirms §3.4's design choice — clause-level and
   bill-level enforcement are genuinely separate data paths in the card, not one
   filtered over the other — and sharpens why EAR-2-2 (clause/bill enforcement
   separation) matters: a card naively merging "any extraction with an
   `enforcement` field" would conflate a single clause's local enforcement
   mention with the bill-level agent's authoritative rollup.

No live-DB fields (confidence tier/score, review status, run id, evidence_spans)
could be validated this way — those remain to be confirmed against real
`Extraction` rows by the operator once LC-1c's assembler runs against Postgres.
Spike script and output were throwaway (`/tmp/lc0d_spike.py`,
`/tmp/lc0d_co_sb205_card.json`) per the plan; not committed.

## Part 5 — Final Recommendation

**Implementation sequence:** LC-0 → LC-1 → LC-2 → LC-3 (**MVP**) → LC-6-lite
(rollout of MVP) → LC-4 → LC-5 → LC-6 (full). LC-1 is the keystone: it fixes the
destructive-edit defect (G-1) that damages data *today*, before any new UI exists —
even if the card UI slipped, LC-1 alone is worth shipping.

**Critical architectural decisions (resolve in LC-0, owners named in D-table):**
1. **D-1 run retention** — the only decision that blocks a whole phase (LC-4).
   Recommended: retain last 3 runs, serving-run scoping everywhere.
2. **D-3 immutable base + edit overlay** — the data-integrity spine of the feature.
3. **D-2 Jinja2/HTMX port, no React island** — the bundle contributes its design
   contract, tokens, and fixtures; its JSX does not run as shipped and would drag in
   a build stack for one page.
4. **D-4 edits never fake confidence** — human review is a separate axis
   (`human_review_state`), applied as precedence at sync, mirroring
   `enforcement_normalizer` and the SFH-3a direction.

**Highest-risk assumptions:**
- That all downstream consumers of `Extraction.payload` are found and switched to
  `current_payload` (missed one = edits silently invisible somewhere). Mitigated by
  grep-audit + integrity sweeps, but it is the likeliest silent failure.
- That cross-run extraction matching is good enough to be honest (split/merged/
  reworded obligations). Mitigated by surfacing match confidence rather than
  overclaiming, but expect iteration.
- That multi-run retention doesn't break an unexamined "all rows are current"
  assumption in analytics/exports. Mitigated by the flag + pre/post count audit.
- That a session-name identity (D-6 interim) is acceptable to the product owner for
  legally-audited edits until 6a lands.

**MVP boundary:** LC-0 through LC-3 + LC-6-lite — every law has a card; complete
extraction visible with verified evidence; any clause-level field editable with
validation, identity, audit, and revert; serving run only. **Explicitly in MVP** the
G-1 fix (non-destructive edits) since it repairs an active defect.

**Deferred until after MVP:**
- Run comparison + retention (LC-4) — needs D-1 and an operator run under retention.
- Bill-level payload editing (D-7) — after EA5-1/EAR-2-3 give those fields spans.
- Triage/priority engine port (`priority.js`), role-based obligation filtering,
  CoverageCard analog, per-jurisdiction chunk exports — product features of the
  source project, not required by the stated goal.
- Concept-layer cards (ComplianceConcept rollups) — valuable, but a second
  aggregation level; keep the MVP at law level.
- Full authn/z (Run-1 6a) — LC ships the interim identity + CSRF; real accounts are
  an existing, separate workstream.
