# Regs Checker — Agent Handoff (2026-04-05)

## Current Project Phase

**Data-quality remediation complete; ready for Phase 6 full pipeline re-run.**
All source-text contamination has been identified and either fixed or quarantined. The pipeline code is stable. Next action is user-executed: full reset → re-seed → ingest → triage → extract → sync to Supabase.

## What Just Happened (this session)

1. **Data Quality Crisis diagnosed as partly false alarm** — Reviewer flagged 53% "null payloads"; root cause was schema misunderstanding (checked `description`/`jurisdiction`/`section_reference`, but ambiguity/threshold/definition payloads use different fields). Real issues isolated to URL-mismatched source files and MN omnibus contamination.

2. **Signal-based agent routing** (`src/ingestion/extractor.py`) — `_select_agents_for_passage()` now uses triage signals + 7 regex patterns to route each passage to a subset of the 7 agents. Always-on: obligation, definition_actor. Expected 30-50% fewer agent calls.

3. **Title disambiguation + regulatory_category** (`src/ingestion/local_ingest.py`) — DocumentFamily canonical_title now includes state + bill number. New `_derive_regulatory_category()` tags each law (synthetic_content, data_privacy, automated_decision, etc.) into family metadata.

4. **Supabase sync `--clear` flag** (`src/scripts/sync_to_supabase.py`) — PostgREST DELETE with `id=gte.0` filter to wipe tables before fresh sync. Also accepts both `REGS_SUPABASE_URL/KEY` and `REGS_SUPABASE_PROJECT_URL/ANON_KEY` env var names.

5. **URL-mismatch fixes** — 20 `.txt` files in `output/law_texts/` contained content for the wrong law (CSV row-offset bug in old `law_fulltext_report.csv`). 4 files swapped back to correct IDs using content already present in quarantine; 16 still need correct source text. See `output/law_texts_quarantine/NEEDED_SOURCES.md`.

6. **MN MCDPA trimmed** — `TMP-MN-DECISIONMINNES.txt` was the full 9,535-line HF4757 omnibus (cannabis articles 1-4 + MCDPA article 5). Trimmed to Article 5+ only (1,533 lines). Full omnibus preserved in quarantine.

## Current Objective

User to run Phase 6:
```powershell
.\venv\Scripts\Activate
python scripts/reset_pipeline.py
# Then via dashboard Step 1: Full Reset + Re-seed + Ingest
# Then Step 2 triage, Step 3 extract, Step 5 sync (--clear)
```

## Known Open Items

- **16 laws still need correct source text** — see `output/law_texts_quarantine/NEEDED_SOURCES.md`. Laws will simply be skipped on re-ingest until source files are placed in `output/law_texts/<canonical_law_id>.txt`.
- **TN quarantine files contain TX bill content** — TX SB 1188, TX SB 2373 etc. may be legitimate TX AI laws not yet in DB. User to decide whether to add as new TX entries.
- **Feature branch merge** — All work on `claude/setup-project-scaffolding-9ApZR`. Needs review + merge to `main` after Phase 6 validation.

## Forbidden / Risky Files

- `src/ingestion/extractor.py` (2600+ lines, pipeline core)
- `src/db/models.py` (ORM — needs Alembic migration)
- `src/api/routes/dashboard.py` (3000+ lines)
- `alembic/versions/*.py` (never edit applied migrations)

## Escalation Conditions

Escalate to user before:
- Schema changes
- Editing extractor.py, models.py, or dashboard.py
- Changes that affect data integrity
- Any destructive git operation
