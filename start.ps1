# start.ps1 — One-command local dev startup
# Usage: .\start.ps1
#
# What it does:
#   1. Activates the Python virtual environment
#   2. Checks if Docker Desktop is running (starts it if not)
#   3. Starts Docker containers (Postgres + MinIO)
#   4. Waits for Postgres to be healthy
#   5. Runs Alembic migrations
#   6. Opens the dashboard in your browser
#   7. Starts uvicorn on http://localhost:8000

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Regs Checker — Automated Startup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# ── Step 1: Activate virtual environment ──────────────────────────────
Write-Host "`n[1/6] Activating virtual environment..." -ForegroundColor Cyan

$venvActivate = ".\venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    & $venvActivate
    Write-Host "  venv activated." -ForegroundColor Green
} else {
    Write-Host "  WARNING: venv not found at $venvActivate" -ForegroundColor Yellow
    Write-Host "  Create it with: python -m venv venv && .\venv\Scripts\Activate && pip install -r requirements.txt" -ForegroundColor Yellow
    Write-Host "  Continuing with system Python..." -ForegroundColor Yellow
}

# ── Step 2: Check Docker Desktop ─────────────────────────────────────
Write-Host "`n[2/6] Checking Docker Desktop..." -ForegroundColor Cyan

$dockerRunning = $false
try {
    docker info 2>$null | Out-Null
    $dockerRunning = ($LASTEXITCODE -eq 0)
} catch {
    $dockerRunning = $false
}

if (-not $dockerRunning) {
    Write-Host "  Docker Desktop is not running. Attempting to start it..." -ForegroundColor Yellow

    # Try common install locations
    $dockerPaths = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe",
        "$env:LocalAppData\Docker\Docker Desktop.exe"
    )

    $started = $false
    foreach ($path in $dockerPaths) {
        if (Test-Path $path) {
            Start-Process $path
            $started = $true
            Write-Host "  Started Docker Desktop from: $path" -ForegroundColor Green
            break
        }
    }

    if (-not $started) {
        # Fall back to Start-Process by name (works if Docker is in PATH)
        try {
            Start-Process "Docker Desktop" -ErrorAction SilentlyContinue
            $started = $true
        } catch {}
    }

    if (-not $started) {
        Write-Host "  ERROR: Could not find Docker Desktop." -ForegroundColor Red
        Write-Host "  Install it from: https://www.docker.com/products/docker-desktop/" -ForegroundColor Red
        exit 1
    }

    # Wait for Docker daemon to be ready (up to 60 seconds)
    Write-Host "  Waiting for Docker daemon..." -NoNewline -ForegroundColor Cyan
    $waited = 0
    while ($waited -lt 60) {
        Start-Sleep -Seconds 2
        $waited += 2
        Write-Host "." -NoNewline
        try {
            docker info 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $dockerRunning = $true
                break
            }
        } catch {}
    }
    Write-Host ""

    if (-not $dockerRunning) {
        Write-Host "  ERROR: Docker daemon did not start within 60 seconds." -ForegroundColor Red
        Write-Host "  Please start Docker Desktop manually and re-run this script." -ForegroundColor Red
        exit 1
    }
}

Write-Host "  Docker is running." -ForegroundColor Green

# ── Step 3: Start containers ──────────────────────────────────────────
# Start Postgres FIRST (independently) so a MinIO pull failure doesn't block the DB.
Write-Host "`n[3/6] Starting Docker containers..." -ForegroundColor Cyan

Write-Host "  Starting Postgres..." -ForegroundColor Cyan
docker compose -f docker/docker-compose.yml up -d postgres 2>$null
if ($LASTEXITCODE -ne 0) {
    Start-Sleep -Seconds 3
    docker compose -f docker/docker-compose.yml up -d postgres
}

# Start MinIO in background — it's used for raw artifact storage but
# the dashboard and extraction pipeline work fine without it.
Write-Host "  Starting MinIO (background, non-blocking)..." -ForegroundColor Cyan
Start-Process -NoNewWindow -FilePath "docker" -ArgumentList "compose -f docker/docker-compose.yml up -d minio minio-init" -ErrorAction SilentlyContinue

# ── Step 4: Wait for Postgres ─────────────────────────────────────────
Write-Host "`n[4/6] Waiting for Postgres to be ready..." -NoNewline -ForegroundColor Cyan

$retries = 0
$pgReady = $false
do {
    Start-Sleep -Seconds 1
    $retries++
    Write-Host "." -NoNewline
    try {
        docker exec docker-postgres-1 pg_isready -U regs -d regs_checker 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $pgReady = $true }
    } catch {}
} while (-not $pgReady -and $retries -lt 20)
Write-Host ""

if (-not $pgReady) {
    Write-Host "  ERROR: Postgres failed to start after 20 seconds." -ForegroundColor Red
    exit 1
}

# Verify password auth from host side
Write-Host "  Verifying database credentials..." -ForegroundColor Cyan
$credCheck = python -c "import psycopg2; psycopg2.connect(host='localhost', port=5434, user='regs', password='regs', dbname='regs_checker'); print('ok')" 2>&1
if ($credCheck -notlike "*ok*") {
    Write-Host "  Password auth failed. Recreating Postgres volume..." -ForegroundColor Yellow
    docker compose -f docker/docker-compose.yml rm -sf postgres
    docker volume rm docker_postgres_data 2>$null
    docker compose -f docker/docker-compose.yml up -d postgres
    $retries = 0
    do {
        Start-Sleep -Seconds 2
        $retries++
        docker exec docker-postgres-1 pg_isready -U regs -d regs_checker 2>$null | Out-Null
    } while ($LASTEXITCODE -ne 0 -and $retries -lt 15)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Postgres failed after volume reset." -ForegroundColor Red
        exit 1
    }
}
Write-Host "  Postgres is ready." -ForegroundColor Green

# ── Step 5: Run migrations ────────────────────────────────────────────
Write-Host "`n[5/6] Running database migrations..." -ForegroundColor Cyan
python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Host "  WARNING: Migrations had issues (may already be applied)." -ForegroundColor Yellow
} else {
    Write-Host "  Migrations applied." -ForegroundColor Green
}

# ── Step 6: Launch ────────────────────────────────────────────────────
Write-Host "`n[6/6] Launching dashboard..." -ForegroundColor Cyan
Write-Host ""
Write-Host "  Dashboard:  http://localhost:8000/dashboard" -ForegroundColor Green
Write-Host "  API docs:   http://localhost:8000/docs" -ForegroundColor Green
Write-Host ""
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan

Start-Process "http://localhost:8000/dashboard"
python -m uvicorn src.api.app:app --reload --port 8000
