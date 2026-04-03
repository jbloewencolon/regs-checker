# test-coverage Agent — Handoff to Manager

## What to Expect Back

After this agent completes, you should have:
1. `test-audit.md` — Full inventory of existing tests with pass/fail/stale status
2. `test-gaps.md` — List of untested features prioritized by risk
3. 3+ new test files in `tests/unit/`
4. Updated `completedtasks.md` with what was done

## How to Verify

```bash
# Run all tests (old + new)
pytest tests/ -v

# Check coverage
pytest tests/ --cov=src --cov-report=term-missing
```

## What Might Need Follow-Up

- If existing tests fail, the agent will document WHY but not fix production code. You may need to update `src/` files.
- If the agent identifies untestable code (tightly coupled to DB/LLM), that's an architecture issue to address separately.
- New tests should be reviewed for correctness before relying on them as regression guards.
