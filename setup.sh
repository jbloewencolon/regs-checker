#!/bin/bash
# setup.sh — Automated one-command setup for regs-checker
# Usage: bash setup.sh
#
# What it does:
#   1. Checks prerequisites (Python, Git, Docker)
#   2. Creates venv + installs dependencies
#   3. Copies .env from template
#   4. Starts Docker containers
#   5. Runs migrations
#
# Requirements: Python 3.11+, Git, Docker, bash

set -e
cd "$(dirname "$0")"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
NC='\033[0m' # No Color

# Functions
step() { echo -e "\n${CYAN}[✓] $1${NC}"; }
error() { echo -e "\n${RED}[✗] $1${NC}"; exit 1; }
warn() { echo -e "  ${YELLOW}⚠ $1${NC}"; }
info() { echo -e "  ${GRAY}$1${NC}"; }

# ============================================================================
# Step 0: Check prerequisites
# ============================================================================

echo ""
echo "========================================================================"
echo "  Regs Checker — Automated Setup"
echo "========================================================================"

step "Checking prerequisites..."

# Python version
if ! command -v python3 &> /dev/null; then
    error "Python 3 not found. Install from https://python.org/"
fi

PYTHON_VERSION=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
if (( $(echo "$PYTHON_VERSION < 3.11" | bc -l) )); then
    error "Python 3.11+ required, found: $PYTHON_VERSION"
fi
info "Python: $(python3 --version) ✓"

# Git
if ! command -v git &> /dev/null; then
    error "Git not found. Install from https://git-scm.com/"
fi
info "Git: $(git --version) ✓"

# Docker
if ! command -v docker &> /dev/null; then
    warn "Docker not found. Install from https://docker.com/"
    warn "You can still run locally, but extraction requires manual LLM setup."
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    info "Docker: $(docker --version) ✓"
fi

# ============================================================================
# Step 1: Create and activate venv
# ============================================================================

step "Creating Python virtual environment..."

if [ -d "venv" ]; then
    warn "venv already exists. Reusing existing environment."
else
    python3 -m venv venv
    info "venv created"
fi

source venv/bin/activate
info "venv activated"

# Upgrade pip
info "Upgrading pip, setuptools, wheel..."
python3 -m pip install --upgrade pip setuptools wheel --quiet || error "Failed to upgrade pip"

# ============================================================================
# Step 2: Install dependencies
# ============================================================================

step "Installing project dependencies..."

if [ "$1" == "--dev" ]; then
    info "Installing with dev/test dependencies..."
else
    info "Installing with optional dependencies (pdf, ocr, dev)..."
fi

pip install -e ".[pdf,ocr,dev]" --quiet || error "Failed to install dependencies. Try: pip install -e '.[pdf,ocr,dev]' --no-cache-dir"
info "Dependencies installed ✓"

# ============================================================================
# Step 3: Create .env
# ============================================================================

step "Configuring environment (.env)..."

if [ -f ".env" ]; then
    warn ".env already exists. Keeping existing configuration."
else
    if [ -f ".env.example" ]; then
        cp .env.example .env
        info ".env created from .env.example"
    else
        error ".env.example not found"
    fi
fi

info "Database: Docker Postgres on localhost:5434"
info "LLM: LM Studio on localhost:1234 (or edit .env)"

# ============================================================================
# Step 4: Docker setup
# ============================================================================

if command -v docker &> /dev/null; then
    step "Starting Docker containers..."

    if ! docker info > /dev/null 2>&1; then
        warn "Docker daemon not running."
        if [ "$(uname)" == "Darwin" ]; then
            info "On macOS, start Docker Desktop from Applications/Docker.app"
        fi
        read -p "Continue? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    else
        info "Starting Postgres and MinIO containers..."
        docker compose -f docker/docker-compose.yml up -d > /dev/null 2>&1

        info "Waiting for Postgres to be ready..."
        for i in {1..30}; do
            if docker compose -f docker/docker-compose.yml exec postgres pg_isready -U regs > /dev/null 2>&1; then
                info "Postgres is ready ✓"
                break
            fi
            echo -n "."
            sleep 1
        done
    fi
fi

# ============================================================================
# Step 5: Database migrations
# ============================================================================

step "Running database migrations..."

info "Running Alembic migrations..."
python3 -m alembic upgrade head 2>&1 | grep -E "upgrade|Creating|error" | while read line; do info "$line"; done || warn "Migration check failed (may succeed on startup)"

# ============================================================================
# Step 6: Summary
# ============================================================================

echo ""
echo "========================================================================"
echo -e "  ${GREEN}✓ Setup Complete!${NC}"
echo "========================================================================"

echo ""
echo "Next steps:"
echo ""
echo "1. Activate venv (if needed):"
echo "   source venv/bin/activate"
echo ""
echo "2. Start the application:"
echo "   python start.py"
echo ""
echo "3. Open dashboard in your browser:"
echo "   http://localhost:8000/dashboard"
echo ""
echo "Optional:"
echo "- Download LM Studio: https://lmstudio.ai/"
echo "- Load a model (mistral-7b, phi, llama2, etc.)"
echo "- Click 'Start Server' to serve on http://localhost:1234"
echo ""
echo "For help, see SETUP.md in the project root."
echo "========================================================================"
echo ""
