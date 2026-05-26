# Taxonomy Redesign: Phased Development Plan

**Status:** Working draft
**Companion docs:** `data_taxonomy_analysis.md` (the audit), `taxonomy_strategy_summary.md` (decisions log)
**Scope:** End-to-end plan for migrating the regs-checker / Policy Navigator taxonomy from the current freetext-mixed-with-enums state to a controlled-vocabulary, profile-aligned structure.

---

## 0. How to Read This Document

Each phase is structured the same way:

1. **Why this phase exists** — the product/data problem being solved
2. **What gets built** — concrete deliverables
3. **Who is responsible** — role assignments (fill in names per your team)
4. **Important context for the engineer** — what they need to understand before writing code, including pipeline gotchas
5. **Acceptance criteria** — what "done" looks like
6. **Testing strategy** — unit, integration, data-quality checks
7. **Monitoring** — what to watch after shipping
8. **Rollback plan** — how to revert if it goes wrong
9. **Parallelizable sub-tracks** — what can run concurrently

Sequential dependencies are noted explicitly. Anything not marked as a dependency can be parallelized.

---

## 1. Role Definitions

Roles map roughly to the existing leadership structure documented in archived planning. Adjust names to your team.

| Role | Domain | Decision Authority |
|---|---|---|
| **Legal Knowledge Architect (LKA)** | Legal correctness, taxonomy semantics, evidence standards | Veto on vocabulary changes, classification rules, what counts as a "harm" |
| **Senior Data Platform Architect (SDPA)** | Schema, migrations, sync architecture, performance | Veto on schema changes, dim table design, FK strategy |
| **Product / Technical Program Lead (PTPL)** | Sequencing, scope, stakeholder delivery | Veto on phasing, resource allocation |
| **Applied NLP / LLM Extraction Lead (NLP)** | Extraction agents, prompts, evaluation harness | Owns agent prompt updates, confidence impact analysis |
| **Backend / API Lead (BE)** | FastAPI, rollup_matrix, sync scripts, dim tables | Owns `rollup_matrix.py`, `sync_extractions.py`, `payload_adapter.py` |
| **Regulatory / Policy Review Lead (RPR)** | Manual classification, vocab curation, gold-standard data | Owns the controlled vocab review committee |
| **DevOps / Platform Reliability Lead (DevOps)** | Alembic, Docker, CI/CD, Supabase ops | Owns migration execution, rollback scripts |
| **Frontend / LawCard (FE)** | LawCard UI, filter rendering | Owns rendering of new taxonomy fields |
| **Data Ops (DO)** | Lookup tables, bulk normalization, manual remediation | Operates the normalization stage and review queue triage |
| **SME / Outside Counsel (SME)** | Legal interpretation, edge cases, harm classification | Consulted on ambiguous classifications |
| **Vocabulary Committee (VC)** | Cross-functional: LKA + RPR + SDPA + PTPL | Approves additions/changes to controlled vocab |

---

## 2. Cross-Cutting Concerns

These apply to every phase and aren't restated below.

### Additive-not-destructive principle

Every schema change adds new columns/tables alongside existing ones. Old columns stay populated until a phase is fully validated. This is non-negotiable — the pipeline has 28,885 live extractions and downstream consumers (LawCard, matching engine, sync_monitor) that can't be broken atomically.

### Immutability and content-addressable artifacts

The pipeline's design principle is "inference is offline, serving is instant." New work preserves this. Don't introduce runtime LLM dependencies. Normalization runs in batch, results materialize to tables, API serves the tables.

### Two-database sync model

The pipeline has three database environments:
- **Local Docker Postgres** (port 5434) — development
- **Regs Checker Supabase** (`wjxlimjpaijdogyrqtxc`) — pipeline storage
- **Policy Navigator Supabase** (`aaxxunfarlhmydvohsrm`) — product, served via LawCard

Schema changes must be applied in all three. Use Alembic for local; `scripts/apply_pending_migrations.sql` pattern for Supabase environments (because Supabase's SQL Editor can't run ALTER TYPE inside transactions — see existing migration patterns).

### Vocab committee approval cadence

New vocab values flow through the review queue. The committee meets weekly to approve batches. No new value goes live in code until approved. Use the existing `review_queue` table pattern; add a row type for vocab-pending decisions.

### Branch and PR workflow

Each phase = one feature branch. Sub-tracks within a phase = PRs against the feature branch. Phase merges to `main` only after acceptance criteria pass.

---

## 3. Phase 1 — Foundation: Law-Level Classification

### 3.1 Why this phase exists

The most visible failure of the current taxonomy is `fact_laws.subject_area` — 37 inconsistent freetext values across 232 laws (the current seed in `data/fact_laws.csv`), a meaningful fraction of which are null. The exact null count predates the latest seed and should be re-verified against the current corpus before Phase 1.H sizing. This is the field that powers LawCard's law-type badge and the first-pass "what kind of law is this" filter. Until this is normalized, no other taxonomy work has a stable foundation, because every other dimension depends on knowing the law's primary category.

This phase also establishes the patterns the rest of the plan reuses: dim table design, lookup tables, the rollup normalization stage, and the vocab committee workflow.

### 3.2 What gets built

| Track | Deliverable |
|---|---|
| 1.A | New `law_category` controlled vocab + `dim_law_categories` table |
| 1.B | `fact_laws.law_category_id` FK column (additive — keeps `subject_area` intact) |
| 1.C | Lookup table: existing `subject_area` freetext → `law_category` |
| 1.D | Normalization stage in `rollup_matrix.py` — populates `law_category_id` |
| 1.E | LawCard updates to render the new badge |
| 1.F | Vocab committee process documented and operating |
| 1.G | `legislative_status` + `enforcement_status` redesign (status split) |
| 1.H | Re-extract `subject_area` for the 64 nulls via Applicability Agent |

### 3.3 Track-by-track detail

---

#### Track 1.A — `dim_law_categories` table

**Owner:** SDPA (schema) + LKA (vocabulary content)
**Depends on:** Nothing
**Parallelizable with:** 1.G

**What to build:**

A new dim table populated with the 7 values from the strategy doc:

```sql
CREATE TABLE dim_law_categories (
  law_category_id   serial PRIMARY KEY,
  category_code     text NOT NULL UNIQUE,    -- e.g. 'comprehensive_ai'
  display_label     text NOT NULL,           -- e.g. 'Comprehensive AI'
  description       text,
  display_order     int,
  created_at        timestamptz DEFAULT now(),
  retired_at        timestamptz              -- soft-delete for retired codes
);
```

Seed values (snake_case, readable-string style, matches the agreed style guide):

| code | display_label |
|---|---|
| `comprehensive_ai` | Comprehensive AI |
| `generative_ai` | Generative AI |
| `automated_decision_making` | Automated Decision-Making |
| `ai_content_integrity` | AI Content Integrity |
| `sector_specific_ai` | Sector-Specific AI |
| `data_privacy_with_ai` | Data Privacy with AI Provisions |
| `csam_child_safety` | CSAM / Child Safety |

**Important context for the engineer:**

- The existing schema uses `dim_*` tables for actor types, AI scopes, requirement types, etc. Match that naming and structure. Look at `dim_actor_types` (5 rows currently) and `dim_requirement_types` (10 rows) for examples of how the existing dim tables are built and granted access.
- RLS policies are required because Supabase enforces them. Match the `authenticated_read` policy pattern visible on all other dim tables.
- `display_order` matters because LawCard renders these as a sorted list. Don't sort alphabetically — sort by display_order so "Comprehensive AI" appears before "Sector-Specific AI" even though C < S alphabetically.
- The `retired_at` column matters because future committee decisions may retire codes. Never `DELETE` from a dim table — soft-delete only, or rolled-back data becomes orphaned.

**Files touched:**
- `alembic/versions/<new>_add_dim_law_categories.py`
- `scripts/apply_pending_migrations.sql` (append for Supabase)
- Seed script (e.g., `src/scripts/seed_dim_law_categories.py`)

---

#### Track 1.B — `fact_laws.law_category_id` FK column

**Owner:** SDPA
**Depends on:** 1.A
**Parallelizable with:** 1.C (write the lookup against the dim table that already exists)

**What to build:**

Add a nullable FK column. Don't make it NOT NULL yet — that comes later after backfill.

```sql
ALTER TABLE fact_laws
  ADD COLUMN law_category_id int REFERENCES dim_law_categories(law_category_id);

CREATE INDEX ix_fact_laws_law_category_id ON fact_laws(law_category_id);
```

**Important context for the engineer:**

- `fact_laws.subject_area` (the old freetext field) stays in place. Two columns coexist until Phase 1 acceptance.
- The `subject_area` field is hardcoded to `'artificial_intelligence'` in the regs-checker pipeline (see the cross-database alignment table in the audit doc). Don't try to "fix" it on the regs-checker side — that's not where this lives in our product DB.
- Apply the same migration in all three environments. Use the existing `scripts/apply_pending_migrations.sql` runbook pattern.

---

#### Track 1.C — Lookup table: freetext → `law_category`

**Owner:** RPR (content) + DO (data entry) + LKA (sign-off)
**Depends on:** 1.A
**Parallelizable with:** 1.B

**What to build:**

A static lookup table maintained in version control. JSON or CSV in `data/lookups/subject_area_to_law_category.json`.

> **Phase 1 prerequisite:** the `data/lookups/` directory does not exist yet (verified 2026-05-26). Create it as the first action of this track, alongside `data/lookups/README.md` documenting file naming convention, lookup-key normalization rules (lowercased, articles stripped at lookup time, raw keys preserved for audit), and the committee-approval process for adding new entries. All subsequent lookup files (Track 2.B `subject_to_actor_code.json`, Phase 2 sector lookups, etc.) land here.

```json
{
  "artificial_intelligence": "comprehensive_ai",
  "ai_content_safety": "ai_content_integrity",
  "AI governance": "comprehensive_ai",
  "ai_governance": "comprehensive_ai",
  "deepfake disclosure": "ai_content_integrity",
  "deepfake labeling": "ai_content_integrity",
  "deepfake regulation": "ai_content_integrity",
  "election deepfakes": "ai_content_integrity",
  "employment AI": "sector_specific_ai",
  "ai_employment": "sector_specific_ai",
  "comprehensive AI regulation": "comprehensive_ai",
  "comprehensive AI law": "comprehensive_ai",
  "consumer AI regulation": "comprehensive_ai",
  "consumer AI transparency": "ai_content_integrity",
  "consumer transparency": "ai_content_integrity"
}
```

This is a partial example — the full set is the 37 distinct values from the audit doc. RPR creates the mapping; LKA reviews; the committee approves the initial set as part of the Phase 1 sign-off.

**Important context for the engineer (and RPR doing the mapping):**

- The case-sensitivity issue is real. `"AI governance"` and `"ai_governance"` are different keys in the JSON file. Don't normalize the *keys* — the lookup logic strips the keys' case/style at lookup time. This preserves the audit trail of what raw values existed.
- Some values are genuinely ambiguous. `"election deepfakes"` could be either `ai_content_integrity` or `sector_specific_ai` (sector = elections). Use the **dominant intent** — laws called "election deepfakes" are usually content-integrity laws applied to a sector. When ambiguous, flag for LKA review.
- A meaningful fraction of the 232 laws have null `subject_area` (audit count: 64 in the pre-2026-05 seed; re-verify against the current corpus before sizing Track 1.H). The lookup doesn't help these — they go through Track 1.H.

**Files touched:**
- `data/lookups/subject_area_to_law_category.json`
- `data/lookups/README.md` (document the format and update process)

---

#### Track 1.D — Normalization stage in `rollup_matrix.py`

**Owner:** BE
**Depends on:** 1.A, 1.B, 1.C
**Parallelizable with:** 1.E (BE and FE work simultaneously)

**What to build:**

Extend `src/scripts/rollup_matrix.py` with a new normalization stage that runs **before** the existing rollup stages. The new stage:

1. Reads `fact_laws.subject_area` for every law where `law_category_id IS NULL`
2. Looks up the freetext value in the lookup table
3. If matched → write `law_category_id`
4. If not matched → log to a new `vocab_review_queue` table for committee triage
5. Reports a summary (matched count, unmatched count, ambiguous count) at the end

```python
def normalize_law_categories(session) -> dict:
    """Phase 1 normalization: subject_area → law_category_id.

    Idempotent. Re-runs are safe (skips already-normalized rows).
    Returns counts for monitoring.
    """
    lookup = _load_lookup("subject_area_to_law_category.json")
    unmatched = []
    matched = 0
    ...
    return {"matched": matched, "unmatched": len(unmatched), ...}
```

**Important context for the engineer:**

- This stage must be **idempotent**. `rollup_matrix.py` is run repeatedly; re-running must not double-write or undo prior decisions. Use `WHERE law_category_id IS NULL` for the read, `ON CONFLICT DO NOTHING` for the write.
- The existing `rollup_matrix.py` writes to `law_enforcement_details`, `law_obligation_flags`, etc. Keep that pattern — write to the `fact_laws.law_category_id` column directly here (simpler than a junction table because it's 1:1).
- Logging is structured (`structlog` is used throughout the pipeline). Don't `print`; use `logger.info("category_normalized", law_id=..., category=...)`.
- Run the new stage **first** in the rollup pipeline. Other rollups (e.g., obligation flags) will eventually depend on it. Order matters because Phase 2 changes will read `law_category_id` to decide what other rollups to run.
- The `vocab_review_queue` table is new. Use the existing `review_queue` model as a template (`src/db/models.py`), but key it by `(field_name, original_value)` so the same unmatched value isn't duplicated per law.

**Files touched:**
- `src/scripts/rollup_matrix.py`
- `src/scripts/normalization/__init__.py` (new module)
- `src/scripts/normalization/law_category.py`
- `alembic/versions/<new>_add_vocab_review_queue.py`
- `src/db/models.py` (add `VocabReviewQueueItem`)
- `tests/unit/test_normalize_law_category.py`

---

#### Track 1.E — LawCard UI updates

**Owner:** FE
**Depends on:** 1.A (needs the dim table to query for display labels), 1.B (needs the FK column populated for at least a subset of laws)
**Parallelizable with:** 1.D

**What to build:**

1. Replace the existing freetext `subject_area` display with a join against `dim_law_categories`.
2. Add the new `law_category` as a filter chip in the LawCard list view.
3. Color-code badges by category (use `display_order` to keep the palette stable).

**Important context for the engineer:**

- For laws where `law_category_id IS NULL` during the migration window, fall back to displaying `subject_area` with a "(legacy)" suffix. Don't hide these laws; just mark them visibly.
- The committee will iterate on `display_label` values. The FE must read labels from the dim table at query time, not bake them into the component.
- LawCard currently uses 5 obligation-domain categories (Risk & Impact Assessment, Transparency & Disclosure, etc.). These are **different** from `law_category` — don't confuse them. Phase 2b reconciles the obligation categories; Phase 1 only touches law categories.

**Files touched:**
- LawCard component(s) in the FE codebase
- Filter chip components
- Color palette config

---

#### Track 1.F — Vocab committee process

**Owner:** PTPL (process design) + VC (operational)
**Depends on:** Nothing
**Parallelizable with:** Everything

**What to build:**

1. A documented process for proposing, reviewing, and approving vocab changes.
2. A standing weekly meeting cadence (30 min, async-first via PR comments).
3. A "fast lane" rule for values that clearly fall within existing patterns (e.g., a new sector qualifier on an existing AI system scope) — these can be approved by any two committee members async.
4. A "deliberation track" for values that require legal interpretation — these wait for the next standing meeting.

**Important context for the participants:**

- This is the bottleneck the strategy doc flagged. Without a fast lane, every new law that introduces a slightly different freetext value will block ingestion.
- Document the process in `docs/vocab_committee.md`. Include: how to propose, how to review, fast-lane criteria, deliberation criteria, retirement process.
- The `vocab_review_queue` table from Track 1.D is the committee's inbox. Empty it weekly.

---

#### Track 1.G — `legislative_status` + `enforcement_status` redesign

**Owner:** SDPA (schema) + LKA (semantics)
**Depends on:** Nothing
**Parallelizable with:** 1.A, 1.B, 1.C, 1.D

**What to build:**

1. Redesign `dim_legislative_statuses` to the 10-value clean set from the strategy doc (`proposed`, `in_committee`, `passed`, `signed`, `enacted`, `failed`, `withdrawn`, `vetoed`, `repealed`, `enjoined`).
2. Add a new `dim_enforcement_statuses` table with 5 values (`pre_effective`, `effective`, `pending_rulemaking`, `stayed`, `sunset`).
3. Add `fact_laws.enforcement_status_id` FK.
4. Lookup table: existing `document_versions.temporal_status` values → new `legislative_status` codes.
5. `enforcement_status` is a derived field — compute from `effective_date` vs `now()` plus any explicit overrides in the source data. This runs in `rollup_matrix.py` after Track 1.D.

**Important context for the engineer:**

- The existing `dim_legislative_statuses` has 10 values, some overlapping (`Signed` vs. `Enacted`, ambiguous `Active`). Don't try to in-place rename — add new codes, populate them via lookup, retire old ones after the FK swap.
- The split between legislative_status and enforcement_status is the cleanest data-modeling improvement in the redesign. Make sure both LKA and SDPA sign off on the semantics so the FE renders the right badge in the right context (LawCard's "Status" badge needs both: "Signed — pre-effective until June 30, 2026").
- The pipeline's `document_versions.temporal_status` uses different values than the product DB's `dim_legislative_statuses`. That's the cross-database misalignment the audit doc flagged. The lookup table fixes it; the sync layer needs no change because the new column is populated by rollup.

---

#### Track 1.H — Re-extract for null `subject_area` laws

**Owner:** NLP (agent prep) + DO (orchestration) + RPR (manual review of low-confidence results)
**Depends on:** 1.A, 1.B, **and a completed extraction run populating `bill_level_extractions`** (see prerequisite note below)
**Parallelizable with:** 1.D, 1.E

> **Prerequisite — not a dependency on a prior track, but on a pipeline run:** `bill_level_extractions` is currently empty (verified 2026-05-26). Track 1.H cannot start until the user runs Dashboard Step 3 ("Extract All") to populate `applicability_agent` rows for every law. Tracks 1.A–1.G can proceed in parallel with the extraction run; 1.H is gated on the run completing.

**What to build:**

The Applicability Agent already extracts enough information to infer `law_category` (it identifies AI system types, sectors, and entity types from the full bill text). Write a post-processing step:

1. For each law with null `subject_area`, retrieve the most recent `applicability_agent` bill-level extraction.
2. Apply a deterministic mapping from Applicability Agent output → `law_category`:
   - If `ai_system_types_in_scope` contains `generative_ai` and is the dominant type → `generative_ai`
   - If exactly one `covered_sectors` value and it's not `general` → `sector_specific_ai`
   - If `government_only = true` and no other strong signal → `comprehensive_ai` (most government-only AI laws are comprehensive in scope within government)
   - ...etc, with explicit rules
3. For laws where the rule produces a confidence-flagged result (multiple possible categories), route to RPR for manual classification.

**Important context for the engineer:**

- Don't write an LLM prompt for this. The Applicability Agent ran already; its output is in `bill_level_extractions`. This is a rule-based mapping over existing data.
- Existing `bill_level_extractions` rows have `agent_name = 'applicability_agent'`. Query that.
- If a law has no Applicability Agent extraction yet, queue it for the next extraction batch — don't try to invent a category.
- The Orrick Gate (auto-Tier D when no Orrick data) means some null-`subject_area` laws might have no validated Applicability extraction. These go to RPR manually.

### 3.4 Phase 1 acceptance criteria

| Criterion | Owner verifies |
|---|---|
| All 232 laws have non-null `law_category_id` | DO + automated test |
| `dim_law_categories` is FK-enforced from `fact_laws` | DevOps |
| `rollup_matrix.py` normalization stage is idempotent (re-runs produce zero changes) | BE + automated test |
| LawCard renders the new badge for all laws | FE + manual QA |
| Vocab committee has met at least twice and processed real items | PTPL |
| `legislative_status` + `enforcement_status` separated, FK-enforced, populated | SDPA + automated test |
| `vocab_review_queue` has been triaged at least once | VC |
| All three databases (local, regs-checker Supabase, policy-navigator Supabase) match | DevOps |

### 3.5 Phase 1 testing strategy

**Unit tests:**
- `tests/unit/test_normalize_law_category.py` — feed known freetext values, assert correct category_id
- `tests/unit/test_lookup_loader.py` — assert lookup file parses, no duplicate keys
- `tests/unit/test_enforcement_status_derivation.py` — date arithmetic edge cases (effective_date = today, future, past, null)

**Integration tests:**
- End-to-end: insert a law with freetext `subject_area`, run `rollup_matrix.py`, assert `law_category_id` is populated
- LawCard query test: filter by `law_category = 'generative_ai'`, assert expected laws returned

**Data quality checks:**
- `SELECT COUNT(*) FROM fact_laws WHERE law_category_id IS NULL` → must be 0 at acceptance
- `SELECT category_code, COUNT(*) FROM fact_laws JOIN dim_law_categories USING(law_category_id) GROUP BY 1` → distribution sanity check (no category should have >50% of laws or <2)
- Spot-check: pick 10 random laws, verify the assigned category matches a human read of the bill title and short summary

**Regression tests:**
- Existing `synced_extractions` queries continue to return identical results
- `v_state_ai_regulation_matrix` view unchanged

### 3.6 Phase 1 monitoring

After deploy, watch for 2 weeks:

| Metric | Threshold | Owner |
|---|---|---|
| `vocab_review_queue` items added per week | <10 (above = pipeline producing too many novel values) | RPR |
| `law_category_id` null rate on new laws | 0% after extraction completes | DO |
| LawCard category filter usage (analytics) | Trend up = working | PTPL |
| `rollup_matrix.py` runtime | <2x baseline (normalization adds load) | DevOps |
| Cross-database row-count parity | Exact match daily | DevOps (via existing `sync_monitor.py`) |

### 3.7 Phase 1 rollback plan

The phase is additive, so rollback is straightforward:

1. **LawCard rollback (fastest):** Feature-flag the new badge component. Flip the flag off; LawCard reverts to displaying `subject_area`.
2. **Normalization rollback:** Drop or comment out the normalize stage in `rollup_matrix.py`. The `law_category_id` column stays populated but isn't refreshed; old `subject_area` queries still work.
3. **Full rollback (only if data corruption):** `ALTER TABLE fact_laws DROP COLUMN law_category_id` + `DROP TABLE dim_law_categories`. The old `subject_area` field is untouched.
4. **Status split rollback:** Same pattern — drop new columns/tables, old `dim_legislative_statuses` was never modified (we added codes via the lookup; original codes still exist).

Do not rollback during the committee's first three weeks; allow time for novel values to surface and be triaged.

---

## 4. Phase 2 — Actor and System Classification

### 4.1 Why this phase exists

Phase 1 classifies laws. Phase 2 classifies the *obligations within laws* — specifically who the obligation applies to (`actor_classification`) and what AI system it covers (`ai_system_scope` and `covered_sectors`). This is what the matching engine joins against. Until this phase ships, `anonymous_audit_profiles.entity_types`, `sectors`, and `ai_system_types` can't reliably JOIN to law data.

### 4.2 What gets built

| Track | Deliverable |
|---|---|
| 2.A | `dim_actor_types` expanded vocab + `actor_scope` field (primary/secondary/protected) |
| 2.B | Normalization: `obligation.subject` → `dim_actor_types.actor_code` |
| 2.C | `dim_ai_scopes` rebuilt with new readable vocab + `law_ai_scopes` junction table |
| 2.D | `covered_sectors` as a standalone law-level array (`law_sectors` junction) |
| 2.E | Profile-side vocab alignment (`anonymous_audit_profiles` reads from dim tables) |
| 2.F | Matching engine update — JOINs against new structures |
| 2.G | Retire the `*` wildcard in old `dim_ai_scopes` (if confirmed unused) |

### 4.3 Track-by-track detail

---

#### Track 2.A — `dim_actor_types` expansion

**Owner:** SDPA + LKA
**Depends on:** Phase 1 complete
**Parallelizable with:** 2.C, 2.D

**What to build:**

Expand `dim_actor_types` from 5 flat values to the agreed 6-value list (`developer`, `deployer`, `provider`, `distributor`, `compute_provider`, `operator`) plus an `actor_scope` enum (`primary`, `secondary`, `protected`).

```sql
ALTER TABLE dim_actor_types
  ADD COLUMN actor_code text;  -- new canonical code
UPDATE dim_actor_types SET actor_code = LOWER(REPLACE(actor_type_name, ' ', '_'));
ALTER TABLE dim_actor_types ADD CONSTRAINT uq_actor_code UNIQUE(actor_code);

-- New rows
INSERT INTO dim_actor_types (actor_code, actor_type_name) VALUES ('operator', 'Operator');

-- New scope enum
CREATE TYPE actor_scope_enum AS ENUM ('primary', 'secondary', 'protected');
ALTER TABLE map_law_requirements ADD COLUMN actor_scope actor_scope_enum;
```

Sector qualifier is **not** a suffix on the actor code (per the standalone-sector decision). Sector context is captured separately via the obligation's `covered_sectors` linkage.

**Important context for the engineer:**

- The existing 5 actor types are referenced by `map_law_requirements` rows. Adding `operator` doesn't break them.
- The proposed taxonomy doc suggested sector-suffixed codes like `DEPLOY_EMPLOYER`. **Don't implement those.** The strategy decision moved sector to a standalone dimension. The `actor_code` stays clean (just `deployer`); the sector context comes from the obligation's sector linkage.
- `actor_scope` is genuinely new. Default value during backfill is `primary` (the most common case). Backfill is rule-based — only switch to `secondary` or `protected` when the obligation text indicates this. RPR provides the rules.

---

#### Track 2.B — Normalize `obligation.subject` → `actor_code`

**Owner:** BE (normalization code) + RPR (lookup table content) + NLP (review of unmapped values)
**Depends on:** 2.A
**Parallelizable with:** 2.C

**What to build:**

A lookup-table-driven normalization in `rollup_matrix.py`. Read each obligation's `payload.subject_normalized` (or `payload.subject` as fallback), map to an `actor_code`, write to a new `obligation_actor` junction table.

```sql
CREATE TABLE obligation_actor (
  obligation_id    int REFERENCES synced_extractions(id),
  actor_type_id    int REFERENCES dim_actor_types(actor_id),
  actor_scope      actor_scope_enum NOT NULL DEFAULT 'primary',
  PRIMARY KEY (obligation_id, actor_type_id)
);
```

Lookup table `data/lookups/subject_to_actor_code.json`:

```json
{
  "developer": "developer",
  "deployer": "deployer",
  "operator": "operator",
  "employer": "deployer",
  "insurance company": "deployer",
  "insurer": "deployer",
  "election authority": "deployer",
  "government agency": "deployer",
  "state agency": "deployer",
  "contractor": "deployer",
  "provider": "provider",
  "vendor": "provider",
  "distributor": "distributor",
  "cloud provider": "compute_provider",
  "compute provider": "compute_provider"
}
```

**Important context for the engineer:**

- The obligation agent's prompt mentions `developer|deployer|operator`. The pipeline's existing `subject_normalized` field uses those three plus freetext. So most rows will map cleanly; sector-specific subjects (employer, insurer, election authority) are the interesting cases.
- The mapping `employer → deployer` is intentional: "employer" describes a sector context, not a supply-chain position. The sector context comes from the obligation's law-level `covered_sectors` linkage.
- The lookup includes synonyms. The existing payload data is messy enough that `vendor`, `provider`, and `supplier` all appear and all mean approximately "provider."
- Rows that don't match the lookup are logged to `vocab_review_queue` for committee triage. Don't auto-create new `actor_code` values from extracted data — the committee approves additions.
- The `subject_normalized` field in payloads sometimes has the LLM's reasoning instead of a code (e.g., `"the developer of the high-risk AI system"`). Strip articles and qualifiers in the lookup logic: lowercase, remove `the`, `a`, `of`, etc., before matching.

**Files touched:**
- `src/scripts/normalization/actor_classification.py`
- `data/lookups/subject_to_actor_code.json`
- `alembic/versions/<new>_add_obligation_actor.py`
- `tests/unit/test_normalize_actor_classification.py`

---

#### Track 2.C — `dim_ai_scopes` rebuild

**Owner:** SDPA + LKA + NLP
**Depends on:** Phase 1 complete
**Parallelizable with:** 2.A, 2.D

**What to build:**

1. Add new `ai_scope_code` column to `dim_ai_scopes` populated with the agreed 8 readable codes (`ai_any`, `high_risk_ai`, `generative_ai`, `foundation_model`, `automated_decision_system`, `synthetic_media`, `biometric_ai`, `personal_data_trained_ai`).
2. Create the `law_ai_scopes` junction table that **doesn't currently exist** (audit doc noted: "no law in the database is linked to these scope codes").
3. Populate `law_ai_scopes` from the Applicability Agent's `ai_system_types_in_scope` array.

```sql
CREATE TABLE law_ai_scopes (
  law_id          int REFERENCES fact_laws(law_id),
  ai_scope_id     int REFERENCES dim_ai_scopes(scope_id),
  PRIMARY KEY (law_id, ai_scope_id)
);
```

Crosswalk from existing Applicability values to new codes:

| Applicability value | New code |
|---|---|
| `high_risk_ai` | `high_risk_ai` |
| `automated_decision_system` | `automated_decision_system` |
| `generative_ai` | `generative_ai` |
| `facial_recognition` | `biometric_ai` |
| `predictive_policing` | `automated_decision_system` |
| `general_purpose_ai` | `foundation_model` |
| `algorithmic_system` | `automated_decision_system` |

**Important context for the engineer:**

- The new codes match `anonymous_audit_profiles.ai_system_types[]` value style (lowercase, snake_case). This is the matching-engine alignment.
- The crosswalk is loss-less except `facial_recognition` and `predictive_policing` which map to broader parents. Preserve the original Applicability value in a `source_label` column on `law_ai_scopes` for audit purposes.
- The old letter-code system (`A`, `F`, `D`, `DH`, etc.) plus `*` wildcard: rebuild the codes in place. Old letter codes get a `retired_at` timestamp; new codes get fresh rows. Don't try to "translate" — there are no `law_scopes` rows currently linked to letter codes (per the audit), so nothing depends on them.
- The `*` wildcard: Track 2.G verifies it's truly unused before retirement.

---

#### Track 2.D — `covered_sectors` as standalone array

**Owner:** SDPA + LKA
**Depends on:** Phase 1 complete
**Parallelizable with:** 2.A, 2.C

**What to build:**

A new `dim_sectors` dim table + `law_sectors` junction. Populated from `applicability_agent.covered_sectors`.

```sql
CREATE TABLE dim_sectors (
  sector_id     serial PRIMARY KEY,
  sector_code   text NOT NULL UNIQUE,
  display_label text NOT NULL,
  display_order int
);

INSERT INTO dim_sectors (sector_code, display_label) VALUES
  ('employment', 'Employment'),
  ('housing', 'Housing'),
  ('credit', 'Credit / Lending'),
  ('education', 'Education'),
  ('healthcare', 'Healthcare'),
  ('insurance', 'Insurance'),
  ('criminal_justice', 'Criminal Justice'),
  ('financial_services', 'Financial Services'),
  ('government_services', 'Government Services'),
  ('elections', 'Elections / Political'),
  ('general', 'General / Not Sector-Specific');

CREATE TABLE law_sectors (
  law_id    int REFERENCES fact_laws(law_id),
  sector_id int REFERENCES dim_sectors(sector_id),
  PRIMARY KEY (law_id, sector_id)
);
```

**Important context for the engineer:**

- The strategy decision was: sector is a standalone dimension, not a modifier suffix on actors or AI scopes. This track implements that.
- `general` is a legitimate value, not a null. Some laws genuinely don't have a sector focus (e.g., Colorado AI Act covers many sectors uniformly).
- The existing Applicability Agent already extracts sectors with the right vocabulary. Crosswalk is 1:1 except for `general` (no change needed).
- The proposed taxonomy doc's `_HEALTH`, `_EMPLOY` suffixes — **don't implement those.** Sector is here, separately.
- `dim_sectors` aligns with `anonymous_audit_profiles.sectors[]`. The matching engine joins these directly. Phase 2.E adds the FK constraint on the profile side.

---

#### Track 2.E — Profile-side vocab alignment

**Owner:** BE + FE (profile entry UI)
**Depends on:** 2.A, 2.C, 2.D
**Parallelizable with:** 2.F

**What to build:**

The strategy decision was "soft enforcement on profile-side initially." Implement that:

1. The profile entry UI reads available values from `dim_sectors`, `dim_actor_types`, `dim_ai_scopes` (not hardcoded lists).
2. New profile submissions store values that **must** match a dim table row (soft check at app layer; not yet a DB FK).
3. Existing profiles with non-matching values are flagged in a migration audit log but not modified.
4. A scheduled job nudges profile-owners to update legacy values.

**Important context for the engineer:**

- `anonymous_audit_profiles` is keyed by `session_id` and may have anonymous data. Don't break existing sessions.
- The FE dropdowns previously had hardcoded options. Move them server-side — the API reads dim tables, returns the option list, the FE renders.
- "Soft enforcement" means: at insert time, log mismatches but don't reject. At Phase 3 the enforcement hardens (FK constraint added once all legacy data is migrated).

---

#### Track 2.F — Matching engine update

**Owner:** BE + PTPL (acceptance)
**Depends on:** 2.A, 2.B, 2.C, 2.D, 2.E
**Parallelizable with:** Nothing in Phase 2

**What to build:**

Update the matching engine queries (the JOINs that produce "which laws apply to this profile?") to use the new dim tables and junctions.

```sql
-- Before (freetext join, broken):
SELECT ... FROM fact_laws WHERE subject_area ILIKE '%' || profile_sector || '%'

-- After (FK-based join):
SELECT ... FROM fact_laws
JOIN law_sectors USING (law_id)
JOIN dim_sectors USING (sector_id)
WHERE sector_code = ANY(profile.sectors)
```

**Important context for the engineer:**

- The matching engine is the heart of the product. Test against a known baseline before/after.
- Phase 1 success criterion 2 requires ≤5% drop in matched laws. Run a comparison query against 10 representative profiles before flipping to the new engine. Any law that drops out gets manual review.
- The new engine should *add* laws too — current freetext matching misses laws with inconsistent tags.

---

#### Track 2.G — Retire the `*` wildcard

**Owner:** SDPA + RPR (verification)
**Depends on:** 2.C
**Parallelizable with:** 2.A, 2.B, 2.D, 2.E

**What to build:**

1. Query existing data: `SELECT COUNT(*) FROM (any table) WHERE ai_scope_code = '*'`. If zero, retire.
2. If non-zero: classify the laws and remap them to the appropriate new code.
3. Soft-delete the `*` row in `dim_ai_scopes` (set `retired_at`).

**Important context for the engineer:**

- Audit doc claims no laws are linked. Verify before retiring.
- The original meaning ("AI trained on personal data") maps to the new `personal_data_trained_ai` code if needed.

### 4.4 Phase 2 acceptance criteria

| Criterion | Owner verifies |
|---|---|
| Every obligation has at least one row in `obligation_actor` with `actor_scope` set | BE |
| Every law has at least one row in `law_ai_scopes` | SDPA |
| Every law has at least one row in `law_sectors` (including `general` for non-sector-specific laws) | SDPA |
| `anonymous_audit_profiles` writes go through dim-table validation (soft) | BE |
| Matching engine returns expected results for 20 representative profiles, with ≤5% delta vs. current | PTPL + BE |
| Vocab committee has approved any novel actor/sector/scope values surfaced during normalization | VC |

### 4.5 Phase 2 testing strategy

**Unit tests:**
- `test_normalize_actor_classification.py` — synonym handling, articles stripping, unmapped → review queue
- `test_law_ai_scopes_crosswalk.py` — known mappings (e.g., `facial_recognition → biometric_ai`)
- `test_matching_engine_profile_match.py` — fixture profiles, expected law sets

**Integration tests:**
- Seed a known profile, run matching, assert expected laws
- Submit a profile with an unknown sector value, assert soft-reject logging

**Data quality checks:**
- Distribution: no actor_code should have >80% or <2% of obligations (sanity)
- Cross-check: profile sectors that appear in audit but no law in `law_sectors` has → flag (potential coverage gap)
- Spot-check 20 obligations: actor classification matches a human read of the obligation text

**Regression tests:**
- Existing `synced_extractions` payloads unchanged
- `v_state_ai_regulation_matrix` view continues to return rows

### 4.6 Phase 2 monitoring

| Metric | Threshold | Owner |
|---|---|---|
| Matching engine result delta vs. baseline | ≤5% drop, additions ok | PTPL |
| Profile-side soft-reject log entries per week | Trend down (users learning new vocab) | BE |
| `vocab_review_queue` additions for actor/sector | <5/week after initial week | VC |
| Matching query p95 latency | Within 2x of current | DevOps |

### 4.7 Phase 2 rollback plan

- Matching engine: feature-flag between old and new query paths
- `obligation_actor`, `law_ai_scopes`, `law_sectors`: drop the junction tables; old data in `synced_extractions` payloads is untouched
- `dim_actor_types` additions: soft-delete via `retired_at`
- Profile UI: revert to hardcoded option lists; the dim-table-driven endpoint can stay (just unused)

---

## 5. Phase 3 — Obligation Classification

### 5.1 Why this phase exists

Phase 2 says *who* the obligation applies to. Phase 3 says *what kind of obligation* it is. The current `dim_requirement_types` has 10 conflated values; the redesign uses a 3-level taxonomy that maps to LawCard's 5 obligation domains.

This phase touches both extraction (prompts must emit Level-2 codes) and serving (LawCard groups by Level-1).

### 5.2 What gets built

| Track | Deliverable |
|---|---|
| 3.A | New `dim_obligation_domains` (Level 1, 5 values) |
| 3.B | New `dim_obligation_types` (Level 2, ~25 values across domains) |
| 3.C | Obligation modifier fields (Level 3 flags: mandatory/conditional, timing, verification) |
| 3.D | Rule-based crosswalk from existing payloads → Level 1 |
| 3.E | Agent prompt update: Obligation + Compliance Mechanism agents emit Level-2 codes |
| 3.F | Targeted re-extraction for Level 2 (sample-first, then full) |
| 3.G | LawCard obligation grouping reconciliation (5 UI categories ↔ 5 Level-1 domains) |
| 3.H | Retirement of conflated `dim_requirement_types` values |

### 5.3 Track-by-track detail

---

#### Track 3.A — `dim_obligation_domains` (Level 1)

**Owner:** SDPA + LKA
**Depends on:** Phase 2 complete
**Parallelizable with:** 3.B, 3.C

**What to build:**

```sql
CREATE TABLE dim_obligation_domains (
  domain_id     serial PRIMARY KEY,
  domain_code   text NOT NULL UNIQUE,
  display_label text NOT NULL,
  display_order int
);

INSERT INTO dim_obligation_domains (domain_code, display_label, display_order) VALUES
  ('risk_impact_assessment', 'Risk & Impact Assessment', 1),
  ('transparency_disclosure', 'Transparency & Disclosure', 2),
  ('governance_documentation', 'Governance & Documentation', 3),
  ('training_awareness', 'Training & Awareness', 4),
  ('subject_rights', 'Subject Rights', 5);
```

**Important context:**

- These 5 align with LawCard's existing 5 UI categories — but the existing categories are cosmetic (per audit doc). Track 3.G makes the alignment functional.

---

#### Track 3.B — `dim_obligation_types` (Level 2)

**Owner:** SDPA + LKA + NLP
**Depends on:** 3.A
**Parallelizable with:** 3.C

**What to build:**

The ~25 Level-2 codes from the strategy doc (`risk_impact_assessment`, `risk_bias_testing`, `transparency_ai_notice`, `governance_policy`, etc.). Each has a FK to its parent domain.

```sql
CREATE TABLE dim_obligation_types (
  obligation_type_id  serial PRIMARY KEY,
  domain_id           int REFERENCES dim_obligation_domains(domain_id),
  type_code           text NOT NULL UNIQUE,
  display_label       text NOT NULL
);
```

**Important context for the engineer:**

- The strategy doc proposed codes like `RISK_BIAS`, `TRANS_AI_NOTICE` (short caps style). The agreed style is readable snake_case → `risk_bias_testing`, `transparency_ai_notice`. Use the snake_case versions.
- The Level-2 codes must be granular enough to power "show me all bias testing requirements across states" but not so granular that they fragment. Aim for codes that map cleanly to LawCard sub-section headers.
- NIST alignment is deferred (per strategy decision). Don't pre-align names to NIST function codes. Names are optimized for product clarity.

---

#### Track 3.C — Obligation modifier fields (Level 3)

**Owner:** SDPA
**Depends on:** Phase 2 complete
**Parallelizable with:** 3.A, 3.B

**What to build:**

Three flag fields on the obligation row (in `synced_extractions` payload or a sidecar table):

```sql
ALTER TABLE synced_extractions
  ADD COLUMN obligation_strength text,  -- 'mandatory' | 'conditional' | 'recommended'
  ADD COLUMN obligation_timing text,    -- 'pre_deploy' | 'at_deploy' | 'post_deploy' | 'recurring'
  ADD COLUMN obligation_verification text; -- 'self_certified' | 'third_party' | 'regulator'
```

**Important context:**

- These are mutually exclusive enums, not arrays. An obligation has one strength, one timing, one verification.
- Backfill is rule-based from existing payload fields (`modality` → strength: `shall/must` = mandatory, `may/should` = recommended).
- Some obligations have null modifiers because the source text doesn't specify. Don't infer aggressively — null is acceptable.

---

#### Track 3.D — Rule-based Level-1 crosswalk

**Owner:** BE + RPR (rules)
**Depends on:** 3.A
**Parallelizable with:** 3.E

**What to build:**

Crosswalk from existing `extraction_type` (7 types) + payload content → Level-1 domain. Most existing extractions have enough info to assign a domain without re-extraction.

| Existing extraction_type | Default Level-1 domain |
|---|---|
| `obligation` (with `action` containing "assess"/"impact"/"bias") | `risk_impact_assessment` |
| `obligation` (with `action` containing "disclose"/"notify"/"inform") | `transparency_disclosure` |
| `obligation` (with `action` containing "document"/"register"/"retain") | `governance_documentation` |
| `obligation` (with `action` containing "train"/"educate") | `training_awareness` |
| `rights_protection` extractions | `subject_rights` |
| `compliance_mechanism` (with `is_bias_testing=true`) | `risk_impact_assessment` |
| `compliance_mechanism` (audits) | `governance_documentation` |
| `compliance_mechanism` (other) | needs human review |

**Important context for the engineer:**

- This is rule-based; no LLM. The rules are fuzzy keyword matches over `payload.action`.
- The "needs human review" bucket goes to RPR via the review queue.
- This produces Level-1 only. Level 2 requires re-extraction (Track 3.F) for granularity that wasn't captured originally.

---

#### Track 3.E — Agent prompt update

**Owner:** NLP
**Depends on:** 3.B
**Parallelizable with:** 3.D

**What to build:**

Update `prompts/obligation.yml` and the compliance_mechanism agent prompt to emit Level-2 codes as new payload fields:

```yaml
# In the obligation prompt:
- obligation_type: One of the Level-2 codes from the controlled vocab below.
- obligation_domain: One of the 5 Level-1 domains.
```

Include the controlled vocab in the prompt itself (the LLM must see the list of valid codes).

**Important context for the engineer:**

- The pipeline's design principle is that the LLM is unreliable at strict enums. Mitigate by:
  1. Including the full enum list in the prompt
  2. Validating output against `dim_obligation_types.type_code` at parse time
  3. Routing mismatches to the review queue
- Don't make Level-2 mandatory in the Pydantic schema (yet). Make it optional during the rollout; rows without it get classified by Level-1 crosswalk + human review.
- The Orrick Gate (auto-Tier D when no Orrick data) still applies. Re-extraction confidence is independent of the new fields.
- Update `tests/fixtures/gold_standard/` examples to include the new fields. Run the eval harness to confirm the agent learns the new vocab.

---

#### Track 3.F — Targeted re-extraction

**Owner:** NLP + DevOps (compute) + RPR (review)
**Depends on:** 3.E
**Parallelizable with:** Nothing (downstream)

**What to build:**

Phased re-extraction:

1. **Sample run:** 50 laws, mixed by state and category. NLP + RPR review results. Adjust prompt if needed.
2. **Tier C/D re-run:** Re-extract low-confidence obligations (the strategy doc's "hybrid: rule-based now, re-extract later for low-confidence" decision). These benefit most from re-extraction because they were going to need review anyway.
3. **Tier A/B fill-in:** Run the new agent only for the *new* fields on existing high-confidence rows. Don't re-extract everything; merge new field values into the existing row.
4. **Full corpus optional:** Decision point after step 3 — is the data quality good enough, or do all 28,885 need full re-extraction?

**Important context for the engineer:**

- The pipeline has a `--mode recover` flag (`src/scripts/seed_pipeline.py:521`) for re-running failed extractions. Use the same pattern for the sample / Tier C/D / fill-in passes.
- **Model:** All extraction is local via LM Studio. The default model is `google/gemma-4-26b-a4b` per `config/agent_models.json`. Re-extraction with a new prompt does not require a different model — agent quality is governed by the prompt change and the per-agent token budget. If a different model is needed for a quality experiment, swap via the Dashboard Models page (`/dashboard/models`) which writes to `agent_models.json` and hot-reloads agents. The `AnthropicProvider` is archived; there is no Claude/Sonnet/Haiku path.
- **Run orchestration:** Sample runs (~50 laws) go through Dashboard "Extract N (Test)" with a `limit` parameter; the auto-purge logic now preserves prior extractions when `limit` is set (Phase 7M-E fix). Full-corpus runs go through Dashboard Step 3 ("Extract All"). Targeted Tier C/D re-runs can be driven by Dashboard "Reset Failed" + re-run; for a more selective re-run set, the rollup script (or a one-off query) tags candidate `extraction_id`s and `--mode recover` consumes them.
- **Versioned re-extraction (A/B store) — `agent_name` suffix convention:** The `bill_level_extractions` table has no `agent_version` column; the unique constraint is `(document_version_id, agent_name)`, so re-runs overwrite via upsert. To keep old and new payloads coexisting during validation, suffix the agent name when running the new prompt: `applicability_agent` → `applicability_agent_v2`. Rollup queries point at the latest version per agent family (`agent_name LIKE 'applicability_agent%'` ordered by `created_at DESC`) unless an `?include_legacy=true` query parameter is passed. Once the new version is validated, the previous version is soft-retired (rows kept; rollup stops querying them). If A/B store usage gets heavy across the project, promote this convention to a real `agent_version` column in a future migration; until then, the suffix avoids a three-database schema change for a Phase-3-only need.
- **Confidence tier shifts** are the biggest risk. Monitor the Tier A/B/C/D distribution before/after each re-extraction batch; >10% A→B drops trigger a prompt review.
- **Cost:** Re-extraction is the heaviest step in the entire plan. Budget GPU time per agent (see Phase 7J per-agent timing in `extractions.duration_ms`). Sample-first sequencing is non-optional.

---

#### Track 3.G — LawCard obligation grouping reconciliation

**Owner:** FE + PTPL + LKA
**Depends on:** 3.A, 3.D
**Parallelizable with:** 3.F

**What to build:**

LawCard's existing 5 UI categories were cosmetic. Reconcile them with the 5 Level-1 domains. If the names need to change, change them. If grouping logic changes, update it.

**Important context:**

- This is mostly an FE renaming + a JOIN swap, but LKA approval is required because the category names are user-facing legal terms.
- Some existing UI categories may have been merging things that the new taxonomy splits. Document the change in user-facing release notes.

---

#### Track 3.H — Retire old `dim_requirement_types`

**Owner:** SDPA
**Depends on:** 3.D, 3.E, 3.F (full re-extraction or rule-based crosswalk complete)
**Parallelizable with:** Nothing (cleanup)

**What to build:**

Soft-delete the conflated 10 values in `dim_requirement_types` after the new system is fully populated. Don't actually drop them — `retired_at` only, for audit history.

### 5.4 Phase 3 acceptance criteria

| Criterion | Owner verifies |
|---|---|
| Every obligation has a Level-1 domain assigned (rule-based + manual review for misses) | BE + RPR |
| At least 80% of obligations have a Level-2 type assigned | NLP |
| Eval harness shows ≥90% Level-2 prompt accuracy on gold-standard fixtures | NLP |
| LawCard groups obligations by the new Level-1 domains with no "Other" bucket | FE |
| Re-extracted obligations don't show widespread Tier shifts (>10% drops from A→B is a problem) | NLP + RPR |

### 5.5 Phase 3 testing strategy

**Unit tests:**
- `test_level1_crosswalk.py` — fuzzy keyword rules return expected domains
- `test_obligation_modifier_backfill.py` — modality → strength derivation
- `test_obligation_prompt_emits_codes.py` — mock LLM response, assert parsed fields

**Integration tests:**
- End-to-end re-extraction on 3 fixture laws → assert new fields populated
- LawCard query → obligations grouped by domain, sorted by display_order

**Eval harness:**
- Run `tests/fixtures/gold_standard/` against the updated agent, compare to baseline
- Track Tier A/B/C/D distribution before and after prompt change

**Data quality checks:**
- Distribution: each Level-1 domain has between 5% and 40% of obligations (no over-concentration)
- Cross-reference: a sample of 20 obligations have human-validated Level-2 codes matching extraction output

### 5.6 Phase 3 monitoring

| Metric | Threshold | Owner |
|---|---|---|
| Tier distribution shift after re-extraction | A+B ≥ 70% (current baseline) | NLP |
| Level-2 unmapped rate | <10% of new extractions | NLP + VC |
| LawCard "Other" bucket | Empty | FE |
| Re-extraction cost (tokens, $) | Within budget | DevOps + PTPL |

### 5.7 Phase 3 rollback plan

- Agent prompt: revert YAML file; pipeline runs with old prompt. New fields stop populating but existing data is fine.
- Level-1 crosswalk: drop the new columns; LawCard falls back to old (cosmetic) grouping.
- Re-extracted rows: each carries a versioned `agent_name` (e.g. `applicability_agent_v2`); delete the new-version rows, retain the original `applicability_agent` rows, and revert rollup queries to drop the `LIKE 'agent_v2%'` filter.

---

## 6. Phase 4 — Harm Categories and Preemption (Deferred Dimensions)

### 6.1 Why this phase exists

Two dimensions in the strategy weren't critical for Phase 1–3 but matter for full product value:

- **`harm_categories`** — what kind of harm the law protects against (discrimination, deception, election interference, etc.). Powers the "Protects against" section on LawCard.
- **`preemption_status`** — whether a law preempts/is preempted by other laws. Powers cross-jurisdictional conflict detection.

Neither blocks the matching engine; both improve product depth.

### 6.2 What gets built

| Track | Deliverable |
|---|---|
| 4.A | `dim_harm_categories` + `law_harm_categories` junction |
| 4.B | Rights Protection agent prompt update to emit harm codes |
| 4.C | Harm-category re-extraction (sample + full) |
| 4.D | `dim_preemption_statuses` + `fact_laws.preemption_status_id` |
| 4.E | Preemption signal → preemption status classification (rule-based, possibly LLM-assisted) |
| 4.F | LawCard "Protects against" + jurisdictional conflict sections |

### 6.3 Track-by-track detail

(Each track follows the same pattern as Phase 1–3: dim table, FK, lookup or agent, normalization, FE.)

**Owner assignments:**

- 4.A, 4.D: SDPA + LKA
- 4.B, 4.C, 4.E: NLP + RPR
- 4.F: FE + PTPL

**Important context for the engineer:**

- The strategy doc identified 8 harm categories but flagged them as needing validation against actual law content (Section 7, open items). Phase 4 starts with that validation: RPR samples 30 laws, identifies harms, refines the list.
- Preemption is the more complex of the two. The `preemption` agent already runs and stores verbatim quotes. Phase 4.E adds a classification layer on top — given the quotes, what status (`preempted_federal`, `preempts_local`, `express_savings`, `neutral`, `contested`) applies?
- This phase is the natural point to revisit the deferred NIST/ISO crosswalk work, because `definition_actor.framework_refs` populates `resp_*` framework tables — both can run in parallel.

### 6.4 Phase 4 acceptance criteria

| Criterion | Owner verifies |
|---|---|
| `harm_categories` populated for ≥80% of laws | RPR |
| `preemption_status` populated for 100% of laws (default `neutral` if no signals) | NLP |
| LawCard "Protects against" renders for ≥80% of laws | FE |
| Multi-state profile matching surfaces preemption flags in output | PTPL |

### 6.5 Testing, monitoring, rollback

Same patterns as prior phases. Specifics deferred to Phase 4 planning kickoff.

---

## 7. Phase 5 — Framework Crosswalk (NIST AI RMF, ISO 42001)

### 7.1 Why this phase exists

The product goal "I follow NIST AI RMF Govern 1.1, which state laws does that satisfy?" requires the `resp_*` framework tables to be populated and crosswalked to the obligation taxonomy. The audit doc noted these tables are entirely empty.

This phase was explicitly deferred during strategy. It runs only after Phases 1–4 are stable.

### 7.2 What gets built

| Track | Deliverable |
|---|---|
| 5.A | Populate `resp_framework` and `resp_framework_version` with NIST AI RMF 1.0, ISO 42001:2023, NIST CSF 2.0 |
| 5.B | Populate `resp_theme` and `resp_subtheme` for each framework |
| 5.C | Populate `resp_control` with individual controls (NIST AI RMF subcategories, ISO clauses) |
| 5.D | Crosswalk from `dim_obligation_types` (Level 2) → `resp_control` codes |
| 5.E | Crosswalk from `definition_actor.framework_refs` extractions → `resp_control` |
| 5.F | LawCard "Frameworks satisfied" + query support for reverse lookups |

### 7.3 Track-by-track detail

**Owners:** SDPA (schema), LKA (legal mapping), RPR (control content), SME (validation), FE (UI), PTPL (sequencing).

**Important context for the engineer:**

- The `resp_*` schema is already in the product DB (per the SQL backup files). It just isn't populated.
- This phase is fundamentally a data-entry + curation effort, not an engineering build. SDPA's role is small; LKA/RPR/SME do the work.
- Crosswalk Level-2 obligation codes → framework controls is potentially many-to-many. Use the existing `control_standard_crosswalk` pattern in the product DB.

### 7.4 Acceptance, testing, monitoring, rollback

Defer to Phase 5 planning. Same patterns.

---

## 8. Cross-Phase Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Re-extraction tier shifts (Phase 3) destabilize the review queue | High | Sample-first rollout; A/B store via `agent_name` suffix convention (e.g. `applicability_agent_v2`); rollback by deleting the suffixed rows |
| Vocab committee becomes a bottleneck | Medium | Fast-lane rule for low-ambiguity additions; weekly cadence |
| Profile-side vocab drift from law-side | Medium | Phase 2.E uses dim tables for FE dropdowns; Phase 3+ hardens to FK |
| Cross-database sync lag during migrations | High | Run migrations during low-traffic windows; `sync_monitor.py` checks parity |
| LawCard regressions during FE updates | Medium | Feature flags per phase; rollback by flag flip |
| Orrick Gate prevents validation of new extractions | Medium | Orrick metadata coverage audited per phase; gaps escalated to RPR |
| 28,885 existing extractions partially migrated | High | Additive model — old data untouched; new columns/tables coexist until validated |

---

## 9. Timeline Notes

Per the strategy decision, timeline is open-ended with incremental shipping. The phases form natural milestones:

- Phase 1 closes when LawCard renders new badges and matching engine has a stable foundation
- Phase 2 closes when the matching engine ships with the new structure
- Phase 3 closes when LawCard groups obligations correctly
- Phase 4 closes when "Protects against" and preemption ship
- Phase 5 closes when framework reverse-lookup queries work

Each phase's sub-tracks have explicit parallelization flags. PTPL sequences within phases based on team capacity.

---

## 10. Document Maintenance

- This document is a working spec, not a contract. Update as phases close and learnings emerge.
- Append decisions to the Section 9 log in `taxonomy_strategy_summary.md`.
- When a phase ships, mark its tracks complete here and link to the actual PRs / migrations.
- Owners and acceptance criteria can be edited mid-phase if the team learns the original definition was wrong — but document the change.
