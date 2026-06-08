# Regs Checker — Data & Taxonomy Reference

**Audience:** Business, product, and review teams (no engineering background assumed)
**Purpose:** Explains, in plain English, every field the pipeline extracts from each
law and how the current taxonomy is organized.
**Authoritative law count:** 232 (`data/fact_laws.csv`)

---

## Contents

1. [The big picture](#1-the-big-picture)
2. [Law-level fields (the "Law Card")](#2-law-level-fields-the-law-card)
3. [Clause-level fields (the detailed extractions)](#3-clause-level-fields-the-detailed-extractions)
4. [The current taxonomy (controlled vocabularies)](#4-the-current-taxonomy-controlled-vocabularies)
5. [How data quality is graded (the trust layer)](#5-how-data-quality-is-graded-the-trust-layer)
6. [Where the taxonomy is heading](#6-where-the-taxonomy-is-heading)

---

## 1. The big picture

Regs Checker reads the **full text of ~232 US state and federal AI laws** and turns
each one into **structured, queryable data** — so that instead of a lawyer reading a
40-page bill, a business can ask *"Does this law apply to me? What do I have to do?
By when? What happens if I don't?"* and get a precise answer.

It captures data at **two levels**:

| Level | Question it answers | How it's produced |
|---|---|---|
| **Law-level** (one record per law) | "What is this law, who does it cover, what's the penalty, when's the deadline?" | 3 AI agents that read the *whole bill* at once |
| **Clause-level** (many records per law) | "What exactly does *this sentence* require, and of whom?" | 6 AI agents that read the bill *passage by passage* |

Every extracted fact is tied back to a **verbatim quote** from the law (the "evidence
span") and graded for **trustworthiness** (a confidence tier from A to D). Nothing is
taken on faith.

---

## 2. Law-level fields (the "Law Card")

These are the headline facts shown for each law. They come partly from source metadata
(Orrick law-firm tracker + IAPP) and partly from the three whole-bill AI agents.

### 2a. Basic identity & status

| Field | Plain-English meaning | Example |
|---|---|---|
| **Title** | The law's name | "Colorado AI Act (SB 205)" |
| **Bill number** | Official legislative number | "SB 205" |
| **Jurisdiction** | Which state / body | "Colorado", "California", "EU" |
| **Status** | Where it is in its lifecycle | *Proposed* → *Passed, not yet in effect* → *In effect* |
| **Effective date** | When it becomes (or became) law | 2026-02-01 |
| **AI scope summary** | One-line topic | "Automated decision-making in employment" |
| **Source** | Where we sourced the law | Orrick, IAPP |

### 2b. "Who does this law apply to?" — *Applicability Agent*

The engine behind the **"does this apply to me?"** matching. Reads the whole bill and
answers:

| Field | Plain-English meaning | Example values |
|---|---|---|
| **Covered entity types** | The kinds of businesses regulated | developer, deployer, provider, employer, state agency |
| **Covered sectors** | Industries in scope | employment, healthcare, credit, housing, insurance, criminal justice |
| **AI system types in scope** | What kinds of AI are regulated | high-risk AI, automated decision systems, generative AI, facial recognition |
| **Size thresholds** | The "you're big enough to be covered" triggers | revenue ≥ $25M; ≥ 50 employees; data on ≥ 100k consumers; compute ≥ 10²⁶ FLOPS |
| **Geographic scope** | Where it reaches | "entities doing business in Colorado" |
| **Key exemptions** | Who's carved out | "small businesses under 50 employees", "HIPAA-covered entities" |
| **Government only?** | Applies only to the public sector? | true / false |
| **Applicability summary** | Plain-language "who's covered" | "Applies to businesses deploying AI for hiring decisions in CA." |

### 2c. "What happens if I don't comply?" — *Enforcement Agent*

| Field | Plain-English meaning | Example |
|---|---|---|
| **Enforcing body** | Who comes after you | "Attorney General" |
| **Max civil penalty (USD)** | Biggest fine per violation | $20,000 |
| **Penalty per** | The unit fines are counted in | per violation / per day / per occurrence |
| **Cure period (days)** | Grace period to fix it before penalties | 60 days |
| **Private right of action** | Can individuals sue you directly? | true / false |
| **Criminal penalties** | Possible jail time / criminal fines? | true / false |

### 2d. "When are the deadlines?" — *Compliance Timeline Agent*

| Field | Plain-English meaning | Example |
|---|---|---|
| **Law effective date** | When the law switches on | 2026-02-01 |
| **Enforcement start date** | When they actually start penalizing | 2026-08-01 |
| **Sunset date** | When the law expires (if ever) | — |
| **Key deadlines** | Each dated action: *what*, *trigger*, *how many days*, *how often* | "Complete impact assessment 90 days before deployment" |
| **Assessment frequency** | How often you must re-do assessments | every 12 months |
| **Consumer response window** | Days to respond to a consumer request | 45 days |
| **First compliance action** | The first thing you must do, and when | "Register the system with the AG before launch." |

---

## 3. Clause-level fields (the detailed extractions)

These six agents read the law **passage by passage**, producing many records per law.
This is the granular layer that powers detailed obligation tracking and cross-state
comparison.

### 3a. "Who must do what?" — *Obligation Agent* (the richest extractor)

| Field | Plain-English meaning | Example |
|---|---|---|
| **Subject** / **Subject (normalized)** | Who must comply (raw + standardized) | "a deployer of a high-risk system" → **deployer** |
| **Modality** | The strength of the duty | must / shall / may / prohibited |
| **Action** | What they must do (or not do) | "conduct an impact assessment" |
| **Object** | What the action applies to | "the automated decision system" |
| **Condition** | The trigger | "before deploying the system" |
| **Timeline** | Dates attached to this duty | deadline, phase-in period, sunset |
| **Enforcement** | Penalty attached to this duty | body, penalty type, max fine, cure period, can-be-sued flag |
| **Safe harbor** | A "get-out-of-jail" path | "Following NIST AI RMF = affirmative defense" |
| **Consent requirements** | Required notice/consent | type (opt-in/opt-out), timing, method |
| **Interpretation risks** | Vague/ambiguous language flagged for review | "'reasonable care' is undefined" (severity: high) |

### 3b. "Who is protected, and what can they demand?" — *Rights & Protections Agent*

The flip side of obligations — what *individuals* are entitled to.

| Field | Plain-English meaning | Example |
|---|---|---|
| **Right holder** | Who holds the right | consumer, employee, job applicant, patient |
| **Protected categories** | Explicitly protected groups | minor, tenant, borrower, student |
| **Right type** | The kind of right | notice, explanation, opt-out, appeal, deletion, human review |
| **Right description** | What they're entitled to | "right to a human review of an adverse AI decision" |
| **Trigger condition** | When the right kicks in | "upon an adverse hiring decision" |
| **Duty bearer** | Who must honor it | the employer / deployer |
| **Remedies** | Recourse if violated | complaint, damages, deletion — plus time limits |

### 3c. "How do I prove compliance?" — *Compliance Mechanism Agent*

The procedural machinery: audits, assessments, reporting, recordkeeping.

| Field | Plain-English meaning | Example |
|---|---|---|
| **Mechanism type** | The procedural requirement | impact assessment, bias audit, registration, certification, reporting |
| **Responsible party** | Who performs it | developer / deployer |
| **Audits** | Audit details | type, frequency, who audits, who sees results, public? |
| **Record retention** | How long to keep records | 36 months — and *what* to keep |
| **Reporting** | Filing cadence + recipient | annual report to the AG |
| **Classification flags** | Quick yes/no tags | is bias testing? / is red-teaming? / is third-party audit? |
| **Incident reporting window** | Hours to report an incident | 72 hours |
| **NIST references** | Links to NIST AI framework controls | "MEASURE-2.1" |

### 3d. "When does it apply / when am I exempt?" — *Threshold & Exception Agent*

| Field | Plain-English meaning | Example |
|---|---|---|
| **Threshold sub-type** | The kind of boundary | scope (who/what), temporal (deadlines), exemption (carve-outs) |
| **Threshold value / unit / condition** | The actual trigger | "$25,000,000 annual revenue" |
| **Revenue / employee / consumer-data thresholds** | Typed numeric triggers | ≥ $25M / ≥ 50 / ≥ 100k |
| **Compute threshold (FLOPS)** | Frontier-model size trigger | 10²⁶ |
| **Sector applicability** | Which consequential-decision sectors | healthcare, employment, credit |
| **Exceptions** | Carve-outs and safe harbors | "research use is exempt" |

### 3e. "What do the words mean?" — *Definition & Actor Agent*

| Field | Plain-English meaning | Example |
|---|---|---|
| **Term** + **Definition text** | A defined term and its full legal definition | "'Algorithmic discrimination' means…" |
| **Scope** | Where the definition applies | "for purposes of this article" |
| **Actors** | Roles named, and their responsibilities | developer, deployer, regulator |
| **Framework references** | External standards the law leans on | "incorporates NIST AI RMF" |

### 3f. "Does this conflict with other laws?" — *Preemption Agent*

| Field | Plain-English meaning | Example |
|---|---|---|
| **Conflict type** | The kind of legal tension | federal preemption, cross-state conflict, First Amendment |
| **Description** | Plain-language conflict summary | "May be preempted by federal AI executive order" |
| **Related authority** | The competing law/authority | "Dec 2025 Federal EO on AI" |
| **Severity** | Risk level | high / medium / low |
| **Cross-law references** | Other laws this one points to | supersedes / incorporates / conflicts-with |

---

## 4. The current taxonomy (controlled vocabularies)

The taxonomy is the set of **standardized labels** that make the data filterable and
comparable across states. Without it, "deployer," "Deployer," and "entity that
deploys" would all be different — and filtering would break. Today the taxonomy lives
in a handful of **dimension tables** (the "approved value lists") plus **mapping
tables** (which law has which labels).

### Dimension tables (the approved value lists)

| Taxonomy | Approved values | Purpose |
|---|---|---|
| **Jurisdictions** | 49 entries — all US states + EU | Filter by location |
| **Actor types** | Deployer, Developer, Provider, Distributor | Who the law regulates |
| **Requirement types** | 12 values: Governance Program, Assessments, Training, Responsible Individual, General Notice, Labeling/Notification, Explanation/Incident Reporting, Provider Documentation, Registration, Third-party Review, Opt-out/Appeal, Nondiscrimination | The category of obligation |
| **AI scopes** | A = all AI systems · F = foundation/frontier models · D = automated decision-making · G = generative AI · \* = AI trained on personal data | What kind of AI is covered |
| **Legislative statuses** | Active, Enacted, Failed/Dead, Signed | Lifecycle stage |
| **Sources** | Orrick, IAPP | Where the law data came from |

### Mapping tables (which law has which labels)

- **Law → Requirements** — links each law to its requirement types *and* the
  responsible actor, with a mandatory-or-not flag.
- **Law → AI scopes** — links each law to its scope code(s).

### Tag categories (the search/filter facets on the public Law Card)

The user-facing export also groups tags into facets: **jurisdiction, source, lifecycle
status, AI scope, AI topic, concept, compliance requirement, regulated actor** — e.g.
concepts like *children, employment, intimate images, political advertising,
transparency*.

---

## 5. How data quality is graded (the trust layer)

Because the data is AI-extracted, every record carries a **confidence tier** so
reviewers and downstream products know how much to trust it. The score blends six
signals — most heavily, **agreement with Orrick law-firm reference data (30%)** and
**independent re-checking (25%)**.

| Tier | Score | What it means for the business |
|---|---|---|
| **A** | ≥ 0.85 | High confidence — candidate for auto-approval |
| **B** | ≥ 0.70 | Solid — standard review |
| **C** | ≥ 0.50 | Needs a careful human look |
| **D** | < 0.50 | Requires human review |

**The key rule (the "Orrick gate"):** any extraction *without* law-firm reference data
to validate against is automatically **Tier D**, no matter how good it looks. This is
the guardrail that keeps unvalidated AI output from reaching production.

---

## 6. Where the taxonomy is heading

The taxonomy is mid-redesign. The strategy work underway expands it toward richer,
profile-matched dimensions — `covered_sectors`, a two-level `obligation_type`,
`harm_categories` (discrimination, privacy, deception, safety, child, election…), and
split status fields (signed vs. actually-in-effect). The redesign principle is
*additive* — old labels stay until new ones are validated.

If the business team will rely on these filters, it's worth knowing which dimensions
are **live today** (Section 4) vs. **planned**. See `docs/taxonomy_strategy_summary.md`
and `docs/taxonomy_dev_plan.md` for the full redesign plan, and
`docs/pipeline_rebuild_plan.md` for the alternative ground-up approach.
