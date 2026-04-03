# Regs Checker — Open Bugs & Tasks

No open bugs. All items found during the codebase walkthrough have been fixed.

## Fixed Bugs (this session)

### BUG-1 [FIXED]: Preemption agent missing from AGENT_EXTRACTION_TYPES — KeyError on extraction
- **File:** `src/ingestion/extractor.py:178-189`
- **Was:** `AGENT_EXTRACTION_TYPES` dict had no `"preemption"` entry. Line 797 `AGENT_EXTRACTION_TYPES[name][0]` raised `KeyError("preemption")`, crashing extraction for every preemption result.
- **Fix:** Added `"preemption": [ExtractionType.preemption_signal]` to the dict.

### BUG-2 [FIXED]: Stale comment says "4 consolidated agents" — now 7
- **File:** `src/ingestion/extractor.py:174`
- **Fix:** Updated comment to `# Agent registry — 7 extraction agents`.

### BUG-3 [FIXED]: `generate_summaries_batch` filter logic broken for JSONB missing keys
- **File:** `src/core/summary_generator.py:382-383`
- **Was:** `not_(Extraction.metadata_["plain_summary"].isnot(None))` — double-negative that excluded rows with missing keys (the ones that actually need summaries).
- **Fix:** Replaced with `~Extraction.metadata_.has_key("plain_summary")`.

### BUG-4 [FIXED]: `callable` used as type hint instead of `Callable`
- **Files:** `src/ingestion/local_ingest.py`, `src/ingestion/extractor.py`
- **Fix:** Replaced `callable | None` with `Callable[[str], None] | None` from `collections.abc`.

## Previously Completed

See `completed_tasks.md` for all resolved items from the State AI Regulation Matrix implementation.
