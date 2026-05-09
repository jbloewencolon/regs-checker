# Regs Checker — Quick Start (2-Minute Setup)

## For Existing Team Members

**Already have Python 3.11+ and Docker?** You're ready in 2 minutes:

```powershell
# Windows
git clone <repo-url> regs-checker
cd regs-checker
.\setup.ps1
# Now run:
python start.py
```

```bash
# macOS / Linux
git clone <repo-url> regs-checker
cd regs-checker
bash setup.sh
# Now run:
python start.py
```

That's it. Dashboard opens automatically at **http://localhost:8000/dashboard**.

---

## For New Team Members

1. **Install prerequisites** (5 min, one-time):
   - Python 3.11+: [python.org](https://www.python.org/downloads/)
   - Git: [git-scm.com](https://git-scm.com)
   - Docker Desktop: [docker.com](https://www.docker.com/products/docker-desktop/)

2. **Clone + Setup** (2 min):
   ```powershell
   git clone <repo-url> regs-checker
   cd regs-checker
   .\setup.ps1          # Windows
   # or on macOS/Linux:
   bash setup.sh
   ```

3. **Start the app** (immediate):
   ```powershell
   python start.py
   ```

4. **Open dashboard**:
   Browser automatically opens to **http://localhost:8000/dashboard**

5. **Optional: Download LM Studio** for extraction (10 min):
   - [lmstudio.ai](https://lmstudio.ai)
   - Load a model (mistral-7b, phi, llama2)
   - Click "Start Server" (listens on http://localhost:1234)

---

## Daily Workflow

Each day, from the project directory:

```powershell
# Activate venv
.\venv\Scripts\Activate.ps1

# Start the app
python start.py

# Open http://localhost:8000/dashboard in your browser
```

---

## Common Tasks

### Run extraction on a batch of laws

1. Dashboard → Step 1 (Seed laws + ingest)
2. Dashboard → Step 2 (Triage for AI relevance)
3. Dashboard → Step 3 (Run extraction agents)
4. Dashboard → Review queue (approve/reject)
5. Dashboard → Step 5 (Sync to Supabase)

### Switch to Supabase instead of Docker

Edit `.env`:
```
REGS_DATABASE_URL=postgresql://postgres.project:password@aws-region.supabase.com:6543/postgres
```

Restart: `python start.py`

### Use GPU for LLM inference

1. Install LM Studio: [lmstudio.ai](https://lmstudio.ai)
2. Load a model in LM Studio
3. Click "Start Server" — done, app auto-connects to http://localhost:1234

### Switch LLM model

Edit `.env`:
```
REGS_LOCAL_LLM_MODEL=openai/mistral-7b
REGS_LOCAL_EXTRACTION_MODEL=openai/mistral-7b
```

Restart: `python start.py`

---

## If Something Goes Wrong

| Problem | Quick Fix |
|---------|-----------|
| "Python not found" | Reinstall Python 3.11+, add to PATH |
| "Docker not running" | Start Docker Desktop |
| "Connection refused (DB)" | Run `docker compose -f docker/docker-compose.yml up -d` |
| "Connection refused (LLM)" | Start LM Studio, load a model, click "Start Server" |
| "Migration failed" | See Troubleshooting in `SETUP.md` |
| "venv issues" | Run `rmdir venv -Recurse; python -m venv venv` (Windows) |

---

## Need Help?

1. **Setup issues**: See `SETUP.md` (Troubleshooting section)
2. **Architecture**: See `architecture.md`
3. **API docs**: http://localhost:8000/docs (while app running)
4. **Code**: `CLAUDE.md` has dev context

---

## Key Files

| File | Purpose |
|------|---------|
| `start.py` | Startup script (handles everything) |
| `SETUP.md` | Detailed setup + troubleshooting guide |
| `SETUP_ISSUES_AND_OPTIMIZATIONS.md` | Known issues + future roadmap |
| `README.md` | Architecture overview |
| `src/api/routes/dashboard.py` | Dashboard UI code |
| `alembic/versions/` | Database migrations |
| `config/agent_models.json` | LLM model assignments |

---

## One-Liner Reference

```powershell
# Full setup (Windows)
git clone <url> regs-checker; cd regs-checker; .\setup.ps1; python start.py

# Full setup (macOS/Linux)
git clone <url> regs-checker; cd regs-checker; bash setup.sh; python start.py
```
