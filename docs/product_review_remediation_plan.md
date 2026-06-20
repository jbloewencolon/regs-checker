# Product-Review Remediation Plan

**Trigger:** Senior legal-analyst product review of the extraction corpus (`test.csv`, 15,543 rows, exported 15–16 Jun 2026). See *"AI-Law Dataset: Taxonomy Evaluation, Law Card Schema, and Validation Kit."*

**Date:** 2026-06-20

---

## 0. Framing: what the review got right, and what has moved on

The review is sharp and its priorities are correct. But it analyzed a **12-column CSV export**, and several of its "critical gaps" are gaps *in that export*, not in the system. The export it profiled (`src/core/run_archiver.py:223`) joins `Extraction → NormalizedSourceRecord → DocumentVersion → DocumentFamily → Source` and then **drops** `effective_date`, `temporal_status`, and `grounding_status` — the exact fields the review says are missing. So the corrective work splits into two very different kinds:

- **WIRE** — the data/logic already exists; it just doesn't reach the concept layer, the UI, or the exports. (Cheap, high-leverage.)
- **BUILD** — a genuine gap with no implementation today. (Real engineering.)

The table below maps every major review finding to current reality. This is the spine of the plan.

| # | Review finding | Reality in codebase | Verdict | Action |
|---|---|---|---|---|
| §3 | No currentness check — repealed law shown as current | `DocumentVersion.temporal_status` (incl. `repealed/stayed/future_effective`), `effective_date`, `sunset_date`, `LegalEvent` log, `status_checker.py`, `cross_reference.py` (diffs effective_date + status across Orrick/IAPP) all exist; DB views filter `temporal_status='active'`. **But** `ComplianceConcept` carries no currentness field, the grouper ignores status, the UI/exports show none, and there is no `as_of`/`amendment_status`. | PARTIAL | **WIRE** + small BUILD |
| §2.5 | 97.6% C/D shown with equal authority; no publish gate | Tier is computed and *surfaced* in concept UI/exports; Orrick hard-gate already forces no-tracker rows to Tier D. **But** publish keys on `review_status` + `is_serving`, not a confidence floor — D-tier approved rows flow through. | PARTIAL | **BUILD** (gate) |
| — | No "not legal advice" disclaimer anywhere | None in any template or export. | GAP | **BUILD** (trivial) |
| §2.6 | 36% verified spans; no URL/anchor | `verified` flag + passage-local offsets exist and feed confidence. No `source_url`/`section_anchor`; unverified spans not gated out of cards. | PARTIAL | **WIRE** (gate) + BUILD (url/anchor) |
| §2.3 | Penalty free-text, ~26% populated | `max_civil_penalty_usd` exists; `penalty_per` exists bill-level only; `penalty_type` is free text with alias CSVs that are never applied to it. | PARTIAL | **BUILD** (enum + apply aliases) |
| §2.2/§2.10 | Actors: business vs government unseparated; `actor_mapping` near-empty | `subject_normalized` exists; `applicability_agent.government_only` exists bill-level; a hardcoded gov-subject set reclassifies gov obligations to `enforcement`. No per-actor `actor_role`. | PARTIAL | **BUILD** (role tag) + WIRE |
| §2.4 | Timelines 72% empty; no normalized effective_date; safe_harbor 1% | `timeline`/`effective_date`/`safe_harbor` schemas exist; ISO-8601 only deterministic in the Orrick regex path; clause/bill LLM dates unvalidated. | PARTIAL | **BUILD** (date validator + safe-harbor pass) |
| §2.1 | 9 types collapse to 6 schemas | **Already by design**: `definition_actor` emits `definition`+`actor_mapping`+`framework_ref`; `threshold_exception` emits `threshold`+`exception` (with `threshold_sub_type`). | RESOLVED | Document only |
| §2.7 | 48% `TMP-` citations; possible non-AI statutes | `TMP-` persists in `fact_laws.csv:canonical_law_id`; `citation_normalizer` normalizes section refs but does not resolve TMP IDs; no `citation` column. | PARTIAL | **BUILD** (resolution pass + relevance audit) |
| §2.9 | 15.4% dupes; cross-model repeats | agent-result + `(record,agent)` + `payload_hash` dedup exist; no cross-model agreement collapse. | PARTIAL | **BUILD** (cross-model) |
| §2.8 | 9 missing states (WA/OH); NE skew | Ingestion-side; `docs/missing_laws_ingest_queue.csv` already tracks the queue. | GAP | **BUILD** (ingest) |
| §2.11 | modality/sector free text; payload metadata redundancy | `vocab_loader` controlled vocab exists but applied only downstream in rollup; in-payload `_model_id` etc. duplicate columns. | PARTIAL | **BUILD** (enforce at write) |
| §2.5 | dual-model agreement not used | `cross_validation` agent + `cross_validation_score` weight slot exist but are passed `None` into `compute_confidence`. | PARTIAL | **WIRE** |

**Guiding principle from the review, adopted here:** *never expose raw extractions to non-lawyers.* The user-facing surface is the Law Card; everything else is internal. Every card carries an `as_of` date, a currentness status, a confidence/verification badge, and a standing disclaimer — or it does not ship.

---

## Phase 0 — Safety guardrails (stop presenting unreliable/stale data as authoritative)

**Goal:** before improving the data, stop *misrepresenting* it. All four items are low-effort (mostly wiring + UI) and directly prevent the two ways a user gets hurt today: acting on repealed law, and acting on a D-tier guess. **This phase ships first and fast.**

0.1 **Disclaimer everywhere.** Add a standing *"Informational only — not legal advice"* banner to `templates/layout.html`, and a header/preamble row to every CSV/JSONL export (`run_archiver.py`, the concept and low-confidence exports in `dashboard.py`). *(BUILD, trivial.)*

0.2 **Currentness badge (wire existing status through).** Propagate `DocumentVersion.temporal_status` + `effective_date` into `list_concepts`, `concepts.html`, and the concept exports. Render repealed/stayed/vetoed/dead/future-effective concepts with a loud visual flag and an "effective" date column. The grouper already joins the DV — this is a read + display change, no new extraction. *(WIRE.)*

0.3 **Confidence floor on what publishes.** Add an explicit publish gate keyed on `confidence_tier` (e.g. block D-tier-only facts from any card-bound surface) in addition to the existing `review_status`/`is_serving` gate. Make the threshold a config constant. Surface tier alongside *every* material field, not just per row. *(BUILD.)*

0.4 **Provenance gate.** Require at least one `verified: true` evidence span before a fact is eligible for a card; label any field resting on an unverified span. The `verified` flag already exists; this gates on it. *(WIRE.)*

**Exit criteria:** no card-bound surface can show a repealed law as current, a D-tier-only fact as authoritative, or an unverified claim unlabeled; disclaimer present on every page and export.

---

## Phase 1 — Currentness system (the review's decisive finding)

**Goal:** make "is this law still in force, and as of when?" a first-class, materialized property — not something reachable only by a 4-table join that the concept/UI/export layer never performs.

1.1 **New concept columns:** add `effective_date`, `temporal_status`, `amendment_status`, and `as_of_date` to `ComplianceConcept`; populate them in `concept_grouping.py` from the parent `DocumentVersion` + `LegalEvent` log. *(BUILD: migration + grouper write.)*

1.2 **`amendment_status` rollup:** derive a single status string ("in force" / "delayed" / "repealed-replaced" / "stayed") from the existing `LegalEvent` rows and the `cross_reference.py` effective-date/status diff (`cross_reference.py:256–273`) — the comparison logic already exists; it just isn't rolled up or surfaced. *(BUILD: rollup function.)*

1.3 **`as_of_date` / staleness:** stamp each concept/card with the verification date and flag any card older than its review interval (e.g. 90 days). The model already has `retrieved_at`; add a legal-currency `as_of` distinct from fetch time. *(BUILD.)*

1.4 **Currentness verification gate + automation:** the review's decisive example (Colorado SB 205, repealed 14 May 2026, extracted 15 Jun 2026) is exactly what `status_checker.py` is built to catch — but it only runs on a manual button click. Wire it into the run/serving path (or a schedule) so status is refreshed before a card publishes, and emit a conflict flag when Orrick and IAPP disagree on in-force status or effective date. *(WIRE + BUILD: scheduling.)*

1.5 **Fix the dead `iapp_grounded` concept filter** (`concepts.html:131`, `dashboard.py:5257`): the grouper never emits this value at concept level, so the filter matches nothing. Either emit it or remove the option. *(BUILD, trivial.)*

**Exit criteria:** every concept/card answers "in force? effective when? amended since extraction? verified as of when?" without a join, and a repealed-since-extraction law is automatically flagged.

---

## Phase 2 — Business-critical taxonomy fields (penalties, actors, deadlines, safe harbors)

**Goal:** make answerable the four questions a non-lawyer actually asks — *does this apply to me / what must I do / what's the penalty / by when* — from controlled, normalized fields.

2.1 **Penalties (§2.3):** introduce a controlled `penalty_type` enum (civil / criminal / administrative / private-action), and carry `penalty_amount_max` (already exists as `max_civil_penalty_usd`) + `per_unit` (per violation / day / consumer) down to the clause level (today `penalty_per` is bill-level only). Apply the existing `enforcement_canonical_codes.csv`/`enforcement_aliases.csv` to collapse the free-text synonyms. Surface the merged penalty on the card. *(BUILD.)*

2.2 **Actor role (§2.2/§2.10):** add an `actor_role` classification (`regulated_entity` / `government` / `individual` / `third_party`) so a duty on the Attorney General is visibly separated from a duty on a business. Reuse `applicability_agent.government_only` and the `IAPP_SCOPE_TO_ACTORS` taxonomy that already encode this distinction. Populate the near-empty `actor_mapping`/applicability view from obligation subjects. *(BUILD + WIRE.)*

2.3 **Dates (§2.4):** add an ISO-8601 normalizer/validator on clause- and bill-level dates (the deterministic parser already exists in `orrick_facts_parser.py:62`; promote it to the write path so LLM date obedience is no longer load-bearing). *(BUILD.)*

2.4 **Safe harbors (§2.4):** run a targeted re-extraction pass for cure / affirmative-defense / safe-harbor language (currently 1% populated, yet among the highest-value facts). The `SafeHarbor` schema already exists on the obligation payload. *(BUILD: focused pass.)*

**Exit criteria:** penalties filterable/comparable across laws; "duties on you" separated from "duties on the state"; deadlines machine-readable; safe-harbor coverage materially up from 1%.

---

## Phase 3 — The Law Card layer (Deliverables 2–4)

**Goal:** build the user-facing per-law card the review specifies — the layer the codebase currently calls a *"(deferred) law-card builder."* `ComplianceConcept` is the grouping primitive (one requirement-group per law); the Law Card rolls *all* of a law's concepts into one card per Deliverable 2's schema.

3.1 **Law-card builder + route + template:** assemble concepts per `DocumentFamily`/`DocumentVersion` into the Deliverable-2 field set (`law_name`, `citation`, `status`, `effective_date`, `as_of_date`, `amendment_status`, `who_must_comply`, `who_is_exempt`, `applicability_triggers`, `key_obligations`, `deadlines_recurring`, `required_artifacts`, `consumer_rights`, `penalties`, `enforcement_authority`, `safe_harbor`, `risk_level`, `confidence`, `plain_english_summary`, `recommended_next_steps`, `ask_your_lawyer_about`, `sources`). Most inputs now exist after Phases 1–2. *(BUILD.)*

3.2 **Derived fields:** `risk_level` from penalty × scope × applicability (not guessed); `plain_english_summary` (reuse `summary_generator.py`); `ask_your_lawyer_about` from `interpretation_risks` (already extracted); `recommended_next_steps`. *(BUILD.)*

3.3 **QC gates (Deliverable 4) as the publish checklist:** encode the review's hard gates as a pre-publish validator — citation resolved (no `TMP-`), status current, effective_date verified against source as-of-today, `amendment_status` checked, `as_of` stamped, ≥1 A/B-tier-or-dual-model-agreed extraction behind each material claim, penalties carry type+amount+per-unit, every field links to a `verified` span. A card that fails a hard gate does not publish. *(BUILD.)*

**Exit criteria:** a single card per law that passes the Deliverable-4 checklist; the Colorado SB 205 card matches Deliverable 3 (shows repealed-and-replaced, effective 1 Jan 2027, stayed, as-of stamped).

---

## Phase 4 — Corpus quality & hygiene (lower urgency, broad improvement)

**Goal:** raise the quality of the underlying corpus once the safety and product surfaces are sound.

4.1 **Citation resolution + relevance audit (§2.7):** resolve `TMP-` IDs to formal citations; run an AI-relevance precision pass to drop ingested non-AI statutes (nursing-home, real-estate, etc.). *(BUILD.)*

4.2 **Cross-model dedup (§2.9):** collapse byte-identical cross-model extractions into one record with an agreement signal; de-dupe repeated `(law, term)` definitions. Builds on the existing `payload_hash`. *(BUILD.)*

4.3 **Coverage backfill (§2.8):** ingest the 9 missing jurisdictions (notably WA, OH) from `docs/missing_laws_ingest_queue.csv`; track per-law record counts to detect the Nebraska-style skew. *(BUILD.)*

4.4 **Controlled vocab at write time (§2.11):** enforce `modality` and `sector_applicability` enums during extraction (vocab exists in `vocab_loader`, applied only downstream today); drop in-payload `_model_id`/`_prompt_hash`/`evidence_spans` duplicates in favor of the columns. *(BUILD.)*

4.5 **Wire dual-model agreement (§2.5):** pass `cross_validation_score` into `compute_confidence` (the agent and weight slot already exist) so A-tier can require agreement. *(WIRE.)*

4.6 **Evidence URLs/anchors (§2.6):** add `source_url` + `section_anchor` to evidence spans so attribution becomes citation. *(BUILD.)*

**Exit criteria:** corpus de-duplicated and relevance-audited, missing states backfilled, controlled vocab enforced at the source, spans carry pinpoint citations.

---

## Sequencing & rationale

- **Phase 0 is the priority and ships independently** — it is the only phase that addresses *active user harm*, and it is mostly wiring, so it lands in days.
- **Phases 1–2 close the decisive currentness gap and fill the business-critical fields** — these are what make a card *trustworthy*.
- **Phase 3 builds the product** the review actually wants, and can only be done well after 1–2 supply its inputs.
- **Phase 4 is corpus quality** — important, but it improves a surface that Phases 0–3 have already made safe.

The single most important takeaway: the system is closer to the review's target than the `test.csv` suggests. The fastest path to "safe to put in front of a business owner" is **Phase 0 wiring**, not a rebuild.
