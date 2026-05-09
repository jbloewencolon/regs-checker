# Regs Checker — Setup Guide

Complete step-by-step instructions for setting up the regs-checker pipeline on Windows, macOS, or Linux.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Initial Setup](#initial-setup)
3. [Configuration](#configuration)
4. [Verification](#verification)
5. [Running the Pipeline](#running-the-pipeline)
6. [Troubleshooting](#troubleshooting)
7. [Multi-PC Deployment](#multi-pc-deployment)

---

## Prerequisites

### Required Software

- **Python 3.11+** — Download from [python.org](https://www.python.org/downloads/)
  - Verify: `python --version`
  - Tip: On Windows, enable "Add Python to PATH" during install
  
- **Git** — Download from [git-scm.com](https://git-scm.com)
  - Verify: `git --version`

- **Docker Desktop** — Download from [docker.com](https://www.docker.com/products/docker-desktop/)
  - Required for: PostgreSQL database + MinIO object storage
  - Verify: `docker --version` and `docker compose version`
  - Must be running before starting the app (will auto-start on first run)

- **LM Studio** (optional but recommended) — Download from [lmstudio.ai](https://lmstudio.ai)
  - Hosts local LLM models for extraction
  - Default: http://localhost:1234
  - If not available, extraction will fail with connection errors

### System Requirements

- **CPU**: 4+ cores recommended (for LM Studio model inference)
- **RAM**: 16+ GB recommended (8GB minimum for Docker + models)
- **Disk**: 50+ GB free (for models, Docker volumes, extractions)
- **GPU** (optional): NVIDIA/AMD/Intel GPU drastically speeds up extraction

### For Windows Users

- PowerShell 7+ recommended (Windows 11 default)
- If using older PowerShell, `start.ps1` may have path issues — fall back to `python start.py`

---

## Initial Setup

### Step 1: Clone the Repository

```powershell
# Windows PowerShell
git clone <repo-url> regs-checker
cd regs-checker

# or on macOS/Linux bash
git clone <repo-url> regs-checker
cd regs-checker
```

### Step 2: Create Python Virtual Environment

```powershell
# Windows PowerShell
python -m venv venv
.\venv\Scripts\Activate.ps1

# macOS/Linux bash
python3 -m venv venv
source venv/bin/activate
```

**Note:** If you see a PowerShell execution policy error:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Step 3: Install Dependencies

```powershell
# Upgrade pip, setuptools, wheel
pip install --upgrade pip setuptools wheel

# Install project dependencies
pip install -e ".[pdf,ocr,dev]"

# Verify installation
python -c "import sqlalchemy, fastapi, pydantic; print('✓ Core packages installed')"
```

**Explanation:**
- `[pdf,ocr,dev]` installs optional dependencies:
  - `pdf`: pdfplumber for PDF text extraction
  - `ocr`: pytesseract + pdf2image for scanned PDFs
  - `dev`: pytest, pytest-asyncio, ruff, mypy for development/testing

### Step 4: Copy Environment Configuration

```powershell
# Create .env from template
cp .env.example .env

# Edit .env for your setup (optional — start.py handles defaults)
# nano .env  (or use your text editor)
```

**Default `.env` points to local Docker Postgres on port 5434.** This is correct for first-time setup.

---

## Configuration

### Database Configuration

The app supports three database options:

#### Option A: Local Docker (Recommended for Development)
Already configured in `.env.example`. No changes needed.

```
REGS_DATABASE_URL=postgresql://regs:regs@127.0.0.1:5434/regs_checker
```

Docker Compose will auto-create the container. Run:
```powershell
docker compose -f docker/docker-compose.yml up -d
```

#### Option B: Remote Supabase
Edit `.env`:

```
REGS_DATABASE_URL=postgresql://postgres.your-project:<password>@aws-0-us-east-1.pooler.supabase.com:6543/postgres
```

Verify connection:
```powershell
python -c "
import psycopg2
url = 'postgresql://user:pass@host/db'
try:
    psycopg2.connect(url, connect_timeout=5).close()
    print('✓ Connection OK')
except Exception as e:
    print(f'✗ Failed: {e}')
"
```

#### Option C: Local Postgres (Manual)
If not using Docker:

```powershell
# Install Postgres 15+ locally
# Create database: createdb -U postgres regs_checker
# Set password and edit .env with your connection string
REGS_DATABASE_URL=postgresql://postgres:password@localhost:5432/regs_checker
```

### LLM Configuration

Edit `.env` to configure local LLM:

```
# LM Studio (http://localhost:1234) — most common
REGS_LOCAL_LLM_URL=http://localhost:1234
REGS_LOCAL_LLM_MODEL=openai/gpt-oss-20b
REGS_LOCAL_EXTRACTION_MODEL=openai/gpt-oss-20b

# Or Ollama
REGS_LOCAL_LLM_URL=http://localhost:11434

# Or vLLM
REGS_LOCAL_LLM_URL=http://localhost:8000
```

**Default:** gpt-oss-20b on LM Studio. If unavailable, start LM Studio and load the model before running extraction.

### Optional: S3 / MinIO Configuration

For artifact storage (optional — app works without it):

```
REGS_S3_ENDPOINT_URL=http://localhost:9000
REGS_S3_ACCESS_KEY=minioadmin
REGS_S3_SECRET_KEY=minioadmin
```

MinIO is included in `docker-compose.yml` and auto-starts with `docker compose up -d`.

---

## Verification

Run startup script to test everything:

```powershell
python start.py
```

**Expected output:**

```
============================================================
  Regs Checker — Pipeline Dashboard
============================================================

[1/4] Loading configuration...
  Database: postgresql://regs:****@127.0.0.1:5434/regs_checker

[2/4] Testing database connection...
  ✓ Connection OK

[3/4] Checking database schema...
  ✓ Migrations applied successfully!

[4/4] Starting server...
  Dashboard:  http://localhost:8000/dashboard
  API docs:   http://localhost:8000/docs

  Press Ctrl+C to stop.
============================================================
```

Then open **http://localhost:8000/dashboard** in your browser.

### If startup fails:

1. **"Docker not found"** → Install Docker Desktop
2. **"Connection refused (Postgres)"** → Run `docker compose -f docker/docker-compose.yml up -d`
3. **"LLM connection error"** → Start LM Studio on http://localhost:1234
4. **"Migration failed"** → Check `.alembic/versions/` files for syntax errors; see [Troubleshooting](#troubleshooting)

---

## Running the Pipeline

### Via Dashboard UI (Recommended)

1. Open **http://localhost:8000/dashboard**
2. Click buttons in order:
   - **Step 1**: Seed laws from CSV + ingest local source files
   - **Step 2**: Triage passages for AI relevance
   - **Step 3**: Run extraction agents
   - **Step 4.5**: Generate plain-English summaries
   - **Step 5**: Sync to Supabase (if configured)

### Via CLI

```powershell
# Activate venv first
.\venv\Scripts\Activate.ps1

# Run individual steps
python -m src.scripts.seed_pipeline --mode seed-local
python -m src.scripts.seed_pipeline --mode triage
python -m src.scripts.seed_pipeline --mode extract
python -m src.scripts.seed_pipeline --mode generate-summaries
python -m src.scripts.sync_to_supabase
```

---

## Troubleshooting

### Database Issues

#### Migration Error: "type 'X' already exists"

**Cause:** Enum type collision in Alembic migration.

**Fix:**
```powershell
# Drop and recreate database
docker compose -f docker/docker-compose.yml exec postgres dropdb -U regs regs_checker
docker compose -f docker/docker-compose.yml exec postgres createdb -U regs regs_checker

# Re-run migrations
python -m alembic upgrade head
```

#### "relation 'X' does not exist"

**Cause:** Incomplete migration due to prior failure.

**Fix:**
```powershell
# Check migration status
python -m alembic current

# Reset to a known-good migration
python -m alembic downgrade -1

# Re-upgrade
python -m alembic upgrade head
```

### Docker Issues

#### Docker containers not starting

```powershell
# Check status
docker compose -f docker/docker-compose.yml ps

# View logs
docker compose -f docker/docker-compose.yml logs postgres

# Restart containers
docker compose -f docker/docker-compose.yml restart
```

#### MinIO bucket errors

```powershell
# Re-initialize buckets
docker compose -f docker/docker-compose.yml restart minio-init
```

### LLM / Extraction Issues

#### "Connection refused (http://localhost:1234)"

**Fix:**
1. Install LM Studio from [lmstudio.ai](https://lmstudio.ai)
2. Open LM Studio → Load a model (e.g., `mistral-7b`, `phi`, `llama2`)
3. Click "Start Server" — it will listen on http://localhost:1234
4. Verify: curl http://localhost:1234/v1/models

#### Extraction takes too long

- GPU too slow? → Use a smaller model (mistral-7b instead of 13b+)
- Out of VRAM? → `--offload-layers` in LM Studio or use CPU mode
- 2+ hour runs normal? → Increase max_tokens or reduce batch size in config

### Permission / Path Issues (Windows)

#### "cannot be loaded because running scripts is disabled"

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\venv\Scripts\Activate.ps1
```

#### "No such file or directory" in .ps1

Use `python start.py` instead of `.\start.ps1` — it's cross-platform.

### Virtual Environment Issues

#### "venv not found" / "pip command not found"

```powershell
# Recreate venv from scratch
rmdir venv -Recurse
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e ".[pdf,ocr,dev]"
```

---

## Multi-PC Deployment

### Recommended Setup for Teams

#### Architecture

```
Developer PC (or shared Linux server)
  ├─ Source data: data/fact_laws.csv, output/law_texts/
  ├─ Docker Postgres (local): http://localhost:5434
  ├─ LM Studio models: http://localhost:1234
  └─ Dashboard: http://localhost:8000

Shared Supabase (production)
  ├─ Regs Checker (wjxlimjpaijdogyrqtxc, us-east-1)
  └─ Policy Navigator (aaxxunfarlhmydvohsrm, us-east-2)

Other team members
  ├─ Clone repo
  ├─ Run: python start.py (local Docker)
  └─ Pull from shared Supabase (if read-only)
```

### Setup for New Team Members

#### Quick Start (5 minutes)

```powershell
# 1. Clone repo
git clone <repo-url>
cd regs-checker

# 2. Create venv + install
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e ".[pdf,ocr,dev]"

# 3. Start everything (auto-handles Docker, migrations)
python start.py
```

That's it. The startup script handles:
- Creating `.env` from defaults
- Starting Docker containers
- Running Alembic migrations
- Opening dashboard browser tab

#### For Production / Shared Extraction

If running on a dedicated machine (Linux server, shared GPU):

1. **Clone repo** with full history:
   ```bash
   git clone <repo-url> /opt/regs-checker
   cd /opt/regs-checker
   ```

2. **Use system-wide Python venv:**
   ```bash
   python3.11 -m venv /opt/venv-regs
   source /opt/venv-regs/bin/activate
   pip install -e ".[pdf,ocr,dev]"
   ```

3. **Set up systemd service** (Linux):
   ```ini
   # /etc/systemd/system/regs-checker.service
   [Unit]
   Description=Regs Checker Pipeline
   After=docker.service

   [Service]
   Type=simple
   User=regs-checker
   WorkingDirectory=/opt/regs-checker
   Environment="PATH=/opt/venv-regs/bin"
   ExecStart=/opt/venv-regs/bin/python start.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable regs-checker
   sudo systemctl start regs-checker
   ```

4. **Configure external LLM access:**
   Edit `.env`:
   ```
   # For LM Studio on different PC
   REGS_LOCAL_LLM_URL=http://gpu-server.local:1234
   
   # For shared Supabase
   REGS_DATABASE_URL=postgresql://postgres.project:password@aws-region.supabase.com:6543/postgres
   ```

### Deployment Checklist

- [ ] **Python 3.11+** installed and in PATH
- [ ] **Docker Desktop** installed and running
- [ ] **Git** installed (for cloning + pulling updates)
- [ ] **venv created** and activated
- [ ] **Dependencies installed** via `pip install -e ".[pdf,ocr,dev]"`
- [ ] **`.env` configured** with DB + LLM URLs
- [ ] **Migrations run** via `python -m alembic upgrade head`
- [ ] **Dashboard loads** at http://localhost:8000/dashboard
- [ ] **LLM reachable** at configured URL
- [ ] **Test pipeline** on small batch (1–2 bills) before running full extraction

### Keeping Multiple PCs in Sync

Use Git branches for each environment:

```powershell
# Main branch = production (reviewed, tested)
git checkout main

# Development branch = latest changes
git checkout develop

# Local branch = your machine customizations
git checkout -b local-setup
git config --local user.name "Your Name"
git config --local user.email "you@example.com"
```

Pull latest changes regularly:
```powershell
git fetch origin
git merge origin/develop
pip install -e ".[pdf,ocr,dev]"  # Re-install in case deps changed
python -m alembic upgrade head   # Re-run migrations
```

---

## Additional Resources

- **Architecture**: See `architecture.md`
- **API Reference**: http://localhost:8000/docs (OpenAPI/Swagger)
- **Extraction Agents**: `src/agents/` directory
- **Database Schema**: `src/db/models.py`
- **Dashboard Code**: `src/api/routes/dashboard.py`
- **Logs**: Check console output; logs are structured with `structlog`

---

## Getting Help

1. **Check logs**: Console output from `python start.py` includes detailed error messages
2. **Review CLAUDE.md**: Developer context specific to the codebase
3. **Check tasks.md**: Known issues and phase completeness
4. **Open an issue**: Include:
   - OS + Python version (`python --version`)
   - Error message (full traceback)
   - What step failed (docker, migration, extraction, etc.)
   - Docker status (`docker ps`, `docker logs`)
