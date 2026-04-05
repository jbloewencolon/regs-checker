# Regs Checker — Tasks

## Active Tasks

- **Phase 6 — Full reset + re-seed + ingest + triage + extract + sync (READY TO EXECUTE)**
  - Pre-flight done: smart routing, title disambiguation, regulatory_category, 4 URL swaps, MN omnibus trim.
  - User runs: `python scripts/reset_pipeline.py`, then dashboard Steps 1→2→3→5 (`--clear`).
  - 16 laws with still-quarantined source text will be skipped on re-ingest (see `output/law_texts_quarantine/NEEDED_SOURCES.md`).

- **Obtain correct source text for 16 quarantined laws** — See `output/law_texts_quarantine/NEEDED_SOURCES.md`. Place correct bill text in `output/law_texts/<canonical_law_id>.txt`.

- **TN quarantine files contain TX bill content** — TX SB 1188, SB 2373, SB 815, SB 20, SB 1621 may be legitimate TX AI laws. Decide whether to add as new TX entries in `fact_laws.csv`.

- **Merge feature branch to main** — All work on `claude/setup-project-scaffolding-9ApZR`. Needs review and merge after Phase 6 validation.

---

## Quality Improvement Backlog

Analysis of the extracted dataset revealed several structural issues. Organized by phase below.
Source: data analysis session 2026-04-05.

### Phase 1 — Code Fixes (no extraction re-run needed, apply before or after current run)

#### BUG-4: Unicode normalization missing in evidence span verification (P0)
**Root cause:** `_verify_evidence_spans` (`src/agents/base.py:622-678`) normalizes whitespace but not
Unicode typography. Source PDFs and HTML contain non-breaking hyphens (U+2011), en-dashes (U+2013),
em-dashes (U+2014), smart quotes (U+2018/2019/201C/201D), and non-breaking spaces (U+00A0). The LLM
outputs ASCII equivalents when quoting. The string match fails even though the span is substantively correct.

**Evidence:** ~1,783 of 2,639 zero-evidence Tier D extractions have their first 40 chars found in the
source passage — the span text is real, the match is failing on character-level differences. The other
856 (32%) are genuine mismatches (hallucinations or wrong-passage citations).

**Fix:** Add `_normalize_unicode()` helper to `BaseExtractionAgent`. Call it on both passage and span text
inside `_verify_evidence_spans`, chained before the existing `_normalize_whitespace` call.

Replacements needed:
- U+2011 (non-breaking hyphen), U+2013 (en-dash), U+2014 (em-dash) → `-`
- U+2018, U+2019 (smart single quotes) → `'`
- U+201C, U+201D (smart double quotes) → `"`
- U+00A0 (non-breaking space) → ` `

**File:** `src/agents/base.py` (~15-line change in `_normalize_whitespace` or new helper)
**Impact:** ~1,783 extractions move from zero-evidence to fully verified. Many will tier-promote.
**Risk:** Low. Additive normalization, no schema changes.
**Validation:** Run `pytest tests/unit/` before and after. Evidence grounding rate should increase
significantly across the dataset after re-scoring (requires re-running confidence scores or fresh extraction).

#### IMPROVEMENT-1: Tighten ambiguity agent routing signals (P1) — SUPERSEDED
**Superseded by RESTRUCTURE-1 below.** If the ambiguity agent is retired and its findings
embedded as `interpretation_risks` annotations on obligation/rights extractions, signal-tightening
becomes irrelevant. Skip this unless RESTRUCTURE-1 is explicitly deferred.

#### IMPROVEMENT-2: Expand triage keyword list (P1)
**Root cause:** `_BASE_AI_KEYWORDS` in `src/agents/section_triage.py:77-126` has ~50 entries.
Confirmed gaps: `profiling`, `companion chatbot`, `AI companion`, `score-based`, `automated risk assessment`,
`social scoring`, `price optimization`, `algorithmic pricing`, `surveillance pricing`, `digital replica`,
`synthetic performer`. The keyword method makes 48% of all triage decisions, so misses here are silent
false negatives — passages classified "not_relevant" with 0.95 confidence, no LLM review.

**Fix (two-part):**

Part A — Add high-confidence AI terms to `_BASE_AI_KEYWORDS` (auto-relevant):
```
"automated profiling", "algorithmic profiling", "profiling system",
"companion chatbot", "ai companion", "social scoring",
"automated risk assessment", "score-based decision",
"digital replica", "synthetic performer",
"price optimization", "surveillance pricing",
```

Part B — Add a `_ADJACENT_AI_KEYWORDS` tier-2 set that routes to LLM triage (not auto-relevant).
These terms are AI-adjacent but context-dependent: `"data broker"`, `"utilization review"`,
`"dynamic pricing"`, `"electronic surveillance"`. Passages containing only tier-2 terms get
`method="llm_generic"` instead of `method="keyword"`.

**File:** `src/agents/section_triage.py:77-132` (keyword list only)
**Impact:** Catches passages about AI companion laws (CA, NY), algorithmic pricing (NY), healthcare AI.
**Risk:** Low for Part A (clear AI terms). Part B requires adding conditional routing logic — review with
care since `section_triage.py` is moderately fragile.
**Validation:** Compare triage "not_relevant" counts before/after. Check that no new `passthrough` errors appear.

---

### Phase 1B — Pipeline Restructure: Retire Ambiguity Agent, Add interpretation_risks

**Goal:** Eliminate the standalone ambiguity agent. Instead, embed ambiguity findings as
`interpretation_risks` annotations directly on the obligation and rights_protection payloads.
The obligation agent already reads each passage closely enough to identify these issues during its
primary extraction pass. Zero additional LLM calls. Zero additional review queue rows. The findings
belong as structured metadata on the obligation or right they qualify, not as orphaned extractions.

**Why this is better than signal-tightening (IMPROVEMENT-1):**
Tightening `_AMBIGUITY_SIGNALS` reduces volume but preserves the structural problem: ambiguity
findings exist as standalone rows with no link to the obligation they affect. An `interpretation_risks`
annotation on the obligation row gives the reviewer the ambiguity context exactly where they need it,
while cutting one full LLM call per passage and eliminating the entire ambiguity review queue.

**Sequencing constraint:** Do this AFTER the current extraction run completes and syncs. Changing
agent schemas mid-run creates mixed-format payloads in the DB. The restructure applies cleanly to
a fresh run.

**Escalation required before starting:** This touches `extractor.py`, `db/models.py` (enum handling),
and `dashboard.py` — all forbidden-list files. Confirm with user before modifying them.

#### RESTRUCTURE-1a: Add InterpretationRisk schema and update ObligationPayload + RightsProtectionPayload

**Schema design** (`src/schemas/extraction.py`):
```python
class InterpretationRisk(BaseModel):
    risk_type: Literal[
        "vague_term",
        "undefined_reference",
        "conflicting_provision",
        "scope_ambiguity",
        "temporal_ambiguity",
        "conditional_ambiguity",
    ]
    term: str = Field(description="The specific term or phrase that is ambiguous")
    concern: str = Field(description="Why this creates compliance uncertainty (1-2 sentences)")
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    evidence_spans: list[EvidenceSpan] = []
```

Add to `ObligationPayload` and `RightsProtectionPayload`:
```python
interpretation_risks: list[InterpretationRisk] | None = Field(
    default=None,
    description="Ambiguous terms or conflicting provisions that affect this extraction"
)
```

**No DB migration needed.** The `payload` column is JSONB — new fields appear automatically in
stored JSON. Existing rows simply won't have the key (treated as `None` by Pydantic).

**Files:** `src/schemas/extraction.py` only. Safe to edit.

#### RESTRUCTURE-1b: Update obligation and rights_protection system prompts

Add a section to both prompts instructing the model to populate `interpretation_risks` when it
encounters vague terms, undefined references, or conflicting provisions while doing its primary
extraction. The model is already reading the passage for the primary extraction — this is a
zero-cost annotation pass.

Key instruction: "If you notice a term that is undefined, vague, or creates a conflicting
obligation, populate `interpretation_risks` with one entry per issue. If none, omit the field."

**Files:** `prompts/obligation.yml`, `prompts/rights_protection.yml` (or inline in agent `.py` if
no YAML prompt exists — check which format each uses).

#### RESTRUCTURE-1c: Remove ambiguity from the extraction pipeline

**Files (all require escalation):**
- `src/ingestion/extractor.py`: Remove `AmbiguityAgent` import (line ~52), remove from
  `AGENT_EXTRACTION_TYPES` dict (line ~311), remove from agent instantiation dict (line ~561),
  remove `_AMBIGUITY_SIGNALS` entry from `_SIGNAL_MAP` (line ~801)
- `src/db/models.py`: **Do NOT remove `ExtractionType.ambiguity`** — existing rows in production
  use this enum value. The enum must stay for backward compat. Simply stop routing new passages
  to it. Add a comment marking it deprecated.

#### RESTRUCTURE-1d: Update downstream systems

These are lighter edits but necessary for a clean codebase:
- `src/core/summary_generator.py`: Keep `_summarize_ambiguity` for backward compat with existing
  rows in the review queue. No deletion needed — just leave it in the dispatch dict.
- `src/core/payload_adapter.py`: Keep `_adapt_ambiguity` for the same reason. Mark deprecated.
- `src/agents/dependency_builder.py:115-116`: Keep the ambiguity branch for existing rows.
- `src/scripts/sync_monitor.py:45`: Remove or convert `AMBIGUITY_ALERT_THRESHOLD` — no new
  ambiguity rows will be created, so the alert will always pass trivially. Consider replacing with
  an alert on `interpretation_risks` population rate instead.
- `src/evaluation/harness.py:36,117`: Remove `AmbiguityAgent` import and dict entry.
- `src/scripts/manual_extraction.py:57,83,138`: Remove ambiguity from manual extraction CLI.

#### RESTRUCTURE-1e: Archive the ambiguity agent

Move `src/agents/ambiguity.py` → `src/ingestion/_archived/ambiguity.py` (following existing
archive convention). Do not delete — keeps the extraction schema and prompt as reference.

#### RESTRUCTURE-1f: Update review queue display

The review queue currently renders ambiguity rows standalone. After this change, `interpretation_risks`
will appear as a collapsible annotation within obligation/rights rows, not as separate queue entries.

**File:** `src/api/routes/dashboard.py` (forbidden — escalate), `templates/dashboard.html`

Display pattern: within each obligation review row, if `interpretation_risks` is non-empty, show
a small "⚠ Interpretation risks" badge that expands to show the risk list inline.

#### Validation

1. Run `pytest tests/unit/` — should pass without ambiguity-related failures
2. Run a single-law test extraction and verify:
   - No new `extraction_type='ambiguity'` rows created
   - Obligation/rights payloads contain `interpretation_risks` where expected
   - Review queue renders interpretation risks inline on obligation rows
3. Check that existing `ambiguity` rows in the review queue still render (backward compat)

#### Definition of done

- `interpretation_risks` populated on at least 10% of obligation extractions from a test law with
  known ambiguous provisions (e.g., a law with "reasonable measures" or undefined scope terms)
- Zero new `ambiguity`-type rows after the change
- Existing ambiguity rows in the DB still display correctly in the review queue
- IMPROVEMENT-1 removed from active backlog (superseded)

---

### Phase 2 — Analysis Tasks (require human judgment or lawyer review)

#### ANALYSIS-1: Build 50–100 row ground-truth eval set (P0)
**Why:** Every downstream quality claim is unverified against human ground truth. The model has never
been calibrated. Before tightening prompts or comparing models, need a labeled baseline.

**Steps:**
1. Sample ~100 extractions: 25 Tier A, 25 Tier B, 25 Tier C, 25 Tier D (stratified across extraction types)
2. Have a lawyer or senior policy analyst manually verify each: correct? evidence accurate? type correct?
3. Record ground truth in `data/eval_set.csv` with columns: extraction_id, extraction_type, human_verdict (correct/incorrect/partial), notes
4. Use as fixture for prompt evaluation and model comparison

**Deliverable:** `data/eval_set.csv` + `tests/fixtures/eval_set/` for regression testing

#### ANALYSIS-2: Investigate 856 genuinely non-matching spans
**Why:** After the Unicode fix (BUG-4), ~856 spans will still fail verification. Understanding their
failure patterns determines whether the fix is prompts, passage context, or model upgrade.

**Steps:**
1. After BUG-4 is deployed and extraction re-run: query `extractions` where `evidence_grounding = 0` and span count > 0
2. Sample 20 rows, pull full passage + span text for each
3. Categorize: adjacent-passage citation? paraphrase? fabrication? wrong law?
4. Document findings in `agents/analysis/span-failures.md`

#### ANALYSIS-3: Gap analysis on keyword-triaged "not_relevant" passages
**Why:** Negative keyword decisions have no reasoning stored — if the keyword list misses a term,
the passage is silently discarded with high confidence.

**Steps:**
1. Query `section_triage_results` where `method = 'keyword'` and `decision = 'not_relevant'`
2. Export passage texts to CSV
3. Scan for AI-adjacent terms not in `_BASE_AI_KEYWORDS`: profiling, algorithmic pricing, companion AI, etc.
4. Add confirmed gaps to `_BASE_AI_KEYWORDS` (feeds IMPROVEMENT-2)

#### ANALYSIS-4: Investigate Orrick alignment Unicode issue
**Why:** Orrick alignment (30% weight in confidence) uses string tokenization in `orrick_validation.py`.
The same Unicode dash/quote variants that break evidence spans may also depress Orrick similarity scores.

**Steps:**
1. Read `src/core/orrick_validation.py` — check tokenization method
2. Test: does `"privacy‑protective"` (U+2011) match `"privacy-protective"` (ASCII)?
3. If not: apply same normalization fix before Orrick tokenization

---

### Phase 3 — Score Quality Improvements (after eval set exists)

#### IMPROVEMENT-3: Span length penalty in evidence grounding (P2)
**Problem:** Evidence grounding is binary. A 10-word verbatim quote scores the same as a 500-word
span that is essentially the entire passage copy-pasted.

**Fix:** In `src/core/confidence.py`, add a span length penalty to the evidence grounding component:
- If any span > 50% of passage character length: penalize grounding score by 0.2
- If avg span length > 30% of passage: penalize by 0.1
- Flag in review queue as "broad span" warning

**Files:** `src/core/confidence.py`, `templates/*.html` (review queue flag display)

#### IMPROVEMENT-4: Continuous sub-signals in confidence scoring (P2)
**Problem:** Scores cluster tightly within tiers (stddev 0.02–0.05 within tier) because the formula
weights binary features. Difficult to prioritize within a tier for human review.

**Proposed additions:**
- Evidence span specificity: does the span contain the extracted subject/action/term?
- Field coverage ratio: populated fields / total schema fields (already exists but may be binary)
- Section reference quality: `§ 6-1-1702(3)(a)` scores higher than `§ 2`

**Files:** `src/core/confidence.py`
**Constraint:** Don't change tier thresholds — only add differentiation within tiers.

---

### Phase 4 — Model & Prompt Improvements (requires eval set from Phase 2)

#### IMPROVEMENT-5: Model comparison on eval set (P3)
**Steps:** Run eval set through a second model (e.g., different Qwen variant or smaller GPT), compare
precision/recall on each extraction type against BUG-4-fixed baseline.

#### IMPROVEMENT-6: Few-shot examples in prompts (P3)
**Steps:** Take 3–5 verified Tier A extractions per agent type from eval set. Add as few-shot examples
to the corresponding YAML prompt. Re-run on eval set to measure improvement.
**Files:** `prompts/*.yml` (one per agent)

---

## Blocked Tasks
- **Cross-validation scoring** — Needs extraction to complete.
- **Phase 3 + 4 improvements** — Require eval set (ANALYSIS-1) to validate changes.

## Questions / Clarifications Needed
- Target extraction count? Previous run: ~28k from ~9k passages.
- Sync to Policy Navigator: all types or approved-only?
- Is MinIO/S3 actually needed? Pipeline works without it.
- Orrick gate: confirmed correct for IAPP-only laws (accept Tier D)? (Current position: yes, BUG-1 ACCEPTED)
- Who will perform lawyer review for eval set (ANALYSIS-1)?

## Next Tasks (after extraction completes)
- **Sync local → Supabase** — Dashboard Step 5. Supabase truncated 2026-04-04.
- **Sync Regs Checker → Policy Navigator** — Dashboard Step 6.
- **Run rollup matrix** — `python -m src.scripts.rollup_matrix`
- **Review test coverage** — 403 pass, 13 fail. 7 DB-required, 5 stale mocks, 1 stale ref.
- **Apply BUG-4 fix** (Unicode normalization) — Can be done before extraction re-run or after.

## Bugs / Issues

### BUG-1: Laws missing Orrick data → auto Tier D — ACCEPTED
Only 2 Orrick laws + 53 IAPP active bills lack Orrick data. The 53 IAPP bills are pending legislation —
the Orrick gate legitimately flags them. Accept Tier D for these. Do NOT soften the gate.
Data shows only 11% of Tier D rows are Orrick-gated; 89% have Orrick data but genuinely score poorly.

### BUG-2: Failed extraction retry — FIXED
### BUG-3: Supabase sync "not configured" — FIXED
### BUG-4: Unicode normalization in evidence spans — SEE Phase 1 above

## Recently Completed

### Triage Switched to Qwen2.5-3B-Instruct — 2026-04-04
- Root cause: GPT-OSS 20B is a reasoning model — burns all tokens on `<think>` blocks even for simple binary classification
- `config.py`: Added `local_triage_model = "qwen2.5-3b-instruct"` config key (overridable via `REGS_LOCAL_TRIAGE_MODEL` env var)
- `section_triage.py`: LLM call now uses `model_override=settings.local_triage_model` (removed `reasoning_effort="low"` — not needed for non-reasoning model)
- **Files modified**: `src/core/config.py`, `src/agents/section_triage.py`

### Passage Explosion Fixed (14,968 → ~1,300 passages) — 2026-04-04
- Removed sub-section markers `(a)`, `(b)`, `(1)` from section regex in `parser.py`
- `_split_on_paragraphs()` rewritten with chunk merging (TARGET=3k, MAX=15k chars)
- `_segment_text()` also merges small adjacent section matches (TARGET=3k chars)
- **File modified**: `src/ingestion/parser.py`

### Triage Error Visibility in Dashboard — 2026-04-04
- Added `GET /dashboard/api/triage-results` endpoint showing decision/method breakdown
- LLM failures (method=passthrough) shown first with red rows + count badge
- **Files modified**: `src/api/routes/dashboard.py`, `templates/dashboard.html`

### S3/MinIO Bypass for Local Ingestion — 2026-04-04
- `local_ingest.py` now stores `local://` reference instead of uploading to MinIO
- **Files modified**: `src/ingestion/local_ingest.py`, `src/ingestion/parser.py`

### Law Tracker Rewired to data/fact_laws.csv (241 laws) — 2026-04-04
- Replaced stale `static/ai_law_tracker.csv` (191 rows) with `data/fact_laws.csv` (241 laws)
- **Files modified**: `src/api/routes/tracker_routes.py`, `src/api/routes/_dashboard_helpers.py`, `templates/dashboard.html`

### Pipeline Reset Script — 2026-04-04
- `scripts/reset_pipeline.py`: FK-safe reset using savepoints
- **File modified**: `scripts/reset_pipeline.py`

### LLM Limits Maxed for GPT-OSS 20B (128k context) — 2026-04-04
- `config.py`: context window 32k→128k, extraction max_tokens 50k→65k
- **Files modified**: `src/core/config.py`, `src/core/llm_provider.py`, `src/core/bill_context.py`, `src/ingestion/parser.py`, `src/agents/section_triage.py`

### Bug Sweep (4 fixes) — 2026-04-04
- `local_ingest.py`, `confidence.py`, `dashboard.py`, `fact_laws.csv`
- Created `scripts/reset_pipeline.py`

### Data Alignment Complete — 2026-04-04
- CSV deduplicated: 244→241 rows
- 187 Orrick titles corrected, 87 bill numbers recovered
- `iapp_scope` and `iapp_section` columns added
