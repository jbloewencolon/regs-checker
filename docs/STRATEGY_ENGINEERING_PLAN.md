# Regs Checker Strategy and Engineering Implementation Plan

## Document status

**Audience:** product, engineering, legal review, data science, policy, and implementation teams  
**Purpose:** provide a comprehensive strategy and engineering roadmap for turning Regs Checker into a reliable business-facing AI law card and policy intelligence platform.  
**Scope:** U.S. state and federal artificial intelligence law and policy, with emphasis on business applicability, structured obligations, human review, source provenance, and product-serving APIs.  
**Review basis:** static repository review of the current Regs Checker architecture, schemas, extraction agents, API routes, confidence scoring, verification logic, seed data, and setup documentation.  
**Runtime status:** this document does not claim that the current pipeline has been executed or benchmarked. All recommendations should be validated in a development environment before production implementation.

---

## 1. Executive summary

Regs Checker is already a substantial regulatory extraction platform. It includes local legal-text ingestion, passage-level extraction agents, bill-level extraction agents, Postgres persistence, human review queues, Supabase synchronization, confidence scoring, a dashboard, and a downstream Policy Navigator target database.

The next phase is not a full rebuild. The next phase is controlled hardening and productization.

The platform should evolve from an extraction pipeline into a two-layer regulatory intelligence system:

1. **Legal extraction infrastructure:** ingest authoritative legal sources, extract structured legal claims, preserve provenance, validate evidence, support human and legal review, version every run, and maintain auditable legal data.
2. **Policy card product layer:** convert reviewed legal data into business-facing law cards, applicability decisions, compliance actions, risk flags, source trails, and state-by-state comparisons.

The immediate goal is to make the system safe for internal policy research and team use. The medium-term goal is to make it reliable enough to power law cards. The long-term goal is a production-grade AI policy intelligence product for businesses, policymakers, and compliance teams.

---

## 2. North star product vision

Regs Checker should answer five practical questions for a business or policymaker:

1. **What does this state AI law or policy do?**
2. **Does it apply to my organization, AI product, sector, or use case?**
3. **What obligations, rights, deadlines, exceptions, and penalties matter?**
4. **What evidence supports each claim, and what is still ambiguous?**
5. **What should product, legal, data science, procurement, and compliance teams do next?**

A good law card should not merely summarize a law. It should translate legal text into operational decisions, while preserving enough source evidence for counsel and policy teams to verify the claim.

---

## 3. Current system strengths

### 3.1 Strong extraction architecture

The current pipeline has a clear staged architecture:

1. seed laws from CSV and ingest local sources;
2. triage passages for AI relevance;
3. run extraction agents;
4. review extractions;
5. generate summaries;
6. sync to Regs Checker Supabase;
7. sync to Policy Navigator Supabase;
8. roll up matrix tables.

This is the right general structure. It separates ingestion, extraction, review, presentation, and serving.

### 3.2 Good agent taxonomy

The agent architecture already reflects key categories needed for law cards:

- obligations;
- definitions and actor mappings;
- thresholds and exceptions;
- rights and protections;
- compliance mechanisms;
- preemption and cross-law conflict signals;
- bill-level enforcement;
- bill-level applicability;
- bill-level compliance timelines.

This is well aligned with the eventual product taxonomy.

### 3.3 Structured legal payloads

The Pydantic schemas are a major asset. They create structured payloads for obligations, timelines, enforcement, safe harbors, consent requirements, rights, compliance mechanisms, thresholds, exceptions, interpretation risk, and preemption.

The product should build on these schemas rather than replace them. The key update is to add a product-layer law card schema above them.

### 3.4 Provenance and auditability foundation

The database already includes source records, raw artifacts, document versions, extractions, review queues, review actions, legal events, dependencies, applicability conditions, export jobs, triage results, failed extraction attempts, and bill-level extractions.

This is a strong foundation, but it needs run versioning and non-destructive historical preservation before the platform can be trusted for legal intelligence.

### 3.5 Evidence span verification

The base agent verifies evidence spans against the source passage using normalized text matching. This is essential. Every business-facing claim should eventually trace back to a verified source span.

---

## 4. Critical risks and remediation priorities

This section defines the highest-priority implementation issues. These should be treated as release blockers for any external-facing or business-facing use.

### 4.1 P0: verification agents appear to use outdated provider return handling

#### Issue

The LLM provider returns an `LLMResponse` object, but cross-validation and gap detection appear to unpack the result as a tuple. This likely causes verification failures. Because those functions catch broad exceptions, failures may be hidden behind neutral fallback results.

#### Risk

The platform may claim a three-layer verification process while two major verification layers silently fail or return neutral results.

#### Engineering action

Update all verification agents to use the provider response object:

```python
response = provider.call(
    system_prompt=system_prompt,
    user_prompt=prompt,
    model_override="openai/gpt-oss-20b",
)

raw_output = response.text
usage = response.usage
model_id = response.model_id
stop_reason = response.stop_reason
```

#### Acceptance criteria

- Unit tests prove `run_cross_validation()` succeeds with a mocked `LLMResponse`.
- Unit tests prove `run_gap_detection()` succeeds with a mocked `LLMResponse`.
- Failed verification does not return an apparently valid result.
- Verification failures are visible in logs, database status, and dashboard.
- A verification failure cannot improve an extraction confidence tier.

---

### 4.2 P0: verification does not appear to update persisted confidence tiers

#### Issue

Confidence is computed during extraction, before verification. Later verification results appear to be written into metadata but do not appear to trigger recomputation of persisted `confidence_score` and `confidence_tier`.

#### Risk

The confidence model may not reflect verification results. This undermines the meaning of confidence tiers served by the API and dashboard.

#### Engineering action

Add a persistent verification table and recompute confidence after verification.

Recommended table: `verification_results`

Suggested fields:

| Field | Type | Notes |
|---|---|---|
| `id` | integer | primary key |
| `extraction_id` | integer | FK to `extractions` |
| `run_id` | integer | FK to `extraction_runs`, after run table exists |
| `verification_type` | text | `cross_validation`, `gap_detection`, `citation_verification`, `human_review`, `legal_review` |
| `status` | text | `passed`, `flagged`, `failed`, `skipped` |
| `score` | float | 0.0 to 1.0 |
| `issues` | jsonb | structured issue list |
| `model_id` | text | model used, if any |
| `input_tokens` | integer | optional |
| `output_tokens` | integer | optional |
| `created_at` | timestamp | audit trail |

Add `pre_verification_confidence_score`, `post_verification_confidence_score`, and `verification_state`, or store confidence history in a separate `confidence_assessments` table.

#### Acceptance criteria

- Running verification changes confidence scores when verification flags a material issue.
- The API can return both extraction-time confidence and post-verification confidence.
- Dashboard clearly shows `not verified`, `verified`, `flagged`, and `verification failed` states.
- A failed verification step does not create a misleading confidence boost.

---

### 4.3 P0: full extraction runs delete prior review and extraction history

#### Issue

The pipeline currently purges extractions, review actions, review queue items, extraction jobs, dependencies, applicability conditions, and failed attempts during full extraction runs.

#### Risk

This undermines legal defensibility, auditability, reproducibility, and reviewer accountability. A legal intelligence product must preserve the reasoning and review trail behind claims.

#### Engineering action

Replace destructive purge behavior with run versioning.

Add table: `extraction_runs`

Suggested fields:

| Field | Type | Notes |
|---|---|---|
| `id` | integer | primary key |
| `run_label` | text | human-readable label |
| `run_type` | text | `triage`, `extract`, `verify`, `sync`, `law_card_build` |
| `status` | text | `running`, `completed`, `failed`, `cancelled`, `archived` |
| `is_serving_run` | boolean | only one active serving run per environment |
| `started_at` | timestamp | required |
| `completed_at` | timestamp | nullable |
| `git_sha` | text | repo state |
| `prompt_versions` | jsonb | prompt template versions |
| `model_config` | jsonb | model IDs and settings |
| `source_snapshot_hash` | text | hash of source corpus snapshot |
| `summary` | jsonb | counts and diagnostics |
| `created_by` | text | user or system |

Add `run_id` to:

- `extractions`;
- `review_queue`;
- `review_actions`;
- `failed_extraction_attempts`;
- `bill_level_extractions`;
- `obligation_dependencies`;
- `applicability_conditions`;
- `verification_results`;
- future `law_cards`.

#### Acceptance criteria

- Full extraction runs no longer delete historical data.
- A run can be marked as the active serving run.
- Previous runs can be compared against the active run.
- Reviewer decisions remain attached to the run they reviewed.
- There is an admin-only archival workflow, not a normal destructive purge.

---

### 4.4 P0: route protection and reviewer identity are insufficient for legal review workflows

#### Issue

The internal review route accepts reviewer identity from the request body and directly applies corrections to extraction payloads. The visible route code does not show authentication or role-based authorization for internal or product endpoints.

#### Risk

Legal review decisions can be spoofed, overwritten, or applied without sufficient validation. This creates serious governance and product-trust risks.

#### Engineering action

Implement authentication and authorization.

Recommended roles:

| Role | Permissions |
|---|---|
| `viewer` | read cards and reviewed extractions |
| `analyst` | inspect unreviewed extractions and verification reports |
| `reviewer` | approve, reject, or revise technical extraction payloads |
| `legal_reviewer` | mark card or obligation as legally reviewed |
| `admin` | manage runs, users, source refreshes, and serving-run promotion |

Requirements:

- Reviewer identity must come from auth context, not request body.
- Corrections must be schema-validated before approval.
- Legal review status must be separate from technical review status.
- Write actions must produce immutable audit-log entries.
- Expensive operations should use POST routes, not GET routes.

#### Acceptance criteria

- Unauthorized users cannot access `/internal` or dashboard review actions.
- Reviewer names cannot be spoofed through request payloads.
- Invalid corrected payloads are rejected.
- Every review action has timestamp, reviewer, previous payload hash, new payload hash, and comment.
- `/v1/verification` or equivalent verification execution endpoint is POST-only.

---

## 5. Product architecture target state

### 5.1 Two-layer architecture

```text
Official legal sources and secondary trackers
        |
        v
Source ingestion and normalization
        |
        v
Passage triage and bill context builder
        |
        v
Extraction agents and bill-level agents
        |
        v
Evidence verification, citation verification, cross-validation, gap detection
        |
        v
Human review and legal review
        |
        v
Reviewed legal extraction store
        |
        v
Law card builder
        |
        v
Policy card API, applicability engine, dashboard, and exports
```

### 5.2 Platform layers

| Layer | Main responsibility |
|---|---|
| Source layer | retrieve, hash, parse, version, and certify source text |
| Extraction layer | produce structured legal payloads from source text |
| Verification layer | test evidence, citations, completeness, and cross-model consistency |
| Review layer | allow humans and legal reviewers to approve or correct extracted claims |
| Product layer | generate law cards, business actions, risk summaries, and applicability decisions |
| Serving layer | expose stable APIs, dashboard views, exports, and syncs |

---

## 6. Law card product model

### 6.1 Why law cards need their own data model

A law card should not be a loose aggregation of extraction rows at request time. It should be a curated product artifact with its own lifecycle, review state, source evidence, and version history.

The extraction layer answers: **what was found in the legal text?**

The law card layer answers: **what should a business, policymaker, or compliance team understand and do?**

### 6.2 Proposed tables

#### `law_cards`

| Field | Type | Description |
|---|---|---|
| `id` | integer | primary key |
| `canonical_law_id` | text | stable law identifier |
| `document_version_id` | integer | FK to source law version |
| `run_id` | integer | FK to build run |
| `state_code` | text | `CA`, `CO`, etc. |
| `jurisdiction_name` | text | full state or federal name |
| `law_name` | text | public title |
| `bill_number` | text | bill number where applicable |
| `citation` | text | codified citation if available |
| `status` | text | introduced, pending, enacted, effective, delayed, repealed, stayed, litigated |
| `effective_date` | date | nullable |
| `enforcement_start_date` | date | nullable |
| `plain_summary` | text | executive summary |
| `business_relevance_summary` | text | why businesses should care |
| `legal_review_status` | text | not reviewed, reviewed, approved, rejected |
| `technical_review_status` | text | extraction review status |
| `confidence_score` | float | product-level confidence |
| `ambiguity_level` | text | low, medium, high, critical |
| `last_reviewed_at` | timestamp | nullable |
| `created_at` | timestamp | required |
| `updated_at` | timestamp | required |

#### `law_card_applicability`

| Field | Type | Description |
|---|---|---|
| `id` | integer | primary key |
| `law_card_id` | integer | FK |
| `covered_entities` | jsonb | developer, deployer, employer, platform, etc. |
| `covered_ai_systems` | jsonb | ADS, generative AI, biometric system, etc. |
| `covered_sectors` | jsonb | employment, health, housing, etc. |
| `covered_decision_types` | jsonb | consequential, high-risk, consumer-facing, etc. |
| `thresholds` | jsonb | revenue, employees, users, compute, time, geography |
| `exemptions` | jsonb | carve-outs and exclusions |
| `evidence_extraction_ids` | jsonb | source extraction references |

#### `law_card_obligations`

| Field | Type | Description |
|---|---|---|
| `id` | integer | primary key |
| `law_card_id` | integer | FK |
| `obligation_type` | text | notice, audit, assessment, reporting, etc. |
| `regulated_party` | text | who must comply |
| `action_required` | text | what must be done |
| `trigger_condition` | text | when it applies |
| `deadline` | text | raw or normalized deadline |
| `evidence_extraction_id` | integer | FK to extraction |
| `confidence_score` | float | obligation-level confidence |
| `review_status` | text | technical and legal review state |

#### `law_card_enforcement`

| Field | Type | Description |
|---|---|---|
| `id` | integer | primary key |
| `law_card_id` | integer | FK |
| `enforcing_body` | text | AG, agency, court, regulator |
| `penalty_type` | text | civil, criminal, injunctive, administrative |
| `max_penalty_usd` | integer | nullable |
| `penalty_unit` | text | per violation, per day, etc. |
| `cure_period_days` | integer | nullable |
| `private_right_of_action` | boolean | nullable |
| `safe_harbor` | jsonb | framework or conditions |
| `evidence_extraction_ids` | jsonb | source extraction references |

#### `law_card_business_actions`

| Field | Type | Description |
|---|---|---|
| `id` | integer | primary key |
| `law_card_id` | integer | FK |
| `team` | text | legal, product, data science, security, procurement, HR, compliance |
| `action_type` | text | assess, document, disclose, monitor, contract, test, review |
| `action_text` | text | plain-English action |
| `priority` | text | low, medium, high, urgent |
| `deadline` | text | nullable |
| `source_obligation_id` | integer | FK to law card obligation or extraction |

#### `law_card_risk_scores`

| Field | Type | Description |
|---|---|---|
| `id` | integer | primary key |
| `law_card_id` | integer | FK |
| `applicability_likelihood` | text | low, medium, high |
| `obligation_burden` | text | low, medium, high |
| `enforcement_risk` | text | low, medium, high |
| `deadline_urgency` | text | low, medium, high |
| `ambiguity_level` | text | low, medium, high, critical |
| `documentation_burden` | text | low, medium, high |
| `rationale` | jsonb | explainable scoring factors |

---

## 7. Product taxonomy

The extraction schemas should be preserved. Add a normalized business taxonomy above them.

### 7.1 Source taxonomy

- statute;
- bill;
- regulation;
- agency guidance;
- attorney general advisory;
- executive order;
- procurement policy;
- enforcement action;
- litigation order;
- settlement;
- model policy;
- secondary tracker.

### 7.2 AI system taxonomy

- automated decision system;
- high-risk AI system;
- consequential decision system;
- generative AI;
- synthetic media;
- deepfake;
- biometric system;
- facial recognition;
- recommender system;
- pricing algorithm;
- chatbot;
- AI agent;
- foundation model;
- frontier model;
- data broker AI training use;
- algorithmic utilization review;
- automated employment decision tool.

### 7.3 Business role taxonomy

- developer;
- deployer;
- provider;
- vendor;
- distributor;
- platform;
- employer;
- insurer;
- lender;
- health plan;
- public agency;
- contractor;
- data broker;
- controller;
- processor;
- social media platform;
- political committee;
- real estate broker;
- school or educational provider.

### 7.4 Use-case taxonomy

- employment;
- housing;
- credit;
- insurance;
- health care;
- education;
- criminal justice;
- public benefits;
- elections;
- consumer services;
- child safety;
- intimate images;
- likeness and publicity rights;
- real estate;
- pricing and competition;
- government procurement;
- training-data transparency;
- data broker disclosures;
- online platform moderation.

### 7.5 Obligation taxonomy

- notice;
- disclosure;
- consent;
- opt-out;
- human review;
- appeal;
- explanation;
- correction;
- deletion;
- impact assessment;
- bias audit;
- risk assessment;
- red teaming;
- record retention;
- reporting;
- registration;
- data provenance;
- training-data summary;
- watermarking;
- content labeling;
- takedown;
- incident reporting;
- vendor due diligence;
- prohibited use;
- safe harbor compliance;
- consumer complaint process.

---

## 8. Business applicability engine

### 8.1 Purpose

The applicability engine should determine whether a law card is likely relevant to a business based on facts about the organization, system, state, users, sector, and use case.

The engine should never pretend to provide legal advice. It should output structured applicability triage:

- likely applicable;
- possibly applicable;
- unlikely applicable;
- not enough information;
- excluded by exemption;
- needs counsel review.

### 8.2 Business intake schema

```json
{
  "states": ["CA", "CO", "UT"],
  "business_roles": ["developer", "deployer"],
  "sectors": ["employment"],
  "system_types": ["automated_decision_system"],
  "uses_personal_data": true,
  "uses_sensitive_data": true,
  "uses_biometric_data": false,
  "consumer_facing": true,
  "decision_impact": "high",
  "annual_revenue_usd": 25000000,
  "employee_count": 120,
  "consumer_count": 50000,
  "public_sector_vendor": false,
  "generates_synthetic_media": false,
  "uses_training_data_from_consumers": false,
  "human_review_available": true,
  "current_documentation": ["model_card", "data_inventory"]
}
```

### 8.3 Output schema

```json
{
  "result_id": "...",
  "jurisdictions_checked": ["CA", "CO", "UT"],
  "likely_applicable": [
    {
      "law_card_id": 123,
      "law_name": "Example AI Law",
      "state": "CO",
      "why": ["business is a deployer", "system is used in employment", "decision impact is high"],
      "required_actions": ["complete impact assessment", "provide notice", "maintain records"],
      "missing_facts": [],
      "confidence": "high"
    }
  ],
  "possibly_applicable": [],
  "unlikely_applicable": [],
  "needs_counsel_review": []
}
```

### 8.4 Implementation approach

Start with deterministic matching, not LLM-only reasoning.

Rules should compare business facts against normalized law card fields:

- state and jurisdiction;
- covered entities;
- covered roles;
- covered AI system types;
- covered sectors;
- thresholds;
- exemptions;
- effective dates;
- enforcement status;
- pending or enacted status.

Use LLMs only for:

- explaining the result in plain language;
- asking follow-up questions when facts are missing;
- mapping ambiguous business descriptions to taxonomy terms.

### 8.5 Acceptance criteria

- The engine can explain why each law was included or excluded.
- Every applicability result links to law-card fields and source evidence.
- Missing facts are explicitly listed.
- The same input produces deterministic results.
- LLM explanations cannot override deterministic applicability logic.

---

## 9. Confidence, review, and trust model

### 9.1 Replace the hard secondary-source gate

The current Orrick-gated confidence approach should be replaced with a broader source-confidence model. Orrick and IAPP are useful but should not function as hard legal ground truth.

Recommended product confidence components:

| Signal | Suggested weight |
|---|---:|
| Official source available and parsed | 20 percent |
| Evidence spans verified | 20 percent |
| Citation verification passed | 10 percent |
| Cross-validation passed | 10 percent |
| Gap detection found no high-confidence gaps | 10 percent |
| Human technical review approved | 10 percent |
| Legal review approved | 15 percent |
| Secondary-source agreement | 5 percent |

### 9.2 Confidence states

Use both score and state.

| State | Meaning |
|---|---|
| `unverified` | extraction exists but verification not run |
| `machine_verified` | evidence and automated checks passed |
| `human_reviewed` | technical reviewer approved |
| `legal_reviewed` | legal reviewer approved |
| `flagged` | material issue found |
| `stale` | source law or bill has changed since review |
| `superseded` | newer extraction or card version exists |

### 9.3 Human review workflow

Recommended review states:

1. pending technical review;
2. technical approved;
3. technical rejected;
4. needs revision;
5. pending legal review;
6. legal approved;
7. legal rejected;
8. stale after source change;
9. superseded by new run.

### 9.4 Review UI requirements

Each review item should show:

- source passage;
- extraction payload;
- evidence spans;
- verified and unverified evidence markers;
- section path;
- official source URL;
- model ID;
- prompt version;
- confidence breakdown;
- verification results;
- similar or duplicate extractions;
- suggested law-card placement;
- correction editor with schema validation;
- reviewer comments;
- legal-review checklist.

---

## 10. Source ingestion and legal corpus strategy

### 10.1 Source hierarchy

The product should use this hierarchy:

1. official enacted law or codified statute;
2. official enrolled bill text;
3. official bill version;
4. official agency regulation or guidance;
5. official attorney general guidance;
6. official enforcement action, settlement, or court order;
7. secondary tracker, such as law firm or professional association tracker;
8. manually added research notes.

### 10.2 Source metadata requirements

For every source artifact:

- source type;
- official or secondary status;
- URL;
- retrieval date;
- content hash;
- document version;
- jurisdiction;
- legal status;
- parse quality;
- OCR status;
- reviewer validation status;
- relationship to prior version;
- relationship to downstream law card.

### 10.3 Source freshness

Add scheduled source checks:

- bill status changed;
- law enacted;
- effective date changed;
- amendment added;
- court stay or injunction added;
- AG guidance added;
- rulemaking opened;
- rulemaking finalized;
- federal preemption risk added.

### 10.4 Acceptance criteria

- Every production law card has at least one official source or an explicit `official_source_missing` flag.
- Secondary-source-only cards are not legal-approved without human confirmation.
- Source freshness checks can mark law cards as stale.
- A stale card is not served as final without a visible warning.

---

## 11. API strategy

### 11.1 Current API direction

The current `/v1` API serves obligations, individual obligations, dependencies, matrix data, changes, completeness, and verification. This should be preserved for internal and advanced users, but product endpoints should be added.

### 11.2 New product endpoints

#### Law card endpoints

```http
GET /v1/law-cards
GET /v1/law-cards/{law_card_id}
GET /v1/states/{state_code}/law-cards
GET /v1/states/{state_code}/ai-policy-summary
```

#### Applicability endpoints

```http
POST /v1/applicability-check
POST /v1/applicability-check/explain
```

#### Evidence endpoints

```http
GET /v1/law-cards/{law_card_id}/evidence
GET /v1/extractions/{extraction_id}/source
```

#### Change intelligence endpoints

```http
GET /v1/changes
GET /v1/law-cards/{law_card_id}/history
GET /v1/law-cards/{law_card_id}/diff?from_run_id=...&to_run_id=...
```

#### Review and internal endpoints

Internal review endpoints should remain under `/internal`, protected by auth and roles.

### 11.3 API response principles

- Every business-facing claim should include traceable evidence IDs.
- Every law card should include review and confidence state.
- Ambiguity should be explicit.
- Pending or non-effective laws should be clearly marked.
- API should distinguish legal status from product readiness.

### 11.4 Acceptance criteria

- API endpoints have stable Pydantic response schemas.
- OpenAPI documentation clearly separates product endpoints and internal endpoints.
- API key scopes control access.
- Product endpoints never serve unreviewed cards as if reviewed.
- Pagination, filtering, and sorting are tested.

---

## 12. Engineering implementation roadmap

## Phase 0: Stabilization and safety fixes

### Goal

Make the current system honest, internally reliable, non-destructive, and safe for team use.

### Workstream A: verification repair

Tasks:

- Fix provider response handling in cross-validation.
- Fix provider response handling in gap detection.
- Add JSON repair reuse or shared parser for verification outputs.
- Add tests for verifier success, malformed JSON, provider error, and empty model response.
- Change failure fallback from neutral approval to explicit failure state.

Acceptance criteria:

- Verification agents run successfully with mocked provider responses.
- Failed verification is visible and cannot raise confidence.
- Verification results are persisted.

### Workstream B: confidence recomputation

Tasks:

- Add `verification_results` table.
- Persist cross-validation scores, gap candidates, and citation verification results.
- Recompute confidence after verification.
- Store confidence history.

Acceptance criteria:

- Confidence changes after verification.
- Dashboard and API expose verification state.

### Workstream C: non-destructive runs

Tasks:

- Add `extraction_runs` table.
- Add `run_id` to relevant tables.
- Replace destructive purge with new run creation.
- Add active serving run concept.

Acceptance criteria:

- Old review actions and extractions survive a full run.
- Active serving run can be promoted or rolled back.

### Workstream D: security and review governance

Tasks:

- Add auth dependency to `/internal`.
- Add API key auth to `/v1`.
- Hide docs in production or protect them.
- Add role-based review permissions.
- Use authenticated identity for reviewer field.
- Validate corrections before applying.

Acceptance criteria:

- Unauthorized write actions fail.
- Review actions are attributable and immutable.

### Phase 0 deliverable

Internal alpha suitable for trusted team use, with clear warnings for unverified data.

---

## Phase 1: Authoritative source foundation

### Goal

Make official legal sources the product’s source of truth.

### Workstream A: source model

Tasks:

- Add source artifact status fields.
- Distinguish official and secondary sources.
- Add source retrieval history.
- Add content hash snapshots.
- Add legal status events.

### Workstream B: official source resolution

Tasks:

- Build connectors or manual workflows for official state legislative pages.
- Map bill numbers to enacted laws and codified statutes where possible.
- Preserve secondary trackers as reference metadata.

### Workstream C: freshness monitor

Tasks:

- Add source refresh jobs.
- Add stale-card detection.
- Add change feed for source changes.

### Acceptance criteria

- Every reviewed law card has official source status.
- Secondary-only cards are flagged.
- Source changes can mark cards stale.

### Phase 1 deliverable

Source-certified legal corpus.

---

## Phase 2: Gold-standard evaluation and accuracy benchmarking

### Goal

Measure extraction and law-card accuracy before scaling.

### Workstream A: gold-standard set

Create a hand-labeled evaluation corpus of 25 to 50 laws across:

- California;
- Colorado;
- Utah;
- Texas;
- Illinois;
- New York;
- Connecticut;
- New Jersey;
- election deepfake laws;
- employment automated decision laws;
- health care AI laws;
- generative AI transparency laws;
- biometric laws;
- insurance and lending laws.

### Workstream B: labels

For each law, label:

- covered entities;
- covered AI systems;
- covered sectors;
- trigger conditions;
- obligations;
- rights;
- deadlines;
- enforcement;
- penalties;
- private right of action;
- cure period;
- exceptions;
- safe harbors;
- evidence spans;
- ambiguity notes.

### Workstream C: metrics

Track:

- precision;
- recall;
- field-level accuracy;
- evidence-span verification rate;
- citation verification rate;
- false positive rate;
- false negative rate;
- confidence calibration;
- reviewer correction rate;
- time per reviewed card;
- model and prompt performance.

### Acceptance criteria

- Evaluation can run from CLI and CI.
- Results are written to an evaluation report.
- Prompt or model changes can be compared against baseline.
- No production prompt changes without benchmark regression check.

### Phase 2 deliverable

Evaluation harness and benchmark dashboard.

---

## Phase 3: Law card builder

### Goal

Create first-class business-facing law cards from reviewed legal extractions.

### Workstream A: law card schema and tables

Tasks:

- Add `law_cards` and related tables.
- Add mapping logic from extraction types to law card sections.
- Add card build run tracking.
- Preserve source extraction IDs behind each card section.

### Workstream B: deterministic summaries

Tasks:

- Build deterministic summary templates.
- Use LLMs only for optional drafting, not authoritative facts.
- Require source evidence for all legal claims.

### Workstream C: law card review

Tasks:

- Add technical review for card assembly.
- Add legal review for final card approval.
- Add stale and superseded states.

### Acceptance criteria

- A card can be generated for a selected law.
- Every card claim links to source evidence.
- Legal-review status is visible.
- Cards can be rebuilt without deleting old versions.

### Phase 3 deliverable

Policy card MVP.

---

## Phase 4: Business applicability and control mapping

### Goal

Help organizations understand which laws apply and what they should do.

### Workstream A: applicability engine

Tasks:

- Add business intake schema.
- Add deterministic matching rules.
- Add missing-facts detection.
- Add explainable inclusion and exclusion logic.

### Workstream B: control mapping

Tasks:

Map law-card obligations to business controls:

| Obligation | Business control |
|---|---|
| notice | UX disclosure component |
| impact assessment | AI impact assessment workflow |
| bias audit | subgroup evaluation and adverse impact testing |
| record retention | evidence archive and audit logs |
| human review | escalation workflow |
| appeal | consumer or applicant recourse process |
| vendor due diligence | vendor questionnaire and contract clauses |
| training-data disclosure | dataset inventory and provenance report |

### Workstream C: product output

Tasks:

- Add applicability report endpoint.
- Add downloadable checklist.
- Add team-specific actions.
- Add risk summary.

### Acceptance criteria

- Same input produces same applicability result.
- Every result explains why it was included or excluded.
- Missing facts are listed.
- LLM explanations cannot override deterministic logic.

### Phase 4 deliverable

Business decision engine.

---

## Phase 5: Productionization

### Goal

Operate the platform reliably for external or semi-external users.

### Workstream A: infrastructure

Tasks:

- Add background job queue.
- Add staged environments: dev, staging, production.
- Add database backups and restore drills.
- Add source artifact storage policy.
- Add rate limiting.
- Add observability and alerting.

### Workstream B: API and access

Tasks:

- Add API key scopes.
- Add organization-level access.
- Add usage logs.
- Add export limits.
- Add OpenAPI client generation.

### Workstream C: operations

Tasks:

- Add runbook.
- Add incident response plan.
- Add source refresh schedule.
- Add reviewer assignment workflows.
- Add release checklist.

### Acceptance criteria

- Failed jobs are visible and retryable.
- Production data can be restored.
- API has scoped access and rate limits.
- Legal review and source freshness are operationally monitored.

### Phase 5 deliverable

Production-grade regulatory intelligence platform.

---

## 13. Testing strategy

### 13.1 Unit tests

Add tests for:

- provider response handling;
- JSON repair;
- evidence span verification;
- confidence scoring;
- citation verification;
- taxonomy normalization;
- applicability matching rules;
- review correction validation;
- API filters and pagination.

### 13.2 Integration tests

Add tests for:

- ingest one fixture law;
- triage passages;
- extract with mocked LLM;
- verify with mocked LLM;
- approve extraction;
- build law card;
- run applicability check.

### 13.3 Golden-file tests

For each gold-standard law:

- expected obligations;
- expected covered entities;
- expected deadlines;
- expected enforcement fields;
- expected evidence spans;
- expected law-card sections.

### 13.4 Regression tests

Every prompt or model change should produce a benchmark report comparing:

- precision;
- recall;
- field-level accuracy;
- evidence verification;
- confidence calibration;
- token usage;
- runtime.

---

## 14. Data governance and legal review policy

### 14.1 Legal status labels

Every law card should display:

- legal status;
- product review status;
- source status;
- confidence status;
- last reviewed date;
- stale or superseded warning where applicable.

### 14.2 Human review separation

Separate technical and legal review:

| Review type | Question |
|---|---|
| Technical review | Did the extraction correctly capture the source text? |
| Legal review | Is the interpretation appropriate for business-facing guidance? |
| Product review | Is the card clear and usable? |

### 14.3 Disclaimers

The product should clearly state that it provides legal information and regulatory intelligence, not legal advice. However, the disclaimer should not become an excuse for weak evidence, stale data, or vague claims.

---

## 15. Immediate implementation backlog

### Week 1: critical repairs

- [ ] Fix provider response handling in cross-validation.
- [ ] Fix provider response handling in gap detection.
- [ ] Add tests for both functions.
- [ ] Change verifier failure behavior to explicit failure.
- [ ] Remove persisted model reasoning.
- [ ] Correct README and setup model drift.
- [ ] Add auth guard scaffolding for `/internal`.

### Week 2: confidence and verification persistence

- [ ] Add `verification_results` migration.
- [ ] Persist cross-validation, gap detection, and citation verification outputs.
- [ ] Recompute confidence after verification.
- [ ] Add dashboard display for verification status.
- [ ] Add tests for confidence recomputation.

### Week 3: run versioning

- [ ] Add `extraction_runs` table.
- [ ] Add `run_id` to extractions and review tables.
- [ ] Replace full-run purge with new run creation.
- [ ] Add active serving run promotion.
- [ ] Add run comparison skeleton.

### Week 4: law-card schema

- [ ] Add `law_cards` table.
- [ ] Add law-card obligation, applicability, enforcement, business-action, and risk-score tables.
- [ ] Build first deterministic law-card builder.
- [ ] Add `GET /v1/law-cards/{id}`.
- [ ] Generate 3 pilot law cards.

### Weeks 5 to 6: gold standard and source certification

- [ ] Select 25 law evaluation set.
- [ ] Create manual labels.
- [ ] Build evaluation harness.
- [ ] Add official-source status fields.
- [ ] Add source freshness monitor prototype.

### Weeks 7 to 8: applicability MVP

- [ ] Add business intake schema.
- [ ] Add deterministic applicability engine.
- [ ] Add `POST /v1/applicability-check`.
- [ ] Add explainable outputs.
- [ ] Add business action checklist.

---

## 16. Definition of done for policy-card readiness

The platform is ready to support business-facing policy cards when:

1. every production card has official source provenance or an explicit warning;
2. every legal claim links to evidence;
3. extraction confidence reflects verification and review;
4. prior runs and review history are preserved;
5. technical review and legal review are separate;
6. source freshness can mark cards stale;
7. benchmark accuracy is measured and visible;
8. applicability decisions are deterministic and explainable;
9. API access is authenticated and scoped;
10. product language clearly distinguishes legal information from legal advice.

---

## 17. Recommended team roles

| Role | Responsibilities |
|---|---|
| Technical lead | architecture, migrations, API, run versioning, production readiness |
| Data scientist | extraction evaluation, gold standard, confidence calibration, prompt and model testing |
| Legal analyst | legal taxonomy, source validation, legal review protocol, card accuracy |
| Product lead | user workflows, card design, applicability report, prioritization |
| Backend engineer | database models, endpoints, job orchestration, auth |
| Frontend or dashboard engineer | review UI, law-card UI, run dashboard, verification dashboard |
| DevOps engineer | environments, backups, observability, scheduled source refresh |
| Policy advisor | state law prioritization, stakeholder review, policy interpretation context |

---

## 18. Open questions for the team

1. Which users are primary for MVP: business compliance teams, policymakers, startup founders, or internal analysts?
2. Should law cards include pending bills, enacted laws only, or both with different labels?
3. What level of legal review is required before a card is product-visible?
4. What are the first 10 priority states?
5. What are the first 5 priority use cases?
6. Which official source APIs or scraping approaches are acceptable?
7. What level of source freshness is required: daily, weekly, or manual release cycles?
8. What is the acceptable false-negative rate for missed obligations?
9. Should the system support customer-specific saved applicability profiles?
10. Should law-card outputs be exportable as PDF, CSV, JSON, or all three?

---

## 19. Suggested MVP scope

The first product MVP should focus on:

### States

- California;
- Colorado;
- Utah;
- Texas;
- Illinois;
- New York;
- Connecticut;
- New Jersey.

### Use cases

- employment automated decision systems;
- consumer-facing generative AI chatbots;
- health care AI;
- AI-generated political media and deepfakes;
- biometric and intimate image laws;
- training-data transparency.

### Product outputs

- law card;
- evidence trail;
- business applicability result;
- compliance action checklist;
- change and stale-card alerts.

---

## 20. Final recommendation

Do not treat Regs Checker as merely an extraction script. Treat it as the foundation of a regulatory intelligence product.

The immediate engineering priority is trust: fix verification, preserve history, authenticate review, and source-certify the corpus. The next product priority is law-card generation. The strategic priority is business applicability: making the product answer whether a law applies and what an organization should do next.

The recommended sequence is:

1. stabilize;
2. source-certify;
3. evaluate;
4. build law cards;
5. add applicability logic;
6. productionize.

That sequence reduces legal risk, preserves the value of the existing architecture, and turns the repo into a credible policy intelligence platform rather than a black-box legal summarizer.
