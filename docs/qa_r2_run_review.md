# QA-R2 — Review of the 2026-07-13 Extraction Run (623a032d CSV)

> **Scope:** 790 extraction rows across 16 laws, exported 2026-07-13/14.
> **Purpose:** verify the QA-1…QA-5 fixes against real output, find new
> failure classes, and turn them into fixes/tasks. Companion to the QA round
> 1 review of the 2026-07-12 run (37 rows, AZ/AR).
>
> **Headline:** the QA-2/QA-3/QA-4 guards verify clean. The run itself
> appears to have executed **without the QA-1 grounding fix** (see §2). Two
> new failure classes dominate the noise: **preemption over-firing**
> (81 signals, ~60% deterministic junk — fixed this session as QA-6) and
> **parallel-version bill multiplication** (CA bills amending one code
> section 2–4× in contingent versions, every version fully re-extracted —
> open as QA-8). A third, **non-AI boilerplate flooding** (96% of SB 926's
> obligations are Penal Code §647 loitering/prostitution provisions with no
> AI nexus), is open as QA-9 and is the biggest PN-matrix pollution risk.

## 1. What's in the dump

- **53 stale rows (2026-07-12):** all of AZ SB 1359 (10), AR HB1877 (28),
  TMP-AZ-AMENDMENTOFARI (15). These predate every QA fix (committed
  2026-07-13 02:15–02:34 UTC) — their duplicate definitions and 8–14%
  grounding are *expected* and not regressions. They should be re-extracted
  or excluded from any quality read.
- **737 new rows (2026-07-13 17:20–22:20):** 13 CA laws + AL HB172.
  Types: obligation 274, definition 110, exception 96, threshold 100,
  preemption_signal 81, compliance_mechanism 45, rights_protection 31.
  Models: gpt-oss-120b (clause agents), llama-3.1-8b (definition,
  preemption).
- **Tiers (all 790):** A 32 / B 25 / C 394 / D 339 — bottom-heavy, partly
  because span verification failed where QA-1 wasn't active (below).

## 2. QA-1 (Tier-4 span grounding): fix is correct, run didn't have it

New-run grounding is 664/930 spans (71.4%), with AB 2355 at 42.7% and AL
HB172 at 54.9%. Sampled failures are line-break-spanning quotes from
margin-numbered sources — the exact class QA-1 fixed. Replaying them
through the *current* code verifies them (AL "distribution occurs within 90
days before an election" → Tier 4 pass against the same `Paragraph 2`
segmentation the run used). Conclusion: **the run executed pre-QA-1 code or
pre-QA-1 stored passages.**

**Operator actions:**
1. Confirm the `claude/legal-extraction-architecture-1exlem` branch (or a
   merge of it) was pulled before the next run.
2. Repair stored rows without re-extraction:
   `python -m src.scripts.reground_spans --dry-run` (inspect), then without
   `--dry-run`, then `python -m src.scripts.recompute_confidence`.

Two genuine (correct) rejection classes to know about when reading
grounding numbers:
- **Context quotes:** the model cites the bill's effective-date clause while
  extracting from a different passage (AL id 674). The quote is real bill
  text but not in the passage — rejecting it is right; it still deflates
  verified_span_count on otherwise-fine extractions.
- **AB 2355 cross-passage quotes:** extractions attributed to the 1,065-char
  `Section 84504` passage quote text from `Section 84504.3` — neighbor-
  context leakage, related to QA-9.

## 3. QA-2 / QA-3 / QA-4 verification — PASS

- **QA-2 (definition_actor guards):** 110 definitions; 46 carry actors, all
  sampled actors grounded in their definition text (HB172 CREATOR/SPONSOR,
  SB 926 peace officer); framework_refs nearly eliminated (2). No invented
  NIST cross-contamination observed.
- **QA-3 (responsible_party_normalized):** 45 compliance_mechanism rows:
  30 honest nulls (routed to vocab review), 15 normalized — every
  non-lexical mapping verified as a ratified alias-table hit
  (`committee→government_agency`, `employer→deployer`, `data broker→
  data_broker`, `disability insurer…→insurer`). No force-fits.
- **QA-4 (definition dedupe):** genuinely distinct same-term definitions
  survive correctly (SB 926 'loiter' ×2 legal meanings, TMP-CA-EMPLOYMENTANDS
  'automated-decision system' across §§). One systematic miss found: copies
  differing only by a quoting preamble ("As used in this subdivision,
  'loiter' means …") score 0.85–0.88 similarity, under the 0.9 threshold —
  observed on SB 926 'loiter'/'prostitution' and SB 1120 'artificial
  intelligence'. **Fixed this session as QA-7** (preamble stripped before
  comparison; validated against all six observed pairs).

## 4. NEW — QA-6: preemption over-firing (fixed this session)

81 preemption signals in the new run, three deterministic junk patterns:

| Pattern | Count | Example |
|---|---|---|
| Law's own state codes as "cross_state_conflict" | ~41 | "This passage references the Penal Code, which may conflict with federal laws or other states' laws" (CA SB 926 — 36 signals from one law) |
| Self-negating descriptions | 8 | "…references the Welfare and Institutions Code and **does not appear to conflict** with federal law" emitted as a signal |
| Prompt-example authorities parroted | 14+ | `related_authority: "Dec 2025 Federal EO on AI"` / `"US Constitution Art. I § 8"` — both copied verbatim from the agent prompt's examples, **including two tier-A rows** (AB 1836 id 758, SB 981 id 721) |

This is the scaled-up version of the run-label-9 finding from QA round 1
(§230 exemption misclassified as cross_state_conflict).

**Fix (landed):**
- `assess_preemption_credibility()` in `src/core/legal_context.py`:
  conflict-asserting signals (cross_state_conflict, federal_preemption,
  interstate_commerce, dormant_commerce_clause) must anchor to a verbatim
  preemption/savings clause, a concrete federal citation, or a named other
  state — after discounting the prompt's own example authorities.
  Self-negating descriptions are dropped outright (unless the negation is a
  quoted savings clause, which is the statute's wording, not the model's).
- **Extraction time:** `PreemptionAgent._postprocess_extraction` drops
  non-credible signals (base agent now treats `None` from the hook as a
  drop); a `preemption_language` absent from the passage is nulled first so
  a fabricated clause can't rubber-stamp credibility.
- **Sync time:** `classify_legal_context` sets `display: False` on stored
  non-credible rows — retroactive repair without re-extraction (QA-3
  pattern).
- **Prompt de-poisoning:** the example authorities are removed from the
  agent prompt and schema description; new rules say a same-state code
  citation is a cross-law reference, not a conflict, and "no conflict" means
  abstain.

**Replay against the run's 81 signals: 32 kept / 49 dropped** (41
no_external_authority, 8 self-negating). Every kept signal traces to a
grounded savings clause (AL HB172 §230, AB 2839 §230(f)(2), AB 325
antitrust, SB 1120 42 U.S.C. §2719), a named federal statute (Title VII,
ADA/GINA, ADEA, HIPAA, 18 U.S.C. §§2251-2252), or a non-conflict-asserting
type (first_amendment / agency_jurisdiction, which claim no second
jurisdiction and pass through to existing handling).

## 5. NEW — QA-8 (open): parallel-version bills multiply extractions

California bills routinely amend the same code section **2–4 times in
parallel contingent versions** (operative-date/conditional-enactment
quirks):
- **SB 926** contains Penal Code §647 in full **four times** → segmented
  into 8 near-identical ~14K-char passages, all named `Section 647` → 178 of
  its 181 extractions come from them.
- **AB 2355** amends §84504.2 twice (as amended by Ch. 777/2018 and by
  Ch. 887/2022) → twin passages, twin extractions (39 rows on §84504.2).

Only definitions have cross-passage dedupe (QA-4/QA-7); obligations,
thresholds, and exceptions from parallel versions are stored as distinct
rows. Options (needs design, not a quick guard): detect parallel-version
headers at ingest and keep the operative version; or extend law-level
near-dup dedupe to obligation/threshold/exception payloads. Until then,
per-law extraction counts and any count-based confidence inputs are
inflated for CA laws.

## 6. NEW — QA-9 (open): non-AI boilerplate flooding

SB 926's §647 passages contain one AI-relevant subsection (the
intimate-image/digitization offense) inside a giant section about
loitering, prostitution, and public intoxication. Passage-level triage
correctly lets the passage through (it does contain AI content), but the
clause agents then extract **everything**: 49 of SB 926's 51 obligations
have no AI nexus ("peace officer shall place the person in civil protective
custody", "person shall not accost others for the purpose of begging").
These would surface in the Policy Navigator matrix as AI-law obligations.

Options (needs product/design input): sub-passage triage; agent-prompt
scoping ("extract only obligations connected to the AI/synthetic-media
provisions"); or a law-level post-filter with an AI-nexus check. Related:
junk definitions from conditional-enactment boilerplate (SB 926 ids
234/235: term "Section 647 of the Penal Code" defined as "proposed by this
bill, Assembly Bill 1962, and Assembly Bill 1874").

## 7. Gold-set impact (feeds docs/ea1_gold_set_plan.md)

- **S4 (tracker-silent) candidates found:** the four `TMP-CA-*` laws in this
  run carry temp IDs (no tracker match). **TMP-CA-EMPLOYMENTANDS**
  (employment ADS regs, 92 extractions, 83.5% grounding, genuine Title
  VII/ADA/ADEA federal-conflict signals) is the strongest 8th-slot
  candidate.
- **Domain breadth:** SB 1120 (healthcare utilization AI) adds a regulated
  domain the set lacks; its enforcement/applicability facts are
  source-verifiable.
- **Stress fixtures:** SB 926 (parallel versions + non-AI flooding) and
  AB 2355 (twin sections + cross-passage quotes) are the natural regression
  fixtures for QA-8/QA-9 once those land.
