# QA-8 / QA-9 Phased Plan — Parallel-Version Multiplication & Non-AI Flooding

> **Status:** plan (2026-07-14), from the QA-R2 review
> (`docs/qa_r2_run_review.md` §§5-6). Owner: NLP (Phases 1-2 are
> sandbox-actionable), operator (Phase 0, re-extraction), product/RPR
> (Phase 2 scoping rules ratification).
>
> **The two issues are one root cause seen twice.** California re-enacts an
> entire code section whenever a bill amends any part of it (Cal. Const.
> art. IV § 9), and when several pending bills touch the same section, the
> bill carries one full restatement per enactment contingency. SB 926
> therefore contains Penal Code § 647 — a ~14K-char section about
> loitering, prostitution, and public intoxication with ONE AI-relevant
> subdivision — **eight times** (2³ contingencies of AB 1874 / AB 1962 /
> SB 1414, switched by its SEC. 1.6). QA-8 is the *horizontal* blowup
> (same content × 8); QA-9 is the *vertical* blowup (agents extract the
> whole restated section, not just what the bill changes). Fixing QA-8
> first is mandatory: it removes ~7/8 of the junk **and** of the token
> cost before any relevance logic runs.

## 0. Measured facts driving the design (2026-07-13 run + committed sources)

1. **Detection is deterministic.** A single regex over amending headers
   ("Section 647 of the Penal Code(, as amended by Section N of Chapter C
   of the Statutes of Y)? is amended to read") finds every affected law in
   the corpus, with zero false positives on the other 208 sources:
   - `TMP-CA-AMENDMENTOFCAL` (SB 926): Penal Code § 647 × **8**
   - `TMP-CA-AMENDMENTTOTHE` (AB 2355): Gov. Code § 84504.2 × **2**
   - `US-CA-SB11` (SB 11): Civil Code § 3344 × **2** (explains its
     'digital replica' definition dupes)
2. **Every parallel version contains the bill's own changes.** Versions
   differ only in whether the *other* pending bills' text is merged in. So
   extracting from any single version preserves this law's content —
   picking one is lossless for our purposes.
3. **Naive per-extraction keyword filtering is disqualified.** Simulated
   against the run (payload + evidence text vs the triage AI-keyword
   vocabulary): it would hide **98.4%** of TMP-CA-EMPLOYMENTANDS (a genuine
   ADS-regulation law whose individual obligations — "retain records for
   four years" — don't name AI) and **97.7%** of AB 2355 (whose
   font/placement rules are the operative body of its AI-disclosure
   regime). Keyword presence in the extraction is the wrong unit; the right
   question is whether the *provision* participates in the bill's AI
   scheme. QA-9 below is scoped accordingly.

## Phase 0 — Operator repair + clean baseline data (now; no code)

Already listed in tasks.md; restated here because Phases 1-2 measure
against its output:

- Confirm `claude/legal-extraction-architecture-1exlem` (with QA-1, QA-6,
  QA-7) is merged/pulled before the next run.
- `python -m src.scripts.reground_spans --dry-run` → apply →
  `python -m src.scripts.recompute_confidence` (repairs the stored rows the
  run verified with pre-QA-1 code).
- Re-extract (or exclude from quality reads) the 53 stale 2026-07-12 rows
  (AZ SB 1359, AR HB1877, TMP-AZ).

**Acceptance:** a post-QA-6/7 extraction dump exists; grounding on AL
HB172 / AB 2355 recovers to the replay-predicted levels; preemption rows
for SB 926 drop from 36 to ≤ a handful.

## Phase 1 — QA-8: parallel-version collapse (sandbox-actionable, deterministic)

> **Status: LANDED 2026-07-14.** Detection + skip logic implemented and
> tested against the real corpus (see steps 1-4 below, all confirmed).
> Step 5 (retroactive re-extraction of SB 926 / AB 2355 / SB 11) remains
> operator work — needs a live pipeline run, not available in-sandbox.

**Where:** `src/ingestion/parser.py` (detection at parse time) +
`src/ingestion/extractor.py` (skip at extraction time). No LLM calls.

1. **Detect:** during `parse_and_normalize`, run the amending-header regex
   per passage; group passages by amendment target `(code_name,
   section_number)`. Groups with >1 member are parallel-version sets.
2. **Choose the representative:** the **last version in bill order** — CA
   drafting convention puts the most-merged contingency last (SB 926's
   final § 647 restatement is the "all bills enacted" case), and by fact
   0.2 any choice preserves this bill's own changes. Record the choice in
   `metadata_`: `parallel_version_group: "<penal code:647>"`,
   `parallel_version_representative: true|false`, plus the version count.
3. **Skip non-representatives at extraction:** the extract loop already
   skips passages for other reasons (triage-not-relevant, dedupe); add
   `parallel_version_representative == false` to the skip conditions, with
   a logged counter in the run summary (`parallel_versions_skipped`).
   Non-destructive: the passages stay stored for provenance; the
   conditional-operativity section (SEC. 1.6) is NOT part of any group and
   still extracts normally.
4. **Tests** (fixtures from the three committed sources, real headers):
   grouping, representative selection, "one version amends its own
   distinct target" negative case (AR HB1877's §§ 5-27-302/-601 must NOT
   group), and an extractor-level test that non-representatives are
   skipped.
5. **Retroactive repair:** only 3 laws are affected — the pragmatic path is
   re-extraction of those laws after this lands (Phase 0 machinery), not a
   migration. QA-4/QA-7 definition dedupe already suppresses re-run
   duplicates; obligations/thresholds from the wiped-and-rerun laws are
   replaced wholesale.

**Acceptance:** on re-ingest + re-extract, SB 926 yields ONE § 647
extraction pass (expected row count ~25 instead of 181), AB 2355 one
§ 84504.2 (not two near-identical sets), SB 11 one § 3344; extraction
token spend for SB 926 drops ~8× on the § 647 content; no loss of any
extraction type present in the representative version.

## Phase 2 — QA-9a: restatement-scoped relevance (sandbox-actionable after ratification)

**Principle (from fact 0.3):** relevance filtering applies **only inside
restated sections** — never law-wide. A bill that is wholly an AI act
(AB 2355, TMP-CA-EMPLOYMENTANDS, CO SB205) is untouched by this phase.

1. **Scope trigger:** a passage is a "restatement" when Phase 1 grouped it
   (parallel versions) OR its amending header matches and the restated
   section exceeds a size threshold (~6K chars) — catching single-version
   restatements of big sections too.
2. **Subdivision in-scope test** (deterministic, on the restatement's
   subdivision tree `(a)(b)(c)…`): a subdivision is in-scope when it
   (a) contains an AI/domain keyword (triage vocabulary + the domain terms:
   deepfake, synthetic, digitization, computer-generated, digital replica,
   intimate image, materially deceptive), or (b) references a section this
   bill *adds* elsewhere (parse "Section X is added to …" targets — this
   is what keeps AB 2355-style formatting rules in scope: they cite the new
   § 84514), or (c) is adjacent context to (a)/(b) (parent/child
   subdivision).
3. **Apply at sync first (QA-6 pattern):** extractions whose evidence spans
   fall wholly in out-of-scope subdivisions of a restatement get
   `ai_nexus: false` → `display: false`. Non-destructive, reversible,
   retroactively repairs stored rows without re-extraction.
4. **Ratification gate:** the in-scope rules ((a)-(c)) and the keyword
   additions go to RPR/product for sign-off before landing — this is a
   relevance judgment, unlike the mechanical QA-6/7 guards. Ship with a
   measured hide-report per law (rerun the Phase-0 dump through the filter)
   and require ~0% hides on the known full-AI laws as the regression bar.
5. **QA-10 micro-guard (ride-along, mechanical):** drop "definitions" whose
   term is a bare code-section citation ("Section 647 of the Penal Code")
   or whose text is conditional-enactment boilerplate ("proposed by this
   bill, Assembly Bill 1962…") — SB 926 ids 234/235. Same
   `_postprocess_extraction` pattern as QA-2/QA-6.

**Acceptance:** SB 926's surviving § 647 extractions show only
(j)(4)-connected rows displayed (~5-8); AB 2355 / TMP-CA-EMPLOYMENTANDS /
CO SB205 hide-rate 0%; hide-report reviewed and ratified.

## Phase 3 — QA-9b: pre-extraction scoping (token savings; gated on EA1-3 baseline)

Apply the Phase-2 in-scope test **before** extraction instead of after:
for restatement passages, feed clause agents only the in-scope subdivisions
(with a one-line context header naming the section). This changes agent
*inputs*, so unlike Phases 1-2 it must be measured by the evaluation
harness — capture the EA1-3 baseline first, and add the SB 926 § 647
stress fixture (below) before flipping it on. Side benefit: smaller
passages reduce the AB 2355-style neighbor-context quoting that misattributes
extractions and deflates grounding.

**Acceptance:** baseline diff shows no F1 regression on gold fixtures;
SB 926 extraction call volume drops further; misattribution/grounding on
AB 2355-style laws improves.

## Phase 4 — EA1 stress fixtures + long-term source fix

- **Stress fixtures** for the gold set (sandbox-authorable from committed
  sources): (1) SB 926 § 647 — expected: obligations only from (j)(4)-scope,
  agents abstain on loitering/prostitution subdivisions; (2) AB 2355
  § 84504.2 — expected: ONE set of formatting obligations, in-scope despite
  no AI keywords (regression against over-filtering); (3) SB 11 § 3344 —
  one 'digital replica' definition.
- **Source-quality track (needs product decision):** leginfo HTML carries
  amendment markup (strikethrough/italics) that flat-text fetching
  discards. Re-fetching CA sources with markup preserved would let the
  pipeline scope directly to *inserted text* — the principled fix for
  restatement flooding, replacing the Phase-2 heuristics for CA. Costs a
  fetcher change + re-ingestion; parser already detects markup
  (`amendment_markup_detected`). Decide after Phase 2's hide-report shows
  how far the heuristics get.

## Sequencing & gates

| Phase | Depends on | Gate | Sandbox-actionable? |
|---|---|---|---|
| 0 operator repair | QA-6/7 merged | — | no (operator) |
| 1 QA-8 collapse | — | tests only (deterministic, no input change to agents on kept passages) | **yes — landed 2026-07-14** |
| 2 QA-9a sync scoping + QA-10 | Phase 1 (grouping metadata) | RPR/product ratification of in-scope rules + hide-report | yes (code); ratification external |
| 3 QA-9b pre-extraction scoping | Phases 1-2 + **EA1-3 baseline** | harness diff, no F1 regression | code yes; measurement operator |
| 4 fixtures + source fix | Phase 2 learnings | product decision on re-fetch | fixtures yes |

## The SB 926 strategy, end to end

Today: 181 extraction rows, of which 178 come from the eight § 647 copies;
36 preemption junk signals; 49/51 obligations with no AI nexus.

| Step | Mechanism | SB 926 effect |
|---|---|---|
| QA-6 (landed) | preemption credibility guard | 36 preemption signals → ~3 (1A/agency flags only) |
| QA-7 (landed) | preamble dedupe | 'loiter'/'prostitution' definition dupes collapse |
| Phase 1 (QA-8) | keep 1 of 8 § 647 versions | ~181 rows → ~25; § 647 token spend ÷ 8 |
| Phase 2 (QA-9a) | subdivision scoping within the restatement | ~20 loitering/prostitution/custody rows hidden; (j)(4) deepfake-porn rows kept |
| Phase 3 (QA-9b) | same test pre-extraction | stop paying for the boilerplate at all |
| Phase 4 | stress fixture | regression-locks all of the above |

End state: SB 926 contributes ~5-8 displayed extractions, all tied to the
intimate-image/digitization offense — which is what the law is *for* in an
AI-regulation matrix.
