# Normalization Vocabulary Ratification Plan

## Document status

**Audience:** policy analysts, data science, engineering, product, extraction reviewers, and future legal reviewers  
**Purpose:** define the ratification process for all normalization vocabularies used by Regs Checker.  
**Current validation posture:** Orrick and IAPP remain the current working ground truth. Normalization vocabularies must be tracker-grounded, evidence-backed, and explicitly ratified before they are used as locked dimension keys in extraction, trust checks, compliance concepts, or product law cards.  

---

## 1. Executive summary

The actor vocabulary received deep treatment first because actor normalization is the most immediate blocker for tracker alignment, applicability, and compliance-concept grouping. But actors are not unique. The same problem applies to every major normalization vocabulary:

- actor codes;
- law domains;
- covered systems;
- obligation families;
- rights;
- enforcement;
- legal context.

None of these vocabularies should be treated as final merely because they appear in a strategy document or prompt. Each vocabulary must go through the same evidence-based cycle:

```text
Inventory -> candidate codes -> fork decisions -> ratification -> locked lookup files -> normalization pass -> re-harvest or recompute
```

This document defines that cycle.

---

## 2. Core principle

Normalization vocabularies are not prompt decorations. They are data contracts.

Once a vocabulary is locked, it affects:

- tracker alignment;
- confidence scoring;
- extraction prompts;
- normalization passes;
- law-card grouping;
- applicability logic;
- analytics;
- product filters;
- downstream user claims.

Therefore, every major vocabulary must be ratified before it is used as a canonical product or trust dimension.

---

## 3. Vocabulary governance model

Each vocabulary should move through these stages.

| Stage | Name | Meaning |
|---|---|---|
| V0 | Inventory | collect raw vocabulary from Orrick, IAPP, extraction payloads, and source text |
| V1 | Candidate codes | propose Tier-1 canonical codes and Tier-2 descriptive values |
| V2 | Fork analysis | identify unresolved semantic splits or merges |
| V3 | Ratification | approve locked codes, definitions, aliases, and unresolved flags |
| V4 | Lookup publication | write canonical lookup files to `data/lookups/` |
| V5 | Normalization implementation | implement deterministic normalization passes |
| V6 | Re-harvest or recompute | re-run extraction, normalization, confidence, or law-card generation with locked codes |
| V7 | Drift monitoring | detect new raw terms or tracker changes that require vocabulary review |

---

## 4. Required ratification artifacts

Every vocabulary should produce the same artifact set.

```text
data/lookups/{vocabulary}_canonical_codes.csv
data/lookups/{vocabulary}_aliases.csv
data/lookups/{vocabulary}_fork_decisions.md
data/lookups/{vocabulary}_mapping_examples.csv
data/lookups/{vocabulary}_unresolved_terms.csv
docs/decisions/{vocabulary}_RATIFICATION_DECISION.md
```

### 4.1 Canonical codes file

Minimum fields:

| Field | Meaning |
|---|---|
| `canonical_code` | locked Tier-1 code |
| `canonical_label` | human-readable label |
| `definition` | operational definition |
| `include_when` | inclusion criteria |
| `exclude_when` | exclusion criteria |
| `tracker_basis` | Orrick, IAPP, both, source-supported, or analyst-derived |
| `status` | candidate, ratified, deprecated |
| `version` | vocabulary version |

### 4.2 Alias file

Minimum fields:

| Field | Meaning |
|---|---|
| `raw_term` | source term from tracker, extraction, or text |
| `source` | Orrick, IAPP, extraction, source text, manual |
| `canonical_code` | mapped Tier-1 code |
| `tier2_value` | source-specific or more granular value |
| `confidence` | high, medium, low |
| `example_law` | example where term appears |
| `notes` | mapping caveats |

### 4.3 Fork decision file

Each fork decision should include:

- issue name;
- decision;
- alternatives considered;
- rationale;
- examples;
- downstream implications;
- unresolved edge cases;
- owner;
- date;
- ratification status.

### 4.4 Mapping examples file

This file should provide evidence-backed examples of mappings.

Minimum fields:

| Field | Meaning |
|---|---|
| `example_id` | stable ID |
| `raw_text` | source phrase or extraction snippet |
| `source` | Orrick, IAPP, extraction, source text |
| `law_id` | relevant law |
| `canonical_code` | mapped code |
| `tier2_value` | granular value |
| `why` | explanation |

### 4.5 Unresolved terms file

Any raw term that cannot be confidently mapped should go here rather than being forced into a bad canonical code.

---

## 5. Vocabulary-specific ratification plans

## 5.1 Actors

### Purpose

Normalize duty bearers, regulated entities, protected persons, regulators, and other legal roles.

### Why it matters

Actor normalization drives:

- tracker alignment;
- obligation grouping;
- applicability logic;
- business user matching;
- enforcement analysis;
- protected-person versus duty-holder separation.

### Known forks

| Fork | Question |
|---|---|
| data handler split | Should `data_handler` split into controller and processor? |
| regulator vs government | Should regulator/enforcer split from government agency as regulated entity? |
| individual | Should individual be a canonical actor, a protected-scope flag, or both? |
| operator vs deployer | Are operator and deployer synonyms or distinct legal roles? |

### Output

- `actor_canonical_codes.csv`
- `actor_aliases.csv`
- `actor_fork_decisions.md`
- `actor_mapping_examples.csv`
- `actor_unresolved_terms.csv`

### Current status

Not ratified. Highest priority vocabulary.

---

## 5.2 Law domains

### Purpose

Classify what kind of AI or AI-adjacent law a record belongs to.

### Why it matters

Law-domain normalization drives:

- product navigation;
- state comparison;
- MVP prioritization;
- evaluation cohorts;
- user-facing filters;
- risk-area reporting.

### Candidate domains to inventory and ratify

- AI governance cross-sector;
- privacy, profiling, and automated decision systems;
- employment AI;
- health care AI;
- insurance AI;
- education AI;
- government AI and procurement;
- synthetic media and elections;
- intimate image, CSAM, likeness, and digital replica;
- consumer chatbot disclosure;
- frontier model safety;
- algorithmic pricing and competition;
- training-data transparency;
- data broker and data provenance;
- general consumer protection;
- other AI-adjacent.

### Known forks

| Fork | Question |
|---|---|
| AI-specific vs AI-adjacent | Should privacy/profiling laws be first-class AI law domains or adjacent domains? |
| synthetic media split | Should election deepfakes split from intimate image and likeness laws? |
| government AI split | Should public-sector AI use split from procurement and inventories? |
| health vs insurance | Should payer/utilization review be health care AI, insurance AI, or both? |
| data broker vs training data | Should data broker laws that affect training data be separate from training-data transparency? |

### Output

- `law_domain_canonical_codes.csv`
- `law_domain_aliases.csv`
- `law_domain_fork_decisions.md`
- `law_domain_mapping_examples.csv`
- `law_domain_unresolved_terms.csv`

### Current status

Not ratified.

---

## 5.3 Covered systems

### Purpose

Normalize the AI system, model, tool, automated process, or regulated technical object that the law covers.

### Why it matters

Covered-system normalization drives:

- applicability matching;
- product intake forms;
- compliance-concept grouping;
- use-case search;
- business relevance scoring.

### Candidate covered-system codes to inventory and ratify

- automated decision system;
- automated employment decision tool;
- high-risk AI system;
- consequential decision system;
- generative AI system;
- foundation model;
- frontier model;
- synthetic media system;
- deepfake generation tool;
- biometric identification system;
- facial recognition system;
- profiling system;
- algorithmic recommendation system;
- chatbot or virtual assistant;
- health care algorithmic utilization review system;
- algorithmic pricing system;
- training data pipeline;
- data broker system;
- content moderation or ranking system.

### Known forks

| Fork | Question |
|---|---|
| ADS vs high-risk AI | Are these separate system categories or risk states attached to systems? |
| consequential decision | Is this a system type, use-case type, or risk flag? |
| generative AI vs synthetic media | Should synthetic media be a subtype of generative AI or separate? |
| model vs system | Should foundation/frontier model be separated from deployed system? |
| profiling vs automated decision | Should profiling be its own system type or an activity? |
| biometric vs facial recognition | Should facial recognition be Tier-2 under biometric or Tier-1? |

### Output

- `covered_system_canonical_codes.csv`
- `covered_system_aliases.csv`
- `covered_system_fork_decisions.md`
- `covered_system_mapping_examples.csv`
- `covered_system_unresolved_terms.csv`

### Current status

Not ratified.

---

## 5.4 Obligation families

### Purpose

Normalize what regulated actors must do.

### Why it matters

Obligation-family normalization drives:

- compliance-concept grouping;
- business action checklists;
- product filtering;
- control mapping;
- risk and burden scoring.

### Candidate obligation-family codes to inventory and ratify

- notice;
- disclosure;
- consent;
- opt out;
- human review;
- appeal or contest;
- explanation;
- impact assessment;
- risk assessment;
- bias audit;
- recordkeeping;
- regulator reporting;
- public reporting;
- registration;
- data provenance;
- training-data summary;
- content labeling;
- watermarking;
- incident reporting;
- vendor due diligence;
- risk management;
- prohibited use;
- safe harbor compliance;
- takedown or removal;
- governance policy;
- testing and monitoring.

### Known forks

| Fork | Question |
|---|---|
| notice vs disclosure | Are these separate or should disclosure be the broader category? |
| impact assessment vs risk assessment | Should these be separate or merged? |
| bias audit vs assessment | Is bias audit a subtype of assessment or separate Tier-1? |
| public reporting vs regulator reporting | Should these split? |
| content labeling vs watermarking | Separate obligations or one provenance/labeling family? |
| opt-out vs right | Is opt-out an obligation, a right, or both linked conceptually? |
| prohibited use | Is prohibition an obligation family or separate restriction taxonomy? |

### Output

- `obligation_family_canonical_codes.csv`
- `obligation_family_aliases.csv`
- `obligation_family_fork_decisions.md`
- `obligation_family_mapping_examples.csv`
- `obligation_family_unresolved_terms.csv`

### Current status

Not ratified.

---

## 5.5 Rights

### Purpose

Normalize the rights, protections, remedies, or individual entitlements created by the law.

### Why it matters

Rights normalization drives:

- individual-rights sections of law cards;
- corresponding business duties;
- applicability for consumer, worker, patient, student, or resident contexts;
- escalation and action checklists.

### Candidate rights codes to inventory and ratify

- right to notice;
- right to access;
- right to correction;
- right to deletion;
- right to opt out;
- right to appeal;
- right to human review;
- right to explanation;
- right to non-discrimination;
- right to complain;
- right to remedy;
- right to withdraw consent;
- right to restrict processing;
- right to know AI use;
- right to contest automated decision;
- right to disclosure of synthetic media.

### Known forks

| Fork | Question |
|---|---|
| right vs duty | Should each right be linked to a corresponding duty rather than stored alone? |
| opt-out | Is opt-out primarily a right, an obligation, or both? |
| appeal vs contest | Same right or distinct? |
| explanation vs meaningful information | Same right or distinct? |
| non-discrimination | Right, prohibition, or both? |
| complaint vs remedy | Separate or merged? |

### Output

- `right_canonical_codes.csv`
- `right_aliases.csv`
- `right_fork_decisions.md`
- `right_mapping_examples.csv`
- `right_unresolved_terms.csv`

### Current status

Not ratified.

---

## 5.6 Enforcement

### Purpose

Normalize enforcement bodies, enforcement mechanisms, remedies, penalties, cure periods, and private rights of action.

### Why it matters

The extraction run produced very few standalone enforcement rows, but many obligations contain embedded enforcement fields. Enforcement must therefore be normalized across tracker fields, bill-level extractions, obligation payloads, and source text.

### Candidate enforcement codes to inventory and ratify

- attorney general enforcement;
- agency enforcement;
- commissioner enforcement;
- civil penalty;
- criminal penalty;
- administrative penalty;
- injunctive relief;
- private right of action;
- no private right of action;
- cure period;
- safe harbor;
- license suspension or revocation;
- rulemaking authority;
- investigation authority;
- complaint process;
- per-violation penalty;
- per-day penalty.

### Known forks

| Fork | Question |
|---|---|
| enforcer vs regulated government actor | Is an agency the duty holder, the regulator, or both? |
| penalty type | Should civil, criminal, administrative, and injunctive relief be separate dimensions? |
| private right of action | Enforcement mechanism, right, or both? |
| cure period | Enforcement concept, exception, or threshold? |
| rulemaking authority | Enforcement, legal context, or governance mechanism? |
| safe harbor | Enforcement mitigation, exception, or compliance mechanism? |

### Output

- `enforcement_canonical_codes.csv`
- `enforcement_aliases.csv`
- `enforcement_fork_decisions.md`
- `enforcement_mapping_examples.csv`
- `enforcement_unresolved_terms.csv`

### Current status

Not ratified.

---

## 5.7 Legal context

### Purpose

Normalize preemption, constitutional risk, agency jurisdiction, cross-law references, litigation/injunction signals, and safe-harbor equivalence.

### Why it matters

The current `preemption_signal` output is too broad. It includes true preemption, agency jurisdiction, federal interaction, First Amendment concerns, and other legal context. Product-facing law cards should not expose this as one undifferentiated category.

### Candidate legal-context codes to inventory and ratify

- true preemption;
- federal preemption;
- state/local preemption;
- constitutional risk;
- First Amendment risk;
- commerce clause risk;
- due process risk;
- agency jurisdiction;
- rulemaking authority;
- cross-law reference;
- federal interaction;
- safe-harbor equivalence;
- litigation signal;
- injunction or stay;
- conflict with other law;
- other legal context.

### Known forks

| Fork | Question |
|---|---|
| preemption vs legal context | Should preemption remain a top-level concept or become one legal-context subtype? |
| agency jurisdiction | Legal context, enforcement, or actor metadata? |
| constitutional risk | One category or separate First Amendment, commerce, due process categories? |
| cross-reference | Legal context or citation metadata? |
| safe-harbor equivalence | Legal context, exception, compliance mechanism, or enforcement mitigation? |
| litigation signal | Legal status, legal context, or product warning? |

### Output

- `legal_context_canonical_codes.csv`
- `legal_context_aliases.csv`
- `legal_context_fork_decisions.md`
- `legal_context_mapping_examples.csv`
- `legal_context_unresolved_terms.csv`

### Current status

Not ratified.

---

## 6. Cross-vocabulary dependency map

These vocabularies are not independent. Ratification should happen in an intentional order.

```text
Actors
  -> covered systems
  -> law domains
  -> obligation families
  -> rights
  -> enforcement
  -> legal context
  -> compliance concepts
  -> law cards
  -> applicability engine
```

### 6.1 Why actors come first

Actors define who has duties, who has rights, who enforces, and who is protected. Without actor normalization, obligations, rights, enforcement, and applicability cannot be reliably mapped.

### 6.2 Why covered systems should come early

Covered systems define what the law regulates. They are central to applicability and law-domain classification.

### 6.3 Why obligation families and rights should be ratified together

Many rights imply corresponding business duties. For example, a right to opt out implies an obligation to provide opt-out infrastructure.

### 6.4 Why enforcement and legal context should be ratified separately

The run shows enforcement and preemption/legal-context signals are scattered and overlapping. They must be separated before law cards can display clean risk and legal-status warnings.

---

## 7. Ratification decision template

Each vocabulary ratification decision should use the following template.

```markdown
# {Vocabulary} Ratification Decision

## Status
Candidate / Ratified / Deprecated

## Decision date
YYYY-MM-DD

## Owner
Name or team

## Grounding sources
- Orrick fields reviewed:
- IAPP fields reviewed:
- Extraction payload fields reviewed:
- Source-text examples reviewed:

## Canonical Tier-1 codes
Table of locked codes.

## Tier-2 source vocabulary policy
Describe how source-specific terms are preserved.

## Fork decisions
List major splits/merges and rationale.

## Examples
Evidence-backed mappings.

## Unresolved terms
Terms not yet ratified.

## Downstream implications
Extraction prompts, lookup files, confidence, product filters, law cards, applicability.

## Change policy
How future codes can be added or deprecated.
```

---

## 8. Change-control policy

After ratification, vocabulary changes should be treated as schema-impacting changes.

### 8.1 Adding a new canonical code

Requires:

- evidence from Orrick, IAPP, source text, or repeated extraction pattern;
- mapping examples;
- decision note;
- reviewer approval;
- version bump;
- regression check.

### 8.2 Deprecating a code

Requires:

- migration path;
- affected rows count;
- backward compatibility note;
- law-card impact analysis.

### 8.3 Adding aliases

Can be lighter weight, but still requires:

- raw term;
- source;
- canonical mapping;
- example;
- confidence.

### 8.4 Handling unresolved terms

Do not force unresolved terms into the nearest code if the semantics are unclear. Put them in unresolved files and route them to review.

---

## 9. Implementation roadmap

### Phase V0: prepare inventory scripts

Build scripts to extract raw terms from:

- Orrick tracker CSV;
- IAPP tracker CSV;
- extraction CSV;
- payload JSON fields;
- source text definitions;
- bill-level extractions.

### Phase V1: actor vocabulary ratification

Actors are first because they block tracker alignment and applicability.

### Phase V2: covered systems and law domains

Ratify covered systems and law domains together because the same terms often determine both.

### Phase V3: obligation families and rights

Ratify these together because rights and duties are paired in the product layer.

### Phase V4: enforcement and legal context

Ratify these together because enforcement, jurisdiction, safe harbor, preemption, and legal-status warnings overlap.

### Phase V5: compliance-concept schema

Only after the vocabularies are ratified should the team build compliance-concept grouping.

### Phase V6: re-harvest or recompute

Once locked vocabularies exist:

- pin prompts to vocabulary versions;
- re-run affected extraction or normalization passes;
- recompute confidence;
- rebuild compliance concepts;
- rebuild law cards.

---

## 10. Definition of done

A normalization vocabulary is ratified when:

1. raw terms have been inventoried from Orrick and IAPP;
2. extraction-output terms have been inventoried separately;
3. candidate Tier-1 codes have definitions;
4. Tier-2 source vocabulary policy is defined;
5. fork decisions are documented;
6. examples exist for each canonical code;
7. unresolved terms are explicitly listed;
8. lookup files are written to `data/lookups/`;
9. normalization pass can consume the lookup files;
10. downstream product and confidence implications are documented.

---

## 11. Final recommendation

Actors are the first ratification gate, but not the only one. Regs Checker should adopt a vocabulary governance model for all normalization dimensions.

The corrected framing is:

```text
B0 is not only the actor decision.
B0 is the pattern for ratifying every canonical normalization vocabulary.
```

Actors should go first because they are the most immediate blocker. But law domains, covered systems, obligation families, rights, enforcement, and legal context all need the same inventory-to-ratification cycle before they become locked product or trust dimensions.
