# start.ps1 - One-command local dev startup
# Usage: .\start.ps1

Set-Location $PSScriptRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Regs Checker - Automated Startup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# -- Step 1: Activate virtual environment --
Write-Host ""
Write-Host "[1/6] Activating virtual environment..." -ForegroundColor Cyan

$venvActivate = ".\venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    & $venvActivate
    Write-Host "  venv activated." -ForegroundColor Green
} else {
    Write-Host "  WARNING: venv not found at $venvActivate" -ForegroundColor Yellow
    Write-Host "  Create it with: python -m venv venv" -ForegroundColor Yellow
    Write-Host "  Then: .\venv\Scripts\Activate; pip install -r requirements.txt" -ForegroundColor Yellow
    Write-Host "  Continuing with system Python..." -ForegroundColor Yellow
}

# -- Step 2: Check Docker Desktop --
Write-Host ""
Write-Host "[2/6] Checking Docker Desktop..." -ForegroundColor Cyan

$dockerRunning = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerRunning = $true }
} catch {
    $dockerRunning = $false
}

if (-not $dockerRunning) {
    Write-Host "  Docker Desktop is not running. Attempting to start it..." -ForegroundColor Yellow

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

    Write-Host "  Waiting for Docker daemon..." -NoNewline -ForegroundColor Cyan
    $waited = 0
    while ($waited -lt 60) {
        Start-Sleep -Seconds 2
        $waited += 2
        Write-Host "." -NoNewline
        try {
            $null = docker info 2>&1
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

# -- Step 3: Start containers --
Write-Host ""
Write-Host "[3/6] Starting Docker containers..." -ForegroundColor Cyan

Write-Host "  Starting Postgres..." -ForegroundColor Cyan
$null = docker compose -f docker/docker-compose.yml up -d postgres 2>&1
if ($LASTEXITCODE -ne 0) {
    Start-Sleep -Seconds 3
    $null = docker compose -f docker/docker-compose.yml up -d postgres 2>&1
}

# Start MinIO in background (non-blocking, not required for dashboard)
Write-Host "  Starting MinIO (background, non-blocking)..." -ForegroundColor Cyan
try {
    Start-Process -NoNewWindow -FilePath "docker" -ArgumentList "compose -f docker/docker-compose.yml up -d minio minio-init" -ErrorAction SilentlyContinue
} catch {}

# -- Step 4: Wait for Postgres --
Write-Host ""
Write-Host "[4/6] Waiting for Postgres to be ready..." -NoNewline -ForegroundColor Cyan

$retries = 0
$pgReady = $false
do {
    Start-Sleep -Seconds 1
    $retries++
    Write-Host "." -NoNewline
    try {
        $null = docker exec docker-postgres-1 pg_isready -U regs -d regs_checker 2>&1
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
if ("$credCheck" -notlike "*ok*") {
    Write-Host "  Password auth failed. Recreating Postgres volume..." -ForegroundColor Yellow
    $null = docker compose -f docker/docker-compose.yml rm -sf postgres 2>&1
    $null = docker volume rm docker_postgres_data 2>&1
    $null = docker compose -f docker/docker-compose.yml up -d postgres 2>&1
    $retries = 0
    do {
        Start-Sleep -Seconds 2
        $retries++
        $null = docker exec docker-postgres-1 pg_isready -U regs -d regs_checker 2>&1
    } while ($LASTEXITCODE -ne 0 -and $retries -lt 15)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Postgres failed after volume reset." -ForegroundColor Red
        exit 1
    }
}
Write-Host "  Postgres is ready." -ForegroundColor Green

# -- Step 5: Run migrations --
Write-Host ""
Write-Host "[5/6] Running database migrations..." -ForegroundColor Cyan
python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Host "  WARNING: Migrations had issues (may already be applied)." -ForegroundColor Yellow
} else {
    Write-Host "  Migrations applied." -ForegroundColor Green
}

# -- Step 6: Launch --
Write-Host ""
Write-Host "[6/6] Launching dashboard..." -ForegroundColor Cyan
Write-Host ""
Write-Host "  Dashboard:  http://localhost:8000/dashboard" -ForegroundColor Green
Write-Host "  API docs:   http://localhost:8000/docs" -ForegroundColor Green
Write-Host ""
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan

Start-Process "http://localhost:8000/dashboard"
python -m uvicorn src.api.app:app --reload --port 8000
