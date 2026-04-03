# test-coverage Agent — Context

## What You Need to Know

This is a legal AI extraction pipeline. You don't need to understand the legal domain — you need to understand the code structure enough to write tests.

## Key Files for Testing

### Already-tested modules (check if tests are current)
- `src/core/confidence.py` — tested in `tests/unit/test_confidence.py`
- `src/core/circuit_breaker.py` — tested in `tests/unit/test_circuit_breaker.py`
- `src/core/bill_context.py` — tested in `tests/unit/test_bill_context.py`
- `src/core/extraction_monitor.py` — tested in `tests/unit/test_extraction_monitor.py`

### Untested modules (priority for new tests)
- `src/agents/base.py` — `_repair_truncated_json()`, `_repair_json()`, `_verify_evidence_spans()`, `_strip_think_blocks()`
- `src/core/summary_generator.py` — `generate_summary()` with all 12 extraction types
- `src/core/orrick_validation.py` — `compute_orrick_similarity()`, `_tokenize()`, `_jaccard()`
- `src/core/payload_adapter.py` — `adapt_payload_for_sync()` for all extraction types
- `src/ingestion/extractor.py` — `_discriminate_extraction_type()`, `_select_agents_for_passage()`

### Testing patterns in this project
- Tests use `pytest` with `asyncio_mode = "auto"`
- No test database fixtures exist — unit tests should mock DB calls
- `from unittest.mock import MagicMock, patch` is the standard mocking approach
- Test files follow `test_<module_name>.py` naming

## How to Run Tests

```bash
# All tests
pytest tests/ -v

# Single file
pytest tests/unit/test_confidence.py -v

# With coverage
pytest tests/ --cov=src --cov-report=term-missing
```

## What NOT to Touch
- Don't modify any `src/` files
- Don't run Docker, Supabase, or LM Studio
- Don't create integration tests that require external services
- Pure unit tests only — mock all I/O
