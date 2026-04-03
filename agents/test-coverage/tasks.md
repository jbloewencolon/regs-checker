# test-coverage Agent — Tasks

## Active Tasks

(none — awaiting assignment)

## Next Tasks

- **Fix stale tests** — Update or delete `test_connector.py`, `test_discovery_agent.py`, `test_llm_provider.py`, `test_pdf_tracker.py` (stale imports).
- **Fix ingestion pipeline tests** — Rewrite `test_ingestion_pipeline.py` for `local_ingest.py` (removed `fetch_document`).
- **Fix `test_orrick_scraper::test_pdf_tracker_is_replacement`** — References removed `src.ingestion.pdf_tracker`.
- **Fix `test_status_checker::test_updates_version_and_logs_event`** — Mock target `db.add` no longer called as expected.
- **Write `adapt_payload_for_sync()` tests for missing types** — Extend `test_payload_adapter.py` for preemption_signal, rights_protection, compliance_mechanism.

## Blocked Tasks

- Integration tests requiring Docker Postgres — Cannot run without Docker started first.
- Tests requiring LM Studio — Cannot unit-test LLM calls without mock.
