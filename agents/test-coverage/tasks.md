# test-coverage Agent — Tasks

## Active Tasks

- **Starter task: Run existing tests and classify results** — Run `pytest tests/ -v` and classify each test as: passing, failing (with reason), or stale (tests code that no longer exists). Write results to `test-audit.md`.

## Next Tasks

- **Map untested features** — Cross-reference `architecture.md` and `completed_tasks.md` against `tests/` to find features with zero test coverage. Priority untested features:
  - `_repair_truncated_json()` in `src/agents/base.py`
  - `_discriminate_extraction_type()` enforcement-subject detection in `src/ingestion/extractor.py`
  - `_ensure_extraction_enums()` in `src/ingestion/extractor.py`
  - `generate_summary()` for all 12 extraction types in `src/core/summary_generator.py`
  - `FailedExtractionAttempt` model and retry logic
  - Retag endpoint in `src/api/routes/review_routes.py`
  - `compute_orrick_similarity()` in `src/core/orrick_validation.py`
  - `adapt_payload_for_sync()` for preemption_signal type in `src/core/payload_adapter.py`

- **Write tests for JSON repair** — Unit tests for `_repair_truncated_json()` covering: complete JSON (no-op), truncated array with one complete element, truncated nested object, empty input.

- **Write tests for type discriminator** — Unit tests for `_discriminate_extraction_type()` covering: obligation with court subject -> enforcement, obligation with developer subject -> obligation, definition with actors -> actor_mapping, threshold with exceptions -> exception, preemption -> preemption_signal.

- **Write tests for summary generator** — Unit tests for `generate_summary()` covering each of the 12 extraction types in `_TEMPLATE_GENERATORS`.

## Blocked Tasks

- Integration tests requiring Docker Postgres — Cannot run without `.\start.ps1` first.
- Tests requiring LM Studio — Cannot unit-test LLM calls without mock.
