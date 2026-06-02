# Regs Checker — Pipeline Rebuild Plan

**Status:** Working draft v1 — gated rebuild proposal, not a committed roadmap
**Companion docs:** `taxonomy_dev_plan.md` (the alternative path), `taxonomy_strategy_summary.md` (shared design decisions)
**Scope:** A bounded, evidence-first plan for rebuilding the Regs Checker pipeline from the ground up, using the existing system as creative reference rather than a constraint

---

## 0. What This Plan Is (and Isn't)

**This is** a phased plan that opens with a one-week parallel-slice experiment to validate whether a clean rebuild is worth committing to. The full rebuild phases are documented but **explicitly gated** on the slice's outcome. We do not commit to the rebuild before we have evidence.

**This is not** an alternative roadmap that runs in addition to the taxonomy redesign. The two plans (`taxonomy_dev_plan.md` and this one) represent **mutually exclusive strategic paths**:
- The **taxonomy redesign** evolves the existing system additively through 5 phases. Lower risk, slower to a clean architecture, preserves all encoded bug fixes.
- The **rebuild** replaces the existing pipeline with a schema-first, bill-level-first, cloud-capable system. Higher risk, faster to a clean architecture, requires re-discovering some bugs.

The Phase 0 slice in this plan is how we decide which path to commit to.

**Companion to:** The "How might this project change" analysis from the 2026-05-26 session. That analysis is the *why*; this is the *how* and *when*.

---

## 1. Strategic Context

### Why consider rebuilding

The existing pipeline carries architectural decisions that are now expensive:
- 3 databases requiring sync scripts, parity monitoring, dual-migration runbooks
- `extractor.py` at 2600 lines with overlapping concerns (triage + extraction + retry + verification + dependency graph + condition parsing)
- 6 passage-level + 3 bill-level agents that grew incrementally; bill-level was added in Phase 7 after passage-level proved insufficient
- Freetext-mixed-with-enums schema that requires a 5-phase taxonomy migration to clean up
- Local-only LLM execution that gates the project on one workstation's GPU
- No eval harness; prompt iteration is "deploy and hope"

### What we keep

The load-bearing design principles survive the rebuild:

1. **Inference is offline, serving is instant.** LLM as build tool, not runtime dependency.
2. **Confidence tiers + human review queue.** The only way to ship trustable legal extraction.
3. **Orrick reference data as a validation gate.** External authoritative source = automatic Tier D when missing.
4. **Controlled vocabularies + lookup tables + vocab committee.** From the taxonomy redesign work — built in from day one rather than retrofitted.
5. **Additive-not-destructive within the new system.** Re-extractions append; the old extraction data is preserved as creative reference.

### What changes

Summarized for context; full rationale in the 2026-05-26 analysis:

- **Schema-first** (vs. pipeline-first)
- **Single database** (vs. local Docker + 2 Supabase projects)
- **Bill-level agents first** (vs. passage-level first; bill-level retrofitted)
- **Extractors and classifiers split** (vs. agents doing both jobs)
- **Source-text hash validation** (vs. trust the file path)
- **Eval harness from day one** (vs. eval set never built)
- **Cloud-capable, local-default** provider abstraction (vs. local-only after Anthropic archived)
- **Versioning built in** (vs. retrofit via `agent_name` suffix)

### What you have as input

- **All 232 currently-written laws** (`data/fact_laws.csv` + `output/law_texts/`)
- **The existing first-pass extraction data** (~28,885 obligation extractions in the existing pipeline) — usable for: bootstrapping the eval set, training classifiers, and as a creative reference for what the new agents should produce
- **Orrick reference data** — same as existing system

---

## 2. Provider Stance: Cloud-Capable, Local-Default

The rebuild un-archives the `AnthropicProvider` and stands up a working provider abstraction from day one.

**Default routing:**
- **Local (Gemma 4 26B via LM Studio)** for high-volume passage-level work, dev iteration, eval-harness runs, and any work that should stay on the local stack for cost/privacy.
- **Anthropic Sonnet 4.5** for quality samples, prompt-comparison runs against the eval set, and bill-level extraction batches where quality matters more than cost. Used for re-extraction passes.
- **Anthropic Haiku 4.5** for high-volume classification work where speed and parallelism matter more than reasoning depth.

**Cost ceiling:**
- A `REBUILD_API_BUDGET_USD` env var caps cloud spend. Pipeline halts with a clear error when ceiling hit.
- Estimated full-corpus run on Sonnet for one bill-level agent across 232 laws: **$30–60**. Three agents: **~$100–200**.
- Estimated Haiku classification run across ~28,885 obligations: **~$20–40**.
- The slice (Phase 0) is budgeted at **$50 hard cap**.

**Selection rule:** Per-agent config picks default provider. Override via CLI flag (`--provider=anthropic`, `--model=claude-sonnet-4-6`) for one-off runs. Eval-harness runs always include both providers so we have evidence per task.

---

## 3. Phase 0 — The Slice (the actual ask)

**Status: This is the only phase currently funded for execution.** Everything from Phase 1 onward is gated on Phase 0's outcome.

**Duration:** ~1 week (5 working days + 1–2 days writeup buffer).
**Budget:** $50 cloud spend ceiling.
**Owner:** Single engineer; the full role roster (LKA, RPR, etc.) is *not* mobilized for the slice.

### 3.1 Why this phase exists

We need evidence — not opinion — about whether the rebuild architecture wins decisively over the existing one. The slice produces that evidence on the highest-value piece of the system (the applicability agent + matching engine) with the smallest possible bet.

### 3.2 What gets built

A single bill-level **applicability agent**, on a clean schema, running against the same 232 laws the existing pipeline has already processed, with a side-by-side comparison harness against the existing pipeline's output.

| Day | Deliverable |
|---|---|
| Pre-flight | Slice lives in `rebuild/` subdirectory of the existing repo (separate Python package, separate Supabase schema `rebuild_v0`). New code does not import from `src/`. Existing pipeline continues to run untouched. Bootstrap eval set: 30 hand-verified applicability extractions sampled from the existing data. |
| Day 1 | Clean schema: `laws`, `dim_law_categories`, `dim_sectors`, `dim_actor_types`, `dim_ai_scopes`, `dim_jurisdictions` + the three junction tables needed for matching (`law_sectors`, `law_actors`, `law_ai_scopes`). Migration applied to `rebuild_v0` schema. Seed Orrick + fact_laws. |
| Day 2 | Provider abstraction: `Provider` interface, `LocalProvider` (LM Studio) + `AnthropicProvider` (Sonnet 4.5 + Haiku 4.5). Cost meter + budget cap. Eval-harness skeleton. |
| Day 3 | Applicability agent: bill-level prompt, structured output via Pydantic, writes directly to `laws` + junctions (no JSONB payload, no rollup_matrix step). Runs against all 232 laws on Sonnet (budgeted ~$30) and on Gemma (free, slower). |
| Day 4 | Minimal LawCard render (Jinja template or single Next.js page — whichever is fastest) + matching engine query: given a profile, return applicable laws via FK joins. No UI polish. |
| Day 5 | Comparison harness: same 20 representative profiles as the taxonomy plan's Phase 2.F baseline. Run against existing pipeline + against rebuild. Measure: matched-law overlap, eval-set accuracy, latency, total cost, lines of code. |
| Day 6–7 | Writeup: evidence table, recommendation, kill-or-commit call. |

### 3.3 Acceptance gate (Phase 0 → Phase 1 commit)

Phase 1 of the full rebuild **only proceeds** if the slice clears all of these:

| Gate | Threshold | Why this threshold |
|---|---|---|
| **Eval accuracy (rebuild vs hand-verified)** | ≥ 90% on the 30-law eval set | Must beat or match the existing pipeline's accuracy on the same set. Below 90% = the architecture isn't materially better at the agent level. |
| **Matching-engine delta vs existing** | ≤ 5% drop, additions OK | Same threshold as taxonomy plan Phase 2.F. The rebuild must not lose matches the existing engine finds. |
| **End-to-end latency per law** | < existing pipeline latency on equivalent agent | If the rebuild is slower, the architecture isn't paying for itself. |
| **Total slice cost** | ≤ $50 cloud spend | Budget discipline; also validates the cost projection for full rebuild. |
| **Code surface** | Rebuild applicability + matching < 500 LOC end-to-end | The "extractor.py is 2600 lines" argument has to actually hold up at small scale. |
| **Operational simplicity** | One database, one migration command, one launch command, no sync scripts | The 3-DB elimination has to be real, not theoretical. |

### 3.4 Kill conditions (Phase 0 → abandon rebuild)

The rebuild idea is killed and we commit to the taxonomy redesign if any of:

- **Eval accuracy < 80%** on the rebuild (worse than existing) — the clean architecture doesn't produce better extractions, only differently-structured ones.
- **Matching engine drops > 10% of laws** vs. existing — the new schema is missing something the freetext schema captured implicitly.
- **Provider abstraction blows the cost budget by > 2×** — cloud-default optionality is more expensive than projected.
- **Slice takes > 2 weeks of calendar time** — if the highest-leverage piece takes that long, the full rebuild estimate (5 weeks) is fantasy.
- **Re-discovers > 3 of the existing project's known bugs** in week 1 — the encoded knowledge in `extractor.py` is more valuable than the architecture penalty.

### 3.5 Phase 0 deliverables

- `rebuild/` directory containing: schema migrations, provider module, applicability agent, comparison harness
- `docs/eval_set_v0.json` — 30 hand-verified applicability extractions
- `docs/slice_results.md` — evidence table + recommendation + go/no-go call
- Decisions log entry in `taxonomy_strategy_summary.md` §9

---

## 4. Decision Gate

After Phase 0, exactly one of three things happens:

### 4.1 COMMIT to rebuild
All Phase 0 gates cleared. Proceed to Phase 1 of this plan. Park the taxonomy redesign (it's no longer needed — the rebuild's schema-first design eliminates it). Communicate the decision; brief the wider team.

### 4.2 ABANDON rebuild
Any kill condition tripped. Delete `rebuild/`. Resume taxonomy redesign work per `taxonomy_dev_plan.md`. The slice still produced two reusable artifacts: the eval set + the provider abstraction. Both can be ported into the existing pipeline.

### 4.3 ITERATE
Mixed results — most gates clear but one or two are borderline. Extend the slice by ≤1 week to address the specific weak gate. **Hard limit: one iteration only.** If a second iteration is needed, that's evidence the architecture isn't ready and we abandon.

---

## 5. Phase 1 — Foundations (Week 2)

**Gated on:** Phase 0 COMMIT decision.
**Duration:** 1 week.
**Cloud budget:** $100 (mostly eval-harness runs).

### 5.1 What gets built

The full clean schema, the full provider abstraction, and the eval harness that all subsequent work depends on.

| Track | Deliverable |
|---|---|
| 1.A | Complete schema: all dim tables (`dim_law_categories`, `dim_legislative_statuses`, `dim_enforcement_statuses`, `dim_sectors`, `dim_actor_types`, `dim_ai_scopes`, `dim_obligation_domains`, `dim_obligation_types`, `dim_harm_categories`, `dim_preemption_statuses`) + all junctions + all FKs. LKA-approved vocabularies seeded. |
| 1.B | Single database: production Supabase project (the existing Regs Checker project, in a new schema `regs_v2`). Local dev uses Supabase branching, not a separate Docker stack. |
| 1.C | Provider abstraction (productionized from slice): `LocalProvider`, `AnthropicProvider`, cost meter, budget ceiling, structured logging per call. |
| 1.D | Eval harness: extends the 30-extraction slice eval set to 100. Hand-verified by LKA + RPR. Used as CI gate for every prompt change. |
| 1.E | Versioning baseline: every extraction row carries `(agent_name, prompt_version, model_id, ran_at)`. Unique constraint `(law_id, agent_name, prompt_version)`. A `currently_active_<agent>` view selects the latest validated prompt_version per agent. |
| 1.F | Source-text hash validation: every law's source bytes are SHA256'd at ingestion; mismatches refuse to enter the pipeline. |
| 1.G | Vocab committee on day one: empty `vocab_review_queue`, documented process, weekly cadence even before there's much to triage. |

### 5.2 Acceptance gate

- All 232 laws ingested with verified source-text hashes
- All dim tables seeded with committee-approved vocab
- Eval harness CI runs on every PR; gate fails on regression
- Provider abstraction produces identical structured output for both providers on a known fixture
- Versioning view returns the right row after a simulated `agent_v2` re-run

### 5.3 Why this phase before agents

If schema + eval + versioning aren't bedrock before agents, you re-create the exact pathology that drove the rebuild: agents grew first; schema chased them; eval never happened; versioning was retrofitted.

---

## 6. Phase 2 — Bill-Level Agents + LawCard (Week 3)

**Duration:** 1 week.
**Cloud budget:** $200 (full-corpus Sonnet runs for three agents).

### 6.1 What gets built

The three bill-level agents from the existing system, on the clean architecture, plus the LawCard view that consumes them.

| Track | Deliverable |
|---|---|
| 2.A | `applicability_agent` (productionized from slice). Writes directly to typed columns + junctions. No JSONB rollup step. |
| 2.B | `enforcement_agent`. Writes to `laws.enforcing_body`, `laws.max_civil_penalty_usd`, `laws.cure_period_days`, etc. — typed columns, no JSONB. |
| 2.C | `compliance_timeline_agent`. Writes to `laws.effective_date`, `laws.enforcement_start_date`, `law_deadlines[]` — typed columns. |
| 2.D | LawCard v1: renders law-level summary using the new typed columns. Single-page, query-driven. No legacy badge fallback (no legacy data). |
| 2.E | Matching engine v1: FK joins against the four matching dimensions (sector, actor, AI system scope, jurisdiction). |

### 6.2 Acceptance gate

- All three bill-level agents pass eval-harness CI at ≥ 90% accuracy
- LawCard renders for all 232 laws with no nulls in displayed columns
- Matching engine returns expected results for the 20 baseline profiles from Phase 0
- Total cloud spend stayed under budget

### 6.3 What ships at end of Phase 2

**End-user-visible product.** LawCard + matching engine are live for the wider team to use. The rebuild has shipped its first user-visible value at the 4-week mark (1 slice + 3 phases).

---

## 7. Phase 3 — Passage-Level Extraction (Week 4)

**Duration:** 1 week.
**Cloud budget:** $150 (passage volume, Haiku for classification, Sonnet for verification).

### 7.1 What gets built

Passage-level extraction for per-obligation detail, **only on passages the bill-level agents flagged as containing specific obligations**. This is the key inversion vs. the existing system, which extracts every passage.

| Track | Deliverable |
|---|---|
| 3.A | Passage flagging in bill-level agents: each agent emits `passages_containing_obligations: list[int]` alongside its main output. |
| 3.B | Passage-level `obligation_extractor` — quotes verbatim spans, identifies action/subject/conditional, writes to `obligations` table. Runs only on flagged passages. |
| 3.C | Passage-level `obligation_classifier` — separate from extractor. Takes an extracted obligation + the controlled vocab; assigns `obligation_domain_id` + `obligation_type_id` + modifier flags. Can run as rule-based + small-model hybrid for cost. |
| 3.D | Review queue v1: HTMX page showing extractions ordered by confidence. Approve / reject / retag. |
| 3.E | Confidence scoring v2: 3-component (evidence verified, Orrick aligned, schema valid) rather than 6-component weighted sum. Tier boundaries derived from quantiles of the eval-set distribution, not hand-picked. |

### 7.2 Acceptance gate

- ~70% reduction in passages requiring LLM calls vs. existing system (the flagging optimization works)
- Obligation extractor passes eval at ≥ 90% on the 100-extraction eval set
- Obligation classifier reaches ≥ 85% on Level-2 codes
- Review queue is usable: a reviewer can process 50 extractions per hour

### 7.3 What ships at end of Phase 3

Reviewer workflow is live. Wider team can start approving extractions. Passage-level obligation detail begins flowing into LawCard.

---

## 8. Phase 4 — Iterate (Week 5+)

**Duration:** open-ended.

The four scheduled phases (0–3) take a clean rebuild from zero to shipped product in 4 weeks of calendar time, assuming Phase 0 clears. Phase 4 is everything after.

### 8.1 What this phase covers

| Track | Why now (not earlier) |
|---|---|
| Harm categories + preemption | Per the taxonomy strategy, deferred dimensions. Ship LawCard first; add depth later. |
| NIST AI RMF / ISO 42001 framework crosswalk | Bigger curation effort than engineering. Runs in parallel with product polish. |
| Re-extraction passes for prompt iteration | The eval harness + versioning view make this cheap. Run nightly if useful. |
| Classifier separation deepening | Move more classification out of LLM calls into rule-based + small-model paths as we learn what's stable. |
| LawCard polish, additional filters, comparison views | Driven by user feedback once Phase 3 ships. |

### 8.2 No fixed acceptance gate

Phase 4 success is product-driven, not architecture-driven. The architecture work was Phase 0–3.

---

## 9. Role Mapping for the Rebuild

The rebuild needs fewer hands than the taxonomy redesign because there's no migration complexity.

| Role | Phase 0 (slice) | Phase 1 (foundations) | Phase 2 (bill-level) | Phase 3 (passage-level) |
|---|---|---|---|---|
| **NLP** | Build applicability agent + provider abstraction | Eval harness; verify provider parity | Build enforcement + timeline agents | Build extractor + classifier split |
| **SDPA** | Slice schema (5 dims + 3 junctions) | Full clean schema | — (schema already done) | Add `obligations` table + review queue tables |
| **BE** | Comparison harness | Cost meter + budget ceiling; versioning view | Matching engine v1 | Review queue API |
| **LKA** | — (uses existing Orrick + fact_laws) | Sign off on full vocab seeding | — | Sign off on obligation Level-1/2 codes |
| **RPR** | Hand-verify the 30-law eval set (Day 0) | Extend eval set to 100 | — | Review eval-set quality for passage-level |
| **FE** | Minimal Jinja LawCard page | — | LawCard v1 | Review queue UI |
| **PTPL** | Hold the Phase 0 gate; make the commit/abandon call | Coordinate; defer feature creep | Stakeholder demos | User feedback gathering |
| **DO / DevOps / VC / SME** | Not mobilized | Light involvement | Light involvement | Standard cadence (committee, ops) |

A small team (NLP + SDPA + BE + 1 reviewer hand-verifying eval set) can run Phase 0 alone. The wider team mobilizes only after the COMMIT decision.

---

## 10. Risk Matrix

| Risk | Likelihood | Severity | Mitigation |
|---|---|---|---|
| Phase 0 slice produces ambiguous results (most gates clear, 1–2 borderline) | Medium | Low | The ITERATE branch in §4.3 — one extension allowed, hard limit |
| Cloud spend overshoots | Medium | Medium | Hard budget caps per phase; pipeline halts on ceiling, not on warning |
| Eval set isn't representative; gates pass but production fails | Medium | High | Phase 1 extends eval set to 100; sampling stratified by category + tier + state |
| Architecture wins on slice but doesn't scale (works at 1 agent, fails at 9) | Low | High | Phase 2 ships 3 agents — second proof point at higher complexity |
| Existing project bugs re-discovered en masse | Medium | Medium | Read the existing bug list as a defensive design spec; defend structurally where possible |
| Team momentum on existing project disrupted | High | Medium | Phase 0 is single-engineer; existing pipeline runs untouched until COMMIT decision |
| Provider abstraction adds enough complexity to outweigh the cloud benefit | Low | Medium | Slice tests both providers end-to-end; the abstraction is itself a Phase 0 deliverable |
| Single-database stance loses pipeline/product firewall | Low | Medium | Schema namespacing (`regs_v2`); RLS policies; separate DB user for the agents |

---

## 11. What Gets Simpler (Carried Forward from the 2026-05-26 Analysis)

For reference; supports the rebuild case if Phase 0 clears.

| Existing | Rebuild |
|---|---|
| 3 databases + sync scripts + `payload_adapter.py` + parity monitoring | 1 database |
| `extractor.py` at 2600 lines | Split into ~6 modules, each <500 lines |
| 9 agents (6 passage + 3 bill-level) with overlapping concerns | Bill-level first; passage-level only where flagged |
| Taxonomy redesign as 5-phase migration | Built in; no migration needed |
| `rollup_matrix.py` doing normalization + crosswalks + aggregation | Aggregation only; normalization is at insert time |
| `vocab_review_queue` triaging years of inconsistent freetext | Empty most days; catches genuine novelty only |
| LM Studio + Docker Postgres + Supabase + MinIO + paused-project recovery | Supabase + a Python venv |
| Adaptive token retry + JSON repair strategies 1–5 + channel-thought HTTP 400 recovery | Cloud fallback + better default model = workarounds unnecessary |

---

## 12. Sequencing Summary

| Week | Phase | Outcome | Spend |
|---|---|---|---|
| 1 | Phase 0 — Slice | Evidence + commit/abandon decision | ≤ $50 |
| 2 | Phase 1 — Foundations | Schema + eval + provider + versioning | ≤ $100 |
| 3 | Phase 2 — Bill-level + LawCard | First user-visible product ship | ≤ $200 |
| 4 | Phase 3 — Passage-level | Reviewer workflow live | ≤ $150 |
| 5+ | Phase 4 — Iterate | Product polish + harm/preemption + framework crosswalk | open |

Total cloud spend through Phase 3: **≤ $500**. Total calendar time: **4 weeks from Phase 0 start to user-visible product**, assuming Phase 0 clears.

---

## 13. Document Maintenance

- Update Phase 0 status as the slice runs.
- Append decisions to `taxonomy_strategy_summary.md` §9.
- If COMMIT: this doc becomes the active project plan; `taxonomy_dev_plan.md` moves to `archive/`.
- If ABANDON: this doc moves to `archive/`; `taxonomy_dev_plan.md` remains the active plan.
- If ITERATE: extend Phase 0 in place; revisit decision at the iteration's end.
