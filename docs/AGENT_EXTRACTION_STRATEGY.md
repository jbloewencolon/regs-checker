# Agent Extraction Strategy

## Document status

**Audience:** engineering, data science, policy analysts, extraction reviewers, and future legal reviewers  
**Purpose:** define the technical strategy for improving the Regs Checker extraction pipeline, agent taxonomy, verification system, confidence model, and post-extraction normalization layer.  
**Current validation posture:** Orrick and IAPP remain the current working ground truth. The extraction system is a candidate-generation, structuring, and normalization layer. It is not the legal authority layer.  
**Input basis:** static repo review plus the May 2026 extraction run, which produced 6,274 extractions from 660 records, with 472 bill-level extractions, 115 reported errors, and a confidence distribution heavily concentrated in C and D tiers.  

---

## 1. Executive summary

The extraction run confirms that the current agent architecture is productive, but it also shows that the raw extraction taxonomy should remain internal. The system produced thousands of structured legal fragments across obligations, definitions, compliance mechanisms, rights, preemption signals, thresholds, exceptions, and enforcement. This is valuable, but it is not yet suitable as a direct product layer.

The extraction strategy should therefore use a layered architecture:

```text
Orrick and IAPP tracker records
  -> attached source text, where available
  -> passage triage
  -> raw agent extraction
  -> tracker alignment
  -> evidence verification
  -> taxonomy normalization
  -> compliance concept grouping
  -> analyst review queue
  -> law-card-ready structured data
```

The key technical shift is this:

> Raw agent outputs are not the final unit of meaning. They are evidence-bearing fragments that must be normalized and grouped into compliance concepts before they can support product law cards.

---

## 2. What the run tells us

### 2.1 Run-level observations

The run produced:

| Metric | Value |
|---|---:|
| Total records | 660 |
| Records processed | 660 |
| Total extractions | 6,274 |
| Bill-level extractions | 472 |
| Agent calls in run summary | 2,043 |
| Total token usage in run summary | 9,942,470 |
| Errors in live monitor | 115 |
| Warnings in live monitor | 2,366 |

The extraction volume is strong, but the confidence profile shows that the system should be treated as a candidate generator rather than a legal truth engine.

### 2.2 Confidence distribution

| Confidence tier | Count | Share |
|---|---:|---:|
| A | 149 | 2.4 percent |
| B | 598 | 9.5 percent |
| C | 3,164 | 50.4 percent |
| D | 2,363 | 37.7 percent |

The practical implication is clear:

- A and B extractions are strong candidates for product use after analyst review.
- C extractions are useful but need normalization, tracker alignment, and often review.
- D extractions should not drive product claims unless supported by Orrick, IAPP, source evidence, or analyst review.

### 2.3 Extraction type distribution

| Extraction type | Count | Share |
|---|---:|---:|
| obligation | 1,843 | 29.4 percent |
| definition | 1,387 | 22.1 percent |
| compliance_mechanism | 731 | 11.7 percent |
| rights_protection | 673 | 10.7 percent |
| preemption_signal | 650 | 10.4 percent |
| threshold | 501 | 8.0 percent |
| exception | 474 | 7.6 percent |
| enforcement | 15 | 0.2 percent |

This distribution indicates that the current extraction taxonomy is agent-shaped. It is good for parsing but not sufficient for product display.

### 2.4 Evidence grounding

Across parsed evidence spans, about 58 percent were marked verified in the local analysis of the extraction CSV. This is promising but not high enough for automatic product claims. Evidence verification should remain central to confidence scoring and analyst review prioritization.

### 2.5 Agent reliability observations

The run shows different reliability patterns by agent:

- `obligation` is the highest-volume agent and central to product value.
- `definition_actor` produces many useful definitions but has parsing and validation fragility on long or complex outputs.
- `compliance_mechanism` has the highest observed failure rate among the main agents and many abstentions.
- `threshold_exception` is valuable but should be split or normalized more aggressively after extraction.
- `preemption` is too broad and should be refactored into legal-context categories.
- `rights_protection` has stronger confidence than many other agents but overlaps heavily with obligations and compliance mechanisms.

---

## 3. Strategic extraction principles

### Principle 1: Orrick and IAPP are the current ground truth

Extraction should be evaluated against Orrick and IAPP where available. Official source text is important, but until lawyer review exists, the system should not overrule Orrick and IAPP based solely on model-generated interpretation.

### Principle 2: Raw extraction type is not product type

`obligation`, `definition`, `rights_protection`, `threshold`, and similar types should be internal primitives. Product-facing law cards should use normalized compliance concepts.

### Principle 3: Prefer recall early, precision later

The extraction stage should continue to favor recall, but downstream layers must apply tracker alignment, evidence verification, deduplication, concept grouping, and analyst review.

### Principle 4: Capture conflicts explicitly

Do not silently merge disagreements between Orrick, IAPP, source text, and model extraction. Conflicts should create review flags.

### Principle 5: Zero extraction is meaningful

A record with zero extractions should not automatically disappear. It may still need a tracker-only law card if Orrick or IAPP provides a valid reference record.

---

## 4. Target extraction architecture

```text
Tracker ingestion
  -> normalize Orrick and IAPP fields
  -> attach source text, if available
  -> create source records
  -> triage passages
  -> run extraction agents
  -> run bill-level agents
  -> verify evidence spans
  -> align against trackers
  -> normalize actors, domains, obligations, rights, enforcement, thresholds, and legal context
  -> group into compliance concepts
  -> push to review queue
  -> publish reviewed structured data to law-card builder
```

---

## 5. Current raw extraction taxonomy

The existing extraction taxonomy should be preserved as the internal agent-output layer:

- `obligation`
- `definition`
- `compliance_mechanism`
- `rights_protection`
- `preemption_signal`
- `threshold`
- `exception`
- `enforcement`

However, the system should stop treating this as the product taxonomy. A law-card user does not need to see an extraction type called `preemption_signal` or `threshold_exception`. They need to know whether the law creates a disclosure duty, an assessment duty, an appeal right, a covered-entity threshold, or an enforcement risk.

---

## 6. New normalization taxonomy

### 6.1 Law-domain taxonomy

Add a first-class `law_domain` classification layer.

Recommended values:

| Law domain | Meaning |
|---|---|
| `ai_governance_cross_sector` | broad AI governance law or framework |
| `privacy_profiling_ads` | privacy, profiling, automated decision, data rights |
| `synthetic_media_elections` | election deepfakes and political synthetic media |
| `intimate_image_csam_likeness` | CSAM, intimate images, digital replicas, likeness rights |
| `healthcare_ai` | clinical, payer, utilization review, hospital, or health system AI |
| `employment_ads` | automated employment decision systems |
| `insurance_algorithmic_discrimination` | insurance AI, predictive models, unfair discrimination |
| `education_ai` | AI in schools, education platforms, student systems |
| `government_ai_procurement` | public-sector AI use, inventories, procurement, agency governance |
| `frontier_model_safety` | frontier model, foundation model, compute, safety reporting |
| `pricing_competition` | algorithmic pricing and competition |
| `consumer_chatbot_disclosure` | chatbot and consumer interaction disclosure |
| `data_broker_training_data` | data broker registration, training-data disclosure, data sales |
| `general_consumer_protection` | deception, unfair practices, consumer notice |
| `other_ai_adjacent` | relevant but not cleanly classified |

A law may have multiple domains, but one should be marked primary.

### 6.2 Actor normalization taxonomy

Raw actor strings must be mapped to normalized actor families.

| Actor family | Examples and aliases |
|---|---|
| `business_entity` | business, company, organization, regulated entity |
| `developer` | AI developer, model developer, system developer |
| `deployer` | deployer, user of AI system, implementer |
| `provider` | provider, vendor, supplier, service provider |
| `controller` | controller, data controller |
| `processor` | processor, data processor |
| `platform` | online platform, social media platform, covered platform |
| `employer` | employer, employment agency, hiring platform |
| `insurer` | insurer, carrier, health carrier, agent |
| `healthcare_entity` | hospital, health plan, utilization reviewer, provider |
| `government_agency` | agency, department, public body |
| `regulator` | AG, commissioner, commission, agency official |
| `individual` | consumer, applicant, patient, worker, student, resident |
| `content_actor` | creator, distributor, publisher, advertiser |
| `political_actor` | candidate, committee, campaign, political advertiser |
| `data_actor` | data broker, data holder, dataset provider |

Implementation requirement: use an alias table rather than relying only on prompt instructions.

### 6.3 Covered-system taxonomy

Normalize covered systems into:

- `automated_decision_system`
- `automated_employment_decision_tool`
- `high_risk_ai_system`
- `consequential_decision_system`
- `generative_ai_system`
- `foundation_model`
- `frontier_model`
- `synthetic_media_system`
- `deepfake_generation_tool`
- `biometric_identification_system`
- `facial_recognition_system`
- `profiling_system`
- `algorithmic_recommendation_system`
- `chatbot_or_virtual_assistant`
- `healthcare_algorithmic_utilization_review_system`
- `algorithmic_pricing_system`
- `training_data_pipeline`

### 6.4 Obligation-family taxonomy

Map raw obligations, rights, and compliance mechanisms into product-usable families:

| Obligation family | Meaning |
|---|---|
| `notice` | tell affected people AI is used |
| `disclosure` | disclose AI use, synthetic content, datasets, or methods |
| `consent` | obtain affirmative permission |
| `opt_out` | allow refusal or withdrawal |
| `human_review` | provide human review or escalation |
| `appeal_or_contest` | allow challenge, appeal, or contestation |
| `explanation` | provide explanation or meaningful information |
| `impact_assessment` | perform impact, risk, or data protection assessment |
| `bias_audit` | audit for bias, discrimination, disparate impact |
| `risk_management` | establish governance, policies, controls, mitigation |
| `recordkeeping` | maintain documentation, logs, or records |
| `reporting_to_regulator` | file report or notify agency |
| `public_reporting` | publish report, inventory, or summary |
| `registration` | register system, model, or data broker |
| `data_provenance` | document source, lineage, or training data |
| `content_labeling` | label synthetic or AI-generated content |
| `watermarking` | apply machine-readable provenance or watermark |
| `incident_reporting` | report significant incident or violation |
| `vendor_due_diligence` | contract, assess, or manage third parties |
| `prohibited_use` | do not engage in specified conduct |
| `safe_harbor_compliance` | satisfy specified framework or substitute requirement |

### 6.5 Rights taxonomy

Map `rights_protection` payloads into:

- `right_to_notice`
- `right_to_access`
- `right_to_correction`
- `right_to_deletion`
- `right_to_opt_out`
- `right_to_appeal`
- `right_to_human_review`
- `right_to_explanation`
- `right_to_non_discrimination`
- `right_to_complain`
- `right_to_remedy`
- `right_to_withdraw_consent`
- `right_to_restrict_processing`

### 6.6 Enforcement taxonomy

Create a normalization layer that extracts enforcement fields from:

- standalone `enforcement` extractions;
- embedded `obligation.enforcement` objects;
- bill-level enforcement agents;
- Orrick enforcement summaries;
- IAPP enforcement notes where available.

Normalize into:

- `enforcing_body`
- `enforcement_mechanism`
- `civil_penalty`
- `criminal_penalty`
- `administrative_penalty`
- `injunctive_relief`
- `private_right_of_action`
- `cure_period`
- `safe_harbor`
- `penalty_unit`
- `maximum_penalty`
- `per_violation_rule`

### 6.7 Legal-context taxonomy

Replace product use of `preemption_signal` with `legal_context`.

| Legal context type | Meaning |
|---|---|
| `true_preemption` | explicit state, local, or federal preemption |
| `constitutional_risk` | First Amendment, commerce clause, due process, etc. |
| `agency_jurisdiction` | agency power, rulemaking, enforcement authority |
| `cross_law_reference` | reference to another law, code, chapter, or regulation |
| `safe_harbor_equivalence` | another framework or assessment satisfies the law |
| `litigation_or_injunction_signal` | stayed, enjoined, challenged, litigated |
| `federal_interaction` | relationship with federal law or agency standard |
| `other_legal_context` | lower-priority context hidden by default |

Only selected legal-context items should surface in law cards.

---

## 7. Compliance concept layer

### 7.1 Why it is needed

The run produced roughly 9.5 extractions per record on average, and some laws produced hundreds of extraction rows. This is expected for legal text, but it is not usable directly.

The law-card unit should be a `compliance_concept`, not an extraction row.

### 7.2 Definition

A compliance concept is a grouped business-facing requirement that may combine several raw extractions.

Example:

```text
Consumer opt-out right for profiling and automated decision-making
```

This concept may group:

- a definition of consumer;
- a definition of profiling;
- an obligation to provide opt-out;
- a right to opt out;
- a response deadline;
- an exception;
- an enforcement penalty;
- a tracker reference;
- one or more evidence spans.

### 7.3 Proposed `compliance_concepts` fields

| Field | Type | Description |
|---|---|---|
| `id` | integer | primary key |
| `law_card_id` | integer | FK to law card |
| `concept_type` | text | notice, assessment, opt-out, audit, penalty, etc. |
| `title` | text | human-readable concept title |
| `summary` | text | concise product-facing summary |
| `regulated_actor_family` | text | normalized actor |
| `right_holder_family` | text | nullable |
| `covered_system_type` | text | normalized system |
| `trigger_condition` | text | when it applies |
| `required_action` | text | what must be done |
| `deadline` | text | normalized or raw |
| `exceptions` | jsonb | associated exceptions |
| `enforcement_refs` | jsonb | associated enforcement data |
| `source_extraction_ids` | jsonb | raw extraction IDs |
| `tracker_ref_ids` | jsonb | Orrick/IAPP refs |
| `confidence_score` | float | concept-level confidence |
| `review_status` | text | analyst review state |

---

## 8. Required post-extraction passes

### 8.1 Tracker alignment pass

Purpose: compare extracted claims with Orrick and IAPP fields.

Outputs:

- `tracker_aligned`
- `orrick_aligned`
- `iapp_aligned`
- `tracker_conflict`
- `extraction_only_claim`
- `tracker_only_claim`

### 8.2 Actor normalization pass

Purpose: normalize raw subjects, duty bearers, actors, right holders, and enforcement bodies.

Outputs:

- normalized actor family;
- original actor string;
- confidence;
- alias mapping;
- review flag where ambiguous.

### 8.3 Law-domain classification pass

Purpose: classify each law into one or more business domains.

Inputs:

- title;
- bill number;
- Orrick scope;
- IAPP scope;
- key requirements;
- extraction content;
- source text, where available.

### 8.4 Obligation-family mapping pass

Purpose: map raw obligations, compliance mechanisms, and rights into product obligation families.

### 8.5 Enforcement normalization pass

Purpose: aggregate enforcement from multiple locations and produce one normalized enforcement record per law card or concept.

### 8.6 Threshold and exception normalization pass

Purpose: move nested exception arrays and threshold fields into normalized child rows.

### 8.7 Legal-context refactor pass

Purpose: reclassify `preemption_signal` rows into the broader `legal_context` taxonomy.

### 8.8 Deduplication and concept grouping pass

Purpose: group extractions into compliance concepts and remove duplicate or overlapping rows.

---

## 9. Verification and confidence strategy

### 9.1 Verification layers

Use the following verification sequence:

1. Schema validation.
2. Evidence-span verification.
3. Tracker alignment against Orrick and IAPP.
4. Citation verification, where source text exists.
5. Cross-validation by a separate model.
6. Gap detection by a separate model.
7. Analyst review.
8. Future lawyer review.

### 9.2 Confidence scoring

Current confidence should remain tracker-grounded.

Suggested near-term weights:

| Signal | Weight |
|---|---:|
| Orrick alignment | 30 percent |
| IAPP alignment or status match | 20 percent |
| Evidence spans verified | 15 percent |
| Citation verification | 10 percent |
| Cross-validation | 10 percent |
| Gap detection | 5 percent |
| Analyst review | 10 percent |

Rules:

- If only Orrick exists, redistribute IAPP weight.
- If only IAPP exists, use an IAPP-grounded pathway rather than forcing Tier D.
- If neither tracker exists, mark as `ungrounded` and hide from product output unless explicitly approved.
- If source evidence conflicts with tracker data, create a conflict flag, not an automatic override.

### 9.3 Review priority rules

High priority review items:

- tracker conflicts;
- extraction-only obligations with no tracker support;
- D-tier extractions that would otherwise affect a law card;
- laws with zero source extractions but high tracker importance;
- high-risk domains, such as employment, health care, insurance, and enforcement;
- parsing or JSON repair failures;
- unusually high extraction counts for one record;
- enforcement or deadline conflicts.

---

## 10. Agent-specific recommendations

### 10.1 Obligation agent

Keep as the central extraction agent, but add stronger downstream grouping.

Improvements:

- reduce obligation fragmentation;
- identify action families directly;
- require clear subject, action, object, condition;
- separate penalty provisions from business duties;
- flag passive obligations for review.

### 10.2 Definition and actor agent

This agent is valuable but needs better handling of long definitions and actor arrays.

Improvements:

- allow nullable or missing definition text only in actor-mapping mode;
- separate pure definitions from actor maps;
- normalize actor aliases immediately after extraction;
- add retry with lower token budget for long passages.

### 10.3 Threshold and exception agent

This should remain combined at extraction time but be split downstream.

Improvements:

- extract each exception as its own child object;
- normalize exception type at top level;
- normalize threshold units;
- separate scope thresholds, temporal thresholds, and entity-size thresholds.

### 10.4 Rights protection agent

This agent has relatively strong output but overlaps with obligation and compliance agents.

Improvements:

- map right types into normalized rights taxonomy;
- link each right to a corresponding duty bearer;
- group rights with related business obligations;
- distinguish individual rights from regulator powers.

### 10.5 Compliance mechanism agent

This agent needs clearer boundaries.

Improvements:

- split mechanism type into stronger categories;
- distinguish recordkeeping, reporting, audit, assessment, registration, and incident reporting;
- avoid over-extracting ordinary enforcement language as a compliance mechanism;
- add examples for privacy and automated-decision laws.

### 10.6 Preemption agent

Refactor into a legal-context agent.

Improvements:

- rename to `legal_context`;
- classify true preemption separately from agency jurisdiction and cross-references;
- reduce product visibility of low-value `other` outputs;
- prioritize federal preemption, constitutional risk, safe harbor equivalence, and litigation signals.

### 10.7 Enforcement agent

Do not rely only on standalone enforcement extraction count.

Improvements:

- create post-extraction enforcement normalizer;
- pull enforcement from obligation payloads and bill-level outputs;
- align with Orrick enforcement fields;
- create one law-card-level enforcement summary and concept-specific enforcement links.

---

## 11. Error and observability strategy

### 11.1 Fix metric ambiguity

The run summary and live monitor appear to count calls and tokens differently. Add explicit metric categories:

| Metric | Definition |
|---|---|
| `llm_call_count` | actual provider calls |
| `agent_invocation_count` | agent run attempts |
| `successful_agent_invocations` | parseable outputs |
| `extraction_item_count` | created extraction rows |
| `abstention_count` | agent chose no extraction |
| `error_count` | failed agent attempts |
| `input_tokens` | provider-reported input tokens |
| `output_tokens` | provider-reported output tokens |
| `retry_tokens` | retry token cost |
| `verification_tokens` | verification token cost |
| `bill_level_tokens` | bill-level agent token cost |

### 11.2 Common failure types to track

- JSON parse failure;
- unterminated string;
- empty LLM response;
- finish reason length;
- Pydantic validation error;
- evidence span unverified;
- tracker conflict;
- source parse failure;
- timeout;
- model loop or repetition;
- retry exhausted.

### 11.3 Acceptance criteria

- Every extraction run produces a machine-readable quality report.
- Metrics are comparable across runs.
- Failed agent outputs can be retried by failure type.
- Run summaries distinguish calls, items, tokens, errors, abstentions, and retries.

---

## 12. Data model additions

Recommended new tables:

- `tracker_refs`
- `extraction_runs`
- `verification_results`
- `normalized_actors`
- `law_domains`
- `normalized_obligation_families`
- `normalized_rights`
- `normalized_enforcement`
- `normalized_thresholds`
- `normalized_exceptions`
- `legal_contexts`
- `compliance_concepts`
- `concept_extraction_links`
- `concept_tracker_links`

---

## 13. Implementation roadmap

### Phase A: repair and stabilize

- Fix cross-validation and gap detection provider response handling.
- Persist verification results.
- Recompute confidence after verification.
- Stop destructive full-run purge.
- Clarify token and call metrics.
- Add IAPP-grounded confidence pathway.

### Phase B: normalize taxonomy

- Add law-domain classifier.
- Add actor normalizer.
- Add obligation-family mapper.
- Add enforcement normalizer.
- Add threshold and exception child tables.
- Refactor preemption into legal context.

### Phase C: build compliance concepts

- Create compliance concept schema.
- Group related extraction rows.
- Link concepts to tracker references.
- Assign concept-level confidence.
- Create concept review queue.

### Phase D: evaluation

- Build a tracker-grounded gold set.
- Measure field-level accuracy against Orrick and IAPP.
- Measure evidence verification where source exists.
- Measure concept-grouping quality.
- Track analyst correction rate.

### Phase E: feed product layer

- Publish only tracker-grounded and reviewed concepts to law-card builder.
- Support tracker-only laws.
- Mark source-supported and future legal-reviewed states separately.

---

## 14. Definition of done

The agent extraction system is ready to support product law cards when:

1. extraction runs are non-destructive and versioned;
2. verification agents run correctly and persist results;
3. confidence reflects tracker alignment, evidence, verification, and review;
4. raw extraction rows are normalized into actors, domains, rights, obligations, thresholds, exceptions, enforcement, and legal context;
5. compliance concepts group related extractions into product-usable requirements;
6. IAPP-only laws are supported without automatic downgrade;
7. tracker conflicts are surfaced and reviewable;
8. zero-extraction tracker records can still produce tracker-only cards;
9. run metrics are clear and comparable;
10. product layer consumes compliance concepts, not raw extraction rows.

---

## 15. Final recommendation

The extraction pipeline is valuable and should be strengthened, not replaced. The major strategy change is to add a normalization and concept-building layer between raw agent output and law-card generation.

The technical north star is:

```text
Raw extraction rows become evidence-bearing fragments.
Normalized fragments become compliance concepts.
Compliance concepts become tracker-grounded law cards.
```

That preserves the current agent work, keeps Orrick and IAPP as the current ground truth, and gives the product team a cleaner, business-facing foundation.
