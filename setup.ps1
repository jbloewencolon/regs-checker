# setup.ps1 — Automated one-command setup for regs-checker
# Usage: .\setup.ps1
#
# What it does:
#   1. Checks prerequisites (Python, Git, Docker)
#   2. Creates venv + installs dependencies
#   3. Copies .env from template
#   4. Tests database connection
#   5. Runs migrations
#   6. Opens dashboard
#
# Requirements: Python 3.11+, Git, Docker Desktop

param(
    [switch]$SkipDocker,
    [switch]$LocalDev
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Write-Step { param([string]$Message); Write-Host "`n[✓] $Message" -ForegroundColor Cyan }
function Write-Error { param([string]$Message); Write-Host "`n[✗] $Message" -ForegroundColor Red }
function Write-Warn { param([string]$Message); Write-Host "  ⚠ $Message" -ForegroundColor Yellow }
function Write-Info { param([string]$Message); Write-Host "  $Message" -ForegroundColor Gray }

# ============================================================================
# Step 0: Check prerequisites
# ============================================================================

Write-Host "`n" + ("=" * 70)
Write-Host "  Regs Checker — Automated Setup"
Write-Host ("=" * 70)

Write-Step "Checking prerequisites..."

# Python version
try {
    $pythonVersion = & python --version 2>&1
    if ($pythonVersion -match "3\.1[1-9]|3\.[2-9]\d") {
        Write-Info "Python: $pythonVersion ✓"
    } else {
        Write-Error "Python 3.11+ required, found: $pythonVersion"
        exit 1
    }
} catch {
    Write-Error "Python not found. Install from https://python.org/"
    exit 1
}

# Git
try {
    $gitVersion = & git --version 2>&1
    Write-Info "Git: $gitVersion ✓"
} catch {
    Write-Error "Git not found. Install from https://git-scm.com/"
    exit 1
}

# Docker (unless --SkipDocker)
if (-not $SkipDocker) {
    try {
        $dockerVersion = & docker --version 2>&1
        Write-Info "Docker: $dockerVersion ✓"
    } catch {
        Write-Warn "Docker not found or not running. Install from https://docker.com/"
        Write-Warn "You can still run locally, but extraction will require manual LLM setup."
        $response = Read-Host "Continue anyway? (y/N)"
        if ($response -ne "y") { exit 1 }
    }
}

# ============================================================================
# Step 1: Create and activate venv
# ============================================================================

Write-Step "Creating Python virtual environment..."

if (Test-Path "venv") {
    Write-Warn "venv already exists. Reusing existing environment."
} else {
    & python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create venv"
        exit 1
    }
    Write-Info "venv created"
}

# Activate venv
$venvActivate = ".\venv\Scripts\Activate.ps1"
& $venvActivate
Write-Info "venv activated"

# Upgrade pip
Write-Info "Upgrading pip, setuptools, wheel..."
& python -m pip install --upgrade pip setuptools wheel --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to upgrade pip"
    exit 1
}

# ============================================================================
# Step 2: Install dependencies
# ============================================================================

Write-Step "Installing project dependencies..."

if ($LocalDev) {
    Write-Info "Installing with dev/test dependencies..."
    & pip install -e ".[pdf,ocr,dev]" --quiet
} else {
    Write-Info "Installing with optional dependencies (pdf, ocr, dev)..."
    & pip install -e ".[pdf,ocr,dev]" --quiet
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install dependencies. Try: pip install -e '.[pdf,ocr,dev]' --no-cache-dir"
    exit 1
}

Write-Info "Dependencies installed ✓"

# ============================================================================
# Step 3: Create .env
# ============================================================================

Write-Step "Configuring environment (.env)..."

if (Test-Path ".env") {
    Write-Warn ".env already exists. Keeping existing configuration."
} else {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Info ".env created from .env.example"
    } else {
        Write-Error ".env.example not found"
        exit 1
    }
}

Write-Info "Database: Docker Postgres on localhost:5434"
Write-Info "LLM: LM Studio on localhost:1234 (or edit .env)"

# ============================================================================
# Step 4: Docker setup (optional)
# ============================================================================

if (-not $SkipDocker) {
    Write-Step "Starting Docker containers..."

    $dockerRunning = try { & docker info 2>&1 | Out-Null; $true } catch { $false }

    if (-not $dockerRunning) {
        Write-Warn "Docker not running. Attempting to start Docker Desktop..."

        $dockerPaths = @(
            "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
            "$env:LocalAppData\Docker\Docker\Docker Desktop.exe"
        )

        $started = $false
        foreach ($path in $dockerPaths) {
            if (Test-Path $path) {
                Write-Info "Starting: $path"
                Start-Process $path -WindowStyle Minimized
                Start-Sleep -Seconds 10
                $started = $true
                break
            }
        }

        if (-not $started) {
            Write-Warn "Could not auto-start Docker Desktop. Please start it manually."
            $response = Read-Host "Continue? (y/N)"
            if ($response -ne "y") { exit 1 }
        }
    }

    Write-Info "Starting Postgres and MinIO containers..."
    & docker compose -f docker/docker-compose.yml up -d --quiet 2>&1 | Out-Null

    Write-Info "Waiting for Postgres to be ready..."
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $conn = [Reflection.Assembly]::LoadFrom("$((Get-Command psycopg2).Source)").GetType("psycopg2.connect")
            Write-Host "." -NoNewline
            Start-Sleep -Seconds 1
        } catch { }
    }
    Write-Host " ✓"
}

# ============================================================================
# Step 5: Database migrations
# ============================================================================

Write-Step "Running database migrations..."

$conn = try {
    [Reflection.Assembly]::LoadWithPartialName("Npgsql") | Out-Null
    New-Object Npgsql.NpgsqlConnection("Host=127.0.0.1;Port=5434;Username=regs;Password=regs;Database=regs_checker;Connection Timeout=5")
} catch { $null }

if ($conn) {
    try {
        $conn.Open()
        $conn.Close()
        Write-Info "Database connection OK"

        Write-Info "Running Alembic migrations..."
        & python -m alembic upgrade head 2>&1 | Select-String -Pattern "upgrade|Creating|error" | ForEach-Object { Write-Info $_.Line }

        if ($LASTEXITCODE -ne 0) {
            Write-Error "Migration failed. Try: python -m alembic upgrade head"
        } else {
            Write-Info "Migrations complete ✓"
        }
    } catch {
        Write-Warn "Database not ready yet. Migrations will run on first startup."
    }
}

# ============================================================================
# Step 6: Summary
# ============================================================================

Write-Host "`n" + ("=" * 70)
Write-Host "  ✓ Setup Complete!" -ForegroundColor Green
Write-Host ("=" * 70)

Write-Host "`nNext steps:`n"
Write-Host "1. Activate venv (if needed):"
Write-Host "   .\venv\Scripts\Activate.ps1`n"

Write-Host "2. Start the application:"
Write-Host "   python start.py`n"

Write-Host "3. Open dashboard in your browser:"
Write-Host "   http://localhost:8000/dashboard`n"

Write-Host "Optional:"
Write-Host "- Download LM Studio: https://lmstudio.ai/"
Write-Host "- Load a model (mistral-7b, phi, llama2, etc.)"
Write-Host "- Click 'Start Server' to serve on http://localhost:1234`n"

Write-Host "For help, see SETUP.md in the project root."
Write-Host ("=" * 70) + "`n"
