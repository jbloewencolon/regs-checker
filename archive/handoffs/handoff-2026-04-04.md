# Regs Checker — Agent Handoff

## Why This Handoff Exists

The legacy agent completed a large feature sprint (State AI Regulation Matrix, 7-agent pipeline, confidence scoring, sync pipeline, dashboard UI) and extensive debugging. The codebase is functional but has accumulated complexity. A new agent is being brought in to help with testing, documentation, and targeted improvements without needing the full legacy context.

## Current Project Phase

**Post-debugging, pre-production extraction run.** The pipeline code is stable and tested against a single law (CA Cartwright Act). The user's next step is running the full 243-law extraction corpus, then syncing to Supabase.

## Current Objective

Prepare the pipeline for a reliable full-corpus extraction run, then sync results through to Policy Navigator. Secondary: improve test coverage and documentation.

## What Is Already Known

- The 7-agent extraction pipeline works end-to-end on local LLM (GPT-OSS 20B via LM Studio)
- All critical runtime bugs have been fixed (enum errors, FK cascades, JSON truncation, timezone mismatches)
- The dashboard UI has all pipeline steps wired up (Steps 1-6)
- The confidence scoring model with Orrick gate is implemented
- The failed extraction retry mechanism is implemented
- The sync pipeline has both legs (local->Regs Checker, Regs Checker->Policy Navigator)

## What Is Still Unclear

- Whether the Orrick gate should apply universally or only to Orrick-sourced laws
- Whether MinIO/S3 storage is actually needed (pipeline works without it)
- How many extractions the full 7-agent run will produce (previous 4-agent run: ~28k)
- Whether existing unit tests pass against current code (likely some failures from schema changes)
- Whether Supabase projects are currently active or paused

## Safe-to-Read Files (any agent can inspect)

- All files in `src/`, `templates/`, `prompts/`, `data/`, `tests/`
- `README.md`, `CLAUDE.md`, `tasks.md`, `completed_tasks.md`, `architecture.md`
- `pyproject.toml`, `docker/docker-compose.yml`, `alembic/`

## Safe-to-Edit Files (with caution)

- `tests/unit/*.py` — Adding or updating tests is always safe
- `templates/*.html` — UI-only changes, low risk
- `prompts/*.yml` — Prompt templates, affects extraction quality but not stability
- `src/core/summary_generator.py` — Presentation only, doesn't affect data
- `src/core/config.py` — Settings, well-isolated
- `tasks.md`, `completed_tasks.md` — Documentation

## Forbidden / Risky Files

- **`src/ingestion/extractor.py`** — 2600+ lines, houses all pipeline logic. Any edit risks breaking extraction, triage, retry, or verification. Must be tested after changes.
- **`src/db/models.py`** — ORM definitions. Changes require Alembic migrations and careful FK analysis.
- **`src/api/routes/dashboard.py`** — 3000+ lines, tightly coupled to templates. Edit with care.
- **`alembic/versions/*.py`** — Migration files. Never edit already-applied migrations.
- **`.env`** — Contains database credentials. Never commit.

## Tests and Checks

```powershell
# Activate venv first
.\venv\Scripts\Activate

# Run all tests
pytest tests/

# Run specific test file
pytest tests/unit/test_confidence.py -v

# Start the app and verify dashboard loads
python start.py
# Then visit http://localhost:8000/dashboard

# Lint
ruff check src/
```

## Escalation Conditions

Escalate to the user (not another agent) when:
- Any change to `extractor.py`, `models.py`, or `dashboard.py` is needed
- Database schema changes are required
- Supabase migration is needed
- A test fails that you can't diagnose in 10 minutes
- You discover a bug that affects data integrity
- You're unsure whether a file is safe to edit
