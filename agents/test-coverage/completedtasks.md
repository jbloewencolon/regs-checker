# test-coverage Agent — Completed Tasks

## 2. Write new unit tests for 3 untested features (2026-04-03)

**What changed**: Created 3 new test files in `tests/unit/`:
- `test_discriminate_extraction_type.py` — 25 tests for `_discriminate_extraction_type()` across all 3 multi-type agents and 4 single-type agents.
- `test_summary_generator.py` — 32 tests for `generate_summary()` covering all 12 extraction types in `_TEMPLATE_GENERATORS`, plus edge cases (unknown type, metadata stripping, jurisdiction=None).
- `test_repair_truncated_json.py` — 16 tests for `_repair_truncated_json()` documenting Strategy 1 (top-level array truncation) vs Strategy 2 (fallback bracket closing) behaviors.

**Tests run**: All 73 pass. No production code modified.

**Notable finding**: `_repair_truncated_json()` has a known limitation — Strategy 1 fires on already-complete top-level arrays, dropping the last element. This is documented in the test file and is an existing production trade-off.

**Follow-up**: Fix 20 existing test failures (see `tasks.md` next tasks).

---

## 1. Run existing tests and classify results (2026-04-03)

**What changed**: Created `test-audit.md` and `test-gaps.md` in `agents/test-coverage/`.

**Results**:
- 320 tests passing across 18 files
- 20 tests failing (7 DB-required, 7 Orrick gate behavior change, 5 stale mocks, 1 mock mismatch)
- 4 test files stale (import removed modules: connector, discovery, llm_provider/AnthropicProvider, pdf_tracker)
- 1 collection error (DB-required)

**Key findings**:
- Orrick gate (auto Tier D without Orrick data) broke 7 tests in confidence + verification. Tests need mock Orrick data.
- `test_ingestion_pipeline.py` mocks `fetch_document` which was removed in local_ingest rewrite.
- 5 untested pure functions identified as high-priority test targets.

**Follow-up**: Write new tests for untested features (next tasks in queue).
