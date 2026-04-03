# Test Gaps — 2026-04-03

## Features with Zero Test Coverage

### Priority 1 — High-risk, untested

| Module | Function | Risk | Notes |
|---|---|---|---|
| `src/ingestion/extractor.py` | `_discriminate_extraction_type()` | HIGH | Determines extraction type routing. Wrong type = wrong DB column. Pure function, easily testable. |
| `src/ingestion/extractor.py` | `_select_agents_for_passage()` | HIGH | Controls which agents run per passage. Pure function. |
| `src/ingestion/extractor.py` | `_ensure_extraction_enums()` | MEDIUM | Auto-adds Postgres enum values. Requires DB mock. |
| `src/core/summary_generator.py` | `generate_summary()` | MEDIUM | 12 extraction type templates. Pure function, easily testable. |
| `src/agents/base.py` | `_repair_truncated_json()` | MEDIUM | JSON truncation repair. Pure function. Existing `test_json_repair.py` covers `_repair_json` but NOT `_repair_truncated_json`. |
| `src/agents/base.py` | `_strip_think_blocks()` | LOW | Strips `<think>` tags from LLM output. Simple regex. |
| `src/agents/base.py` | `_verify_evidence_spans()` | MEDIUM | Validates extracted evidence against source passage. Needs mock self. |

### Priority 2 — Missing coverage for recent features

| Feature | Location | Notes |
|---|---|---|
| Failed extraction retry | `src/ingestion/extractor.py` | `FailedExtractionAttempt` model + retry logic. Requires DB mock. |
| Retag endpoint | `src/api/routes/review_routes.py` | HTMX endpoint. Requires FastAPI TestClient + DB mock. |
| Preemption agent | `src/ingestion/extractor.py` | 7th agent added recently. No dedicated tests. |
| `adapt_payload_for_sync()` for preemption_signal | `src/core/payload_adapter.py` | Existing tests cover obligation/threshold/definition/ambiguity but NOT preemption_signal or rights_protection or compliance_mechanism. |

### Priority 3 — Stale tests needing replacement

| Stale File | Reason | Replacement Needed |
|---|---|---|
| `test_connector.py` | `src.ingestion.connector` removed | Delete or rewrite for current connector |
| `test_discovery_agent.py` | `src.agents.discovery` removed | Delete |
| `test_llm_provider.py` | `AnthropicProvider` archived | Update to test only `LMStudioProvider` |
| `test_pdf_tracker.py` | `src.ingestion.pdf_tracker` removed | Delete |
| `test_ingestion_pipeline.py` | Mocks `fetch_document` which no longer exists | Rewrite for `local_ingest.py` |

## Recommended Test Writing Order

1. **`_discriminate_extraction_type()`** — Pure function, high impact, easy to test
2. **`generate_summary()`** — Pure function, 12 types, medium risk
3. **`_repair_truncated_json()`** — Pure function, complements existing JSON repair tests
4. **`adapt_payload_for_sync()` for missing types** — Extends existing test file
5. **`_select_agents_for_passage()`** — Pure function, needs understanding of agent registry
