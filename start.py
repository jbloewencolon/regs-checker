"""Startup script — validates config, tests DB, launches the dashboard.

Usage (PowerShell or any terminal):
    python start.py

What it does:
    1. Loads .env and validates required settings
    2. Tests the database connection (with retry for cold-start Supabase)
    3. Opens the dashboard in your default browser
    4. Starts uvicorn on http://localhost:8000
"""

import os
import sys
import time
import webbrowser
import threading
from pathlib import Path

# Ensure we're in the project root
os.chdir(Path(__file__).resolve().parent)


def _load_env():
    """Load .env file manually (pydantic-settings does this too, but we need it early)."""
    env_path = Path(".env")
    if not env_path.exists():
        print("ERROR: No .env file found.")
        print("  Copy .env.example to .env and fill in your database URL:")
        print("    cp .env.example .env")
        print()
        print("  Required setting:")
        print("    REGS_DATABASE_URL=postgresql://postgres.YOUR_PROJECT:PASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres")
        sys.exit(1)

    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def _test_db_connection(url: str, retries: int = 3) -> bool:
    """Test database connectivity with retries (Supabase can be slow after restore)."""
    import psycopg2

    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(url, connect_timeout=10)
            conn.close()
            return True
        except psycopg2.OperationalError as e:
            err = str(e).strip()
            if attempt < retries:
                wait = 2 ** attempt
                print(f"  Attempt {attempt}/{retries} failed: {err[:100]}")
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"  All {retries} attempts failed: {err[:200]}")
                return False


def _open_browser_delayed(url: str, delay: float = 2.0):
    """Open browser after a short delay to let uvicorn start."""
    time.sleep(delay)
    webbrowser.open(url)


def main():
    print("=" * 60)
    print("  Regs Checker — Pipeline Dashboard")
    print("=" * 60)

    # Step 1: Load .env
    print("\n[1/3] Loading configuration...")
    _load_env()

    db_url = os.environ.get("REGS_DATABASE_URL", "")
    if not db_url or "regs:regs@127.0.0.1:5434" in db_url:
        print("  WARNING: Using default local DB URL.")
        print("  Set REGS_DATABASE_URL in .env for Supabase.")
        if "regs:regs@127.0.0.1:5434" in db_url:
            print("  (Default points to local Docker Postgres on port 5434)")
    else:
        # Mask password in output
        masked = db_url
        if "@" in db_url and ":" in db_url.split("@")[0]:
            parts = db_url.split("@")
            creds = parts[0].rsplit(":", 1)
            masked = f"{creds[0]}:****@{parts[1]}"
        print(f"  Database: {masked}")

    # Step 2: Test DB connection
    print("\n[2/3] Testing database connection...")
    if _test_db_connection(db_url):
        print("  Connected successfully!")
    else:
        print()
        print("  Could not connect to the database.")
        print("  Check your .env file:")
        print(f"    REGS_DATABASE_URL={db_url[:50]}...")
        print()
        print("  Common fixes:")
        print("    - Check password is correct (no <angle brackets>)")
        print("    - Supabase project may be paused — restore at supabase.com/dashboard")
        print("    - Try the pooler URL: postgresql://postgres.PROJECT:PASS@aws-0-us-east-1.pooler.supabase.com:6543/postgres")
        print()
        response = input("  Start anyway without DB? (y/N): ").strip().lower()
        if response != "y":
            sys.exit(1)
        print("  Starting in degraded mode (DB features will error)...")

    # Step 3: Launch
    port = int(os.environ.get("REGS_API_PORT", "8000"))
    url = f"http://localhost:{port}/dashboard"

    print(f"\n[3/3] Starting server on port {port}...")
    print(f"  Dashboard: {url}")
    print(f"  API docs:  http://localhost:{port}/docs")
    print(f"  Health:    http://localhost:{port}/health")
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
