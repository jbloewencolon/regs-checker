# Regs Checker — Open Bugs & Tasks

## CRITICAL Bugs (will crash at runtime)

### BUG-1: Preemption agent missing from AGENT_EXTRACTION_TYPES — KeyError on extraction
- **File:** `src/ingestion/extractor.py:178-189`
- **Impact:** The `AGENT_EXTRACTION_TYPES` dict maps agent names to extraction types, but has no `"preemption"` entry. The preemption agent IS registered in `_get_agents()` (line 418) as `"preemption"`, so it runs successfully. But when processing results at line 797, `AGENT_EXTRACTION_TYPES[name][0]` raises `KeyError("preemption")`, crashing the extraction for that passage.
- **Fix:** Add `"preemption": [ExtractionType.preemption_signal]` to the `AGENT_EXTRACTION_TYPES` dict.

## HIGH Bugs (incorrect behavior, no crash)

### BUG-2: Stale comment says "4 consolidated agents" — now 7
- **File:** `src/ingestion/extractor.py:174`
- **Impact:** Comment reads `# Agent registry — 4 consolidated agents per Recommendation #1` but there are 7 agents. Misleading for new developers.
- **Fix:** Update comment to `# Agent registry — 7 extraction agents`.

### BUG-3: `generate_summaries_batch` filter logic is fragile for JSONB missing keys
- **File:** `src/core/summary_generator.py:382-383`
- **Impact:** The filter `not_(Extraction.metadata_["plain_summary"].isnot(None))` is a double-negative. For JSONB columns, when the key doesn't exist the path returns SQL NULL, so `isnot(None)` evaluates to NULL (falsy), and `not_(NULL)` evaluates to NULL (falsy) — meaning rows with missing keys are **excluded**, the opposite of the intent. The batch summary generator may skip extractions that don't have a summary yet.
- **Fix:** Replace with `Extraction.metadata_["plain_summary"].is_(None)` or use `~Extraction.metadata_.has_key("plain_summary")`.

## LOW Bugs (cosmetic / type hints)

### BUG-4: `callable` used as type hint instead of `typing.Callable`
- **Files:** `src/ingestion/local_ingest.py:122`, `src/ingestion/extractor.py:968`, `src/ingestion/extractor.py:1112`
- **Impact:** `callable` is a builtin function, not a type. Works at runtime (Python doesn't enforce annotations) but fails static type checking (mypy, pyright).
- **Fix:** Use `Callable[[str], None] | None` from `typing` or `collections.abc`.

## Previously Fixed (reference)

See `completed_tasks.md` for all resolved items from the State AI Regulation Matrix implementation.
