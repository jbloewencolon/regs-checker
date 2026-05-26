# Taxonomy Strategy: Working Spec

**Status:** Draft v1, derived from data audit taxonomy doc + pipeline review
**Scope:** Consolidates the decisions made during the strategy review
**Purpose:** Establishes the agreed direction for the taxonomy redesign and migration

---

## 1. Product Goals (Equal Weight)

The taxonomy work serves three product goals, each treated as primary:

1. **LawCard filtering** — End users can filter laws by sector, AI system type, obligation category, etc., with complete and accurate results.
2. **Matching engine** — `anonymous_audit_profiles` joins against law-side fields to answer "which laws apply to me."
3. **Framework crosswalk** — Future-state mapping from law obligations to NIST AI RMF, ISO 42001, etc. (deferred to a later phase but design-influencing).

---

## 2. Foundational Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Sector dimensionality | Standalone array dimension (not modifier suffix) | Aligns with existing `ApplicabilityAgent.covered_sectors` output and `anonymous_audit_profiles.sectors[]`; avoids combinatorial code explosion |
| Migration strategy | Hybrid: rule-based crosswalk for derivable fields, re-extraction for new dimensions | Existing 28,885 extractions already contain most taxonomy data in JSONB; only new dimensions (`harm_categories`, obligation Level-2) require re-extraction |
| Normalization location | New stage in `rollup_matrix.py` | Already does crosswalk work; idempotent upsert pattern fits; keeps "inference offline" principle |
| Vocabulary governance | Committee approval for new codes | New values flagged via review queue, batched for committee decision |
| User profile source of truth | `anonymous_audit_profiles` (existing table) | Pre-existing schema constrains the law-side vocabulary to match |
| Vocab style | Readable strings, lowercase snake_case | Matches existing profile fields and Applicability Agent output; avoids translation layer |
| DB-level enforcement | Hybrid: strict on law-side (FK to dim tables), soft on profile-side initially | Law-side data is curated; profile-side is user-entered and needs gentle migration |
| NIST/framework alignment | Deferred to a later phase | Phase 1 focuses on product-driven codes; framework crosswalk is later work |
| Timeline | Open-ended, ship incrementally | Each phase gated by its own success criteria |

---

## 3. Taxonomy Dimensions (Updated)

| Dimension | Type | Vocab Style | Pipeline Source | Phase Priority |
|---|---|---|---|---|
| `law_category` | single value | readable string | Orrick + Applicability Agent | P1 |
| `ai_system_scope` | array | readable string, matches profile | Applicability Agent | P1 |
| `covered_sectors` | array | readable string, matches profile | Applicability + Threshold | P1 |
| `actor_classification` | enum per obligation | readable string, matches profile | Obligation Agent | P1 |
| `obligation_type` Level 1 (5 domains) | single value per obligation | snake_case | Obligation + Compliance Mechanism | P1 |
| `obligation_type` Level 2 (specific obligation) | single value per obligation | snake_case | Re-extract with prompt update | P2 |
| `obligation_modifier` (mandatory/conditional, timing, verification) | optional flags | snake_case | Obligation + Compliance Mechanism | P2 |
| `harm_categories` | array per law | readable string | New extraction or extended Rights Protection agent | P2 |
| `legislative_status` | single | snake_case enum | Crosswalk from `document_versions.temporal_status` | P1 |
| `enforcement_status` | single | snake_case enum | Derived from `effective_date` vs `now()` | P1 |
| `preemption_status` | single per law | snake_case enum | Preemption Agent + normalization | P3 |

### Non-taxonomy outputs (keep extracting, don't classify)

- **Ambiguity** — Stays as a per-obligation quality flag (boolean or count). Drives review queue. Not a controlled vocab.
- **Preemption signals (verbatim quotes)** — Stay as evidence supporting `preemption_status` classification. Verbatim quotes are not the taxonomy.
- **`framework_refs`** — Continues extracting; populates future `resp_*` crosswalk.

---

## 4. Style Guide

| Element | Style | Example |
|---|---|---|
| Single concept | lowercase snake_case | `developer`, `automated_decision_system` |
| Multi-word | snake_case | `high_risk_ai`, `generative_ai` |
| Status enums | snake_case | `enacted`, `pre_effective`, `pending_rulemaking` |
| Boolean flags | `is_*` / `has_*` | `is_bias_testing`, `has_private_right_of_action` |
| Compound obligation properties | separate fields, not concatenated | `obligation_type: bias_testing` + `timing: pre_deploy` + `verification: self_certified` |

---

## 5. Migration Architecture

```
Agents (extract concepts, freetext + suggested codes)
        │
        ▼
synced_extractions (Policy Navigator) — raw payloads, unchanged
        │
        ▼
rollup_matrix.py [EXTENDED] — taxonomy normalization stage
        │      ├── Lookup tables (subject_area → law_category, etc.)
        │      ├── Unmapped values → review queue
        │      └── Idempotent upserts to controlled-vocab tables
        ▼
Controlled-vocab tables (new + existing dim_* tables)
        │
        ▼
LawCard UI + matching engine queries (clean JOINs, no freetext)
```

### Division of labor

| Stage | Responsibility | Don't do here |
|---|---|---|
| Agent prompts | Extract concepts from text; emit lowercase strings for known enums | Don't enforce strict taxonomy — LLMs are unreliable at it |
| `payload_adapter.py` | Shape transformation (nested → flat, missing keys added) | Don't add vocabulary mapping |
| `rollup_matrix.py` (extended) | Read freetext + suggested codes from synced_extractions; apply lookup tables; flag unmapped values for review | Don't run LLM inference |
| Review queue | Receive unmapped/ambiguous values; committee approves additions to dim tables | Don't store codes that haven't been approved |

---

## 6. Success Criteria (Phased)

### Phase 1 success gates

Each phase ships independently per the incremental-shipping decision (§2). Phase 1's gates are scoped to law-level normalization only; gates that depend on Phase 2 deliverables (actor classification, matching engine swap) are listed under "Phase 2 success gates" below.

**Data quality (law-level):** Zero nulls in `law_category_id`, `legislative_status_id`, `enforcement_status_id` across 232 laws. All `subject_area` freetext either mapped via lookup or routed to `vocab_review_queue` for committee triage. `dim_law_categories`, `dim_legislative_statuses`, `dim_enforcement_statuses` populated and FK-enforced from `fact_laws`.

**LawCard filtering (law-category only in Phase 1):** The `law_category` filter chip renders from a controlled-vocab field. Validation test: 20 manually-classified laws return 100% expected matches, zero false positives. (Sector, actor, and obligation-domain filters ship in later phases.)

**Process:** Vocab committee operational with ≥1 batch processed. `vocab_review_queue` triaged at least once.

**Framework readiness (structural only):** `resp_*` tables structurally aligned. `framework_refs` extractions retained. Mapping plan to NIST AI RMF documented but not executed.

### Phase 2 success gates

**Obligation-level normalization:** `dim_actor_types` populated with the expanded 6-value vocab + `actor_scope` enum. `obligation_actor` junction populated; >95% of obligations have at least one row joining to `dim_actor_types`. `law_ai_scopes` and `law_sectors` junctions populated for every law.

**Matching engine:** Test profile returns stable, expected applicable laws via the new FK-based JOINs (no freetext fallbacks). ≤5% drop in matched laws vs. the current freetext engine (any drops audited); net additions are acceptable. All four matching dimensions (sector, actor, AI scope, jurisdiction) work without freetext fallbacks.

**Profile alignment:** `anonymous_audit_profiles` insert path reads option lists from dim tables (soft enforcement; hardens in Phase 3).

### Phase 2+ incremental gates

| Sub-phase | Deliverable | Gate |
|---|---|---|
| 1a | `law_category` live | All 232 laws non-null; LawCard badge renders |
| 1b | `actor_classification` normalized | >95% of obligations have non-null actor matching profile vocab |
| 1c | Status split | LawCard distinguishes signed-not-effective from effective |
| 2a | `covered_sectors` standalone | Matching engine returns expected results for sector profile |
| 2b | Obligation Level-1 (5 domains) | LawCard groups obligations without an "Other" bucket |
| 2c | `harm_categories` | "Protects against" renders for >80% of laws |
| 3a | `preemption_status` | Multi-state profiles see preemption flags |
| 3b | Obligation Level-2 | Cross-state comparison matrix at granular obligation level |
| 4 | NIST/ISO crosswalk | `resp_*` populated; framework-to-law queries work |

### Non-criteria (don't measure these)

- "All 28,885 extractions migrated" — process milestone, not quality measure
- "Manual remediation <2%" — depends on baseline data quality
- "Zero freetext anywhere" — overly strict; some fields are legitimately freetext (subcategory, geographic_scope, evidence quotes)

---

## 7. Open Items and Risks

### Items not yet decided

- **`harm_categories` vocab** — proposed set (`discrimination`, `privacy`, `deception`, `safety`, `child`, `election`, `labor`, `autonomy`) needs validation against actual law content; some laws may have harms not in this list
- **`preemption_status` vocab** — proposed but not validated against the existing `preemption_signals` extractions; needs a sample-based design pass
- **Existing `*` wildcard in `dim_ai_scopes`** — needs verification that no laws link to it before retirement
- **Profile-side vocab enforcement timing** — "soft initially" needs a defined point at which it becomes strict
- **Sectors gap in user profile** — `anonymous_audit_profiles.sectors` is unconstrained text; needs alignment with law-side `covered_sectors` enum

### Known risks

- **Re-extraction may shift confidence tiers** — items currently Tier A could drop to B/C under new prompts. Mitigation: store new extractions alongside old ones until A/B comparison validates.
- **LawCard category vs. Level-1 obligation type collision** — LawCard currently displays 5 categories that may not align 1:1 with the proposed 5 Level-1 domains. Reconcile before shipping 2b.
- **Committee approval bottleneck** — if every new code requires committee review, ingestion of new laws may stall. Mitigation: define a "fast lane" for codes that clearly fall within existing patterns.
- **Profile-law vocab drift** — if the profile UI adds new sector options before the law-side dim table is updated, matching fails silently. Mitigation: profile UI reads from dim tables, not hardcoded lists.

---

## 8. Recommended Next Actions

1. **Validate `harm_categories` against a sample of 30 laws** — confirm the 8 proposed harms cover real content; identify gaps.
2. **Design the normalization lookup tables** — start with `subject_area → law_category` (highest-impact, smallest scope).
3. **Map current `ai_system_types_in_scope` values to new `ai_system_scope` vocab** — confirm 1:1 crosswalk works.
4. **Audit the `*` value in `dim_ai_scopes`** — confirm zero references; retire.
5. **Reconcile LawCard's 5 UI categories with proposed Level-1 obligation domains** — either align or document why they differ.
6. **Define committee composition and approval cadence** for vocab changes.
7. **Spec the `rollup_matrix.py` extension** — function signatures, lookup table schema, review queue integration.

---

## 9. Appendix: Decisions Log

| Date | Decision | Source |
|---|---|---|
| Session 1 | Sector is standalone dimension | Recommendation accepted |
| Session 1 | Hybrid migration: rule-based + targeted re-extraction | Recommendation accepted |
| Session 1 | Normalization runs in `rollup_matrix.py` | Recommendation accepted |
| Session 1 | All three product goals weighted equally | Explicit user choice |
| Session 1 | NIST alignment deferred to later phase | Explicit user choice |
| Session 1 | Ambiguity excluded from taxonomy; preemption added as new dimension | Recommendation accepted |
| Session 1 | Committee approval for vocab changes | Explicit user choice |
| Session 1 | `anonymous_audit_profiles` is profile schema source of truth | Explicit user choice |
| Session 1 | Timeline open-ended; incremental ship | Explicit user choice |
| Session 1 | Hybrid enforcement (strict law-side, soft profile-side) | Explicit user choice |
| Session 1 | Readable strings, snake_case style | Recommendation accepted |
| Session 1 | Phased success criteria (Phase 1 gates + incremental gates after) | Recommendation accepted |
| 2026-05-26 | Authoritative law count = 232 (matches `data/fact_laws.csv` data rows) | Explicit user choice |
| 2026-05-26 | Phase 1 success gates rewritten to be Phase-1-internal; actor-classification + matching-engine gates moved to new Phase 2 gates section | Codebase drift correction |
| 2026-05-26 | Versioned re-extraction handled via `agent_name` suffix convention (e.g. `applicability_agent_v2`); no `agent_version` column added to `bill_level_extractions` | Recommendation accepted |
