# EA1 Gold Set — Stratification Plan & Annotation Worklist

> **Status:** living plan (2026-07-13). Gates EA1-3 (baseline capture), EA3
> (confidence rebalance), TA-8 (threshold retuning), and SFH-3 (trust model).
> Owner: RPR (annotation) + NLP (harness). Set-size ruling: **8 laws**
> (SFH-2c, 2026-07-12 — the EA amendment #4 solo-capacity floor).
>
> This plan is the bridge between the *harness* (EA1-2, landed — scores all 9
> agents) and the *baseline* (EA1-3, needs a live LLM on the operator's
> machine). It records what the gold set must contain, what it already
> contains, and the precise annotation gaps — so the annotation pass is a
> worklist, not a blank page.

## 1. Required strata (from EA1-1 / SFH-2c)

The 8-law set must jointly satisfy:

| # | Stratum | Why | Status |
|---|---------|-----|--------|
| S1 | ≥2 OCR-quality PDFs | margin numbers / page breaks broke Tier-4 span grounding (QA-1) | **1 of 2** (AZ SB1359) |
| S2 | ≥1 amendment-markup (engrossed) bill | struck/underlined + mid-page engrossment headers (EA1-4) | ✅ AR HB1877 |
| S3 | ≥1 deepfake / likeness law | fastest-moving legal category | ✅ AZ SB1359, TX HB149 |
| S4 | ≥1 tracker-silent law | tier must not collapse for uncovered laws (SFH-3a) | **candidates found (QA-R2)** — the four `TMP-CA-*` laws in the 2026-07-13 run carry temp IDs (no tracker match); TMP-CA-EMPLOYMENTANDS is the strongest (see §3). Operator still confirms via `fact_laws` flags. |
| S5 | per-agent expected extractions for all **6 clause agents** | today: obligation/definition/threshold strong; rights/mechanism thin; preemption zero | **partial** |
| S6 | bill-level ground truth for the PN-matrix agents | enforcement now seeded (2 laws); applicability + timeline zero | **partial** |

Priority ordering within the set (EA amendment #4): the agents that feed the
Policy Navigator matrix come first — **obligation, threshold_exception,
enforcement_agent, applicability_agent**.

## 2. Current coverage (measured 2026-07-13)

35 clause fixtures across 12 statutes + 2 bill-level fixtures. Per-law
clause-agent coverage:

| Law | Jur | #fix | clause agents covered |
|-----|-----|------|-----------------------|
| ar_hb1877 (CSAM, engrossed) | AR | 1 | definition, threshold_exception |
| az_sb1359 (deepfake) | AZ | 1 | definition, obligation, threshold_exception |
| ca_sb1047 (frontier AI) | CA | 3 | compliance_mechanism, definition, obligation, threshold_exception |
| co_sb205 (Colorado AI Act) | CO | 11 | definition, obligation, threshold_exception |
| ct_sb1103 | CT | 2 | obligation, threshold_exception |
| il_hb3773 (employer AI) | IL | 3 | obligation, rights_protection, threshold_exception |
| nist_rmf (framework) | US | 2 | definition, obligation |
| ny_s7543 (AEDT) | NY | 3 | definition, obligation, rights_protection, threshold_exception |
| tx_hb149 (deepfake) | TX | 2 | definition, obligation |
| us_eo14110 | US | 3 | definition, obligation, threshold_exception |
| ut_sb149 | UT | 2 | definition, obligation, threshold_exception |
| va_hb2154 | VA | 2 | compliance_mechanism, obligation |

Per-agent coverage (laws with ≥1 fixture):

| Agent | Laws | Assessment |
|-------|------|------------|
| obligation | 11 | strong |
| definition | 9 | strong |
| threshold_exception | 9 | strong |
| rights_protection | 2 (IL, NY) | **thin** |
| compliance_mechanism | 2 (CA, VA) | **thin** |
| preemption | **0** | **missing (positive fixtures)** |
| enforcement_agent (bill) | 2 (AZ, AR) | seeded, needs breadth |
| applicability_agent (bill) | **0** | **missing** |
| compliance_timeline_agent (bill) | **0** | **missing** |

**Note on preemption negatives vs positives:** the reworked harness runs the
preemption agent against *all* 35 clause fixtures; since none carry a
`preemption` expectation, every one is scored as "should abstain." So
preemption **over-firing (false positives)** — the exact failure the
2026-07-12 run label id 9 caught (a §230 exemption misclassified as
`cross_state_conflict`) — is **already measured**. The gap is a **positive**
preemption fixture: a passage with a genuine federal-preemption or cross-law
conflict where the agent *should* fire. That needs RPR judgment (preemption is
the hardest legal call in the set) — do not synthesize it mechanically.

## 3. Proposed 8 laws

Chosen to satisfy every stratum while reusing the deepest existing coverage:

| Law | Strata it anchors | Existing | To add |
|-----|-------------------|----------|--------|
| **CO SB205** (Colorado AI Act) | PN-matrix flagship; comprehensive | 11 clause fixtures | enforcement + applicability + timeline bill fixtures; rights_protection clause |
| **AZ SB1359** (deepfake) | S1 (OCR PDF), S3 (deepfake) | clause + enforcement bill | preemption negative already covered; timeline bill (if effective date resolvable) |
| **AR HB1877** (CSAM) | S2 (engrossed) | clause + enforcement bill | — (criminal-penalty enforcement done) |
| **CA SB1047** (frontier AI) | compliance_mechanism | 3 clause fixtures | enforcement bill; shutdown-capability compliance_mechanism |
| **NY S7543** (AEDT bias audit) | rights_protection, employment | 3 clause fixtures | applicability bill (employer-size threshold) |
| **IL HB3773** (employer AI) | rights_protection, data retention | 3 clause fixtures | — |
| **TX HB149** (deepfake) | S1 2nd OCR-PDF candidate, S3 2nd deepfake | 2 clause fixtures | confirm source is OCR-PDF; add threshold/enforcement |
| **8th slot: tracker-silent law (S4)** | S4 | **none yet** | pick a `fact_laws` law flagged not-in-Orrick AND not-in-IAPP; author obligation + threshold |

The 8th slot is deliberately open: S4 (tracker-silent) can only be filled by
checking the `fact_laws` / tracker-alignment flags in the DB — which this
sandbox can't reach. Operator action below.

**QA-R2 update (2026-07-14):** the 2026-07-13 run surfaced four `TMP-CA-*`
temp-ID laws (no tracker match) — the natural S4 candidate pool.
**TMP-CA-EMPLOYMENTANDS** (employment ADS regulations) is the strongest 8th
slot: 92 extractions, 83.5% span grounding, real federal-conflict signals
(Title VII / ADA-GINA / ADEA savings analysis survived the QA-6 credibility
guard), and its source is committed at
`output/law_texts/TMP-CA-EMPLOYMENTANDS.txt` so clause fixtures can be
authored in-sandbox once the operator confirms it is genuinely
tracker-silent. Two more laws from that run add breadth beyond the 8-law
floor when wanted: **CA SB 1120** (healthcare-utilization AI — a regulated
domain the set lacks; enforcement/applicability facts source-verifiable) and
**CA SB 926 / AB 2355** as *stress* fixtures for the open QA-8
(parallel-version multiplication) and QA-9 (non-AI flooding) failure
classes — see `docs/qa_r2_run_review.md` §§5-7.

## 4. Annotation worklist (prioritized)

Ordered by PN-matrix impact, then by how mechanically verifiable each is.

**Tier 1 — mechanically verifiable now (no live LLM, source-grounded):**
- [x] **enforcement bill — AZ SB1359** (`bill_level/az_sb1359_enforcement.json`) — per-day civil penalty. *(done, EA1-2)*
- [x] **enforcement bill — AR HB1877** (`bill_level/ar_hb1877_enforcement.json`) — Class B felony (criminal). *(done)*
- [ ] **enforcement bill — CO SB205** — CO AI Act names the Attorney General as sole enforcer and disclaims a private right of action; both are explicit and verifiable from the committed source once the correct `TMP-CO-*` file is identified.
- [ ] **applicability bill — CO SB205 or NY S7543** — CO's small-business carve-out and NY's employer-size trigger are numeric thresholds stated in text (verifiable) → the first `applicability_agent` ground truth.

**Tier 2 — needs the operator's DB/tracker access:**
- [ ] **S4 tracker-silent law** — query `fact_laws` + tracker-alignment for a law with neither Orrick nor IAPP data; author obligation + threshold_exception fixtures for it.
- [ ] **confirm S1 2nd OCR-PDF** — verify `output/law_sources/TMP-TX-*.pdf` (or AZ GENERALDEEPFAK / AL CHILDPROTECTIO / CA AITRANSPARENCY, all `.pdf`) are genuinely OCR-quality (margin numbers / artifacts), then ensure a clause fixture for it copies text verbatim incl. artifacts (QA-1 regression value).

**Tier 3 — needs RPR legal judgment (do not synthesize):**
- [ ] **positive preemption fixture** — one passage with a genuine federal-preemption or cross-law conflict where the agent should fire (the hardest call; run-label id 9 shows the model over-fires on mere cross-references).
- [ ] **compliance_timeline bill** — a law stating an unambiguous effective/compliance date. NB: AZ SB1359 and AR HB1877 state only *approval* dates, not effective dates, so neither is a clean timeline fixture; CO SB205's Feb-2026 effective date is the natural candidate.
- [ ] **rights_protection breadth** — expand beyond IL/NY to a 3rd law so the agent isn't tuned on two examples.

## 5. How the harness consumes this

- Clause fixtures: `tests/fixtures/gold_standard/*.json`, keyed by extraction
  TYPE in `expected_extractions` (a key may hold one dict or a list of dicts).
- Bill fixtures: `tests/fixtures/gold_standard/bill_level/*.json`, keyed by
  agent_name in `expected_bill_extractions`; `bill_text` inline **or**
  `bill_text_file` pointing at a committed source. An agent with no ground
  truth on any fixture is simply not scored — **the baseline grows
  monotonically as fixtures are added**, so partial coverage is safe to ship.
- Baseline capture (EA1-3): `EvaluationHarness().run_all()` →
  `harness.write_baseline(result, "evaluation/baselines/<date>.json")`.
  Requires `NVIDIA_API_KEY` (or local LM Studio) — operator machine only.

## 6. Honesty boundary

Everything in §4 Tier 1 is asserted only where the fact is stated verbatim in
the committed bill text and hand-checked. Enforcement fixtures deliberately
omit fields that would require inferring an unstated value (e.g.
`max_civil_penalty_usd` when the amount lives in a cross-referenced section, or
`enforcing_body` when none is named) — a null-valued expected field is not
scored, so omission is the honest default and the annotation notes say why.
Tier 3 items are flagged for RPR precisely because they need a legal call this
plan will not fabricate.
