# test-coverage Agent — Tasks

## Active Tasks

(none — awaiting assignment)

## Next Tasks

### Run-1 Unified Plan v2 — assigned items (see `docs/run1_unified_plan.md`)

- **WS-B4 — Normalization idempotency unit tests** — One `tests/unit/test_normalize_*.py` per normalization stage once the unified loader exists (WS-B4 in `rollup_matrix.py`): known value → expected canonical code, articles stripped, unmapped value → `vocab_review_queue`, **idempotent re-run produces zero changes** (load-bearing test). Blocked until WS-B4 lands the loader.
- **WS-C0 / fixtures — Gold-standard fixtures** — Extend `tests/fixtures/gold_standard/` from the 149-row Tier-A + evidence-span pool. **Prioritize** human-corrected Tier-C/D + abstention fixtures (decision boundary) over easy Tier-A wins — start with `compliance_mechanism` abstentions and `subject_normalized` hedges. NOTE: SB 205 fixtures must wait on the WS-A4 text re-fetch (current corpus text is truncated/bad).
- **WS-C0 — Eval-harness baseline** — Wire a pre/post run that records verified-span rate + A/B/C/D distribution so the C0 verbatim-prompt change (and the C1 cross-validation re-wire) are measurable; >10% A→B drop triggers prompt review.
- **WS-C1 regression guard** — When cross-validation is wired into confidence (C1), add a test asserting a populated `cross_validation_score` actually moves the confidence result, and that a swallowed/failed cross-validation does NOT silently pass as a neutral score.

### Standing test debt

- **Fix stale tests** — Update or delete `test_connector.py`, `test_discovery_agent.py`, `test_llm_provider.py`, `test_pdf_tracker.py` (stale imports).
- **Fix ingestion pipeline tests** — Rewrite `test_ingestion_pipeline.py` for `local_ingest.py` (removed `fetch_document`).
- **Fix `test_orrick_scraper::test_pdf_tracker_is_replacement`** — References removed `src.ingestion.pdf_tracker`.
- **Fix `test_status_checker::test_updates_version_and_logs_event`** — Mock target `db.add` no longer called as expected.
- **Write `adapt_payload_for_sync()` tests for missing types** — Extend `test_payload_adapter.py` for preemption_signal, rights_protection, compliance_mechanism.

## Blocked Tasks

- Integration tests requiring Docker Postgres — Cannot run without Docker started first.
- Tests requiring LM Studio — Cannot unit-test LLM calls without mock.
