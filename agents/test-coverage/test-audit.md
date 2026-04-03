# Test Audit — 2026-04-03

## Summary

| Category | Count |
|---|---|
| Total test files | 22 (20 unit + 2 integration) |
| Passed | 320 |
| Failed | 20 |
| Collection errors | 1 |
| Stale (won't collect) | 4 files |

**Environment**: Linux, Python 3.11, no Docker/DB available, deps installed via `pip install -e ".[dev]"`

---

## Stale Test Files (4)

These files fail at import because they reference removed/renamed modules.

| File | Import Error | Status |
|---|---|---|
| `tests/unit/test_connector.py` | `ModuleNotFoundError: No module named 'src.ingestion.connector'` | STALE — module removed |
| `tests/unit/test_discovery_agent.py` | `ModuleNotFoundError: No module named 'src.agents.discovery'` | STALE — module removed |
| `tests/unit/test_llm_provider.py` | `ImportError: cannot import name 'AnthropicProvider'` | STALE — AnthropicProvider archived |
| `tests/unit/test_pdf_tracker.py` | `ModuleNotFoundError: No module named 'src.ingestion.pdf_tracker'` | STALE — module removed |

---

## Failing Tests (20 failures + 1 error)

### Integration failures — DB connection required (6)

These fail because Docker Postgres isn't running (port 5434 connection refused). Expected in this environment.

| Test | Reason |
|---|---|
| `test_pipeline_e2e::test_seed_script_creates_records` | DB connection refused |
| `test_pipeline_e2e::test_extraction_creates_review_queue_item` | DB connection refused (ERROR) |
| `test_v1_api::test_list_obligations_returns_paginated` | DB connection refused |
| `test_v1_api::test_get_obligation_404` | DB connection refused |
| `test_v1_api::test_dependency_tree_endpoint` | DB connection refused |
| `test_v1_api::test_matrix_endpoint` | DB connection refused |
| `test_v1_api::test_changes_endpoint` | DB connection refused |

### Confidence scoring — Orrick gate changed behavior (4)

Tests expect Tier A/B but get Tier D because the Orrick gate now forces Tier D when no Orrick data is present. Tests predate this feature.

| Test | Expected | Got | Root Cause |
|---|---|---|---|
| `test_confidence::test_perfect_score` | Tier A | Tier D | Orrick gate: no orrick data = Tier D |
| `test_confidence::test_tier_b_threshold` | Tier A/B | Tier D | Same |
| `test_confidence::test_weight_redistribution_without_optional_components` | Tier A | Tier D | Same |
| `test_confidence::test_cross_validation_lowers_tier` | Tier B/C | Tier D | Same |

### Ingestion pipeline — stale mock targets (5)

Tests mock `src.ingestion.pipeline.fetch_document` which no longer exists. Pipeline was rewritten to use local files.

| Test | Error |
|---|---|
| `test_ingestion_pipeline::test_successful_ingestion` | `AttributeError: does not have the attribute 'fetch_document'` |
| `test_ingestion_pipeline::test_fetch_failure_marks_failed` | Same |
| `test_ingestion_pipeline::test_parse_failure_marks_failed` | Same |
| `test_ingestion_pipeline::test_progress_callback` | Same |
| `test_ingestion_pipeline::test_long_error_message_truncated` | Same |

### Orrick scraper — stale reference (1)

| Test | Error |
|---|---|
| `test_orrick_scraper::test_pdf_tracker_is_replacement` | `ModuleNotFoundError: No module named 'src.ingestion.pdf_tracker'` |

### Status checker — mock mismatch (1)

| Test | Error |
|---|---|
| `test_status_checker::test_updates_version_and_logs_event` | `Expected 'add' to have been called once. Called 0 times.` — mock target changed |

### Verification agents — Orrick gate interference (3)

Tests expect cross-validation scores to affect confidence, but Orrick gate clamps everything to 0.49/Tier D regardless.

| Test | Expected | Got |
|---|---|---|
| `test_verification_agents::test_high_cv_score_boosts_confidence` | score > 0.49 | 0.49 == 0.49 |
| `test_verification_agents::test_low_cv_score_reduces_confidence` | score < 0.49 | 0.49 == 0.49 |
| `test_verification_agents::test_perfect_cv_can_reach_tier_a` | Tier A | Tier D |

---

## Passing Tests (320)

All passing tests grouped by file:

| File | Tests | Status |
|---|---|---|
| `test_pipeline_e2e.py` | 2 (gold standard fixtures) | PASS |
| `test_v1_api.py` | 1 (health endpoint) | PASS |
| `test_bill_context.py` | 27 | ALL PASS |
| `test_circuit_breaker.py` | 25 | ALL PASS |
| `test_confidence.py` | 14 (of 18) | 14 PASS, 4 FAIL |
| `test_evaluation_harness.py` | ~20 | ALL PASS |
| `test_extraction_monitor.py` | ~25 | ALL PASS |
| `test_extraction_pipeline.py` | ~15 | ALL PASS |
| `test_iapp_scraper.py` | ~20 | ALL PASS |
| `test_ingestion_pipeline.py` | 0 (of 5) | ALL FAIL |
| `test_json_repair.py` | ~15 | ALL PASS |
| `test_jurisdiction_check.py` | ~20 | ALL PASS |
| `test_manual_extraction.py` | ~15 | ALL PASS |
| `test_orrick_scraper.py` | ~5 (of 6) | 5 PASS, 1 FAIL |
| `test_orrick_validation.py` | ~20 | ALL PASS |
| `test_payload_adapter.py` | ~10 | ALL PASS |
| `test_section_triage.py` | ~25 | ALL PASS |
| `test_status_checker.py` | ~20 (of 21) | 20 PASS, 1 FAIL |
| `test_sync_exclusions.py` | ~15 | ALL PASS |
| `test_verification_agents.py` | ~15 (of 18) | 15 PASS, 3 FAIL |

---

## Failure Categories Summary

| Category | Count | Fix Location |
|---|---|---|
| DB connection required | 7 | Expected — need Docker |
| Orrick gate changed behavior | 7 | Tests need updating (provide mock orrick data) |
| Stale mock targets | 5 | Tests need rewrite for new pipeline |
| Stale modules | 4 files | Tests reference removed code |
| Mock mismatch | 1 | Test needs updated mock |
