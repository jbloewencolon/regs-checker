# Product Strategy: Tracker-Grounded AI Law Cards

## Document status

**Audience:** product, business, policy, design, go-to-market, analyst review, and engineering leadership  
**Purpose:** define the pragmatic product strategy for turning Regs Checker into a business-facing AI policy intelligence tool built around state law cards, applicability checks, and compliance action guidance.  
**Current validation posture:** Orrick and IAPP are the current working ground truth. The product should be transparent that it provides tracker-grounded legal information and regulatory intelligence, not lawyer-validated legal advice.  
**Relationship to technical strategy:** this product strategy depends on the agent extraction system, but it should not expose raw extraction rows directly. The product consumes tracker-grounded compliance concepts and law cards.

---

## 1. Executive summary

Regs Checker should become a practical business-facing product that helps organizations understand U.S. state AI laws and policies without drowning them in legal text.

The product should answer:

1. What law or policy exists in this state?
2. Does it appear relevant to my business, AI product, sector, or use case?
3. What actions should my team consider?
4. What is the source basis for the answer?
5. What remains uncertain, stale, or not yet legally reviewed?

The product should not claim to replace lawyers. It should create structured, tracker-grounded intelligence that helps business leaders, product teams, compliance teams, and policymakers make better initial decisions.

The product’s core artifact is the **AI Law Card**.

---

## 2. Product positioning

### 2.1 What the product is

Regs Checker is a **tracker-grounded AI regulatory intelligence platform**.

It helps users understand state AI laws by combining:

- Orrick and IAPP tracker data;
- attached official source text where available;
- structured extraction and normalization;
- analyst review;
- business-facing summaries;
- applicability logic;
- compliance action checklists.

### 2.2 What the product is not

The product is not:

- a law firm;
- a replacement for counsel;
- a guarantee of compliance;
- a final legal opinion;
- a raw legislative tracker;
- a generic chatbot summarizer.

### 2.3 Recommended product language

Use:

- “Tracker-grounded law card”
- “Based on Orrick and IAPP reference data”
- “Analyst reviewed”
- “Official source attached”
- “Source-supported”
- “Future legal review available”
- “Not legal advice”

Avoid:

- “Legally verified”
- “Counsel approved”
- “Compliant”
- “Safe to deploy”
- “Authoritative legal conclusion”

---

## 3. Primary users

### 3.1 Startup founder or product leader

Needs:

- Can I launch this AI product in a state?
- What disclosures, assessments, audits, or notices might I need?
- Which laws should I care about first?
- What should I ask my lawyer later?

Product value:

- fast triage;
- plain-English explanation;
- action checklist;
- state-by-state comparison.

### 3.2 Enterprise compliance or privacy team

Needs:

- Which state laws affect our AI systems?
- What controls should we map to each law?
- Where do we need impact assessments, notices, opt-outs, or audits?
- What changed since last review?

Product value:

- structured law cards;
- applicability profile;
- control mapping;
- stale-card alerts;
- exportable reports.

### 3.3 Product counsel or outside counsel support

Needs:

- Which laws should I review?
- What did the system extract and from what source?
- Where are conflicts or ambiguity?
- What evidence supports the card?

Product value:

- evidence trail;
- tracker references;
- extraction payloads;
- review queue;
- future legal review package.

### 3.4 State policymaker or policy analyst

Needs:

- How does one state compare with another?
- What types of obligations are emerging?
- What sectors are regulated?
- What gaps or overlaps exist?

Product value:

- matrix view;
- policy taxonomy;
- state comparison;
- trend analysis.

### 3.5 Procurement officer or buyer

Needs:

- What should we ask AI vendors?
- Which state requirements affect procurement?
- What documentation should vendors provide?

Product value:

- procurement checklists;
- vendor due diligence prompts;
- documentation requirements;
- risk flags.

---

## 4. Core product artifact: AI Law Card

### 4.1 Law card purpose

A law card translates a state AI law or AI-adjacent law into a concise business-facing summary.

It should answer:

- What is the law?
- What is its status?
- Who does it apply to?
- What systems or activities are covered?
- What are the major obligations?
- What rights or protections does it create?
- What penalties or enforcement mechanisms matter?
- What should a business do next?
- What is the grounding source?
- What is uncertain?

### 4.2 Law card levels

The product should support multiple levels of confidence and completeness.

| Level | Label | Meaning |
|---|---|---|
| Level 1 | Tracker-only card | Orrick/IAPP record exists, but source extraction is limited or absent |
| Level 2 | Source-supported card | Tracker data plus attached source text and verified evidence spans |
| Level 3 | Analyst-reviewed card | Analyst has reviewed tracker alignment and product card quality |
| Level 4 | Future legal-reviewed card | Counsel has reviewed legal interpretation in a future phase |

This matters because some records produce zero extractions but still have valuable Orrick or IAPP grounding.

### 4.3 Law card sections

Recommended sections:

1. **Plain-English summary**
2. **Status and effective dates**
3. **Tracker grounding**
4. **Who should care**
5. **Covered actors**
6. **Covered systems or activities**
7. **Covered sectors or use cases**
8. **Main business obligations**
9. **Individual rights or protections**
10. **Assessments, audits, reporting, and documentation**
11. **Exceptions and thresholds**
12. **Penalties and enforcement**
13. **Business action checklist**
14. **Open questions and ambiguity**
15. **Evidence and source trail**
16. **Future counsel review notes**

---

## 5. Product taxonomy

The product should not expose raw agent extraction types. Instead, law cards should use a pragmatic business taxonomy.

### 5.1 Law domains

| Domain | Business meaning |
|---|---|
| AI governance | broad AI governance, high-risk AI, general AI duties |
| Privacy and profiling | privacy, automated decisions, profiling, data rights |
| Employment AI | hiring, promotion, performance, worker screening |
| Health care AI | utilization review, clinical use, payer systems, hospital AI |
| Insurance AI | insurance models, discrimination, underwriting, pricing |
| Education AI | school, student, and educational technology rules |
| Government AI | agency use, procurement, inventories, public benefits |
| Synthetic media and elections | deepfakes, election ads, political synthetic content |
| Intimate image and likeness | CSAM, intimate images, voice/likeness, digital replicas |
| Consumer chatbot disclosure | consumer-facing bot or AI interaction disclosure |
| Frontier model safety | foundation or frontier model safety and reporting |
| Algorithmic pricing | pricing, competition, collusion, rent or hotel pricing |
| Training data transparency | data provenance, training data summaries, dataset disclosures |
| Other AI-adjacent | relevant but not directly AI-specific |

### 5.2 Business action categories

| Category | Example action |
|---|---|
| Disclose | tell users AI is used or content is synthetic |
| Notify | provide notice before or during AI use |
| Assess | complete risk, impact, or data protection assessment |
| Audit | conduct bias audit or algorithmic audit |
| Document | maintain model, data, governance, and decision records |
| Report | file or publish required reports |
| Register | register system, data broker, or model where required |
| Enable rights | provide opt-out, appeal, human review, or explanation |
| Restrict use | avoid prohibited uses or sensitive contexts |
| Manage vendors | require contracts, attestations, audit rights, and documentation |
| Monitor | track performance, bias, incidents, and drift |
| Escalate | send to counsel, compliance, privacy, security, or executive review |

### 5.3 Product risk dimensions

Use qualitative risk scoring, not fake numerical compliance scoring.

| Dimension | Meaning |
|---|---|
| Applicability likelihood | how likely the law applies to the user’s facts |
| Obligation burden | how much work the law appears to require |
| Deadline urgency | how soon action may be needed |
| Enforcement exposure | whether penalties or regulators are significant |
| Ambiguity level | how uncertain the interpretation is |
| Documentation burden | how much evidence or process must be created |
| Tracker confidence | whether Orrick, IAPP, both, or neither support the card |
| Source support | whether official source text is attached and verified |

---

## 6. Product workflows

### 6.1 Browse by state

User chooses a state and sees:

- list of AI and AI-adjacent laws;
- law status;
- law domain;
- effective date;
- tracker grounding;
- analyst review state;
- top obligations;
- urgency and risk flags.

### 6.2 Browse by use case

User chooses a use case such as employment AI, health care AI, or synthetic media.

Output:

- relevant states;
- relevant laws;
- common obligations;
- differences between states;
- business checklist.

### 6.3 Applicability check

User enters a business profile:

- states of operation;
- business role;
- sector;
- system type;
- consumer-facing or internal;
- decision impact;
- data types;
- current documentation;
- vendor role.

Output:

- likely applicable laws;
- possibly applicable laws;
- unlikely laws;
- missing facts;
- recommended actions;
- counsel review flags.

### 6.4 Compliance action checklist

For each applicable or possibly applicable law, the product generates a checklist grouped by team:

- product;
- legal or compliance;
- data science;
- privacy;
- security;
- procurement;
- HR;
- policy or government affairs.

### 6.5 Evidence review

For a selected law card, users can inspect:

- Orrick reference fields;
- IAPP reference fields;
- official source text, where attached;
- extracted evidence spans;
- analyst review notes;
- tracker conflicts;
- future legal review status.

---

## 7. Applicability engine strategy

### 7.1 Product posture

The applicability engine should say:

- “likely relevant”
- “possibly relevant”
- “unlikely based on provided facts”
- “not enough information”
- “future counsel review recommended”

It should not say:

- “you are compliant”
- “this law does not apply as a legal conclusion”
- “safe to launch”

### 7.2 Deterministic first

Applicability logic should be deterministic wherever possible.

Rules should match:

- state;
- law status;
- effective date;
- covered actor;
- business role;
- system type;
- sector;
- use case;
- data type;
- consumer count or size thresholds;
- exemptions;
- public-sector status;
- tracker grounding.

Use LLMs only to:

- explain the output;
- map natural language business descriptions to taxonomy terms;
- suggest missing facts;
- draft business-readable summaries.

### 7.3 Applicability output

Each result should include:

- law card ID;
- state;
- law name;
- why included;
- why excluded, where applicable;
- confidence;
- grounding source;
- missing facts;
- recommended actions;
- escalation flags.

---

## 8. MVP scope

### 8.1 Recommended initial states

Start with:

- California;
- Colorado;
- Utah;
- Texas;
- Illinois;
- New York;
- Connecticut;
- New Jersey;
- Maryland;
- Virginia.

These provide coverage across broad AI governance, privacy/profiling, employment, health care, synthetic media, and consumer protection.

### 8.2 Recommended initial use cases

Start with:

1. employment automated decision systems;
2. consumer-facing generative AI chatbots;
3. health care AI and utilization review;
4. AI-generated political media and deepfakes;
5. biometric, intimate image, and likeness laws;
6. privacy, profiling, and automated decision laws;
7. training-data transparency;
8. government AI procurement and agency use.

### 8.3 MVP product features

Must have:

- state law-card list;
- individual law card page;
- tracker grounding label;
- business-facing summary;
- obligation checklist;
- evidence and tracker references;
- status and effective-date display;
- analyst review state;
- basic applicability check;
- export to Markdown or PDF.

Should have:

- comparison by state;
- use-case browsing;
- stale-card alerts;
- tracker conflict warnings;
- team-specific checklist;
- JSON export.

Later:

- saved business profiles;
- vendor questionnaire generator;
- counsel review package;
- regulatory change alerts;
- API subscription;
- procurement scoring.

---

## 9. Product data model

### 9.1 Product-facing objects

The product should use these primary objects:

- `law_card`
- `tracker_reference`
- `compliance_concept`
- `business_action`
- `applicability_result`
- `risk_flag`
- `evidence_link`
- `review_state`
- `change_event`

### 9.2 Law card fields

Essential law card fields:

- law name;
- state;
- bill number;
- citation, if available;
- status;
- effective date;
- law domain;
- tracker grounding;
- source attachment status;
- analyst review state;
- future legal review state;
- plain summary;
- who should care;
- covered actors;
- covered systems;
- covered sectors;
- main obligations;
- individual rights;
- enforcement summary;
- exceptions and thresholds;
- business actions;
- ambiguity notes;
- evidence links.

---

## 10. UX principles

### 10.1 Show the answer first, then the evidence

Business users need a clear answer first:

> “This law may matter if you deploy AI in employment decisions involving residents of this state.”

Then provide:

- why;
- source;
- evidence;
- action checklist;
- uncertainty.

### 10.2 Label confidence honestly

Use labels like:

- tracker-grounded;
- source-supported;
- analyst-reviewed;
- future legal review not completed;
- stale;
- conflict detected.

### 10.3 Avoid legalistic overload

Do not make users read extraction payloads unless they choose to expand evidence.

### 10.4 Design for escalation

Every law card should make it easy to ask:

- should legal review this?
- should product change something?
- should data science run an assessment?
- should procurement ask vendors for documentation?

---

## 11. Business action checklist strategy

Law cards should generate action checklists grouped by team.

### Product team

- add disclosure or notice flow;
- add human review pathway;
- add appeal or contestation workflow;
- update onboarding or consent flow;
- restrict prohibited use cases.

### Data science and ML team

- maintain model documentation;
- run bias or subgroup testing;
- document training data and evaluation data;
- monitor performance drift;
- log automated decisions.

### Legal and compliance team

- review applicability;
- maintain impact assessment;
- monitor effective dates;
- prepare regulator-facing reports;
- review vendor contracts.

### Privacy team

- map personal and sensitive data use;
- connect AI use to privacy notices;
- implement opt-out rights;
- maintain data retention policies.

### Procurement team

- request vendor model cards;
- require audit rights;
- request data provenance documentation;
- add AI-specific contract clauses.

### Executive team

- decide launch risk;
- approve high-risk deployments;
- fund compliance controls;
- assign accountable owner.

---

## 12. Product trust and governance

### 12.1 Current review labels

Use:

| Label | Meaning |
|---|---|
| `tracker_only` | based on Orrick/IAPP, no source support yet |
| `source_supported` | official source attached and evidence spans verified |
| `analyst_reviewed` | analyst checked the law card for product quality |
| `tracker_conflict` | Orrick, IAPP, source text, or extraction disagree |
| `stale` | tracker or source changed since review |
| `future_legal_review_pending` | counsel review not yet completed |
| `future_legal_reviewed` | counsel review completed in later phase |

### 12.2 Product disclaimer

Recommended short disclaimer:

> This law card is tracker-grounded regulatory intelligence based on Orrick and IAPP reference data, plus source text where available. It is not legal advice and has not been independently validated by counsel unless marked as legal-reviewed.

### 12.3 Staleness rules

A card should become stale when:

- Orrick row changes;
- IAPP row changes;
- official source changes;
- bill status changes;
- effective date changes;
- enforcement provision changes;
- analyst updates a core interpretation;
- future legal review contradicts prior card.

---

## 13. Go-to-market angle

### 13.1 Core promise

> Understand which state AI laws may matter to your business, what they require, and what to do next, without starting from scratch.

### 13.2 Differentiation

Regs Checker is different from a legal tracker because it translates law into action.

Differentiators:

- business-facing law cards;
- AI-specific and AI-adjacent taxonomy;
- applicability triage;
- team-specific action checklists;
- tracker-grounded confidence;
- evidence trail;
- future legal review pathway.

### 13.3 First customers or users

Likely early users:

- AI startups selling into multiple states;
- privacy and compliance teams;
- policy teams at AI companies;
- procurement teams evaluating AI vendors;
- state innovation offices;
- AI governance consultants.

---

## 14. Metrics for product success

### Adoption metrics

- number of law cards viewed;
- number of applicability checks run;
- number of exports;
- number of saved profiles;
- number of user return visits.

### Quality metrics

- analyst correction rate;
- tracker conflict rate;
- stale-card rate;
- evidence coverage rate;
- card confidence distribution;
- user-reported issue rate.

### Business value metrics

- time saved per legal or compliance review;
- number of relevant laws identified per profile;
- number of business actions generated;
- number of vendor questions generated;
- conversion from law-card view to report export.

---

## 15. Roadmap

### Phase 1: Tracker-grounded law-card MVP

Deliver:

- state law-card index;
- law-card detail page;
- tracker grounding labels;
- basic obligation and action checklist;
- evidence and tracker references;
- analyst review state;
- export to Markdown.

### Phase 2: Applicability checker

Deliver:

- business intake form;
- deterministic matching engine;
- likely and possible laws;
- missing facts;
- recommended actions;
- product disclaimer.

### Phase 3: Better product taxonomy

Deliver:

- law-domain filters;
- use-case filters;
- actor filters;
- obligation-family filters;
- risk flags;
- state comparisons.

### Phase 4: Workflow and reporting

Deliver:

- exportable reports;
- team-specific checklists;
- saved business profiles;
- stale-card alerts;
- tracker update notifications.

### Phase 5: Counsel review and enterprise readiness

Deliver:

- legal review workflow;
- counsel review package;
- organization accounts;
- role-based access;
- API keys;
- audit logs;
- PDF exports.

---

## 16. Product definition of done

The product is MVP-ready when:

1. users can browse law cards by state;
2. every law card shows tracker grounding;
3. every law card clearly says whether it is tracker-only, source-supported, analyst-reviewed, or future legal-reviewed;
4. law cards use business language, not raw extraction taxonomy;
5. law cards include action checklists;
6. users can run a basic applicability check;
7. applicability results are explainable and deterministic;
8. product does not claim legal advice or compliance certainty;
9. stale and conflict states are visible;
10. users can export a useful report.

---

## 17. Final recommendation

The product should be simpler and more pragmatic than the extraction system.

The extraction system can be comprehensive, nuanced, and technically detailed. The product should translate that complexity into a few clear outputs:

1. Law card.
2. Applicability result.
3. Business action checklist.
4. Evidence and tracker trail.
5. Escalation flag.

The product strategy should not expose the internal complexity of agents, payloads, and extraction types. It should make the complexity useful for business decision-making.

The product north star is:

```text
From AI law uncertainty to practical next steps.
```
