# Regs Checker — Claude Code Context

## Environment
- **OS**: Windows with PowerShell
- **Never** use Unix/Linux shell syntax (`source`, `export`, `&&` chaining)
- Always use PowerShell equivalents (`$env:VAR = 'value'`, semicolons, etc.)
- Python virtual environment: `.\venv\Scripts\Activate`

## Startup
- Run `python start.py` to launch everything (Docker, migrations, browser, uvicorn)
- Dashboard: http://localhost:8000/dashboard

## Database
- **Primary**: Local Docker Postgres on port 5434 (`docker/docker-compose.yml`)
- **Remote**: Supabase project `wjxlimjpaijdogyrqtxc` (us-east-1) — may be paused
- When Supabase is unreachable, pivot to local Docker Postgres immediately
- Always verify DB connection before writing migration/sync code

## LLM
- All extraction uses **local models** via LM Studio (http://localhost:1234)
- Default model: `openai/gpt-oss-20b` on AMD Radeon AI PRO R9700
- No Anthropic API — the AnthropicProvider has been archived

## Code Changes
- After editing large files (especially `extractor.py`), verify imports are intact
- Do not add unrequested features — only implement what was asked
- The extraction pipeline has 7 agents: obligation, definition_actor, threshold_exception, ambiguity, rights_protection, compliance_mechanism, preemption
- Archived code lives in `_archived/` and `src/ingestion/_archived/`

## Project Structure
- `data/` — CSV law metadata (fact_laws.csv, dim tables)
- `output/` — Pre-fetched source files (law_sources/, law_texts/)
- `src/ingestion/local_ingest.py` — Primary ingestion (replaces old URL fetcher)
- `src/ingestion/extractor.py` — Extraction pipeline (7 AI agents)
- `src/api/routes/dashboard.py` — Dashboard UI (HTMX)
- `templates/` — Jinja2 templates
