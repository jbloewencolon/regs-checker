"""Startup script — validates config, tests DB, launches the dashboard.

Usage (PowerShell or any terminal):
    python start.py

What it does:
    1. Loads .env and validates required settings
    2. Tests the database connection (offers to start Docker if needed)
    3. Runs Alembic migrations if tables are missing
    4. Opens the dashboard in your default browser
    5. Starts uvicorn on http://localhost:8000
"""

import os
import shutil
import subprocess
import sys
import time
import webbrowser
import threading
from pathlib import Path

# Ensure we're in the project root
os.chdir(Path(__file__).resolve().parent)

LOCAL_DB_URL = "postgresql://regs:regs@127.0.0.1:5434/regs_checker"
DOCKER_COMPOSE = Path("docker/docker-compose.yml")


def _load_env():
    """Load .env file manually (pydantic-settings does this too, but we need it early)."""
    env_path = Path(".env")
    if not env_path.exists():
        print("  No .env file found — creating from .env.example...")
        example = Path(".env.example")
        if example.exists():
            shutil.copy(example, env_path)
            print("  Created .env from .env.example")
        else:
            # Create a minimal .env pointing to local Docker
            env_path.write_text(f"REGS_DATABASE_URL={LOCAL_DB_URL}\n")
            print("  Created .env with local Docker database URL")

    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def _test_db_connection(url: str, retries: int = 2) -> bool:
    """Test database connectivity with retries."""
    import psycopg2

    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(url, connect_timeout=5)
            conn.close()
            return True
        except psycopg2.OperationalError as e:
            err = str(e).strip().split("\n")[0]
            if attempt < retries:
                print(f"  Attempt {attempt}/{retries}: {err[:100]}")
                time.sleep(2)
            else:
                print(f"  Connection failed: {err[:120]}")
                return False


def _docker_available() -> bool:
    """Check if Docker is installed and running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _docker_postgres_running() -> bool:
    """Check if Docker Postgres container is already running."""
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(DOCKER_COMPOSE), "ps", "--status=running", "-q", "postgres"],
            capture_output=True, text=True, timeout=10,
        )
        return bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _start_docker():
    """Start Docker Compose services (Postgres + MinIO)."""
    print("  Starting Docker containers (Postgres + MinIO)...")
    result = subprocess.run(
        ["docker", "compose", "-f", str(DOCKER_COMPOSE), "up", "-d"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"  Docker Compose failed: {result.stderr[:200]}")
        return False

    # Wait for Postgres to be healthy
    print("  Waiting for Postgres to be ready...", end="", flush=True)
    for i in range(15):
        time.sleep(1)
        print(".", end="", flush=True)
        if _test_db_connection(LOCAL_DB_URL, retries=1):
            print(" ready!")
            return True
    print(" timeout!")
    return False


def _switch_to_local_db():
    """Update .env to use local Docker database."""
    env_path = Path(".env")
    content = env_path.read_text() if env_path.exists() else ""

    # Replace or add REGS_DATABASE_URL
    lines = content.splitlines()
    new_lines = []
    replaced = False
    for line in lines:
        if line.strip().startswith("REGS_DATABASE_URL="):
            new_lines.append(f"# {line}  # commented out by start.py")
            new_lines.append(f"REGS_DATABASE_URL={LOCAL_DB_URL}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"REGS_DATABASE_URL={LOCAL_DB_URL}")

    env_path.write_text("\n".join(new_lines) + "\n")
    os.environ["REGS_DATABASE_URL"] = LOCAL_DB_URL
    print(f"  Updated .env → REGS_DATABASE_URL={LOCAL_DB_URL}")


def _run_migrations(db_url: str) -> bool:
    """Run Alembic migrations if tables are missing."""
    import psycopg2

    try:
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'sources'"
        )
        has_tables = cur.fetchone()[0] > 0
        conn.close()

        if has_tables:
            return True

        print("  Database is empty — running migrations...")
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print("  Migrations applied successfully!")
            return True
        else:
            print(f"  Migration failed: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"  Migration check error: {e}")
        return False


def _open_browser_delayed(url: str, delay: float = 2.5):
    """Open browser after a short delay to let uvicorn start."""
    time.sleep(delay)
    webbrowser.open(url)


def _mask_url(db_url: str) -> str:
    """Mask password in database URL for display."""
    if "@" in db_url and ":" in db_url.split("@")[0]:
        parts = db_url.split("@")
        creds = parts[0].rsplit(":", 1)
        return f"{creds[0]}:****@{parts[1]}"
    return db_url


def main():
    print("=" * 60)
    print("  Regs Checker — Pipeline Dashboard")
    print("=" * 60)

    # Step 1: Load .env
    print("\n[1/4] Loading configuration...")
    _load_env()

    db_url = os.environ.get("REGS_DATABASE_URL", LOCAL_DB_URL)
    is_local = "127.0.0.1" in db_url or "localhost" in db_url
    print(f"  Database: {_mask_url(db_url)}")

    # Step 2: Test DB connection
    print("\n[2/4] Testing database connection...")
    connected = _test_db_connection(db_url)

    if not connected and not is_local:
        # Remote DB failed — offer to switch to local Docker
        print()
        print("  Remote database is unreachable.")
        if _docker_available():
            print("  Docker is available — switch to local Postgres?")
            response = input("  Start local Docker database? (Y/n): ").strip().lower()
            if response != "n":
                _switch_to_local_db()
                db_url = LOCAL_DB_URL
                is_local = True

                if _docker_postgres_running():
                    print("  Docker Postgres already running!")
                    connected = _test_db_connection(db_url)
                else:
                    connected = _start_docker()
        else:
            print("  Install Docker Desktop to run a local database:")
            print("    https://www.docker.com/products/docker-desktop/")

    if not connected and is_local:
        # Local DB URL but not connected — try starting Docker
        if _docker_available():
            if not _docker_postgres_running():
                print("  Local DB not running — starting Docker...")
                connected = _start_docker()
            else:
                print("  Docker container running but DB unreachable.")
        else:
            print("  Local DB URL configured but Docker not available.")
            print("  Install Docker Desktop: https://www.docker.com/products/docker-desktop/")

    if not connected:
        print()
        response = input("  Start anyway without DB? (y/N): ").strip().lower()
        if response != "y":
            sys.exit(1)
        print("  Starting in degraded mode...")

    # Step 3: Run migrations
    if connected:
        print("\n[3/4] Checking database schema...")
        _run_migrations(db_url)
    else:
        print("\n[3/4] Skipping migrations (no DB connection)")

    # Step 4: Launch
    port = int(os.environ.get("REGS_API_PORT", "8000"))
    url = f"http://localhost:{port}/dashboard"

    print(f"\n[4/4] Starting server...")
    print(f"  Dashboard:  {url}")
    print(f"  API docs:   http://localhost:{port}/docs")
    print()
    print("  Press Ctrl+C to stop.")
    print("=" * 60)

    # Open browser in background after server starts
    threading.Thread(
        target=_open_browser_delayed,
        args=(url, 2.5),
        daemon=True,
    ).start()

    # Start uvicorn
    import uvicorn
    uvicorn.run(
        "src.api.app:app",
        host="127.0.0.1",
        port=port,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
