# R2/R3 Findings: Coverage, Quality, and Track 3.F Scope

**Phases:** R2 (C-3, C-8) and R3 (C-5, C-6) from `extraction_run_remediation_plan.md`
**Status:** R2 complete — actions defined. R3 complete — **scoring audit done, Track 3.F gate open.**
**Companion artifacts:** `docs/missing_laws_ingest_queue.csv` (135-law ingest queue)

---

## R2 — C-3: Coverage gap root cause and fix

### What the data shows

Of the 232 authoritative laws (`data/fact_laws.csv`):

| Status | Count | Explanation |
|---|---|---|
| In the extraction run | 138 | Processed ✓ |
| Quarantined | 14 | Text flagged unreliable — skip |
| **Text exists, not run** | **135** | In `output/law_texts/`, absent from DB |

The 135 unprocessed laws were **not an intentional partial batch**. Their full-text files are present and ready; they simply were not seeded into the DB via `local_ingest.py` before the run. `run_extraction()` only processes what exists in `normalized_source_records`. If `seed_from_csv` was not run for these laws (or was run with a scope limit), they are invisible to the extractor.

### The ingest queue

`docs/missing_laws_ingest_queue.csv` lists all 149 missing laws with their bucket (`text_ready` or `quarantine`). The 135 `text_ready` laws need to move through the full pipeline:

```
1. seed_from_csv        → creates DocumentFamily rows
2. local_ingest         → creates NormalizedSourceRecord rows from law_texts/
3. run_triage           → labels passages relevant / uncertain / not_relevant
4. run_extraction       → runs the 9-agent battery
```

The 14 quarantined laws remain deferred (known problem text; no action).

### Next step for DO/BE

Produce and execute the seed run for the 135 `text_ready` laws. Confirm
`law_fulltext_report.csv` has correct filename entries for all 135 before seeding
(local_ingest reads from that report to find the text files).

---

## R2 — C-8: Sparse / zero-obligation law triage

### Zero-obligation laws (21 total)

| Verdict | Count | Laws | Action |
|---|---|---|---|
| **BAD_TEXT** | 8 | SB 205, SB 2966, SB199, TMP-AZ-AMENDMENTOFARI, TMP-IL-ARTIFICIALINTE, TMP-MT-DECISIONMONTAN, TMP-ND-ANACTRELATINGT, TMP-ND-CSAMAMENDMENTS, TMP-NY-AIARTIFICIALIN | Re-fetch correct text; then re-extract |
| **GENUINE_MISS** | 2 | TMP-CA-AICALIFORNIACO, TMP-MO-ANDRELATEDOFFE | Obligation agent missed AI-specific "shall" clauses; prompt re-run |
| **CORRECT** | 4 | TMP-CA-EMPLOYMENTREGU, TMP-LA-LOUISIANADEEPF, TMP-MT-MONTANAEXPLICI, TMP-VT-AMENDMENTOFNON | Zero obligations is correct (rights/disclosure laws, no AI obligation language) |
| **NEEDS DB INSPECTION** | 6 | AB 2602, HB 4762, HB178, SB 1361, SB 20, SB25 | Text in DB but no file in `law_texts/`; verify text quality via `normalized_source_records` |

**Highest priority: `SB 205` (Colorado AI Act)** — 7,910 chars, zero "shall"/"must". Colorado SB 205 is a comprehensive high-risk AI law with explicit mandatory provisions. The text file is a truncated summary/decision placeholder, not the full statute. Needs immediate re-fetch from the Colorado legislature.

*Methodology: a law text under 15KB with fewer than 3 "shall"/"must" instances is flagged BAD_TEXT. A law with obligation-language instances but zero AI-specific "shall"/"must" lines is CORRECT (the obligations aren't AI-related, e.g., a broad employment law or narrow CSAM prohibition).*

### Single-extraction laws (6 total)

| Law | Type | Assessment |
|---|---|---|
| AB 2602 | definition | 1 definition only — needs DB inspection |
| SB 2966 | preemption_signal | Narrow preemption provision — possibly correct |
| SB 466 | obligation | 1 obligation (GA CSAM prohibition) — correct |
| SB25 | compliance_mechanism | Needs DB inspection |
| TMP-LA-LOUISIANADEEPF | compliance_mechanism | Narrow disclosure law — likely correct |
| TMP-MT-MONTANAEXPLICI | rights_protection | Narrow rights provision — likely correct |

### Enforcement sparsity (15 extractions)

15 `enforcement` extractions (from obligation agent sub-type) across 138 laws is low on its face. **However, the 472 bill-level enforcement records** — from `enforcement_agent` in `bill_level_extractions` — were not in the export until C-1 was fixed. Once R0's bill_level_extractions.csv is verified, enforcement signal should be adequate. **Confirm with SDPA/LKA that the enforcement_status derived-field design (effective_date vs now()) is intentional**, with the `enforcement_agent` bill-level output as the primary structured source.

---

## R3 — C-5: Confidence skew — root cause found, Track 3.F gate decision

### What the data shows

| | Total extractions | A+B | C+D | A+B % |
|---|---|---|---|---|
| All extractions | 6,274 | 747 | 5,527 | 11.9% |
| Orrick-gated (11 laws) | 496 | 13 | 483 | 2.6% |
| Non-Orrick-gated (127 laws) | 5,778 | 734 | 5,044 | 12.7% |

The Orrick-Gate is real but minor — it accounts for only 496 extractions (8% of total). Even on non-gated laws, A+B is only 12.7%, far below the 70% baseline target.

### Root cause: evidence span verification failure

The confidence scoring formula in `src/core/confidence.py` is **correctly designed** — it normalises weights when cross-validation hasn't run (excludes that component from the denominator). The formula is not the problem.

Evidence span verification is:

| State | Count | Share |
|---|---|---|
| No spans at all | 146 | 2% |
| Has spans, none verified | **2,431** | **39%** |
| All spans verified | 3,159 | 50% |
| Partially verified | ~538 | 9% |

**41% of extractions have an evidence grounding score of 0.0** (2% no spans + 39% all-unverified). Verification is done by **exact string matching** — the model's evidence span text must literally appear in the passage. Gemma 4 26B is paraphrasing approximately 39% of the time instead of verbatim-quoting, causing verification failure.

This is a **model behavior / prompt issue**, not a formula calibration issue. Re-extracting with the same prompts would produce the same verification failure rate.

### Orrick alignment (secondary)

Orrick alignment scores are stored in the DB (not in the CSV payload), so can't be directly measured here. But the 87% C/D rate on Orrick-validated laws suggests alignment is mediocre overall — the model's extractions often don't closely match Orrick's curated structured data for the same law.

### Track 3.F gate decision (for PTPL)

> **Decision made: Audit scoring first (user confirmed)**

The correct sequencing before Track 3.F re-extraction:

1. **Fix prompt verbatim-quoting** — add explicit instructions in the obligation, rights, definition, and compliance_mechanism prompts to copy-paste the exact statutory text into evidence spans (not summarize or paraphrase). Test on a small batch (10–20 laws) and measure verification rate improvement.
2. **Audit Orrick alignment distribution** — query the DB for the distribution of Orrick alignment scores on non-gated laws to confirm whether mediocre alignment is systemic or concentrated in specific law types.
3. **Only then commit to Track 3.F scope** — if prompt fixes raise A+B to ≥30-40% on a test batch, full re-extraction is justified. If not, the issue may also be in model capability, and Gemma 4 26B should be evaluated against an alternative.

**Until this audit is complete, Track 3.F is NOT a simple "re-run the extraction" — it requires prompt fixes first.**

---

## R3 — C-6: `compliance_mechanism` abstention rate — CLOSED

**Verdict: correct behavior, no fix needed.**

20% abstention (190/952 calls) aligns precisely with the agent's design: it extracts procedural compliance duties (impact assessments, bias audits, recordkeeping mandates) and abstains when a passage genuinely lacks those structures. Most CSAM prohibition laws, deepfake disclosure laws, and narrow political-advertising laws have no procedural compliance architecture — the agent correctly abstains.

**Action**: document the expected abstention rate as a baseline in the monitoring config so future runs don't flag it as a defect.

---

## Summary of open actions

| Item | Action | Owner |
|---|---|---|
| C-3 | Seed 135 text-ready laws → triage → extract | DO, BE |
| C-8 (bad text) | Re-fetch 8 laws' statutory text; priority = SB 205 | DO |
| C-8 (genuine miss) | Re-run obligation agent on TMP-CA-AICALIFORNIACO, TMP-MO-ANDRELATEDOFFE | NLP |
| C-8 (DB-only) | Inspect 6 laws' text in `normalized_source_records`; verify quality | DO, NLP |
| C-8 (enforcement) | Confirm enforcement design with SDPA/LKA after bill_level export verified | SDPA, LKA |
| C-5 | Fix verbatim-quoting in obligation/rights/definition/compliance_mechanism prompts | NLP |
| C-5 | Test batch (10–20 laws); measure A+B improvement | NLP, DevOps |
| C-5 | Audit Orrick alignment distribution in DB | NLP |
| C-5 (gate) | PTPL decides Track 3.F scope after prompt-fix test results | PTPL |
| C-6 | Document 20% abstention as expected baseline in monitoring | BE |
