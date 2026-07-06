# Engineering Spec: Vocabulary Harvest & Agent Self-Improvement Loop

**Audience:** Engineering team (NLP, BE, SDPA, DO) + Vocabulary Committee (VC)
**Source run:** Extraction 2026-05-10 → 2026-05-11 (`extractions.csv`, 6,274 rows)
**Plan references:** Taxonomy Redesign Plan Tracks 2.B, 3.C, 3.E, 3.F; Strategy doc §5 (migration architecture), §3 (style guide)
**Starter artifacts produced with this spec:**
`subject_to_actor_code_candidates.csv`, `modality_to_strength_candidates.csv`

---

## 1. Objective

Use the just-completed extraction run as a **self-bootstrapping training signal** to strengthen the agents on future runs. The run generated, for free, the artifact the plan otherwise hand-authors: the empirical distribution of classification values the agents actually emit. This spec turns that distribution into four concrete deliverables:

1. A frequency-ranked, tier-stratified **vocabulary harvest** of agent-emitted classification fields.
2. A **candidate lookup map** (agent value → controlled-vocab code) for the VC to ratify — feeding Track 2.B and 3.C.
3. A **prompt enum list** seeded from observed values — feeding the Track 3.E prompt update (the plan's core "put the vocab in the prompt" mitigation).
4. A **gold-standard fixture set** drawn from Tier-A extractions + evidence spans — feeding the Track 3.F eval harness.

The confidence tier is the **routing key** throughout, not a separate output: Tier A/B feeds exemplars; Tier C/D + abstentions feed the active-learning review queue.

---

## 2. Key finding that shapes this work

The harvest already surfaced a result that must be resolved before the lookup is built:

- The obligation agent's `subject_normalized` field emitted **209 distinct values** against a target vocabulary of **6 actor codes** (`developer`, `deployer`, `provider`, `distributor`, `compute_provider`, `operator`).
- The **dominant values are privacy-law roles, not supply-chain roles**: `controller` (299), `person` (153), `business` (122), `processor` (105). These do **not** map cleanly onto the 6-value model.
- A naive auto-map of only the obvious supply-chain synonyms covers **18% of obligation volume**. The other 82% sits in roles the current actor taxonomy doesn't represent.

**Implication:** this is not just a normalization exercise — it is evidence that the actor dimension may need extension (e.g., a privacy-actor axis: `controller`/`processor`, or a `regulated_entity`/`covered_business` parent). That is a VC + LKA decision, and it should be made *before* Track 2.B's lookup is finalized, not discovered during it.

**Scoping relief:** the committee does **not** need to rule on all 209 values. The distribution is Pareto:

| Coverage of obligation rows | Values needed |
|---|---|
| 80% | top **44** values |
| 90% | top **82** values |

So a single committee pass over ~44–82 values clears the large majority of volume; the remaining ~125+ long-tail values go to the standing review queue.

---

## 3. Deliverables

### D-1 — Vocabulary harvest job
**Owner:** BE
**What:** A reusable script (`src/scripts/harvest_vocab.py`) that, given an extraction export, emits per-field value distributions stratified by confidence tier, for the classification fields of each agent. At minimum:

| Agent / type | Field(s) to harvest | Feeds |
|---|---|---|
| obligation | `subject_normalized`, `modality` | Track 2.B (actor), Track 3.C (strength) |
| applicability* | `ai_system_types_in_scope`, `covered_sectors` | Track 2.C, 2.D |
| obligation / compliance_mechanism | `action` (verb head) | Track 3.D Level-1 crosswalk keywords |
| preemption | preemption signal phrasing | Phase 4.E classification |

\*`applicability_agent` was absent from this run (see corrections doc C-1); its fields harvest on the next run.

**Output format:** one CSV per (agent, field): `value, count, A, B, C, D, proposed_code`. Pin every harvest to the `_prompt_hash` / `_template_version` present in the payloads (see §5) so results are reproducible and not conflated across prompt versions.

**Done when:** running the job on this export reproduces `subject_to_actor_code_candidates.csv` and `modality_to_strength_candidates.csv` (the attached starter artifacts).

---

### D-2 — Candidate lookup maps for committee ratification
**Owner:** RPR (draft) → LKA (interpretation) → VC (approval) → DO (commit)
**What:** The starter CSVs are the *input* to the existing vocab-committee workflow, not the final lookup. Process them through the plan's "standing up a new dim_* table" chain:

- **`subject_to_actor_code_candidates.csv`** — 209 rows, top 44 covering 80% of volume. Auto-mapped rows (13) are pre-filled; `REVIEW`-flagged rows need a ruling. The privacy-actor question (§2) is the first agenda item.
- **`modality_to_strength_candidates.csv`** — 13 rows, 8 auto-mapped (`must`/`shall`/`prohibited` → `mandatory`, `may` → `conditional`, `should` → `recommended`). The 5 `REVIEW` rows are edge phrasings (`shall be liable`, `is guilty of`, `must not be exempt from criminal liability`). This field is nearly clean and should clear in one fast-lane pass.

**Constraint (from plan):** do **not** auto-create new codes from extracted data. Auto-mapped rows are committee-ratified; everything else is `REVIEW` until approved. Approved maps land in `data/lookups/` (per Track 1.C — create the directory if D-1 of the Phase-1 plan hasn't yet).

**Done when:** the top-44 actor values and all 13 modality values have committee-approved codes committed to `data/lookups/`; long-tail values are queued in `vocab_review_queue`.

---

### D-3 — Prompt enum injection
**Owner:** NLP
**What:** Feed the ratified controlled-vocab lists into the agent prompts (`prompts/obligation.yml` and the others), per Track 3.E. The agent must *see* the valid codes. Two mechanisms, both required by the plan's "LLMs are unreliable at strict enums" principle:

1. Include the approved code list inline in the prompt.
2. Validate agent output against `dim_*` codes at parse time; route mismatches to `vocab_review_queue` rather than silently accepting them.

Use the harvested distribution to also build **disambiguation examples** for the values the agent currently conflates (e.g., when it emits `controller/processor` as a hedge — show it how to pick one or flag genuine dual-role).

**Done when:** the updated prompts carry the approved enums; a sample run shows agent output validating against `dim_*` codes at ≥ the plan's Phase 3 target, with mismatches queued not dropped.

---

### D-4 — Gold-standard fixture set
**Owner:** NLP (build) + RPR (validate)
**What:** Build `tests/fixtures/gold_standard/` entries from **Tier-A extractions paired with their evidence spans** (98% of rows carry evidence spans). Available Tier-A fixture pool from this run:

| extraction_type | Tier-A + evidence-span rows |
|---|---|
| obligation | 61 |
| rights_protection | 30 |
| definition | 25 |
| compliance_mechanism | 20 |
| preemption_signal | 6 |
| threshold | 5 |
| exception | 1 |
| enforcement | 1 |
| **total** | **149** |

**Active-learning note:** Tier-A cases are *positive exemplars* but low-information (the model already gets them right). The higher-value fixtures are **human-corrected Tier-C/D and abstention cases** — these teach the decision boundary. Prioritize building corrected fixtures from the `compliance_mechanism` abstentions (20% rate, see corrections C-6) and the `subject_normalized` hedges, not just the easy Tier-A wins.

**Done when:** a baseline fixture set exists per agent; the eval harness runs against it and produces a pre-change accuracy baseline to measure the D-3 prompt update against.

---

## 4. Sequence & dependencies

```
D-1 harvest job ──┬──> D-2 committee ratification ──> D-3 prompt enums ──> (Track 3.F re-extraction)
                  └──> D-4 gold-standard fixtures ───────────────────────^ (eval baseline)
```

- D-1 is unblocked now and runs against the existing export.
- D-2 depends on D-1 and on the §2 privacy-actor decision (VC + LKA) — this is the gating decision.
- D-3 depends on D-2 (needs ratified codes) and D-4 (needs an eval baseline to measure against).
- All four are **prerequisites to a quality-improved re-extraction** (Track 3.F); running 3.F before D-2/D-3 wastes GPU budget on un-improved prompts.

---

## 5. Reproducibility constraint (do not skip)

Every payload carries `_prompt_hash` and `_template_version`. The harvested vocabulary is only valid as a feedback signal **for the prompt version that produced it**. Before treating any harvest as ground truth:

- Pin the harvest to a single `_prompt_hash` per agent; if multiple versions appear in one export, segment the harvest and do not pool values across versions.
- Record the pinned version in the committed lookup file header, so a later prompt change triggers a re-harvest rather than silently invalidating the map.

This prevents the loop from training the next prompt on values produced by a now-superseded prompt.

---

## 6. Summary table

| ID | Deliverable | Owner(s) | Depends on |
|----|-------------|----------|------------|
| D-1 | Vocabulary harvest job | BE | — (run now) |
| D-2 | Candidate lookup maps ratified | RPR, LKA, VC, DO | D-1 + privacy-actor decision |
| D-3 | Prompt enum injection + validation | NLP | D-2, D-4 |
| D-4 | Gold-standard fixture set | NLP, RPR | D-1 |
| — | Quality-improved re-extraction | NLP, DevOps, PTPL | D-2, D-3, D-4 (Track 3.F) |

**Attached starter artifacts:** `subject_to_actor_code_candidates.csv` (209 values, top 44 = 80% coverage), `modality_to_strength_candidates.csv` (13 values, 8 pre-mapped).
