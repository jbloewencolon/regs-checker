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
- **Primary**: NVIDIA hosted API (`integrate.api.nvidia.com`) — `openai/gpt-oss-120b` for heavy agents, `meta/llama-3.1-8b-instruct` for triage/definition_actor/preemption. Requires `NVIDIA_API_KEY` in `.env`.
- **Fallback**: LM Studio (local, http://localhost:1234) with `google/gemma-4-26b-a4b` — switch via `"provider": "local"` in `config/agent_models.json`.
- No Anthropic API — the AnthropicProvider has been archived

## Code Changes
- After editing large files (especially `extractor.py`), verify imports are intact
- Do not add unrequested features — only implement what was asked
- The extraction pipeline has **6 clause-level agents** (obligation, definition_actor, threshold_exception, rights_protection, compliance_mechanism, preemption) + **3 bill-level agents** (applicability_agent, enforcement_agent, compliance_timeline_agent). The ambiguity agent is retired — findings are embedded as `interpretation_risks` on obligation/rights payloads.
- Archived code lives in `_archived/` and `src/ingestion/_archived/`

## Project Structure
- `data/` — CSV law metadata (fact_laws.csv, dim tables)
- `output/` — Pre-fetched source files (law_sources/, law_texts/)
- `src/ingestion/local_ingest.py` — Primary ingestion (replaces old URL fetcher)
- `src/ingestion/extractor.py` — Extraction pipeline (7 AI agents)
- `src/api/routes/dashboard.py` — Dashboard UI (HTMX)
- `templates/` — Jinja2 templates
