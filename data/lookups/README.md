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

### Tier-1 actor codes (~10, from `actor_taxonomy_analysis.md`)

| Tier-1 code | % of actor volume | Status |
|---|---:|---|
| `data_handler` *(new)* | 25.4% | controller + processor + business — biggest blind spot of the old model |
| `deployer` | 19.9% | absorbs all sector-specific users; sector carried by sector dim |
| `regulator` *(new; see fork 2)* | 15.9% | enforcement/oversight bodies + gov agencies |
| `individual` *(new; see fork 3)* | 9.1% | usually the *protected* party, default `actor_scope=protected` |
| `operator` | 9.1% | |
| `developer` | 8.4% | |
| `provider` | 4.5% | vendor, supplier, manufacturer, service_provider |
| `regulated_entity` *(new)* | 2.5% | generic covered/regulated entity |
| `data_broker` *(new)* | <1% | |
| `distributor` | 0.3% | |
| `compute_provider` | unused this run | retained |

**Four LKA legal-semantic forks gate ratification** (rulings, not engineering calls):
1. Split `data_handler` (controller vs processor — different legal duties)? — 25% of volume.
2. Split `regulator` into enforcer vs government-deployer? (CSV pre-merges as `regulator_or_gov` pending this ruling.)
3. Is `individual` a `protected`-scope flag rather than a compliance actor? — 9% of volume.
4. `operator` vs `deployer` — keep distinct or fold?

## Files

| File | Status | Source | Driver |
|---|---|---|---|
| `agent_to_extraction_type.json` | **committed** | mirrors `AGENT_EXTRACTION_TYPES` in `extractor.py` | C-7 |
| `candidates/actor_value_to_code_full.csv` | **candidate — NOT ratified** | full 209-value harvest → ~10-code map | D-1/D-2/WS-B |
| `candidates/modality_to_strength_candidates.csv` | **candidate — NOT ratified** | harvest of `obligation.modality` | D-1/D-2 |
| `dim_actor_types` (Tier-1) + Tier-2 `dim_*` + Tier2→Tier1 lookup | _pending — WS-B2_ | from candidates + B0 tracker vocab | WS-B |
| `modality_to_strength.json` | _pending — fast-lane_ | from candidate above | D-2 |

> The earlier partial `subject_to_actor_code_candidates.csv` was **removed** —
> superseded by `actor_value_to_code_full.csv`, which carries all 209 values with
> the ~10-code model and tier breakdowns.

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
