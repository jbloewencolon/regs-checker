# start.ps1 — One-command local dev startup
# Usage: .\start.ps1

Write-Host "Starting infrastructure (Postgres + MinIO)..." -ForegroundColor Cyan
docker compose -f docker/docker-compose.yml up -d postgres minio minio-init

Write-Host "Waiting for Postgres to be ready..." -ForegroundColor Cyan
$retries = 0
do {
    Start-Sleep -Seconds 1
    $retries++
    $ready = docker exec docker-postgres-1 pg_isready -U regs -d regs_checker 2>$null
} while ($LASTEXITCODE -ne 0 -and $retries -lt 15)

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Postgres failed to start." -ForegroundColor Red
    exit 1
}
Write-Host "Postgres is ready." -ForegroundColor Green

Write-Host "Running database migrations..." -ForegroundColor Cyan
python -m alembic upgrade head

Write-Host ""
Write-Host "Dashboard ready: " -ForegroundColor Green -NoNewline
Write-Host "http://localhost:8000/dashboard" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""
Start-Process "http://localhost:8000/dashboard"
python -m uvicorn src.api.app:app --reload --port 8000
