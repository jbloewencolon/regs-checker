# Engineering Strategy v2 — Tracker-Grounded Data Quality

**Supersedes:** `code_update_strategy_eng.md`
**Integrates:** corrections (`extraction_run_corrections_eng.md`, C-1…C-8), vocab harvest (`vocab_harvest_spec_eng.md`, D-1…D-4), the uploaded `STRATEGY_ENGINEERING_PLAN.md` (trust + verification spine), and the clarifying decisions captured below.
**Audience:** Engineering (NLP, BE, SDPA, DevOps, DO), VC, PTPL, RPR
**Near-term goal (decided):** clean, **trustworthy** data across **all** laws — where *trustworthy* = "matches Orrick/IAPP."

---

## 1. What changed and why

Twelve clarifying decisions reframed the strategy. The build is staged (data platform now, business product later); both Orrick **and** IAPP are reference sources; **no human reviews the data yet**; and the bar for trust is concrete: **the AI's extraction must match the trackers.** Disagreements flag a human; every fact links to its source; unchecked data may be shown only if labeled.

This means the work is no longer "migrate the taxonomy" running alongside "fix the run." It is one thing: **make extracted data comparable to the trackers, then measure and enforce that comparison.** The taxonomy is the substrate that makes the comparison possible; verification is the act of comparing; run versioning is what makes it auditable. The product layer (law cards, applicability engine, API) is explicitly deferred.

---

## 2. The trust model (the spine)

Every workstream below serves this pipeline:

1. The AI extracts the law's contents.
2. **Every fact links to a source** — a tracker entry or a verified evidence span in the law text.
3. The system **normalizes** the extraction to a shared controlled vocabulary (§4).
4. The system **compares** the normalized extraction to Orrick/IAPP fields (§5, WS-C).
5. **Match → `tracker_grounded`.** **Disagree → flag for human review** (WS-D). **Neither tracker present → `ungrounded`, shown only if labeled.**
6. Confidence is **recomputed after** this comparison — not fixed at extraction time.

The 88% low-confidence (Tier C/D) found in the run is most likely a *symptom* of step 4–6 not running: if alignment/verification silently fails, the confidence boost never lands. Fixing the comparison may clean the confidence picture for free.

---

## 3. Why the taxonomy cannot be deferred

Step 4 — the comparison that defines trust — requires that both sides use the same words. Orrick and IAPP categorize laws by scope, status, covered entities, and requirements; the agents extract those same dimensions as free-ish text (the run emitted **209 distinct `subject_normalized` values** against a 6-code target). You cannot compute "does the AI's actor match the tracker's covered entity" across 209 raw values and a tracker's own categories. **Normalization to a shared taxonomy is the precondition for tracker alignment.** Therefore the taxonomy work (WS-B) is sequenced *before* the trust check (WS-C), not parallel to it.

This also resolves the open question from the prior assessment: the richer taxonomies in the uploaded doc's §8 are not a competing vocabulary to reconcile later — they are the **descriptive layer** the trackers actually speak, and they supply the privacy-law roles (`controller`, `processor`, `employer`, `insurer`) that the run proved dominate real extractions.

---

## 4. The unified taxonomy (the incorporation)

### 4.1 A layered model

The tension was: our taxonomy plan chose a **lean, profile-aligned** vocabulary (6 actors, 8 AI scopes); the uploaded §8 lists are **rich** (19 roles, 17 AI systems); the AI emits **209** actor values. A single flat list can't satisfy matching (wants lean) and tracker-alignment + display (want rich). Resolve with two tiers:

- **Tier 1 — Canonical (matching key).** Lean, snake_case, aligned to `anonymous_audit_profiles`. Used for the matching-engine join and profile comparison.
- **Tier 2 — Descriptive (source-facing).** The rich §8 vocabulary. Used to capture what the law/tracker actually says, to compare against tracker fields, and to display.

Every Tier-2 value maps to exactly one Tier-1 value via a committee-approved lookup. The harvest's 209 raw values normalize **into Tier 2**, then Tier 2 rolls up to Tier 1.

### 4.2 Actors — sized to the data (see `actor_taxonomy_analysis.md`)

A full count of the run (2,559 actor mentions, 209 distinct values across `obligation.subject_normalized` + `compliance_mechanism.responsible_party_normalized`) showed the original 6-code supply-chain model covers only **~42% of actor volume**. The remaining ~58% falls into categories the model never had. **Tier-1 therefore extends from 6 to ~10 canonical codes**, each clearing a real volume threshold:

| Tier 1 (canonical, matching) | % of actor volume | Tier 2 (descriptive, rolls up to this code) |
|---|---:|---|
| `data_handler` *(new)* | 25.4% | controller, processor, business, data_processor |
| `deployer` | 19.9% | deployer + **all sector-specific users** (employer, insurer, hospital, school, platform, publisher, advertiser, licensee…) — sector carried by the separate sector dim, **not** new actor codes |
| `regulator` *(new)* | 15.9% | regulator, agency, department, commission, attorney_general, enforcement_authority |
| `individual` *(new)* | 9.1% | person, individual, applicant, consumer, user, minor — default `actor_scope = protected` |
| `operator` | 9.1% | operator |
| `developer` | 8.4% | developer |
| `provider` | 4.5% | vendor, supplier, manufacturer, service_provider, model_management_company |
| `regulated_entity` *(new)* | 2.5% | regulated/covered entity, entity, third_party |
| `data_broker` *(new)* | <1% | data_broker |
| `distributor` | 0.3% | distributor |
| `compute_provider` | (unused this run) | cloud/compute provider |

Full value→code assignments for all 209 values: `actor_value_to_code_full.csv`. The `data_handler` cluster (controller 359 + processor 133 + business 122) is the single largest bucket — the original 6-code model's biggest blind spot.

**Four legal-semantic forks gate WS-B — LKA rulings, not engineering calls:**

1. **Split `data_handler`?** `controller` and `processor` carry different legal duties; merging is simpler for matching, splitting preserves the distinction. Highest-stakes (25% of volume).
2. **`regulator` conflates two roles** — enforcement/oversight bodies (which *enforce*) vs. government agencies that *deploy* AI (really `deployer` + public-sector sector tag). "Who must comply" semantics differ.
3. **`individual` is usually the *protected* party, not a complier** — it may belong on the existing `actor_scope = protected` flag rather than implying an obligation. 9% of volume hinges on this.
4. **`operator` vs `deployer` overlap** — keep distinct or fold.

### 4.3 AI systems

- **Tier 1 (8, canonical):** `ai_any`, `high_risk_ai`, `generative_ai`, `foundation_model`, `automated_decision_system`, `synthetic_media`, `biometric_ai`, `personal_data_trained_ai`.
- **Tier 2 (from §8.2):** adds `recommender_system`, `pricing_algorithm`, `chatbot`, `ai_agent`, `frontier_model`, `algorithmic_utilization_review`, `automated_employment_decision_tool`, `facial_recognition`, etc. Each maps to a Tier-1 parent (e.g. `facial_recognition` → `biometric_ai`; `automated_employment_decision_tool` → `automated_decision_system`).

### 4.4 Obligations

Keep the planned 3-level structure as canonical (5 Level-1 domains, ~25 Level-2 codes). Adopt §8.5's 26-item list as the **Level-2 candidate set** (notice, disclosure, consent, opt_out, human_review, appeal, impact_assessment, bias_audit, watermarking, content_labeling, incident_reporting, vendor_due_diligence, etc.), each FK'd to a Level-1 domain. The run's `modality` field (8 clean values) drives the Level-3 `obligation_strength` flag.

### 4.5 Sectors, use-cases, status

Sectors stay the planned standalone 11-value dimension; §8.4's use-case list is the Tier-2 descriptive layer over it. Legislative + enforcement status split as planned, with the IAPP status crosswalk added (the plan only covered Orrick — closed here because both trackers are in scope).

### 4.6 How the layered taxonomy powers the trust check

Tracker alignment (WS-C) compares on **Tier 2** where the tracker is specific (covered-entity, scope) and falls back to **Tier 1** where it isn't. A match at either tier with a linked source = `tracker_grounded`; a Tier-conflict (AI says `employer`/deployer, tracker says provider) routes to human review. The taxonomy is thus not cosmetic — it is the unit of comparison.

---

## 5. Workstreams

### WS-A — Run integrity & versioning *(enables everything; run now)*

| Item | Change | Driver | Owner |
|---|---|---|---|
| A1 | Run the bill-level **applicability** extraction across all 232 laws (it did **not** run — confirmed). It produces the scope fields compared to tracker scope. Verify migration `l8i4j0k2g713` first. | C-1 | NLP, DevOps |
| A2 | Add `extraction_runs` table (`run_id`, `git_sha`, `prompt_versions`, `model_config`, `source_snapshot_hash`, `summary`); add `run_id` to extractions/review/bill-level tables. Replace destructive full-run purge with run creation + serving-run promotion. | uploaded §5.3 | SDPA, BE, DevOps |
| A3 | Single authoritative tokens/calls figure per run; surface dropped-passage skips as non-zero in run summary. | C-2, C-4 | BE |
| A4 | Reconcile 138 → 232 coverage; classify missing laws (deferred / text-missing / ingest-gap). | C-3 | DO, BE |

### WS-B — Taxonomy normalization *(the substrate; gates the trust check)*

| Item | Change | Driver | Owner |
|---|---|---|---|
| B0 | **Pull Orrick/IAPP's own "covered entity" / scope vocabulary first** (~1 day). Choose canonical codes to **maximize comparability with the trackers**, since trust = "matches Orrick/IAPP." This re-defines "correct" for B2–B4 (e.g. if trackers say controller/processor, `data_handler` mirrors them; if they say deployer, map accordingly). | trust model | RPR, LKA |
| B1 | `harvest_vocab.py`: tier-stratified value distributions per agent field, pinned to `_prompt_hash`/`_template_version`. (Done once already — see `actor_taxonomy_analysis.md`.) | D-1 | BE |
| B1.5 | **Clean the actor field before mapping.** ~5% of values are non-actors (`contract`, `document`) or garbled strings (`operat`, `socia`, tab chars). Fix at the extraction/parse layer, then re-harvest, so the committee maps signal not noise. | data-quality | NLP, BE |
| B2 | Stand up the **layered dim model** (§4): Tier-1 canonical (**~10 actor codes**, incl. new `data_handler`, `regulator`, `individual`, `regulated_entity`, `data_broker`) + Tier-2 descriptive `dim_*` tables + Tier2→Tier1 lookup. Apply to all three DBs. | §4.2, plan + §8 | SDPA, LKA |
| B3 | Ratify maps through VC (`subject_to_actor_code` full 209; `modality_to_strength`; `agent_to_extraction_type` naming map). **Defer the four §4.2 forks to LKA with the data in hand** (data_handler split, regulator-vs-gov-deployer, individual-as-protected, operator-vs-deployer). | D-2, C-7, §4.2 | RPR, LKA, VC, DO |
| B4 | Unified normalization stage in `rollup_matrix.py` reading `data/lookups/*` → writes canonical IDs; idempotent; mismatches → `vocab_review_queue`. | D-2, C-7 | BE |
| B5 | Inject ratified enums into agent prompts + parse-time validation against `dim_*`. | D-3 | NLP |
| B6 | **Re-confirm before locking.** This distribution is from one prompt version, and the applicability agent (A1) hasn't run. Re-harvest after A1; lock codes only when two runs agree, pinned to `_prompt_hash`. | reproducibility | BE, NLP |

### WS-C — Tracker alignment & verification *(the trust check; your #1 priority)*

| Item | Change | Driver | Owner |
|---|---|---|---|
| C1 | **Audit whether tracker comparison runs at all** (you weren't sure). Check the verification agents (cross-validation, gap-detection) for the swallowed-failure / response-handling bug; make failure explicit, never a neutral pass. | uploaded §5.1 | NLP, BE |
| C2 | Persist `verification_results` (per-extraction alignment/verification status + score). | uploaded §5.2 | BE, SDPA |
| C3 | Tracker-alignment module: compare normalized extraction (§4.6) to **both** Orrick and IAPP; emit `tracker_grounded` / `iapp_grounded` / `tracker_conflict` / `ungrounded`. Refine the Orrick gate so IAPP-only laws aren't auto-Tier-D. | uploaded §9.1–9.4 | NLP, RPR |
| C4 | **Recompute confidence after** alignment/verification (tracker-grounded weighted model). | uploaded §5.2, §9.2 | BE |
| C5 | Enforce **source linkage**: every served fact carries a tracker ref or verified evidence span, or is marked `ungrounded`. (98% already have evidence spans — good base.) | your "yes, always" | BE, NLP |

### WS-D — Minimal human review *(the "flag for a human" requirement)*

| Item | Change | Driver | Owner |
|---|---|---|---|
| D1 | Stand up an **analyst-review** step + queue (conflicts from C3 land here). Reviewer identity from auth context, not request body; corrections schema-validated; immutable audit log. Keep analyst review distinct from future legal review. | uploaded §5.4, §9.5; your "flag for a human" | BE, RPR |
| D2 | Review UI surfaces Orrick + IAPP fields, evidence spans, conflict warnings, confidence breakdown. | uploaded §9.6 | FE |

---

## 6. Sequencing

```
WS-A (run integrity + versioning) ──┐
   A1 applicability run (all 232) ──┤── feeds normalization & alignment
                                    ▼
WS-B  B0 align codes to Orrick/IAPP ─► B1.5 clean field ─► B2/B3 [4 LKA forks = gate] ─► B6 re-confirm
                                    ▼
WS-C (tracker alignment + verification + confidence recompute)  ◄── your trust bar
                                    ▼
WS-D (human review for conflicts)
                                    ▼
            DEFERRED: law cards · applicability product · API · productionization
```

- **WS-A first** — applicability must run (A1) and runs must stop being destructive (A2) before anything is trustworthy.
- **WS-B before WS-C** — no shared vocabulary, no comparison (§3). Within WS-B, **codes are chosen against the trackers (B0) and on a cleaned field (B1.5)** before the model is built — so the taxonomy serves the trust check rather than just internal tidiness.
- **The actor model extends to ~10 codes** (§4.2); the four legal-semantic forks are the WS-B gate, ruled by LKA with the harvest data in hand.
- **WS-C is the priority deliverable** — it operationalizes your definition of trust. Do not run a quality-improved re-extraction (old Track 3.F) until B + the prompt enums (B5) land.
- **WS-D** is small but required: "flag for a human" needs a human step that doesn't exist today.

---

## 7. Definition of done (tied to your trust bar)

1. Applicability extraction populated for all 232 laws (A1).
2. Runs are non-destructive and versioned; prior review history survives (A2).
3. Every law's extraction normalizes to the layered taxonomy (~10 actor codes); the actor field is cleaned of non-actor/garbled values; <10% lands in `vocab_review_queue` (B).
4. Tracker alignment runs against **both** Orrick and IAPP and persists a per-law status; IAPP-only laws are not unfairly downgraded (C3).
5. Confidence is recomputed post-comparison; a silently-failed check **cannot** raise a tier (C1, C4).
6. Every served fact links to a tracker ref or verified evidence span, else is labeled `ungrounded` (C5).
7. Tracker disagreements are queued for an authenticated analyst, not silently merged (D1).

---

## 8. Deferred (confirmed — not now)

Law-card data model (uploaded §7), business applicability product (§10), product API (§12), productionization/ops (§6). These sit on top of clean, tracker-grounded data and resume once §7's done-criteria hold.

---

## 9. Open divergences to confirm before building

1. **Does the verification provider layer actually exist?** The uploaded doc's C1 fix assumes `provider.call() → LLMResponse` cross-validation/gap-detection agents and references `openai/gpt-oss-20b`. The run was **local Gemma only** (`google-gemma-4-26b-a4b-local`), and the taxonomy plan says the cloud provider is archived. **WS-C1 must first confirm the layer is present** — if alignment was never built (vs. built-but-broken), C1 becomes "build it," not "fix it."
2. **Is IAPP data ingested, or only referenced?** Both are in scope per your answer; confirm IAPP records are actually loaded so C3 can compare against them.
3. **Four actor-code forks** (§4.2) — the LKA rulings that gate WS-B: whether to split `data_handler` (controller vs processor), whether `regulator` splits into enforcer vs government-deployer, whether `individual` is a `protected`-scope flag rather than an actor, and `operator` vs `deployer`.
4. **Tracker entity vocabulary** — B0 needs Orrick/IAPP's own covered-entity categories to finalize the ~10 codes; confirm these are accessible.

---

## 10. Inherited principles (binding, not restated per item)

Additive-not-destructive (no column/table drops this cycle); inference offline / serving instant (no runtime LLM in the serving path); three-database parity via `apply_pending_migrations.sql`; reproducibility pinning (`_prompt_hash`/`_template_version` on every derived artifact); structured logging; idempotent normalization.
