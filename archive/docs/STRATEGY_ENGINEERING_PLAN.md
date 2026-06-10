# Regs Checker Strategy and Engineering Implementation Plan

## Document status

**Audience:** product, engineering, data science, policy, analyst review, and implementation teams  
**Purpose:** provide a comprehensive strategy and engineering roadmap for turning Regs Checker into a reliable business-facing AI law card and policy intelligence platform.  
**Scope:** U.S. state and federal artificial intelligence law and policy, with emphasis on Orrick and IAPP grounded extraction, business applicability, structured obligations, human analyst review, source provenance, and product-serving APIs.  
**Current validation posture:** Orrick and IAPP are the working ground truth for now. The team does not currently have lawyers who can independently validate the underlying legal interpretations. Legal review is a future goal, not a current release dependency.  
**Review basis:** static repository review of the current Regs Checker architecture, schemas, extraction agents, API routes, confidence scoring, verification logic, seed data, and setup documentation.  
**Runtime status:** this document does not claim that the current pipeline has been executed or benchmarked. All recommendations should be validated in a development environment before production implementation.

---

## 1. Executive summary

Regs Checker is already a substantial regulatory extraction platform. It includes local legal-text ingestion, passage-level extraction agents, bill-level extraction agents, Postgres persistence, human review queues, Supabase synchronization, confidence scoring, a dashboard, and a downstream Policy Navigator target database.

The next phase is not a full rebuild. The next phase is controlled hardening and productization, with one important strategic constraint:

**Orrick and IAPP should remain the current ground truth for law identification, status, scope, key requirements, and enforcement summaries until the project has access to lawyer review or another validated primary-source interpretation process.**

This means Regs Checker should not try to replace Orrick and IAPP legal analysis in the near term. It should use them as the trusted reference layer, then add structured extraction, consistency checks, business-facing cards, applicability logic, evidence links, and workflow tools around that reference layer.

The platform should evolve from an extraction pipeline into a two-layer regulatory intelligence system:

1. **Tracker-grounded legal extraction infrastructure:** ingest Orrick and IAPP tracker data, attach available official source text where possible, extract structured legal claims, preserve provenance, validate against tracker fields, support analyst review, version every run, and maintain auditable data.
2. **Policy card product layer:** convert tracker-grounded and analyst-reviewed data into business-facing law cards, applicability decisions, compliance actions, risk flags, source trails, and state-by-state comparisons.

The immediate goal is to make the system safe for internal policy research and product development. The medium-term goal is to make it reliable enough to power law cards grounded in Orrick and IAPP. The long-term goal is a production-grade AI policy intelligence product that can later incorporate lawyer validation and fuller primary-source legal interpretation.

---

## 2. Ground truth strategy

### 2.1 Current ground truth sources

The project should use the following hierarchy during the current phase:

1. **Orrick tracker data:** working ground truth for enacted or tracked AI law summaries, key requirements, enforcement summaries, effective dates where provided, law titles, and broad AI scope categories.
2. **IAPP tracker data:** working ground truth for broader state AI governance tracking, especially pending bills, active bills, enacted bills, scope categories, and cross-sector AI governance developments.
3. **Official law or bill text:** supporting evidence source and extraction substrate where available, but not the sole interpretive authority until lawyer review exists.
4. **Model-generated extraction:** structured interpretation derived from source text and tracker context, never treated as ground truth on its own.
5. **Analyst review:** product and data quality review, not legal review.
6. **Future legal review:** future validation layer for counsel-reviewed interpretation, legal risk, and primary-source authority.

### 2.2 Practical implication

For now, the product should answer:

> “Based on Orrick and IAPP as the current reference sources, what does this state AI law or policy appear to require, and what should a business know?”

It should not claim:

> “This is a lawyer-validated legal interpretation of the statute.”

### 2.3 Product language

Use careful language in the UI and API:

- “Tracker-grounded summary”
- “Based on Orrick and IAPP reference data”
- “Analyst reviewed”
- “Official source attached”
- “Official source not yet attached”
- “Legal review not yet completed”
- “Not legal advice”

Avoid language like:

- “Legally verified”
- “Counsel approved”
- “Authoritative legal conclusion”
- “Compliant”
- “Safe to deploy”

### 2.4 Near-term trust model

The current trust model should be:

| Trust source | Current role |
|---|---|
| Orrick | primary reference for law summaries, key requirements, enforcement, and enacted-law intelligence where present |
| IAPP | primary reference for AI governance bill tracking, status, and broader legislative coverage |
| Official law text | source evidence and extraction target, used to support and expand tracker data |
| LLM agents | structured extraction and normalization tools |
| Analyst review | quality assurance, consistency checking, and product readiness review |
| Lawyer review | future validation layer, not current dependency |

---

## 3. North star product vision

Regs Checker should answer five practical questions for a business or policymaker:

1. **What does this state AI law or policy do, according to Orrick and IAPP?**
2. **Does it appear relevant to my organization, AI product, sector, or use case?**
3. **What obligations, rights, deadlines, exceptions, and penalties are reported or extractable?**
4. **What evidence supports each claim, and what is still ambiguous or not yet validated?**
5. **What should product, policy, data science, procurement, and compliance teams consider next?**

A good law card should not merely summarize a law. It should translate tracker-grounded legal intelligence into operational decisions, while preserving enough source evidence for future counsel or policy experts to verify the claim.

---

## 4. Current system strengths

### 4.1 Strong extraction architecture

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

### 4.2 Good agent taxonomy

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

### 4.3 Structured legal payloads

The Pydantic schemas are a major asset. They create structured payloads for obligations, timelines, enforcement, safe harbors, consent requirements, rights, compliance mechanisms, thresholds, exceptions, interpretation risk, and preemption.

The product should build on these schemas rather than replace them. The key update is to add a product-layer law card schema above them.

### 4.4 Provenance and auditability foundation

The database already includes source records, raw artifacts, document versions, extractions, review queues, review actions, legal events, dependencies, applicability conditions, export jobs, triage results, failed extraction attempts, and bill-level extractions.

This is a strong foundation, but it needs run versioning and non-destructive historical preservation before the platform can be trusted for product-facing regulatory intelligence.

### 4.5 Evidence span verification

The base agent verifies evidence spans against the source passage using normalized text matching. This is essential. Every business-facing claim should eventually trace back to a tracker field, an official source passage, or both.

### 4.6 Orrick and IAPP grounding already exists

The current system already uses Orrick and IAPP fields in seed data, ingestion context, confidence scoring, and metadata enrichment. The strategy should strengthen this instead of replacing it too early with a primary-source-only model that the current team cannot legally validate.

---

## 5. Critical risks and remediation priorities

This section defines the highest-priority implementation issues. These should be treated as release blockers for any external-facing or business-facing use.

### 5.1 P0: verification agents appear to use outdated provider return handling

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

### 5.2 P0: verification does not appear to update persisted confidence tiers

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
| `verification_type` | text | `tracker_alignment`, `cross_validation`, `gap_detection`, `citation_verification`, `analyst_review`, `future_legal_review` |
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
- Dashboard clearly shows `not verified`, `tracker aligned`, `verified`, `flagged`, and `verification failed` states.
- A failed verification step does not create a misleading confidence boost.

---

### 5.3 P0: full extraction runs delete prior review and extraction history

#### Issue

The pipeline currently purges extractions, review actions, review queue items, extraction jobs, dependencies, applicability conditions, and failed attempts during full extraction runs.

#### Risk

This undermines auditability, reproducibility, product stability, and reviewer accountability. A policy intelligence product must preserve the reasoning and review trail behind claims.

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
| `source_snapshot_hash` | text | hash of Orrick, IAPP, and attached source corpus snapshot |
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

### 5.4 P0: route protection and reviewer identity are insufficient for analyst review workflows

#### Issue

The internal review route accepts reviewer identity from the request body and directly applies corrections to extraction payloads. The visible route code does not show authentication or role-based authorization for internal or product endpoints.

#### Risk

Analyst review decisions can be spoofed, overwritten, or applied without sufficient validation. This creates governance and product-trust risks.

#### Engineering action

Implement authentication and authorization.

Recommended current roles:

| Role | Permissions |
|---|---|
| `viewer` | read cards and reviewed extractions |
| `analyst` | inspect unreviewed extractions and verification reports |
| `reviewer` | approve, reject, or revise technical extraction payloads |
| `admin` | manage runs, users, source refreshes, and serving-run promotion |

Recommended future role:

| Role | Permissions |
|---|---|
| `legal_reviewer` | mark card or obligation as legally reviewed after counsel review process exists |

Requirements:

- Reviewer identity must come from auth context, not request body.
- Corrections must be schema-validated before approval.
- Analyst review status must be separate from future legal review status.
- Write actions must produce immutable audit-log entries.
- Expensive operations should use POST routes, not GET routes.

#### Acceptance criteria

- Unauthorized users cannot access `/internal` or dashboard review actions.
- Reviewer names cannot be spoofed through request payloads.
- Invalid corrected payloads are rejected.
- Every review action has timestamp, reviewer, previous payload hash, new payload hash, and comment.
- `/v1/verification` or equivalent verification execution endpoint is POST-only.

---

## 6. Product architecture target state

### 6.1 Two-layer architecture

```text
Orrick and IAPP tracker data
        |
        v
Tracker normalization and reference corpus
        |
        v
Optional official law or bill text attachment
        |
        v
Source ingestion, parsing, and passage normalization
        |
        v
Passage triage and bill context builder
        |
        v
Extraction agents and bill-level agents
        |
        v
Tracker alignment, evidence verification, citation verification, cross-validation, gap detection
        |
        v
Analyst review
        |
        v
Tracker-grounded reviewed extraction store
        |
        v
Law card builder
        |
        v
Policy card API, applicability engine, dashboard, and exports
        |
        v
Future lawyer validation layer
```

### 6.2 Platform layers

| Layer | Main responsibility |
|---|---|
| Tracker layer | ingest, normalize, and preserve Orrick and IAPP records as current ground truth |
| Source layer | attach, hash, parse, and version official text when available |
| Extraction layer | produce structured legal payloads from tracker context and source text |
| Verification layer | test tracker alignment, evidence, citations, completeness, and cross-model consistency |
| Review layer | allow analysts to approve, reject, or correct extracted claims for product use |
| Product layer | generate law cards, business actions, risk summaries, and applicability decisions |
| Serving layer | expose stable APIs, dashboard views, exports, and syncs |
| Future legal layer | counsel review, primary-source legal interpretation, and legal approval when available |

---

## 7. Law card product model

### 7.1 Why law cards need their own data model

A law card should not be a loose aggregation of extraction rows at request time. It should be a curated product artifact with its own lifecycle, review state, tracker grounding, source evidence, and version history.

The extraction layer answers: **what was found in the tracker-contextualized legal text?**

The law card layer answers: **what should a business, policymaker, or compliance team understand and do based on the current tracker-grounded record?**

### 7.2 Proposed tables

#### `law_cards`

| Field | Type | Description |
|---|---|---|
| `id` | integer | primary key |
| `canonical_law_id` | text | stable law identifier |
| `document_version_id` | integer | FK to source law version, if source text exists |
| `run_id` | integer | FK to build run |
| `state_code` | text | `CA`, `CO`, etc. |
| `jurisdiction_name` | text | full state or federal name |
| `law_name` | text | public title |
| `bill_number` | text | bill number where applicable |
| `citation` | text | codified citation if available from tracker or source |
| `status` | text | introduced, active bill, enacted, effective, delayed, repealed, stayed, litigated |
| `effective_date` | date | nullable |
| `enforcement_start_date` | date | nullable |
| `plain_summary` | text | executive summary |
| `business_relevance_summary` | text | why businesses should care |
| `tracker_grounding_status` | text | `orrick`, `iapp`, `orrick_and_iapp`, `secondary_only`, `manual` |
| `source_attachment_status` | text | `official_source_attached`, `tracker_only`, `source_missing`, `source_parse_failed` |
| `analyst_review_status` | text | not reviewed, reviewed, approved, rejected |
| `future_legal_review_status` | text | not available, not reviewed, reviewed, approved, rejected |
| `confidence_score` | float | product-level confidence |
| `ambiguity_level` | text | low, medium, high, critical |
| `last_reviewed_at` | timestamp | nullable |
| `created_at` | timestamp | required |
| `updated_at` | timestamp | required |

#### `law_card_tracker_refs`

| Field | Type | Description |
|---|---|---|
| `id` | integer | primary key |
| `law_card_id` | integer | FK |
| `tracker_name` | text | `orrick`, `iapp` |
| `tracker_law_id` | text | upstream ID where available |
| `tracker_title` | text | source title |
| `tracker_status` | text | status from tracker |
| `tracker_scope` | text | scope category from tracker |
| `key_requirements_raw` | text | raw Orrick or IAPP requirement text where available |
| `enforcement_raw` | text | raw enforcement text where available |
| `last_updated_at` | timestamp | source record update time |
| `payload` | jsonb | full normalized tracker record |

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
| `tracker_ref_ids` | jsonb | source tracker references |
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
| `tracker_support` | text | `orrick`, `iapp`, `both`, `source_only`, `extraction_only` |
| `evidence_extraction_id` | integer | FK to extraction |
| `confidence_score` | float | obligation-level confidence |
| `review_status` | text | analyst review state |

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
| `tracker_support` | text | `orrick`, `iapp`, `both`, `source_only`, `extraction_only` |
| `evidence_extraction_ids` | jsonb | source extraction references |

#### `law_card_business_actions`

| Field | Type | Description |
|---|---|---|
| `id` | integer | primary key |
| `law_card_id` | integer | FK |
| `team` | text | product, data science, security, procurement, HR, compliance, policy |
| `action_type` | text | assess, document, disclose, monitor, contract, test, review |
| `action_text` | text | plain-English action |
| `priority` | text | low, medium, high, urgent |
| `deadline` | text | nullable |
| `source_obligation_id` | integer | FK to law card obligation or extraction |
| `grounding_note` | text | tracker-grounded, source-supported, analyst-added, or future legal review needed |

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
| `tracker_confidence` | text | low, medium, high |
| `rationale` | jsonb | explainable scoring factors |

---

## 8. Product taxonomy

The extraction schemas should be preserved. Add a normalized business taxonomy above them.

### 8.1 Source taxonomy

- Orrick tracker;
- IAPP tracker;
- official statute;
- official bill;
- regulation;
- agency guidance;
- attorney general advisory;
- executive order;
- procurement policy;
- enforcement action;
- litigation order;
- settlement;
- model policy;
- manual analyst note;
- future lawyer note.

### 8.2 AI system taxonomy

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

### 8.3 Business role taxonomy

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

### 8.4 Use-case taxonomy

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

### 8.5 Obligation taxonomy

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

## 9. Tracker-grounded confidence, review, and trust model

### 9.1 Do not remove the Orrick gate yet

The previous recommendation to replace the Orrick gate should be deferred. Because the team does not currently have lawyers who can validate legal interpretation, Orrick and IAPP should remain the current grounding layer.

However, the existing gate should be refined so it supports both Orrick and IAPP and does not accidentally penalize laws that are IAPP-only.

### 9.2 Recommended near-term confidence model

Use a tracker-grounded confidence model:

| Signal | Suggested weight |
|---|---:|
| Orrick alignment | 30 percent |
| IAPP alignment or status match | 20 percent |
| Evidence spans verified against attached source text | 15 percent |
| Citation verification passed | 10 percent |
| Cross-validation passed | 10 percent |
| Gap detection found no high-confidence gaps | 5 percent |
| Analyst review approved | 10 percent |

If only Orrick is available, redistribute IAPP weight across Orrick alignment, evidence, and analyst review.

If only IAPP is available, do not force Tier D solely because Orrick is missing. Instead, use an `iapp_grounded` pathway.

If neither Orrick nor IAPP is available, the item should remain `ungrounded` and should not be product-visible without explicit admin override.

### 9.3 Confidence states

Use both score and state.

| State | Meaning |
|---|---|
| `unverified` | extraction exists but verification not run |
| `orrick_grounded` | extraction aligns with Orrick data |
| `iapp_grounded` | extraction aligns with IAPP data |
| `tracker_grounded` | extraction aligns with Orrick, IAPP, or both |
| `source_supported` | evidence spans verify against attached official source text |
| `analyst_reviewed` | analyst approved for product use |
| `flagged` | material issue found |
| `tracker_conflict` | Orrick, IAPP, source text, or extraction disagree |
| `stale` | tracker or attached source changed since review |
| `superseded` | newer extraction or card version exists |
| `future_legal_reviewed` | lawyer review completed in a future phase |

### 9.4 Tracker conflict handling

Create explicit conflict states:

| Conflict type | Example |
|---|---|
| `orrick_iapp_status_conflict` | Orrick marks law enacted, IAPP marks active bill |
| `tracker_source_date_conflict` | tracker effective date differs from attached official text |
| `extraction_tracker_requirement_conflict` | LLM extracts obligation not found in tracker summary |
| `enforcement_conflict` | penalty differs between tracker and extraction |
| `scope_conflict` | covered sector differs between tracker and extraction |

Conflict behavior:

- Do not silently merge conflicts.
- Surface conflict in review UI.
- Assign analyst review priority.
- Mark law card as `needs_tracker_resolution` or `needs_source_check`.
- Do not promote conflicted card to product-visible unless the conflict is explained.

### 9.5 Analyst review workflow

Recommended review states:

1. pending analyst review;
2. analyst approved;
3. analyst rejected;
4. needs revision;
5. tracker conflict;
6. source attachment needed;
7. stale after tracker update;
8. superseded by new run;
9. future legal review pending;
10. future legal review complete.

### 9.6 Review UI requirements

Each review item should show:

- Orrick fields;
- IAPP fields;
- source passage, if available;
- extraction payload;
- evidence spans;
- verified and unverified evidence markers;
- section path;
- official source URL, if available;
- model ID;
- prompt version;
- confidence breakdown;
- verification results;
- tracker alignment score;
- tracker conflict warnings;
- suggested law-card placement;
- correction editor with schema validation;
- reviewer comments;
- future legal review placeholder.

---

## 10. Business applicability engine

### 10.1 Purpose

The applicability engine should determine whether a law card is likely relevant to a business based on facts about the organization, system, state, users, sector, and use case.

The engine should never pretend to provide legal advice. It should output structured applicability triage:

- likely applicable;
- possibly applicable;
- unlikely applicable;
- not enough information;
- excluded by exemption;
- needs analyst review;
- future counsel review recommended.

### 10.2 Business intake schema

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

### 10.3 Output schema

```json
{
  "result_id": "...",
  "jurisdictions_checked": ["CA", "CO", "UT"],
  "grounding_note": "Results are based on Orrick and IAPP as current reference sources.",
  "likely_applicable": [
    {
      "law_card_id": 123,
      "law_name": "Example AI Law",
      "state": "CO",
      "why": ["business is a deployer", "system is used in employment", "decision impact is high"],
      "required_actions": ["complete impact assessment", "provide notice", "maintain records"],
      "missing_facts": [],
      "tracker_grounding": "orrick_and_iapp",
      "confidence": "high"
    }
  ],
  "possibly_applicable": [],
  "unlikely_applicable": [],
  "needs_analyst_review": [],
  "future_counsel_review_recommended": []
}
```

### 10.4 Implementation approach

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
- pending or enacted status;
- tracker grounding status.

Use LLMs only for:

- explaining the result in plain language;
- asking follow-up questions when facts are missing;
- mapping ambiguous business descriptions to taxonomy terms.

### 10.5 Acceptance criteria

- The engine can explain why each law was included or excluded.
- Every applicability result links to law-card fields and tracker/source evidence.
- Missing facts are explicitly listed.
- The same input produces deterministic results.
- LLM explanations cannot override deterministic applicability logic.
- The output clearly says the result is tracker-grounded, not lawyer-validated.

---

## 11. Source and tracker corpus strategy

### 11.1 Current source hierarchy

During the current phase, use this hierarchy:

1. Orrick tracker;
2. IAPP tracker;
3. attached official law or bill text;
4. attached official agency guidance, AG guidance, enforcement action, or court order;
5. model-generated extraction;
6. analyst review;
7. future lawyer review.

### 11.2 Tracker metadata requirements

For every Orrick or IAPP record, preserve:

- tracker name;
- tracker record ID if available;
- jurisdiction;
- bill number;
- canonical title;
- status;
- scope category;
- effective date;
- key requirements raw text;
- enforcement raw text;
- source URL or tracker URL;
- last updated date;
- imported date;
- normalized law ID;
- raw payload snapshot;
- row hash.

### 11.3 Official source attachment

Official law or bill text should still be attached where possible, but the current product should not require official-source interpretation before producing tracker-grounded cards.

Source attachment statuses:

| Status | Meaning |
|---|---|
| `tracker_only` | card is based only on Orrick or IAPP |
| `official_source_attached` | official text is attached and parsed |
| `official_source_unparsed` | official text exists but parse failed or is not processed |
| `official_source_missing` | no official source attached yet |
| `source_conflict` | attached source appears to conflict with tracker fields |

### 11.4 Source freshness

Add scheduled tracker checks:

- Orrick row changed;
- IAPP row changed;
- bill status changed;
- law enacted;
- effective date changed;
- amendment added;
- court stay or injunction added;
- AG guidance added;
- rulemaking opened;
- rulemaking finalized;
- federal preemption risk added.

### 11.5 Acceptance criteria

- Every production law card has Orrick or IAPP grounding, or an explicit ungrounded warning.
- Cards can be product-visible without lawyer review if they are tracker-grounded and analyst-reviewed.
- Cards without official source attachment are allowed but visibly marked `tracker_only`.
- Tracker changes can mark cards stale.
- A stale card is not served as final without a visible warning.

---

## 12. API strategy

### 12.1 Current API direction

The current `/v1` API serves obligations, individual obligations, dependencies, matrix data, changes, completeness, and verification. This should be preserved for internal and advanced users, but product endpoints should be added.

### 12.2 New product endpoints

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
GET /v1/law-cards/{law_card_id}/tracker-refs
```

#### Change intelligence endpoints

```http
GET /v1/changes
GET /v1/law-cards/{law_card_id}/history
GET /v1/law-cards/{law_card_id}/diff?from_run_id=...&to_run_id=...
```

#### Review and internal endpoints

Internal review endpoints should remain under `/internal`, protected by auth and roles.

### 12.3 API response principles

- Every business-facing claim should include traceable tracker references or evidence IDs.
- Every law card should include tracker grounding and analyst review state.
- Ambiguity should be explicit.
- Pending or non-effective laws should be clearly marked.
- API should distinguish legal status from product readiness.
- API should distinguish analyst review from future legal review.

### 12.4 Acceptance criteria

- API endpoints have stable Pydantic response schemas.
- OpenAPI documentation clearly separates product endpoints and internal endpoints.
- API key scopes control access.
- Product endpoints never serve unreviewed cards as if reviewed.
- Product endpoints never serve tracker-grounded cards as lawyer-validated.
- Pagination, filtering, and sorting are tested.

---

## 13. Engineering implementation roadmap

## Phase 0: Stabilization and tracker-grounded safety fixes

### Goal

Make the current system honest, internally reliable, non-destructive, and safe for team use while keeping Orrick and IAPP as the current ground truth.

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

### Workstream B: tracker-grounded confidence recomputation

Tasks:

- Add `verification_results` migration.
- Persist tracker alignment, cross-validation, gap detection, and citation verification outputs.
- Recompute confidence after verification.
- Add support for both Orrick-grounded and IAPP-grounded pathways.
- Store confidence history.

Acceptance criteria:

- Confidence changes after verification.
- Dashboard and API expose verification state.
- IAPP-only records are not automatically forced to Tier D solely because Orrick is absent.
- Records with no Orrick or IAPP grounding are excluded from product-visible cards unless explicitly overridden.

### Workstream C: non-destructive runs

Tasks:

- Add `extraction_runs` table.
- Add `run_id` to extractions and review tables.
- Replace full-run purge with new run creation.
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
- Add future legal review fields but do not require them for current release.

Acceptance criteria:

- Unauthorized write actions fail.
- Review actions are attributable and immutable.
- Analyst review is clearly distinct from future legal review.

### Phase 0 deliverable

Internal alpha suitable for trusted team use, with clear warnings for unverified, tracker-only, and non-lawyer-reviewed data.

---

## Phase 1: Tracker corpus foundation

### Goal

Make Orrick and IAPP the explicit, preserved, normalized reference corpus.

### Workstream A: tracker reference model

Tasks:

- Add `tracker_sources` or `law_card_tracker_refs` table.
- Preserve raw Orrick and IAPP payloads.
- Add row hash and imported timestamp.
- Add tracker update history.
- Normalize status fields across Orrick and IAPP.

### Workstream B: tracker alignment

Tasks:

- Extend Orrick similarity into a broader tracker alignment module.
- Add IAPP alignment logic.
- Detect Orrick/IAPP conflicts.
- Add conflict queue for analyst review.

### Workstream C: source attachment as support

Tasks:

- Attach official source text where available.
- Preserve source hash and parse quality.
- Do not block product cards when official source is missing if tracker grounding is strong.
- Mark card as `tracker_only` when official source is missing.

### Acceptance criteria

- Every product-visible law card has tracker grounding status.
- Orrick and IAPP record changes can be detected.
- Conflicting tracker records are flagged.
- Official source attachment improves confidence but is not required for current release.

### Phase 1 deliverable

Tracker-grounded legal corpus.

---

## Phase 2: Evaluation and analyst benchmark

### Goal

Measure extraction quality against Orrick and IAPP reference data before scaling.

### Workstream A: tracker-grounded gold set

Create a hand-reviewed evaluation corpus of 25 to 50 laws across:

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

For each law, labels should be based on Orrick and IAPP, plus source text when available:

- tracker title;
- tracker status;
- tracker scope category;
- key requirements;
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
- evidence spans if official source is attached;
- ambiguity notes.

### Workstream C: metrics

Track:

- tracker alignment precision;
- tracker alignment recall;
- field-level accuracy against Orrick and IAPP;
- evidence-span verification rate where source text exists;
- citation verification rate where source text exists;
- false positive rate against tracker data;
- false negative rate against tracker data;
- confidence calibration;
- analyst correction rate;
- time per reviewed card;
- model and prompt performance.

### Acceptance criteria

- Evaluation can run from CLI and CI.
- Results are written to an evaluation report.
- Prompt or model changes can be compared against baseline.
- No production prompt changes without benchmark regression check.
- Evaluation clearly distinguishes tracker accuracy from lawyer-validated legal accuracy.

### Phase 2 deliverable

Tracker-grounded evaluation harness and benchmark dashboard.

---

## Phase 3: Law card builder

### Goal

Create first-class business-facing law cards from tracker-grounded and analyst-reviewed extractions.

### Workstream A: law card schema and tables

Tasks:

- Add `law_cards` and related tables.
- Add tracker reference tables.
- Add mapping logic from extraction types to law card sections.
- Add card build run tracking.
- Preserve source extraction IDs and tracker refs behind each card section.

### Workstream B: deterministic summaries

Tasks:

- Build deterministic summary templates.
- Use LLMs only for optional drafting, not authoritative facts.
- Require tracker support or source evidence for all legal claims.
- Flag any extraction-only claim for analyst review.

### Workstream C: law card review

Tasks:

- Add analyst review for card assembly.
- Add future legal review fields but do not require them.
- Add stale and superseded states.

### Acceptance criteria

- A card can be generated for a selected law.
- Every card claim links to tracker refs or source evidence.
- Analyst-review status is visible.
- Future legal-review status is visible as not yet available or not reviewed.
- Cards can be rebuilt without deleting old versions.

### Phase 3 deliverable

Tracker-grounded policy card MVP.

---

## Phase 4: Business applicability and control mapping

### Goal

Help organizations understand which laws may apply and what they should consider doing, based on tracker-grounded law cards.

### Workstream A: applicability engine

Tasks:

- Add business intake schema.
- Add deterministic matching rules.
- Add missing-facts detection.
- Add explainable inclusion and exclusion logic.
- Add tracker grounding notes to every applicability result.

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
- Add not-legal-advice and not-lawyer-reviewed labels.

### Acceptance criteria

- Same input produces same applicability result.
- Every result explains why it was included or excluded.
- Missing facts are listed.
- LLM explanations cannot override deterministic logic.
- Reports clearly state that results are based on Orrick and IAPP as current reference sources.

### Phase 4 deliverable

Business decision engine.

---

## Phase 5: Future official-source and legal-review hardening

### Goal

Prepare the platform for lawyer validation and primary-source legal review when resources allow.

### Workstream A: official source certification

Tasks:

- Add official source retrieval workflows.
- Map bills to enacted laws and codified statutes.
- Improve PDF and HTML parsing quality.
- Add source-text diffing.
- Add source conflict resolution workflows.

### Workstream B: legal review workflow

Tasks:

- Add legal reviewer role.
- Add legal review checklist.
- Add counsel notes and review signatures.
- Add legal approval gates for high-risk product claims.
- Add legal-review export package with tracker refs, source text, extraction payload, and analyst notes.

### Workstream C: confidence model evolution

Tasks:

- Add lawyer-reviewed confidence component.
- Reduce dependency on tracker alignment only after counsel-reviewed benchmarks exist.
- Preserve tracker alignment as a continuing support signal.

### Acceptance criteria

- Legal reviewer can approve or reject law cards.
- Legal review status is preserved and auditable.
- Counsel-reviewed cards can be distinguished from tracker-grounded cards.
- Confidence model can incorporate lawyer validation without breaking current tracker-grounded behavior.

### Phase 5 deliverable

Lawyer-review-ready regulatory intelligence platform.

---

## Phase 6: Productionization

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
- Add tracker refresh schedule.
- Add reviewer assignment workflows.
- Add release checklist.

### Acceptance criteria

- Failed jobs are visible and retryable.
- Production data can be restored.
- API has scoped access and rate limits.
- Tracker freshness and analyst review are operationally monitored.

### Phase 6 deliverable

Production-grade tracker-grounded regulatory intelligence platform.

---

## 14. Testing strategy

### 14.1 Unit tests

Add tests for:

- provider response handling;
- JSON repair;
- evidence span verification;
- confidence scoring;
- tracker alignment;
- Orrick-only confidence pathway;
- IAPP-only confidence pathway;
- citation verification;
- taxonomy normalization;
- applicability matching rules;
- review correction validation;
- API filters and pagination.

### 14.2 Integration tests

Add tests for:

- ingest one Orrick fixture row;
- ingest one IAPP fixture row;
- attach one official source fixture;
- triage passages;
- extract with mocked LLM;
- verify with mocked LLM;
- compute tracker alignment;
- approve extraction through analyst review;
- build law card;
- run applicability check.

### 14.3 Golden-file tests

For each tracker-grounded gold law:

- expected tracker status;
- expected scope category;
- expected obligations;
- expected covered entities;
- expected deadlines;
- expected enforcement fields;
- expected tracker references;
- expected source evidence spans where source text exists;
- expected law-card sections.

### 14.4 Regression tests

Every prompt or model change should produce a benchmark report comparing:

- tracker alignment precision;
- tracker alignment recall;
- field-level accuracy against Orrick and IAPP;
- evidence verification where source exists;
- confidence calibration;
- token usage;
- runtime;
- analyst correction rate.

---

## 15. Data governance and review policy

### 15.1 Legal and product status labels

Every law card should display:

- legal or legislative status from Orrick and IAPP;
- tracker grounding status;
- source attachment status;
- analyst review status;
- future legal review status;
- confidence status;
- last reviewed date;
- stale or superseded warning where applicable.

### 15.2 Analyst review and future legal review separation

Separate current analyst review from future legal review:

| Review type | Question |
|---|---|
| Analyst review | Does the structured card accurately reflect Orrick and IAPP tracker data and available source evidence? |
| Technical review | Did the extraction correctly capture and normalize the source text and tracker fields? |
| Product review | Is the card clear and usable? |
| Future legal review | Is the interpretation legally appropriate after counsel review? |

### 15.3 Disclaimers

The product should clearly state that it provides tracker-grounded legal information and regulatory intelligence, not legal advice. It should also state that Orrick and IAPP are the current reference sources and that lawyer validation is a future goal.

The disclaimer should not become an excuse for weak evidence, stale tracker data, or vague claims. The product must still be precise about what is known, what source supports it, and what remains uncertain.

---

## 16. Immediate implementation backlog

### Week 1: critical repairs

- [ ] Fix provider response handling in cross-validation.
- [ ] Fix provider response handling in gap detection.
- [ ] Add tests for both functions.
- [ ] Change verifier failure behavior to explicit failure.
- [ ] Remove persisted model reasoning.
- [ ] Correct README and setup model drift.
- [ ] Add auth guard scaffolding for `/internal`.
- [ ] Rename current review language from legal review to analyst review where appropriate.

### Week 2: tracker alignment and verification persistence

- [ ] Add `verification_results` migration.
- [ ] Persist tracker alignment, cross-validation, gap detection, and citation verification outputs.
- [ ] Recompute confidence after verification.
- [ ] Add IAPP-grounded confidence pathway.
- [ ] Add dashboard display for tracker grounding and verification status.
- [ ] Add tests for confidence recomputation.

### Week 3: run versioning

- [ ] Add `extraction_runs` table.
- [ ] Add `run_id` to extractions and review tables.
- [ ] Replace full-run purge with new run creation.
- [ ] Add active serving run promotion.
- [ ] Add run comparison skeleton.

### Week 4: tracker reference model and law-card schema

- [ ] Add tracker reference table.
- [ ] Preserve raw Orrick and IAPP snapshots.
- [ ] Add `law_cards` table.
- [ ] Add law-card obligation, applicability, enforcement, business-action, and risk-score tables.
- [ ] Build first deterministic law-card builder.
- [ ] Add `GET /v1/law-cards/{id}`.
- [ ] Generate 3 pilot tracker-grounded law cards.

### Weeks 5 to 6: tracker-grounded evaluation

- [ ] Select 25 law evaluation set.
- [ ] Create manual labels based on Orrick and IAPP.
- [ ] Build evaluation harness.
- [ ] Add source attachment status fields.
- [ ] Add tracker freshness monitor prototype.

### Weeks 7 to 8: applicability MVP

- [ ] Add business intake schema.
- [ ] Add deterministic applicability engine.
- [ ] Add `POST /v1/applicability-check`.
- [ ] Add explainable outputs.
- [ ] Add business action checklist.
- [ ] Add tracker-grounded disclaimer language.

---

## 17. Definition of done for tracker-grounded policy-card readiness

The platform is ready to support tracker-grounded business-facing policy cards when:

1. every production card has Orrick or IAPP grounding, or an explicit ungrounded warning;
2. every legal or business-facing claim links to a tracker reference, source evidence, or both;
3. extraction confidence reflects tracker alignment, verification, and analyst review;
4. IAPP-only laws are supported without being unfairly downgraded solely because Orrick is missing;
5. prior runs and review history are preserved;
6. analyst review and future legal review are separate;
7. tracker freshness can mark cards stale;
8. benchmark accuracy is measured against Orrick and IAPP;
9. applicability decisions are deterministic and explainable;
10. API access is authenticated and scoped;
11. product language clearly distinguishes tracker-grounded legal information from legal advice;
12. product language clearly states that lawyer validation is a future goal.

---

## 18. Recommended team roles

| Role | Responsibilities |
|---|---|
| Technical lead | architecture, migrations, API, run versioning, production readiness |
| Data scientist | extraction evaluation, tracker alignment, confidence calibration, prompt and model testing |
| Policy analyst | Orrick and IAPP interpretation, taxonomy refinement, tracker conflict resolution, law-card review |
| Product lead | user workflows, card design, applicability report, prioritization |
| Backend engineer | database models, endpoints, job orchestration, auth |
| Frontend or dashboard engineer | review UI, law-card UI, run dashboard, verification dashboard |
| DevOps engineer | environments, backups, observability, scheduled tracker refresh |
| Future legal reviewer | counsel review, legal approval, primary-source interpretation, once available |

---

## 19. Open questions for the team

1. Which users are primary for MVP: business compliance teams, policymakers, startup founders, or internal analysts?
2. Should law cards include pending bills, enacted laws only, or both with different labels?
3. What analyst review is required before a tracker-grounded card is product-visible?
4. What product language should be used to describe Orrick and IAPP grounding?
5. What are the first 10 priority states?
6. What are the first 5 priority use cases?
7. What level of tracker freshness is required: daily, weekly, or manual release cycles?
8. How should the system handle conflicts between Orrick and IAPP?
9. How should the system handle extraction claims that are supported by official source text but absent from tracker summaries?
10. What is the acceptable false-negative rate against Orrick and IAPP key requirements?
11. Should the system support customer-specific saved applicability profiles?
12. Should law-card outputs be exportable as PDF, CSV, JSON, or all three?
13. What would trigger future legal review: high-risk cards, customer requests, revenue milestones, or all production cards?

---

## 20. Suggested MVP scope

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

- tracker-grounded law card;
- tracker reference trail;
- source evidence trail where available;
- business applicability result;
- compliance action checklist;
- tracker freshness and stale-card alerts.

---

## 21. Final recommendation

Do not treat Regs Checker as merely an extraction script. Treat it as the foundation of a tracker-grounded regulatory intelligence product.

The immediate engineering priority is trust within the current constraint: fix verification, preserve history, authenticate review, and make Orrick and IAPP grounding explicit. The next product priority is law-card generation. The strategic priority is business applicability: making the product answer whether a law may apply and what an organization should consider doing next.

The recommended sequence is:

1. stabilize;
2. formalize Orrick and IAPP as the current ground truth;
3. evaluate against Orrick and IAPP;
4. build law cards;
5. add applicability logic;
6. add official-source hardening;
7. add future lawyer review;
8. productionize.

That sequence fits the team’s current capabilities, preserves the value of the existing architecture, and turns the repo into a credible policy intelligence platform without pretending to provide lawyer-validated legal advice before the project has legal reviewers.
