# Regs Checker — Tasks

## Active Tasks

- **Phase 6 — Full reset + re-seed + ingest + triage + extract + sync (READY TO EXECUTE)**
  - Pre-flight done: smart routing, title disambiguation, regulatory_category, 4 URL swaps, MN omnibus trim, ambiguity agent retired, Unicode fix, keyword expansion, confidence improvements.
  - User runs: `python scripts/reset_pipeline.py`, then dashboard Steps 1→2→3→4.5→5 (`--clear`).
  - 16 laws with still-quarantined source text will be skipped on re-ingest (see `output/law_texts_quarantine/NEEDED_SOURCES.md`).
  - Step 3 uses **6 agents** (ambiguity retired — findings embedded as `interpretation_risks` on obligation/rights payloads)

- **Obtain correct source text for 16 quarantined laws** — See `output/law_texts_quarantine/NEEDED_SOURCES.md`. Place correct bill text in `output/law_texts/<canonical_law_id>.txt`.

- **TN quarantine files contain TX bill content** — TX SB 1188, SB 2373, SB 815, SB 20, SB 1621 may be legitimate TX AI laws. Decide whether to add as new TX entries in `fact_laws.csv`.

- **Merge feature branch to main** — All work on `claude/onboard-government-project-3bq7i`. Needs review and merge after Phase 6 validation.

---

## Quality Improvement Backlog

### Phase 1 — DONE (2026-04-05)

- ~~BUG-4: Unicode normalization in evidence span verification~~ — Fixed. `_normalize_unicode()` + `_normalize_text()` added to `BaseExtractionAgent`. 27 tests.
- ~~IMPROVEMENT-1: Tighten ambiguity agent routing signals~~ — Superseded by Phase 1B (agent retired).
- ~~IMPROVEMENT-2: Expand triage keyword list~~ — Done. `_BASE_AI_KEYWORDS` expanded from ~50 to ~65 entries. `_ADJACENT_AI_KEYWORDS` documented constant added.

---

### Phase 1B — Pipeline Restructure: Retire Ambiguity Agent — DONE (2026-04-05)

**Goal:** Retire the standalone ambiguity agent. Embed ambiguity findings as `interpretation_risks`
annotations directly on obligation and rights_protection payloads. Zero additional LLM calls, zero
additional review queue rows, findings attached to the obligation they affect.

#### RESTRUCTURE-1a: InterpretationRisk schema + ObligationPayload + RightsProtectionPayload — DONE
#### RESTRUCTURE-1b: Update obligation and rights_protection prompts — DONE
#### RESTRUCTURE-1c: Remove ambiguity from extraction pipeline — DONE
#### RESTRUCTURE-1d: Update downstream systems — DONE
#### RESTRUCTURE-1e: Archive ambiguity agent — DONE (`src/agents/ambiguity.py` → `src/ingestion/_archived/`)
#### RESTRUCTURE-1f: Dashboard inline display — `templates/dashboard.html` (deferred — lower risk, separate commit)

**Definition of done:** No new `ambiguity`-type rows after extraction. `interpretation_risks` populated
on obligation/rights rows where relevant. Existing `ambiguity` rows in DB still display. Tests pass. ✓

---

### Phase 2 — Analysis Tasks (human judgment required) — DONE where automatable (2026-04-05)

#### ANALYSIS-1: Build 50–100 row ground-truth eval set
Sample ~100 extractions across tiers/types, have a lawyer verify each.
Record in `data/eval_set.csv`. Gates Phase 3 + 4.

#### ANALYSIS-2: Investigate 856 genuinely non-matching spans
After Unicode fix deployed and extraction re-run: query zero-evidence rows with spans. Sample 20,
categorize failure pattern (adjacent passage? paraphrase? fabrication?).

#### ANALYSIS-3: Gap analysis on keyword-triaged "not_relevant" passages
Query `section_triage_results` where `method='keyword'` and `decision='not_relevant'`. Scan for
AI-adjacent terms not in `_BASE_AI_KEYWORDS`. Feed confirmed gaps to IMPROVEMENT-2 follow-up.

#### ANALYSIS-4: Check Orrick alignment for same Unicode issue — DONE
Confirmed `re.findall(r"[a-z0-9]+", text.lower())` in `orrick_validation.py` is immune to Unicode
typography variants. No fix needed.

---

### Phase 3 — Score Quality — DONE (2026-04-05)

#### IMPROVEMENT-3: Span length penalty in evidence grounding — DONE
Penalizes verified spans >50% of passage length in `src/core/confidence.py`.
- >50%: 20% penalty on evidence_score (×0.80); `broad_spans=True` in breakdown
- >75%: 40% penalty on evidence_score (×0.60); `broad_spans=True` in breakdown
- Only verified spans count; unverified spans and absent `passage_text` skip penalty gracefully

#### IMPROVEMENT-4: Section reference quality sub-signal — DONE
`_score_section_reference()` scores specificity of `section_reference` field (0.0–1.0):
- 1.0: § + subsection detail; 0.6: § or numeric citation; 0.3: generic label; 0.2: unrecognized; 0.0: absent
- Blended into completeness at 20% weight. `section_ref_quality` in `ConfidenceBreakdown`.
- 23 tests in `tests/unit/test_confidence_improvements.py`

---

### Phase 4 — Model & Prompt Improvements (requires eval set)

#### IMPROVEMENT-5: Model comparison on eval set
#### IMPROVEMENT-6: Few-shot examples in prompts — `prompts/*.yml`

---

## Blocked Tasks
- **Cross-validation scoring** — Needs extraction to complete.
- **Phase 4** — Requires eval set (ANALYSIS-1).

## Questions / Clarifications Needed
- Sync to Policy Navigator: all types or approved-only?
- Is MinIO/S3 actually needed? Pipeline works without it.
- Who performs lawyer review for eval set (ANALYSIS-1)?

## Next Tasks (after extraction completes)

- **Sync local → Supabase** — Dashboard Step 5.
- **Sync Regs Checker → Policy Navigator** — Dashboard Step 6.
- **Run rollup matrix** — `python -m src.scripts.rollup_matrix`
- **Review test coverage** — 448 pass, 9 fail (pre-existing). 4 stale import files.
- **Dashboard: inline interpretation_risks display** — RESTRUCTURE-1f (deferred from Phase 1B).

## Bugs / Issues

### BUG-1: Laws missing Orrick data → auto Tier D — ACCEPTED
Only 2 Orrick laws + 53 IAPP active bills lack Orrick data. The 53 IAPP bills are pending legislation — the Orrick gate legitimately flags them. Accept Tier D for these.

### BUG-2: Failed extraction retry — FIXED
### BUG-3: Supabase sync "not configured" — FIXED
### BUG-4: Unicode normalization in evidence spans — FIXED (Phase 1, 2026-04-05)
