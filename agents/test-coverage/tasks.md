# test-coverage Agent — Tasks

## Active Tasks

(none — awaiting assignment)

## Next Tasks

### Run-1 Unified Plan — assigned items (see `docs/run1_unified_plan.md`)

- **Phase 6.5 — Normalization idempotency unit tests** — One `tests/unit/test_normalize_*.py` per normalization stage once `src/scripts/normalization/` exists (Phase 7.1): known value → expected code, articles stripped, unmapped value → `vocab_review_queue`, **idempotent re-run produces zero changes** (load-bearing test). Blocked until Phase 7.1 lands the loader.
- **Phase 6.1/6.2 — Gold-standard fixtures** — Extend `tests/fixtures/gold_standard/` from the 149-row Tier-A + evidence-span pool. **Prioritize** human-corrected Tier-C/D + abstention fixtures (decision boundary) over easy Tier-A wins — start with `compliance_mechanism` abstentions and `subject_normalized` hedges. NOTE: SB 205 fixtures must wait on the Phase 1.2 text re-fetch (current corpus text is truncated/bad).
- **Phase 6.4 — Eval-harness baseline** — Wire a pre/post run that records verified-span rate + A/B/C/D distribution so the Phase 2 (E-1) verbatim-quoting prompt change is measurable; >10% A→B drop triggers prompt review.

### Standing test debt

- **Fix stale tests** — Update or delete `test_connector.py`, `test_discovery_agent.py`, `test_llm_provider.py`, `test_pdf_tracker.py` (stale imports).
- **Fix ingestion pipeline tests** — Rewrite `test_ingestion_pipeline.py` for `local_ingest.py` (removed `fetch_document`).
- **Fix `test_orrick_scraper::test_pdf_tracker_is_replacement`** — References removed `src.ingestion.pdf_tracker`.
- **Fix `test_status_checker::test_updates_version_and_logs_event`** — Mock target `db.add` no longer called as expected.
- **Write `adapt_payload_for_sync()` tests for missing types** — Extend `test_payload_adapter.py` for preemption_signal, rights_protection, compliance_mechanism.

## Blocked Tasks

- Integration tests requiring Docker Postgres — Cannot run without Docker started first.
- Tests requiring LM Studio — Cannot unit-test LLM calls without mock.
