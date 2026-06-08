# Normalization Vocabulary Ratification Plan

**Governing process** for all vocabulary normalization work.
Referenced by: Engineering Guide "Moving Past B0", `run1_unified_plan.md` Phase 3.

---

## Stages

| Stage | Vocabulary | Status | Gates |
|---|---|---|---|
| **V0** | Inventory scripts — extract actor/dimension terms from Orrick (`static/ai_law_tracker.csv`), IAPP (`static/iapp_law_tracker.csv`), extraction DB, bill-level | ⏳ | — |
| **V1** | **Actors** — 13 canonical codes, fork decisions, alias table, crosswalk | ✅ artifacts built | VC ratification |
| **V2** | Covered systems + law domains — ratify together | 🔒 after V1 | — |
| **V3** | Obligation families + rights — ratify together | 🔒 after V2 | — |
| **V4** | Enforcement + legal context (legal_context.py is Phase 2d) | 🔒 after V3 | — |
| **V5** | Compliance-concept schema | ⏳ deferred | After V1–V4 |
| **V6** | Re-harvest / recompute — full re-ingestion across all 232 laws, pinned to locked vocabulary versions | 🔒 after V1–V4 | — |

---

## Six-file artifact set per vocabulary

Each vocabulary (V1–V4) produces six committed files under `data/lookups/`:

| # | File | Description |
|---|---|---|
| 1 | `{vocab}_aliases.csv` | Raw inventory: one row per raw term, with source, count, example_law, surrounding_phrase, prompt_version, proposed_tier1_code, ambiguity_flag |
| 2 | `{vocab}_canonical_codes.csv` | Canonical code list: code, definition, duty/liability note, Orrick term(s), IAPP term(s) |
| 3 | `{vocab}_mapping_examples.csv` | Two-tier mapping: raw_term → tier1_code + tier2_label |
| 4 | `{vocab}_fork_decisions.md` | Fork decision log: each open question with decision, rationale, status |
| 5 | `{vocab}_unresolved_terms.csv` | Terms that couldn't be confidently mapped, with provisional routing |
| 6 | `{vocab}_crosswalk.csv` | Crosswalk: canonical \| Orrick term \| IAPP term \| raw_values — the comparison key the trust check consumes |

V1 (actors) files are at: `actor_aliases.csv`, `actor_canonical_codes.csv`, `actor_mapping_examples.csv`, `actor_fork_decisions.md`, `actor_unresolved_terms.csv`, `actor_crosswalk.csv`.

---

## Done-criteria per vocabulary

1. Alias file covers all sources (extraction + Orrick + IAPP), cleaned and prompt-pinned.
2. Canonical codes ratified by VC; each code maps to ≥1 Orrick or IAPP category.
3. All open forks have a recorded decision in the fork decisions log.
4. Mapping and unresolved-terms files committed; unresolved-term routing rule live.
5. Crosswalk exists and is the documented input to the trust check.

---

## Guardrails

- **Clean before mapping** (B1.5): fix non-actor/garbled values at the parse layer first.
- **Pin to prompt version**: inventory carries `_prompt_hash`/`_template_version`; never pool across versions.
- **Validate tracker coverage**: every Tier-1 code must map to ≥1 Orrick or IAPP category.
- **Prioritize by coverage**: top ~44 raw values ≈ 80% of volume; don't spend equal effort on the long tail.
- **Sector is a standalone dimension**: sector roles (employer, insurer, hospital, platform) map to `deployer` Tier-1 + the law's separate sector dimension. Do **not** create sector actor codes.

---

## Change-control policy

After a vocabulary is locked, changes require:
1. A new entry in the fork decisions log with the change rationale.
2. VC approval before the alias/mapping files are updated.
3. `_prompt_hash`/`_template_version` bump if the change affects extraction prompts.
4. Re-harvest scheduled (V6) if locked codes change materially.

---

## What gates the re-ingestion (V6)

Do **not** re-ingest before V1–V4 are ratified. The full re-ingestion:
- Applies all locked canonical codes
- Runs the applicability agent (Phase 1a)
- Picks up the 94 missing laws (Phase 1d)
- Regenerates extractions against improved prompts
- Records the run in `extraction_runs` with `_prompt_hash`/`_template_version`
