# data/lookups/ — Controlled-vocabulary mapping artifacts

Single home for every **agent-value → controlled-code** map consumed by the
rollup normalization loader (`src/scripts/normalization/`, planned) and the
Level-1 crosswalk. This directory is the unification point called for by:

- `extraction_run_corrections_eng.md` **C-7** (agent→extraction_type map)
- `code_update_strategy_eng.md` §3.3 (coupling #2 — one loader, not two)
- `vocab_harvest_spec_eng.md` **D-2** (vocab maps) + Track 1.C
- `run1_unified_plan.md` Phases 0, 3, 4, 7

**Rule:** no normalization stage may hard-code an agent/type/vocab relationship.
Every map lives here and is read through the one loader.

## Files

| File | Status | Source | Driver |
|---|---|---|---|
| `agent_to_extraction_type.json` | **committed** | mirrors `AGENT_EXTRACTION_TYPES` in `extractor.py` | C-7 |
| `candidates/subject_to_actor_code_candidates.csv` | **candidate — NOT ratified** | harvest of `obligation.subject_normalized` | D-1/D-2 |
| `candidates/modality_to_strength_candidates.csv` | **candidate — NOT ratified** | harvest of `obligation.modality` | D-1/D-2 |
| `subject_to_actor_code.json` | _pending VC ratification_ | from candidates above | D-2 |
| `modality_to_strength.json` | _pending — fast-lane_ | from candidates above | D-2 |

## Ratification rules (from the harvest spec)

- **Do not auto-create new codes from extracted data.** Auto-mapped rows are
  committee-ratified; everything else stays `REVIEW` until approved.
- Candidate CSVs in `candidates/` are the *input* to the VC workflow, not the
  final lookup. Ratified maps land here as `.json`.
- **Reproducibility pinning:** every ratified map records the `_prompt_hash` /
  `_template_version` it was harvested from. A prompt change invalidates the map
  and triggers a re-harvest (machinery lives in `base.py` / `prompt_loader.py`).

## Two blockers surfaced during planning (see run1_unified_plan.md)

1. **The actor target vocab is only 4 codes, not 6.** `data/dim_actor_types.csv`
   contains `Deployer, Developer, Provider, Distributor` — `operator` and
   `compute_provider` (assumed by the harvest spec) are absent. The candidate CSV
   proposes codes that do not yet exist in the dim table. **D-2 must extend
   `dim_actor_types` before any lookup validates against it**, and the
   privacy-actor axis (`controller`/`processor`/`business`/`person` — 82% of
   obligation volume) is a VC + LKA schema-extension decision, not normalization.
2. **`modality_to_strength` has no target dim table.** `dim_requirement_types.csv`
   is a different taxonomy. A strength vocab home (`mandatory`/`conditional`/
   `recommended`) must be created before the fast-lane map lands.
