# data/lookups/ — Controlled-vocabulary mapping artifacts

Single home for every **agent-value → controlled-code** map consumed by the
unified normalization stage (`rollup_matrix.py` reading `data/lookups/*`) and the
tracker-alignment trust check. This directory is the unification point called for by:

- `extraction_run_corrections_eng.md` **C-7** (agent→extraction_type map)
- `vocab_harvest_spec_eng.md` **D-1/D-2** (vocab maps) + Track 1.C
- `engineering_strategy_v2.md` **WS-B** (the two-tier taxonomy substrate)
- `run1_unified_plan.md` (current sequencing)

**Rule:** no normalization stage may hard-code an agent/type/vocab relationship.
Every map lives here and is read through one loader.

## The two-tier taxonomy (Strategy v3 §6)

The matching key wants **lean**; tracker-alignment + display want **rich**. Resolve
with two tiers — every Tier-2 value maps to exactly one Tier-1 value via an alias table:

- **Tier 1 — Canonical (matching key).** Lean, snake_case, profile-aligned.
  Actors extend from the old 6 supply-chain codes to **~10**, sized to the data.
- **Tier 2 — Descriptive (source-facing).** The rich vocabulary the trackers
  actually speak. The harvest's 209 raw actor values normalize into Tier 2, then
  roll up to Tier 1.

**v3 extends normalization beyond actors** to the full set of dimensions, each of which
will get its own Tier-1/Tier-2 alias tables here as the work lands: `actors` (~10),
`law_domain` (new), covered `systems`, `obligation` families (21), `rights`,
`enforcement`, and `legal_context` (refactor of `preemption_signal`). This directory is
the home for **all** of these maps, read by the one normalization loader.

### Tier-1 actor codes — V1 LOCKED (13 codes, all forks split)

Forks resolved in `actor_fork_decisions.md`. See `actor_canonical_codes.csv` for full definitions.

| Tier-1 code | Fork | Notes |
|---|---|---|
| `developer` | distinct from `provider` | design-time obligations |
| `provider` | distinct from `developer` | supply-chain obligations |
| `deployer` | distinct from `operator` (F4) | sector roles collapse here + sector dim |
| `operator` | F4 kept distinct | operational obligations |
| `distributor` | — | thin; kept |
| `compute_provider` | — | training-run reporting |
| `controller` | F1 split from data_handler | determines purposes/means |
| `processor` | F1 split from data_handler | processes on behalf of controller |
| `data_broker` | distinct | registration + opt-out obligations |
| `regulator` | F2 split from gov umbrella | enforcer/oversight only |
| `government_agency` | F2 split from gov umbrella | gov-as-deployer/user |
| `individual` | F3 both code + actor_scope=protected | protected party |
| `regulated_entity` | — | generic catch-all |

**Pending LKA:** `business` (122 mentions) — controller vs regulated_entity ruling.
Until ruled: provisional `regulated_entity`, excluded from product output.

## Files

| File | Status | Description |
|---|---|---|
| `agent_to_extraction_type.json` | ✅ committed | mirrors `AGENT_EXTRACTION_TYPES` in `extractor.py` |
| `actor_canonical_codes.csv` | ✅ V1 built | 13 canonical codes, definitions, tracker alignment |
| `actor_aliases.csv` | ✅ V1 built | 215 rows: extraction + Orrick + IAPP terms, B0 spec schema |
| `actor_mapping_examples.csv` | ✅ V1 built | 162 rows: raw_term → tier1_code + tier2_label |
| `actor_fork_decisions.md` | ✅ V1 built | 4 fork decisions + business pending |
| `actor_unresolved_terms.csv` | ✅ V1 built | 48 rows: INVALID/REVIEW/PENDING terms with routing |
| `actor_crosswalk.csv` | ✅ V1 built | canonical \| Orrick \| IAPP \| raw_values — trust-check input |
| `candidates/actor_value_to_code_full.csv` | legacy candidate | 209 raw values with old data_handler/regulator_or_gov codes |
| `candidates/modality_to_strength_candidates.csv` | candidate — NOT ratified | modality → strength harvest |

> `candidates/actor_value_to_code_full.csv` is superseded by `actor_aliases.csv` (V1).
> It is retained for provenance and re-harvest comparison.

## Ratification rules (binding)

- **Choose Tier-1 codes against the trackers first (WS-B0).** Trust = "matches
  Orrick/IAPP," so pull their covered-entity vocabulary before locking codes.
- **Clean the actor field before mapping (WS-B1.5).** ~5% of values are non-actors
  (`contract`, `document`) or garbled (`operat`, `socia`, literal tab chars) —
  flagged `INVALID_nonactor` in the CSV. Fix at the parse layer and re-harvest so
  the committee maps signal, not noise.
- **Do not auto-create codes from extracted data.** Auto-mapped rows are
  committee-ratified; `REVIEW_*` rows stay queued until approved.
- **Reproducibility pinning:** every ratified map records the `_prompt_hash` /
  `_template_version` it was harvested from. **Re-harvest after the applicability
  run (WS-A1)** and lock codes only when two runs agree.
- **`modality_to_strength` has no target dim table yet** — `dim_requirement_types.csv`
  is a different taxonomy. A strength vocab home (`mandatory`/`conditional`/
  `recommended`) must be created before that fast-lane map lands.
