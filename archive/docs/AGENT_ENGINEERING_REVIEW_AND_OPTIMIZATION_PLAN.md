# Agent Engineering Review and Optimization Plan

## Document status

**Audience:** engineering, data science, policy analysts, extraction reviewers, product leadership, and future legal reviewers  
**Purpose:** evaluate the current coding agents and supporting agent infrastructure after PR 127, identify structural strengths and weaknesses, and define a prioritized improvement plan.  
**Scope:** passage-level agents, bill-level agents, verification agents, normalization infrastructure, compliance-concept grouping, IAPP/Orrick grounding, vocabulary loading, and product bridge layers.  
**Current validation posture:** Orrick and IAPP remain the current working ground truth. Agents are candidate-generation, structuring, verification, and normalization tools. They are not legal authorities.  

---

## 1. Executive summary

PR 127 moved Regs Checker in the correct architectural direction. The codebase now has the main ingredients of a tracker-grounded legal intelligence pipeline:

```text
tracker records
  -> source text
  -> passage-level extraction
  -> bill-level extraction
  -> verification persistence
  -> vocabulary normalization
  -> compliance-concept grouping
  -> product law-card bridge
```

The main agent architecture is now strong enough to keep. The next challenge is not to add more extraction agents. The next challenge is to make the existing agents trustworthy, observable, normalized, and product-safe.

The most important engineering conclusion is:

> The extraction agents are useful, but their raw outputs are not the product. Raw outputs must pass through verification, tracker alignment, vocabulary normalization, concept grouping, and analyst review before they become law-card claims.

The strongest parts of the current system are:

- the separation between passage-level and bill-level agents;
- the addition of deterministic Orrick parsing;
- IAPP alignment scaffolding;
- verification persistence tables;
- vocabulary lookup files and review queue;
- compliance-concept grouping;
- evidence-span verification;
- local LLM resilience and JSON repair.

The highest-risk issues are:

- cross-validation and gap detection still appear to assume the old tuple return shape from the provider;
- the tests for verification agents mock the old tuple shape and can hide the real integration bug;
- provider failure currently returns neutral or empty verification results instead of explicit failure states;
- vocabulary fallbacks can silently over-normalize unknown values;
- several vocabularies are present as lookup files but should still be treated as provisional until ratified;
- concept grouping is correct in principle but too coarse for some legal duties;
- enforcement and exception references are attached law-wide to every concept, which is acceptable for MVP but legally imprecise.

---

## 2. Current agent architecture

The codebase has five agent or agent-adjacent layers:

1. **Passage-level extraction agents**  
   Run on individual normalized source records or sections.

2. **Bill-level extraction agents**  
   Run once per law or bill to capture whole-document applicability, enforcement, and timelines.

3. **Verification agents**  
   Cross-validate extractions, detect missed obligations, and verify citations.

4. **Normalization agents and utilities**  
   Load lookup files, normalize actors and other dimensions, classify legal context, normalize enforcement, and queue unresolved vocabulary.

5. **Product bridge agents and utilities**  
   Group raw extractions into compliance concepts and adapt payloads for downstream systems.

This layered model is correct. The main optimization task is to sharpen the boundary between these layers.

---

## 3. Architectural strengths

## 3.1 Passage-level plus bill-level split is the right design

Passage-level agents are good at extracting local legal fragments, such as obligations, definitions, thresholds, rights, and compliance mechanisms. Bill-level agents are necessary because enforcement, effective dates, applicability, and scope often depend on cross-section context.

This split should remain. It prevents passage-level agents from trying to infer whole-law meaning from a single clause.

## 3.2 Orrick and IAPP grounding is now part of the architecture

The pipeline now reflects the right trust posture: Orrick and IAPP are the current reference sources, while LLM outputs are structured candidate interpretations.

The deterministic Orrick facts parser is especially valuable. It reduces unnecessary LLM calls and ensures that tracker-provided facts can directly populate bill-level outputs.

The IAPP alignment module is also a major improvement because it moves IAPP from passive metadata into active trust checking.

## 3.3 Verification persistence is the right destination

The new verification run summary and extraction verification status models are the right shape. They make it possible to preserve:

- cross-validation results;
- gap detection summaries;
- citation verification results;
- confidence before and after recompute;
- Orrick status;
- IAPP status;
- grounding status.

This is foundational for auditability.

## 3.4 Vocabulary lookup infrastructure is the right direction

The new lookup files and `vocab_loader` module move the system toward a data-contract model. That is essential because actors, law domains, covered systems, obligation families, rights, enforcement, and legal context cannot live only inside prompts.

The `vocab_review_queue` is also a strong addition. Unknown terms should be routed to review rather than silently accepted.

## 3.5 Compliance concepts are the right product bridge

The compliance-concept layer is the correct bridge between raw extraction rows and law cards. The product should not consume raw extractions directly. It should consume grouped, normalized, tracker-grounded concepts.

This is one of the most important architectural improvements in PR 127.

---

## 4. Cross-cutting structural problems

## 4.1 Verification agents still appear to depend on the old provider interface

The provider returns an object with fields like `text`, `usage`, `model_id`, and `stop_reason`. The cross-validation and gap-detection agents still appear to unpack the provider result as a tuple.

This creates a serious trust risk:

```python
raw_output, usage, model_id, stop_reason = provider.call(...)
```

The correct pattern should be:

```python
response = provider.call(...)
raw_output = response.text
usage = response.usage
model_id = response.model_id
stop_reason = response.stop_reason
```

This is the highest-priority fix because verification is now wired into confidence and persistence.

## 4.2 Verification failures should fail closed

Provider failure should not become a neutral pass.

Current risk pattern:

```text
cross-validation failure -> neutral-ish score
gap-detection failure -> zero gaps
```

Correct pattern:

```text
cross-validation failure -> verification_failed
gap-detection failure -> gap_detection_failed
no confidence boost
review priority increases
run summary records failure
```

A verification layer that silently passes on failure is worse than no verification layer because it creates false confidence.

## 4.3 Tests currently reinforce the old provider shape

The unit tests for cross-validation and gap detection mock the provider as a tuple return. They should mock the real `LLMResponse` object.

This should be changed before the next extraction run is treated as trustworthy.

## 4.4 Vocabulary fallbacks can produce false certainty

The vocabulary loader currently returns fallback canonical codes for unrecognized values. This is useful for keeping the pipeline moving, but risky for product output.

The actor fallback is especially risky:

```text
unknown actor -> regulated_entity
```

This can make an unmapped value look like a valid broad actor. Better behavior:

```text
canonical_code = REVIEW_unclassified
fallback_display_code = regulated_entity
normalization_status = unmapped
review_required = true
```

The same issue applies to covered systems, law domains, rights, enforcement, and legal context.

## 4.5 Candidate vocabularies should not be treated as fully ratified

The lookup files are extremely useful, but they should not be treated as permanently ratified merely because they exist.

Every vocabulary should carry explicit status:

```text
candidate
candidate_locked_for_run1
ratified
ratified_with_exceptions
deprecated
```

This matters because product claims should distinguish between:

- tracker-grounded values;
- source-supported values;
- extraction-inferred values;
- analyst-ratified values;
- unresolved or fallback values.

## 4.6 Concept grouping is useful but too coarse

The current grouping key is approximately:

```text
document_version_id + concept_type + regulated_actor_family
```

This is a good MVP key, but it can merge distinct obligations when the same actor has multiple duties of the same family.

Recommended future key:

```text
document_version_id
concept_type
regulated_actor_family
covered_system_type
right_holder_family
trigger_condition_hash
section_cluster
```

Do not overfit this immediately. Start by adding `covered_system_type` and a normalized trigger hash.

## 4.7 Enforcement and exceptions are attached too broadly

The concept grouping layer currently attaches law-wide enforcement and exception references to every concept. That is acceptable as a temporary fallback, but it is imprecise.

A future relation model should classify each enforcement or exception link as:

```text
law_wide
section_matched
obligation_linked
tracker_inferred
unknown
```

This will allow product cards to say whether an exception truly applies to a specific concept or is merely law-wide context.

---

## 5. Passage-level agent evaluation

## 5.1 Base extraction agent

### Role

Shared infrastructure for passage-level extraction. Handles prompt rendering, LLM calls, retries, JSON repair, evidence-span verification, schema validation, token accounting, deduplication, and result metadata.

### Strengths

- Strong shared abstraction.
- YAML prompt support.
- Local model resilience.
- JSON repair and fence stripping.
- Evidence-span verification.
- Adaptive retry on truncation.
- Deduplication of repeated extraction items.
- Prompt hashing and model metadata.

### Structural problems

- The base result still supports `model_reasoning`, and parts of the pipeline may persist truncated reasoning into metadata.
- Return-type documentation has drifted in some places.
- JSON repair logic is strong but should be shared by bill-level and verification agents too.

### Recommendations

1. Remove persisted model reasoning from metadata.
2. Keep model diagnostics, prompt hash, model ID, stop reason, token usage, and parse errors.
3. Centralize JSON repair utilities in one module.
4. Add strict tests for malformed JSON, truncation, empty response, and stop-reason length.

### Evaluation

**Grade: B+**

Strong foundation. Needs governance cleanup and shared utility extraction.

---

## 5.2 Obligation agent

### Role

Extracts legal duties, including subject, modality, action, object, condition, timeline, enforcement, safe harbor, consent, preemption signals, and interpretation risks.

### Strengths

- Highest-value passage agent.
- Good recall for legal duties.
- Captures obligation context that often co-occurs in statutes.
- Includes useful fields for timelines, enforcement, safe harbors, and consent.
- Produces core input for compliance concepts.

### Structural problems

- Too many semantic responsibilities are packed into one payload.
- Enforcement, timeline, preemption, consent, and safe harbor fields need downstream normalization.
- `subject_normalized` should not be treated as final until actor vocabulary is ratified.
- Preemption/legal-context signals should eventually move out of the obligation agent.

### Recommendations

1. Treat obligation outputs as raw duty fragments, not final product obligations.
2. Rename internal normalized actor fields as candidate codes until ratification is complete.
3. Move preemption/legal-context extraction responsibility to the legal-context agent.
4. Feed embedded enforcement to the enforcement normalizer.
5. Add obligation-family mapping after extraction, not inside the prompt.

### Evaluation

**Grade: B**

Powerful and necessary, but semantically overloaded. Keep it broad for recall, then normalize downstream.

---

## 5.3 Definition and actor agent

### Role

Extracts definitions, actor mappings, and framework references.

### Strengths

- Strategically essential for vocabulary ratification.
- Captures raw definitional material needed for actors, systems, and legal roles.
- Helps build source-grounded inventories.
- Useful for framework references and crosswalks.

### Structural problems

- Actor type outputs still depend on unratified vocabulary.
- Some actor mappings may appear without a formal definition, but the schema may still be definition-centered.
- Actor extraction and definition extraction are related but should feed separate downstream inventories.

### Recommendations

1. Split downstream outputs into three inventories:
   - definition inventory;
   - actor inventory;
   - framework reference inventory.
2. Preserve raw actor terms even when normalized fields are null.
3. Route unresolved actor values into `vocab_review_queue`.
4. Use this agent as the primary source for vocabulary evidence, not direct product claims.

### Evaluation

**Grade: B-**

Strategically important, but still limited by unratified actor and definition schemas.

---

## 5.4 Threshold and exception agent

### Role

Extracts scope thresholds, temporal thresholds, applicability thresholds, and exemptions.

### Strengths

- Strong fit for applicability logic.
- Captures revenue, employee, consumer, compute, sector, and temporal thresholds.
- The `threshold_sub_type` distinction is a major improvement.
- Directly supports business intake matching.

### Structural problems

- Exceptions are still nested in threshold payloads.
- Temporal thresholds, compliance deadlines, cure periods, response deadlines, and effective dates can blur together.
- Applicability thresholds should not be merged with compliance timelines.

### Recommendations

1. Normalize into child tables:
   - `normalized_thresholds`;
   - `normalized_exceptions`.
2. Add threshold type taxonomy:
   - entity-size threshold;
   - revenue threshold;
   - consumer/data threshold;
   - compute threshold;
   - geography threshold;
   - sector threshold;
   - temporal threshold;
   - age or child threshold.
3. Link thresholds and exceptions to obligations or concepts when possible.

### Evaluation

**Grade: B+**

One of the strongest agents for product applicability, but it needs child-table normalization.

---

## 5.5 Rights protection agent

### Role

Extracts rights and protections granted to individuals, consumers, applicants, workers, patients, students, or other protected groups.

### Strengths

- Essential for law-card individual-rights sections.
- Links rights to duty bearers.
- Captures remedies and protected categories.
- Helps map the business duty that corresponds to an individual right.

### Structural problems

- The agent can infer implied rights from obligations, which can overstate legal text if not labeled clearly.
- `right_holder_normalized` and `duty_bearer` depend on actor vocabulary.
- Rights and obligations overlap and need paired concept grouping.

### Recommendations

1. Add `right_basis`:

```text
explicit
implied_from_obligation
tracker_inferred
source_inferred
```

2. Link rights to corresponding obligation families where possible.
3. Separate right-holder actor normalization from duty-bearer normalization.
4. Flag implied rights for analyst review before product display.

### Evaluation

**Grade: B**

Useful and product-relevant, but implied-right logic needs stronger controls.

---

## 5.6 Compliance mechanism agent

### Role

Extracts procedural requirements, such as audits, assessments, reporting, registration, recordkeeping, certification, incident reporting, red teaming, NIST references, and third-party review.

### Strengths

- Directly maps to business action checklists.
- Captures structured operational details that generic obligations flatten.
- Useful for product, compliance, data science, privacy, and procurement teams.

### Structural problems

- `mechanism_type` overlaps with obligation families.
- Responsible-party normalization depends on actor vocabulary.
- Some enforcement, reporting, and assessment signals can overlap with other agents.

### Recommendations

1. Map `mechanism_type` into ratified `obligation_family` values.
2. Distinguish:
   - public disclosure;
   - individual notice;
   - regulator reporting;
   - internal recordkeeping;
   - third-party audit;
   - recurring assessment;
   - incident reporting.
3. Use this agent as a high-quality source for business action checklists.
4. Add more examples for privacy and automated-decision laws.

### Evaluation

**Grade: B-**

Very useful, but vocabulary overlap must be cleaned up before product use.

---

## 5.7 Preemption or legal-context agent

### Role

Currently extracts preemption signals, agency jurisdiction, constitutional concerns, cross-state conflicts, federal interaction, and cross-law references.

### Strengths

- Captures legal context that can materially affect risk.
- Cross-law references are important for source trails and product warnings.
- The new legal-context classifier is a good move away from the broad preemption bucket.

### Structural problems

- The agent is still conceptually broader than its name.
- Constitutional-risk inference is high-risk without future legal review.
- The current legal-context classifier is narrower than the full desired vocabulary.
- Low-value `other` outputs need to be hidden by default.

### Recommendations

1. Rename or reframe as `LegalContextAgent`.
2. Use ratified legal-context codes:
   - true preemption;
   - constitutional limit;
   - interstate conflict;
   - agency jurisdiction;
   - cross-law reference;
   - litigation or injunction signal;
   - safe-harbor equivalence;
   - federal interaction;
   - unclassified.
3. Require analyst review for constitutional-risk and litigation-style product claims.
4. Hide unclassified rows by default.

### Evaluation

**Grade: C+**

Useful signal, but too interpretive and too broad. Needs strict product gating.

---

## 6. Bill-level agent evaluation

## 6.1 Enforcement agent

### Role

Extracts law-level enforcement details, including enforcing body, civil penalty, penalty unit, cure period, private right of action, and criminal penalties.

### Strengths

- Correctly runs at bill level.
- Compensates for passage-level enforcement sparsity.
- Works well with the enforcement normalizer.
- Directly supports product risk scoring.

### Structural problems

- One enforcement row per law can flatten multiple enforcement regimes.
- Some laws have different penalties by provision or actor.
- Orrick parser coverage may be partial but still skip LLM enrichment.

### Recommendations

1. Evolve output from one object to:

```json
{
  "summary": {},
  "provisions": []
}
```

2. Add field-level provenance.
3. Add `coverage_status` for Orrick facts:

```text
none
partial
sufficient
complete
```

4. Link enforcement to concepts using relation scope.

### Evaluation

**Grade: B**

Correct architecture. Needs multi-provision support and better coverage scoring.

---

## 6.2 Applicability agent

### Role

Extracts who and what the law applies to across the whole bill.

### Strengths

- Extremely important for product value.
- Correctly runs at bill level.
- Captures covered entities, sectors, AI systems, thresholds, geographic scope, exemptions, and government-only status.

### Structural problems

- Covered entity, sector, and AI system values are still partly hard-coded or prompt-defined.
- Applicability should use ratified actor, law-domain, covered-system, and sector vocabularies.
- The line between covered system, use case, sector, and risk category is still not fully normalized.

### Recommendations

1. Load allowed values from lookup files once ratified.
2. Treat current outputs as candidates:

```text
covered_entity_type_candidates
covered_sector_candidates
covered_system_type_candidates
```

3. Add deterministic post-processing:
   - actor normalization;
   - covered-system normalization;
   - law-domain classification;
   - threshold and exemption linking.
4. Use applicability outputs as law-level context for compliance-concept grouping.

### Evaluation

**Grade: B-**

Product-critical, but depends on vocabulary ratification before it can drive user-facing applicability.

---

## 6.3 Compliance timeline agent

### Role

Extracts effective dates, enforcement start dates, sunset dates, key deadlines, assessment frequencies, response days, cure periods, and first compliance action.

### Strengths

- Well-scoped.
- Bill-level context is appropriate.
- Directly useful for law cards and action checklists.
- Cleaner than most other agents.

### Structural problems

- Date precision can be over-normalized if the law only provides a year.
- Cure period overlaps with enforcement.
- Response deadline overlaps with rights and obligations.

### Recommendations

1. Add date precision fields:

```text
date_value
date_precision
normalized_date
date_assumption
source_text
```

2. Do not default year-only dates to January 1 without an explicit assumption flag.
3. Link deadlines to concepts where possible.
4. Keep cure periods synchronized with enforcement normalizer.

### Evaluation

**Grade: B+**

Strong and product-relevant. Needs date precision safeguards.

---

## 7. Verification agent evaluation

## 7.1 Cross-validation agent

### Role

Second-pass model reviews extractions against the source passage for hallucination, contradiction, or unsupported fields.

### Strengths

- Strategically necessary.
- Now connected to confidence recomputation and persistence.
- Can become a major trust signal.

### Structural problems

- Appears to use old provider tuple return shape.
- Failure returns neutral-ish results.
- Tests currently mock the old tuple shape.

### Recommendations

1. Fix provider handling.
2. Update tests to mock `LLMResponse`.
3. Change failure output to explicit failure status.
4. Add confidence regression test:
   - failed CV cannot improve confidence;
   - missing CV cannot be treated as valid CV;
   - low CV lowers score.
5. Persist verification failure as a first-class status.

### Evaluation

**Grade: D until fixed, B potential**

The concept is right, but the current implementation is unsafe until the provider bug and failure semantics are fixed.

---

## 7.2 Gap detector

### Role

Second-pass model identifies missed obligations or extraction gaps.

### Strengths

- Necessary because zero-extraction records and abstentions matter.
- Helps detect false negatives.
- Useful for improving prompts and recovery extraction.

### Structural problems

- Appears to use old provider tuple return shape.
- Failure returns zero gaps.
- No-gap and failed-gap-detection are currently too easy to confuse.

### Recommendations

1. Fix provider handling.
2. Update tests to mock `LLMResponse`.
3. Return explicit failure status.
4. Add dashboard metric:

```text
gap_detection_status = passed | failed | skipped
```

5. Route failed gap detection to review rather than treating it as clean.

### Evaluation

**Grade: D until fixed, B potential**

Important agent, but zero gaps on failure is not acceptable for trust-sensitive workflows.

---

## 7.3 Citation verifier

### Role

Rule-based citation and section-reference checker.

### Strengths

- Deterministic and cheap.
- Good fit for citation validation.
- Does not need an LLM for most tasks.

### Structural problems

- Documentation may overstate LLM fallback behavior.
- Citation verification is not the same as legal support verification.

### Recommendations

1. Keep it deterministic.
2. Update docs to match actual implementation.
3. Persist citation failures into verification status.
4. Distinguish:
   - citation exists;
   - evidence span verified;
   - tracker confirms claim;
   - legal interpretation reviewed.

### Evaluation

**Grade: B**

Good deterministic verifier. Needs clearer documentation and status semantics.

---

## 8. Normalization and product bridge evaluation

## 8.1 Vocabulary loader

### Strengths

- Centralizes lookup-driven normalization.
- Supports all major dimensions.
- Queues unknown values for review.
- Enables a proper ratification workflow.

### Structural problems

- Fallbacks can look like canonical matches.
- Does not return normalization metadata, only the canonical code.
- Does not yet include vocabulary versioning or ratification status.

### Recommendations

Return a richer object:

```python
NormalizedValue(
    dimension="actor",
    raw_value="operator",
    canonical_code="operator",
    tier2_value="operator",
    status="matched",
    vocabulary_version="actors_v0.1",
    review_required=False,
)
```

Use statuses:

```text
matched
alias_matched
fallback
unmapped
review_required
deprecated
```

### Evaluation

**Grade: B**

Right foundation. Needs richer status handling.

---

## 8.2 IAPP alignment

### Strengths

- Adds IAPP as an active alignment source.
- Three-state model is useful.
- Indexing by full jurisdiction and abbreviation is practical.

### Structural problems

- IAPP scope to actor mapping is hard-coded.
- Alignment currently focuses on actor scope, not obligation column alignment.
- It does not yet generate tracker conflict details rich enough for analyst review.

### Recommendations

1. Move IAPP scope mapping to lookup files.
2. Add obligation-family alignment using IAPP obligation columns.
3. Add conflict reason fields:

```text
iapp_scope_mismatch
iapp_obligation_mismatch
iapp_status_conflict
iapp_tracker_silent
```

4. Preserve raw IAPP column evidence in tracker references.

### Evaluation

**Grade: B-**

Important addition, but still incomplete as a trust checker.

---

## 8.3 Enforcement normalizer

### Strengths

- Correctly recognizes that enforcement facts are scattered.
- Merges Orrick, future IAPP, bill-level, and obligation-level enforcement.
- Uses source precedence.
- Records field provenance.

### Structural problems

- IAPP facts are only future-facing.
- One merged record can hide multiple provision-specific penalties.
- Field-level conflicts need explicit representation.

### Recommendations

1. Add conflict detection when lower-priority sources disagree with Orrick or IAPP.
2. Add multi-provision enforcement support.
3. Add relation scope when linking enforcement to concepts.
4. Persist normalized enforcement records rather than only returning dicts.

### Evaluation

**Grade: B+**

Strong and aligned with strategy. Needs persistence and conflict handling.

---

## 8.4 Legal-context classifier

### Strengths

- Correctly reframes preemption as legal context.
- Preserves raw conflict type.
- Adds display gating for low-value rows.

### Structural problems

- Vocabulary is narrower than the full legal-context plan.
- Some constitutional risk claims need future legal review.
- Safe-harbor equivalence and litigation signals are not fully represented.

### Recommendations

1. Expand legal-context vocabulary.
2. Add legal-review-required flag for high-risk legal interpretations.
3. Split true preemption from constitutional limits more explicitly.
4. Preserve cross-law references as citation metadata too.

### Evaluation

**Grade: B-**

Good first pass. Needs broader vocabulary and stricter product gating.

---

## 8.5 Compliance-concept grouping

### Strengths

- Correctly defines the product unit as a concept, not a raw extraction.
- Deterministic and testable.
- Links concepts to extraction members and tracker references.
- Flags D-tier and tracker-conflict concepts for review.

### Structural problems

- Grouping key is too coarse.
- Covered system is present in the model but not deeply used in grouping.
- Enforcement and exceptions attach broadly to every concept.
- Concept summaries are thin joins of action strings.

### Recommendations

1. Add covered-system and trigger hash to grouping.
2. Add relation scope for enforcement and exception links.
3. Add concept type confidence based on normalized vocabulary status.
4. Generate product summaries only after concept review or with clear provisional labeling.
5. Add concept-level evidence coverage metrics.

### Evaluation

**Grade: B**

Strategically correct and valuable. Needs precision improvements before product law cards.

---

## 9. Optimization roadmap

## Phase 0: Immediate trust fixes

### Goal

Prevent false confidence from verification failures.

### Tasks

- Fix provider response handling in cross-validation.
- Fix provider response handling in gap detection.
- Update verification tests to mock `LLMResponse`.
- Replace neutral failure behavior with explicit failure states.
- Ensure failed verification cannot improve confidence.
- Remove persisted model reasoning from extraction metadata.

### Acceptance criteria

- Real provider and mocked provider use the same interface.
- Verification failure is visible in run summaries.
- Gap-detection failure is not counted as zero gaps.
- Cross-validation failure is not counted as neutral accuracy.

---

## Phase 1: Vocabulary status and fallback safety

### Goal

Make normalization honest and reviewable.

### Tasks

- Add vocabulary version and ratification status to lookup files.
- Update `vocab_loader.normalize()` to return a rich normalization object.
- Add status fields: matched, alias matched, fallback, unmapped, review required.
- Stop returning `regulated_entity` as a clean actor match for unknown actors.
- Flush unrecognized values to `vocab_review_queue` with source and dimension.

### Acceptance criteria

- Product code can distinguish matched from fallback values.
- Unmapped values never look cleanly ratified.
- Every fallback value is reviewable.

---

## Phase 2: Lookup-driven agent prompts

### Goal

Prevent prompt/code vocabulary drift.

### Tasks

- Load allowed actor codes from lookup files.
- Load covered-system codes from lookup files.
- Load obligation-family codes from lookup files.
- Load rights codes from lookup files.
- Move IAPP scope to actor mapping into a lookup artifact.
- Add prompt version metadata tied to vocabulary versions.

### Acceptance criteria

- No hard-coded canonical vocabulary lists in prompts or alignment modules unless explicitly temporary.
- Vocab changes can trigger prompt version changes.
- Tests confirm prompt allowed values match lookup files.

---

## Phase 3: Concept grouping precision

### Goal

Reduce over-merging and imprecise law-wide attachments.

### Tasks

- Add `covered_system_type` to grouping logic.
- Add `trigger_condition_hash` or normalized condition key.
- Add relation scope for enforcement and exception links.
- Add concept evidence coverage metrics.
- Add concept review queue filters for overbroad concepts.

### Acceptance criteria

- Distinct obligations of the same family can remain distinct when legally meaningful.
- Law-wide enforcement is labeled as law-wide.
- Concept summaries can explain which extraction rows support them.

---

## Phase 4: IAPP alignment expansion

### Goal

Make IAPP alignment more than actor-scope matching.

### Tasks

- Map IAPP obligation columns to obligation-family codes.
- Add IAPP obligation alignment score.
- Add IAPP status alignment.
- Add tracker conflict records for IAPP mismatches.
- Add analyst review filters for IAPP conflicts.

### Acceptance criteria

- IAPP can confirm actor scope and obligation families.
- IAPP conflicts are distinguishable from IAPP silence.
- IAPP alignment can affect confidence without overclaiming.

---

## Phase 5: Agent efficiency and cost optimization

### Goal

Reduce unnecessary LLM calls without lowering recall.

### Tasks

- Use Orrick parser coverage scores to skip bill-level agents only when coverage is sufficient or complete.
- Use IAPP tracker fields to provide grounding context before calling LLMs.
- Track per-agent abstention rates by law domain.
- Add model-specific prompt variants for agents with high JSON failure rates.
- Use shorter prompts for low-risk extraction types.

### Acceptance criteria

- Skip decisions are explainable.
- Token usage is reported by agent, verification layer, and retry path.
- Abstention and failure rates are visible per agent.
- Reducing calls does not reduce gold-set recall beyond an agreed threshold.

---

## 10. Recommended agent ownership model

| Agent or layer | Primary owner | Review partner |
|---|---|---|
| Base extraction agent | engineering | data science |
| Obligation agent | data science | policy analyst |
| Definition and actor agent | policy analyst | data science |
| Threshold and exception agent | data science | product |
| Rights protection agent | policy analyst | product |
| Compliance mechanism agent | product/data science | compliance analyst |
| Legal context agent | policy analyst | future legal reviewer |
| Enforcement agent | policy analyst | engineering |
| Applicability agent | product | policy analyst |
| Timeline agent | product | engineering |
| Cross-validation | data science | engineering |
| Gap detection | data science | policy analyst |
| Citation verifier | engineering | policy analyst |
| Vocabulary loader | engineering | policy analyst |
| Compliance concepts | product/engineering | data science |

---

## 11. Definition of done for an optimized agent system

The agent system is ready to support reliable law-card generation when:

1. verification agents use the correct provider interface;
2. verification failures fail closed;
3. verification results persist and affect confidence honestly;
4. all major vocabularies have version and ratification status;
5. unknown vocabulary values are visibly unmapped and routed to review;
6. IAPP alignment checks actor scope and obligation families;
7. concept grouping uses enough dimensions to avoid obvious over-merging;
8. enforcement and exceptions are linked with relation scope;
9. model reasoning is not persisted as product metadata;
10. every product-facing concept has tracker grounding, source evidence, or an explicit warning;
11. tests use the same provider interfaces as production;
12. prompt versions are tied to vocabulary versions.

---

## 12. Final recommendation

The current agent set is sufficient. Do not add new extraction agents yet.

Instead, optimize the system in this order:

```text
fix verification
  -> make normalization status explicit
  -> ratify and version vocabularies
  -> move prompt vocabularies to lookup-driven values
  -> improve concept grouping precision
  -> expand IAPP alignment
  -> optimize cost and recall
```

The core design should remain:

```text
agents generate structured fragments
verification tests those fragments
normalization gives them stable meaning
concept grouping turns them into business requirements
law cards turn requirements into product guidance
```

This preserves the strengths of PR 127 while addressing the main structural risks before the system is used for customer-facing or policy-facing law cards.
