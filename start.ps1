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

# Verify password auth from the HOST side via the mapped port (5434).
# Container-internal checks can pass even with wrong passwords because
# pg_hba.conf often grants trust to 127.0.0.1 inside the container.
Write-Host "Verifying database credentials..." -ForegroundColor Cyan
$env:PGPASSWORD = "regs"
python -c "import psycopg2; psycopg2.connect(host='localhost', port=5434, user='regs', password='regs', dbname='regs_checker'); print('ok')" 2>$null
$authOk = $LASTEXITCODE -eq 0
$env:PGPASSWORD = $null

if (-not $authOk) {
    Write-Host "WARNING: Password auth failed for user 'regs'. Recreating Postgres volume..." -ForegroundColor Yellow
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
        Write-Host "ERROR: Postgres failed to start after volume reset." -ForegroundColor Red
        exit 1
    }
    Write-Host "Postgres volume recreated successfully." -ForegroundColor Green
}

Write-Host "Running database migrations..." -ForegroundColor Cyan
python -m alembic upgrade head

Write-Host ""
Write-Host "Dashboard ready: " -ForegroundColor Green -NoNewline
Write-Host "http://localhost:8000/dashboard" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""
Start-Process "http://localhost:8000/dashboard"
python -m uvicorn src.api.app:app --reload --port 8000
