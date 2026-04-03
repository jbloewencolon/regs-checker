# test-coverage Agent — Tasks

## Active Tasks

(none — see next tasks below)

## Next Tasks

- **Fix stale tests** — Update or delete `test_connector.py`, `test_discovery_agent.py`, `test_llm_provider.py`, `test_pdf_tracker.py` (stale imports).
- **Fix confidence tests** — Update `test_confidence.py` and `test_verification_agents.py` tests that expect Tier A/B but now get Tier D due to Orrick gate. Add mock Orrick data to affected tests.
- **Fix ingestion pipeline tests** — Rewrite `test_ingestion_pipeline.py` for `local_ingest.py` (removed `fetch_document`).
- **Write `adapt_payload_for_sync()` tests for missing types** — Extend `test_payload_adapter.py` for preemption_signal, rights_protection, compliance_mechanism.

## Completed Tasks

- **Starter task: Run existing tests and classify results** — DONE. See `test-audit.md` and `test-gaps.md`. Result: 320 pass, 20 fail, 4 stale files.
- **Write tests for type discriminator** — DONE. `tests/unit/test_discriminate_extraction_type.py`, 25 tests all passing.
- **Write tests for summary generator** — DONE. `tests/unit/test_summary_generator.py`, 32 tests all passing (all 12 extraction types covered).
- **Write tests for `_repair_truncated_json()`** — DONE. `tests/unit/test_repair_truncated_json.py`, 16 tests all passing. Documented known behavior: Strategy 1 drops last element of top-level arrays.

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
