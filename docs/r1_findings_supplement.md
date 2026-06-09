# R1/Phase-0 Supplement: C-2 Root Cause + Phase-1 Operator Notes

**Phases:** R1 (C-2) root-cause closure, Phase 0.2/0.4 confirmations, Phase 1 operator guidance
**Status:** C-2 root cause identified (code analysis). Phase 1 tasks unblocked pending local environment.

---

## R1 — C-2: Token/call telemetry mismatch — root cause found

### What the code shows

`run_summary.json` reports 9.94M tokens / 2,043 calls.
`agent_stats.json` reports 38.55M tokens / 6,726 calls.

The ExtractionMonitor **does** reset at `start_run()` (line 1819 in `extractor.py`):
- `self._events.clear()`
- `self._agent_stats = {}`
- `self._total_tokens = 0`

The residual gap is **retry accounting**. `token_usage.add()` (line 1114) is called once
per *successful* extraction result. The monitor's `record_agent_result()` is called for
**every call attempt** — including adaptive-retry retries (each doubling the token budget
on `stop_reason=length`). On passages that exhaust the token budget 2–3 times before
succeeding, the monitor counts 3× the tokens and calls that `token_usage` does.

With ~20% of passages triggering at least one retry, and each retry roughly doubling
token consumption, the ~4× ratio is consistent: `token_usage` ≈ first-attempt tokens
only; `agent_stats` ≈ all-attempt tokens.

### Resolution

Both files are **intentionally different scopes**:
- `run_summary.json` → **final extraction cost** (tokens consumed by successful results only)
- `agent_stats.json` → **total LLM cost** (all call attempts, including retries)

Both figures are useful; neither is wrong. **Document the scope in both files** rather
than merging them. Label should already exist (`"scope": "passage-level + bill-level
agents"` added to `token_usage` in the R1 fix); the `agent_stats.json` writer should
emit a matching scope label `"scope": "all call attempts including adaptive retries"`.

### Action

Add scope label to the `agent_stats.json` writer in `ExtractionMonitor.to_dict()` or
`snapshot()`. One-liner; tracked in tasks.md Phase 0.2.

---

## Phase 0.4 — Model-of-record: RESOLVED

`CLAUDE.md` has been updated to `google/gemma-4-26b-a4b` (was stale `openai/gpt-oss-20b`).
Agent count corrected to 6 clause-level + 3 bill-level. `_prompt_hash` derivations are now
tied to the correct model.

---

## Phase 1 — Coverage backfill: operator notes

### Law text file status for BAD_TEXT laws (C-8)

7 of 8 BAD_TEXT law files are present in `output/law_texts/` but contain truncated/summary
content. One law has no file at all:

| Law | File in law_texts/ | Action needed |
|---|---|---|
| US-CO-SB205 | ✅ present (bad text — 7,910 chars, 0 "shall"/"must") | Re-fetch from colorado.gov legislature |
| US-NV-SB199 | ✅ present (suspected bad text) | Verify; re-fetch if confirmed |
| TMP-AZ-AMENDMENTOFARI | ✅ present (suspected bad text) | Verify; re-fetch if confirmed |
| TMP-IL-ARTIFICIALINTE | ✅ present (suspected bad text) | Verify; re-fetch if confirmed |
| TMP-MT-DECISIONMONTAN | ✅ present (suspected bad text) | Verify; re-fetch if confirmed |
| TMP-ND-ANACTRELATINGT | ✅ present (suspected bad text) | Verify; re-fetch if confirmed |
| TMP-ND-CSAMAMENDMENTS | ✅ present (suspected bad text) | Verify; re-fetch if confirmed |
| TMP-NY-AIARTIFICIALIN | ✅ present (suspected bad text) | Verify; re-fetch if confirmed |
| SB_2966 (NV) | ❌ MISSING entirely | Locate and fetch from source |

**Priority order:** US-CO-SB205 first (Colorado AI Act, confirmed comprehensive law with 0
obligation language in corpus text). Then SB_2966 (missing entirely). Then verify the
remaining 6 before committing to re-fetch.

### 135-law seed pipeline: checklist for DO/BE

Before running the seed:

1. **Confirm `law_fulltext_report.csv` entries.** `local_ingest.py` reads this report to
   find text files. Verify it has correct filename entries for all 135 `text_ready` laws
   in `docs/missing_laws_ingest_queue.csv`.

2. **Run sequence** (PowerShell, venv active):
   ```powershell
   # Step 1 — seed DocumentFamily rows
   python -m src.scripts.seed_pipeline --mode seed --input data/fact_laws.csv
   # Step 2 — ingest text files into NormalizedSourceRecord rows
   python -m src.ingestion.local_ingest --limit 135
   # Step 3 — triage (labels passages relevant/uncertain/not_relevant)
   python start.py  # or Dashboard Step 2 (Triage)
   # Step 4 — extract
   # Dashboard Step 3 (Extract All) or:
   python -m src.ingestion.extractor
   ```

3. **After extraction**, re-run the RunArchiver export to pick up the new laws:
   the `output/extraction_runs/active/extractions.csv` rebuilds all-DB on every finalize,
   so re-running extraction naturally updates it.

4. **C-8 GENUINE_MISS re-runs** (TMP-CA-AICALIFORNIACO, TMP-MO-ANDRELATEDOFFE): these
   are already in the DB — run the obligation agent only on those two laws' passages.
   Use Dashboard "Extract Selected" or the `--law-ids` flag if available in extractor.
