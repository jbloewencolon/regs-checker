# Engineering Strategy v3 — Tracker-Grounded Data Quality (merged)

**Supersedes:** `engineering_strategy_v2.md`
**Merges the strengths of:** v2 (evidence-grounded taxonomy, current-state accuracy, trust calibration, gating) + `AGENT_EXTRACTION_STRATEGY.md` (compliance-concept layer, full-breadth normalization, metric schema, per-agent refactors) + `STRATEGY_ENGINEERING_PLAN.md` (trust/verification spine).
**Grounded in:** the May 2026 run (6,274 extractions; full actor analysis in `actor_taxonomy_analysis.md` + `actor_value_to_code_full.csv`) and twelve clarifying decisions.
**Near-term goal (decided):** clean, **trustworthy** data across **all 232 laws** — where *trustworthy* = "matches Orrick/IAPP."

---

## 1. What v3 adds over v2

v2 was right on the spine but thin in four places the agent-extraction doc covered better. v3 keeps v2's backbone and folds those in:

1. **A compliance-concept layer** — raw rows (~9.5 per law, hundreds for some) are not the product unit; they must be grouped into business-facing requirements (new §7, WS-D).
2. **Full-breadth normalization** — not just actors, but law domains, covered systems, obligation families, rights, enforcement, and legal context (§6).
3. **A real metric schema** — replacing v2's "reconcile to one figure" with explicit, comparable counters (WS-A).
4. **Per-agent refactors** — agent-by-agent improvement plan (WS-F).

What v3 *keeps* from v2 because it's stronger there: the **evidence-sized actor model** (counted, not asserted), **calibration to your actual decisions**, the **"taxonomy is the substrate for trust"** logic, the **gates** (align-to-trackers-first; confirm the verification layer exists before "fixing" it; applicability hasn't run), and the **two-tier** canonical/descriptive structure.

---

## 2. The trust model (the spine)

Every workstream serves this pipeline:

1. The AI extracts the law's contents (recall-first — over-extract, normalize later).
2. **Every fact links to a source** — a tracker entry or a verified evidence span.
3. The extraction is **normalized** to a shared controlled vocabulary (§6).
4. Normalized fragments are **grouped into compliance concepts** (§7).
5. Concepts are **compared to Orrick/IAPP** (the trust check).
6. **Match → `tracker_grounded`. Disagree → flag a human. Neither tracker → `ungrounded`, shown only if labeled.**
7. Confidence is **recomputed after** the comparison.

The 88% Tier-C/D skew in the run is most likely a symptom of steps 5–7 not running, not a data-quality failure (see WS-C).

---

## 3. Architecture — the full layered pipeline

```
Orrick/IAPP tracker records
  → attach official source text (where available)
  → passage triage
  → raw agent extraction  ───────────────► internal primitives, NOT product
  → evidence-span verification
  → NORMALIZATION (actors, domains, systems, obligations, rights, enforcement, legal context)  [§6]
  → COMPLIANCE-CONCEPT grouping  [§7]
  → tracker alignment + confidence recompute  [WS-C]
  → analyst review queue (conflicts)  [WS-E]
  → law-card-ready structured data  ───────► DEFERRED product layer
```

**Why normalization sits where it does (the load-bearing argument):** your trust bar is "matches Orrick/IAPP." A computer can only measure a match if both sides use the same words; the run emitted 209 distinct actor strings against the trackers' own categories. **Normalization is therefore the precondition for the trust check, not a quality nicety** — which is why §6 gates WS-C.

---

## 4. Strategic principles

Merged from both docs; binding throughout:

1. **Orrick/IAPP are the current ground truth.** Until lawyer review exists, the system does not overrule the trackers on model interpretation alone.
2. **Raw extraction type is not product type.** `obligation`, `preemption_signal`, `threshold_exception` are internal primitives; product consumes normalized concepts.
3. **Recall early, precision later.** Extraction over-extracts; normalization, dedup, alignment, and review tighten it.
4. **Capture conflicts explicitly** — never silently merge tracker/source/model disagreements.
5. **Zero extraction is meaningful** — a law with no extractions may still need a tracker-only card; it does not disappear.
6. **Additive-not-destructive; inference offline; three-DB parity; reproducibility pinning** (`_prompt_hash`/`_template_version` on every derived artifact).

---

## 5. (reserved — see §6 normalization and §7 concepts)

---

## 6. The normalization taxonomy (full breadth)

### 6.1 Two-tier model

- **Tier 1 — Canonical (matching key).** Lean, snake_case, aligned to `anonymous_audit_profiles` and chosen to mirror tracker categories. Used for matching and tracker comparison.
- **Tier 2 — Descriptive (source-facing).** The rich vocabulary the law text and trackers actually use. Each Tier-2 value rolls up to exactly one Tier-1 value via a committee-approved alias table (not prompt instructions alone).

### 6.2 Actors — evidence-sized (`actor_taxonomy_analysis.md`)

Counting the run (2,559 mentions, 209 distinct values) showed the original 6-code model covers only ~42% of volume. **Tier-1 = ~10 canonical codes:**

| Tier 1 (canonical) | % vol | Tier 2 (descriptive, rolls up) |
|---|---:|---|
| `data_handler` *(new)* | 25.4% | controller, processor, business, data_processor |
| `deployer` | 19.9% | deployer + sector users (employer, insurer, healthcare_entity, platform, publisher, advertiser, school) — sector via separate dim |
| `regulator` *(new)* | 15.9% | regulator, agency, department, commission, attorney_general, enforcement_authority |
| `individual` *(new)* | 9.1% | person, applicant, consumer, user, minor — default `actor_scope = protected` |
| `operator` | 9.1% | operator |
| `developer` | 8.4% | developer |
| `provider` | 4.5% | vendor, supplier, manufacturer, service_provider |
| `regulated_entity` *(new)* | 2.5% | regulated/covered entity, entity, third_party |
| `data_broker` *(new)* | <1% | data_broker, data_actor |
| `distributor` | 0.3% | distributor |
| `compute_provider` | — | cloud/compute provider |

The agent-extraction doc's richer actor families (content_actor, political_actor, healthcare_entity, platform…) are absorbed as **Tier-2** labels. Four legal-semantic forks are LKA rulings, not engineering calls: split `data_handler` (controller vs processor); split `regulator` (enforcer vs government-deployer); treat `individual` as a `protected`-scope flag vs an actor; `operator` vs `deployer`.

### 6.3 Law domains *(from agent-extraction doc §6.1 — new dimension)*

A first-class `law_domain` (multi-valued, one primary): `ai_governance_cross_sector`, `privacy_profiling_ads`, `synthetic_media_elections`, `intimate_image_csam_likeness`, `healthcare_ai`, `employment_ads`, `insurance_algorithmic_discrimination`, `education_ai`, `government_ai_procurement`, `frontier_model_safety`, `pricing_competition`, `consumer_chatbot_disclosure`, `data_broker_training_data`, `general_consumer_protection`, `other_ai_adjacent`.

### 6.4 Covered systems

- **Tier 1 (8):** `ai_any`, `high_risk_ai`, `generative_ai`, `foundation_model`, `automated_decision_system`, `synthetic_media`, `biometric_ai`, `personal_data_trained_ai`.
- **Tier 2:** `automated_employment_decision_tool`, `consequential_decision_system`, `deepfake_generation_tool`, `facial_recognition_system`, `profiling_system`, `algorithmic_recommendation_system`, `chatbot_or_virtual_assistant`, `healthcare_algorithmic_utilization_review_system`, `algorithmic_pricing_system`, `frontier_model`, `training_data_pipeline` — each mapping to a Tier-1 parent.

### 6.5 Obligation families

Canonical = the planned 5 Level-1 domains; **Level-2 candidate set adopts the agent-extraction doc's 21 families**: notice, disclosure, consent, opt_out, human_review, appeal_or_contest, explanation, impact_assessment, bias_audit, risk_management, recordkeeping, reporting_to_regulator, public_reporting, registration, data_provenance, content_labeling, watermarking, incident_reporting, vendor_due_diligence, prohibited_use, safe_harbor_compliance. The run's clean `modality` field (must/shall/prohibited/may) drives the `obligation_strength` flag.

### 6.6 Rights *(maps `rights_protection` into a parallel set)*

`right_to_notice/access/correction/deletion/opt_out/appeal/human_review/explanation/non_discrimination/complain/remedy/withdraw_consent/restrict_processing`. Each right links to its duty-bearing actor (resolving the rights↔obligation overlap the run showed).

### 6.7 Enforcement *(normalize, don't rely on the 15 standalone rows)*

Only 15 standalone `enforcement` extractions exist. Build a normalizer that aggregates enforcement from: standalone rows + embedded `obligation.enforcement` + bill-level agents + Orrick/IAPP fields, into one record per law/concept: `enforcing_body`, `enforcement_mechanism`, `civil/criminal/administrative_penalty`, `injunctive_relief`, `private_right_of_action`, `cure_period`, `safe_harbor`, `penalty_unit`, `maximum_penalty`, `per_violation_rule`.

### 6.8 Legal context *(refactor `preemption_signal`)*

`preemption_signal` is too broad. Replace product use with `legal_context`: `true_preemption`, `constitutional_risk`, `agency_jurisdiction`, `cross_law_reference`, `safe_harbor_equivalence`, `litigation_or_injunction_signal`, `federal_interaction`, `other_legal_context` (hidden by default).

---

## 7. The compliance-concept layer

**The product unit is a compliance concept, not an extraction row.** The run averaged ~9.5 extractions per law (hundreds for some) — unusable directly. A concept groups several normalized fragments into one business-facing requirement.

Example — "Consumer opt-out right for profiling / automated decisions" groups: a consumer definition + a profiling definition + an opt-out obligation + an opt-out right + a response deadline + an exception + an enforcement penalty + tracker refs + evidence spans.

`compliance_concepts` (key fields): `concept_type`, `title`, `summary`, `regulated_actor_family`, `right_holder_family`, `covered_system_type`, `trigger_condition`, `required_action`, `deadline`, `exceptions` (jsonb), `enforcement_refs` (jsonb), `source_extraction_ids` (jsonb), `tracker_ref_ids` (jsonb), `confidence_score`, `review_status`. Concepts are the hand-off unit to the (deferred) law-card builder.

---

## 8. Workstreams

### WS-A — Run integrity, versioning & metrics *(enables everything; run now)*

| Item | Change | Owner |
|---|---|---|
| A1 | Run the bill-level **applicability** extraction across all 232 laws (confirmed **not run**). Verify migration `l8i4j0k2g713` first. | NLP, DevOps |
| A2 | Add `extraction_runs` (`run_id`, `git_sha`, `prompt_versions`, `model_config`, `source_snapshot_hash`, `summary`); add `run_id` to extraction/review/bill-level/verification tables; replace destructive purge with run creation + serving-run promotion. | SDPA, BE, DevOps |
| A3 | **Metric schema** (resolves the 4× run-summary vs monitor mismatch): distinct counters — `llm_call_count`, `agent_invocation_count`, `successful_agent_invocations`, `extraction_item_count`, `abstention_count`, `error_count`, `input/output/retry/verification/bill_level_tokens`. Every run emits a machine-readable quality report. | BE |
| A4 | Reconcile 138 → 232 coverage; classify missing laws (deferred / text-missing / ingest-gap); surface dropped-passage skips as non-zero. | DO, BE |

### WS-B — Normalization (the substrate; gates the trust check)

| Item | Change | Owner |
|---|---|---|
| B0 | **Align canonical codes to Orrick/IAPP's own categories first** (~1 day). Choose codes to maximize tracker comparability — this defines "correct" for the rest of WS-B. | RPR, LKA |
| B1.5 | **Clean the actor field** before mapping (~5% are non-actors/garbled, e.g. `contract`, `operat`, tab chars); fix at the parse layer, re-harvest. | NLP, BE |
| B2 | Stand up the layered dim model across **all** dimensions (§6) via alias tables: actors (~10), law_domain, covered systems, obligation families, rights, enforcement, legal_context. Apply to all three DBs. | SDPA, LKA |
| B3 | Ratify maps through VC; **defer the four actor forks (§6.2) to LKA** with the data in hand. | RPR, LKA, VC, DO |
| B4 | Unified normalization passes in `rollup_matrix.py` reading `data/lookups/*` → canonical IDs; idempotent; mismatches → `vocab_review_queue`. | BE |
| B5 | Inject ratified enums into agent prompts + parse-time validation against `dim_*`. | NLP |
| B6 | **Re-confirm before locking** — re-harvest after A1; lock codes only when two prompt versions agree, pinned to `_prompt_hash`. | BE, NLP |

### WS-C — Tracker alignment & verification *(the trust check; #1 priority)*

| Item | Change | Owner |
|---|---|---|
| C1 | **Audit whether the verification/comparison layer exists** (you said it's only "supposed to" work). If `provider.call()→LLMResponse` cross-validation/gap-detection agents are present but failing, fix the swallowed-failure bug; **if absent, build it.** Failure must be explicit, never a neutral pass. | NLP, BE |
| C2 | Persist `verification_results` (per-item alignment/verification status + score). | BE, SDPA |
| C3 | Tracker-alignment pass vs **both** Orrick and IAPP: emit `tracker_grounded`/`orrick_aligned`/`iapp_aligned`/`tracker_conflict`/`extraction_only_claim`/`tracker_only_claim`. Refine the Orrick gate so IAPP-only laws aren't auto-Tier-D. | NLP, RPR |
| C4 | **Recompute confidence after** alignment (weights: Orrick 30 / IAPP 20 / evidence 15 / citation 10 / cross-val 10 / gap 5 / analyst 10; redistribute when a tracker is absent; `ungrounded` hidden unless approved). | BE |
| C5 | Enforce **source linkage**: every served fact carries a tracker ref or verified evidence span, else `ungrounded` (98% already have spans). | BE, NLP |

### WS-D — Compliance-concept building *(bridge to the deferred product)*

| Item | Change | Owner |
|---|---|---|
| D1 | `compliance_concepts` + `concept_extraction_links` + `concept_tracker_links` tables (§7). | SDPA |
| D2 | Dedup + concept-grouping pass: group related normalized fragments into concepts; assign concept-level confidence; link to tracker refs + evidence. | BE, RPR |
| D3 | Concept review queue; concepts (not raw rows) are the unit handed to the future law-card builder. | BE |

### WS-E — Human review *(the "flag for a human" requirement)*

| Item | Change | Owner |
|---|---|---|
| E1 | Analyst-review step + queue; conflicts from C3 land here. Reviewer identity from auth context, schema-validated corrections, immutable audit log; analyst review kept distinct from future legal review. | BE, RPR |
| E2 | Review priority rules: tracker conflicts, extraction-only obligations, D-tier items affecting a card, zero-extraction high-importance laws, high-risk domains (employment/health/insurance), parse failures, abnormal extraction counts. | RPR, PTPL |
| E3 | Review UI surfaces Orrick + IAPP fields, evidence spans, conflict warnings, confidence breakdown. | FE |

### WS-F — Agent-specific refactors *(parallel; quality of the raw layer)*

| Agent | Refactor | Owner |
|---|---|---|
| obligation | Reduce fragmentation; require subject/action/object/condition; separate penalties from duties; flag passive obligations. | NLP |
| definition_actor | Handle long definitions; separate pure definitions from actor maps; normalize aliases immediately; retry long passages at lower token budget. | NLP |
| threshold_exception | Keep combined at extraction, split downstream: each exception a child object; normalize threshold units (scope/temporal/entity-size). | NLP, BE |
| rights_protection | Map to rights taxonomy (§6.6); link each right to a duty-bearer; distinguish individual rights from regulator powers. | NLP |
| compliance_mechanism | Tighten boundaries (20% abstention, highest failure rate); split recordkeeping/reporting/audit/assessment/registration/incident; stop over-extracting ordinary enforcement language. | NLP, RPR |
| preemption → `legal_context` | Rename; classify true preemption vs jurisdiction vs cross-reference; hide low-value `other`. | NLP |
| enforcement | Post-extraction normalizer (§6.7); pull from obligation payloads + bill-level + Orrick. | NLP, BE |

---

## 9. Sequencing

```
WS-A (applicability run + versioning + metrics) ──┐
                                                  ▼
WS-B  B0 align-to-trackers → B1.5 clean → B2/B3 [4 forks = gate] → B6 re-confirm
                                                  ▼
WS-C (tracker alignment + verification + confidence recompute)  ◄── trust bar
                                                  ▼
WS-D (compliance concepts)  ──►  WS-E (human review of conflicts)
                                                  ▼
                    DEFERRED: law cards · applicability product · API · productionization
```

- **WS-A first** — applicability must run and runs must stop being destructive before anything is trustworthy.
- **WS-B before WS-C** — no shared vocabulary, no comparison (§3). Codes are chosen against the trackers (B0) on a cleaned field (B1.5) before the model is built.
- **WS-C is the priority deliverable** — it operationalizes your trust bar. **Do not run a quality-improved re-extraction until B + prompt enums (B5) land**, and **confirm the verification layer exists (C1) before scoping it as a fix.**
- **WS-D + WS-E** follow alignment. **WS-F runs in parallel** — it improves the raw layer and can start anytime.

---

## 10. Definition of done (merged)

1. Applicability extraction populated for all 232 laws (A1).
2. Runs non-destructive and versioned; metrics distinct and comparable across runs (A2, A3).
3. Every extraction normalizes across all dimensions (§6); actor field cleaned; <10% in `vocab_review_queue`.
4. Verification/alignment runs correctly and persists results; a silently-failed check **cannot** raise a tier (C1, C2, C4).
5. Confidence reflects tracker alignment + evidence + verification + review; IAPP-only laws not auto-downgraded (C3, C4).
6. Every served fact links to a tracker ref or verified evidence span, else `ungrounded` (C5).
7. Compliance concepts group related extractions into product-usable requirements (WS-D).
8. Tracker conflicts surfaced and queued for an authenticated analyst; zero-extraction tracker records can still yield tracker-only cards (WS-E, principle 5).
9. Product layer (when resumed) consumes **concepts, not raw rows** (§7).

---

## 11. Deferred (confirmed — not now)

Law-card data model, business applicability product, product API, productionization/ops. These resume once §10's done-criteria hold; the concept layer (WS-D) is the hand-off boundary.

---

## 12. Open divergences to confirm before building

1. **Does the verification/comparison layer actually exist?** (you said only "supposed to"). The agent-extraction doc assumes it exists and just needs fixing; the run was local Gemma only. C1 must confirm **build-vs-fix** before scoping.
2. **Is IAPP data ingested, or only referenced?** Both are in scope; confirm IAPP records are loaded so C3 can compare.
3. **Four actor-code forks** (§6.2) — the LKA rulings that gate WS-B.
4. **Tracker entity/scope vocabulary** — B0 needs Orrick/IAPP's own categories to finalize the canonical codes.
