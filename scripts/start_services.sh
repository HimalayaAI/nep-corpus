#!/bin/bash
set -o pipefail

# Nepali Corpus Service Startup
# Starts Docker DB and Dashboard
# NOTE: Activate your Python environment before running this script

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT" || exit 1

# Ensure local package imports work
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH}"

# Load .env if present
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

DB_PORT=${DB_PORT:-5432}
DB_NAME=${DB_NAME:-nepali_corpus}
DB_USER=${DB_USER:-postgres}
DB_PASSWORD=${DB_PASSWORD:-postgres}
DB_HOST=${DB_HOST:-localhost}
DASHBOARD_PORT=${DASHBOARD_PORT:-8000}

echo "=== Nepali Corpus Service Startup ==="
echo ""

echo "--- Stopping existing dashboard ---"
for pidfile in .dashboard.pid; do
    if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping PID $pid ($pidfile)..."
            kill "$pid" 2>/dev/null || true
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$pidfile"
    fi
done

pid=$(lsof -nP -iTCP:$DASHBOARD_PORT -sTCP:LISTEN -t 2>/dev/null | head -1)
if [ -n "$pid" ]; then
    echo "Stopping process $pid on port $DASHBOARD_PORT..."
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
fi
sleep 1
echo ""

echo "--- Checking Services ---"

is_port_in_use() {
    lsof -nP -iTCP:$1 -sTCP:LISTEN > /dev/null 2>&1
}

docker_available() {
    docker info >/dev/null 2>&1
}

DB_AVAILABLE=false
if is_port_in_use "$DB_PORT"; then
    echo "PostgreSQL is already running on port $DB_PORT."
    DB_AVAILABLE=true
elif docker_available; then
    echo "Starting PostgreSQL (Docker)..."
    COMPOSE_FILE="nepali_corpus/core/services/storage/docker/docker-compose.yaml"
    if docker compose -f "$COMPOSE_FILE" up -d 2>/dev/null; then
        echo "Waiting for DB to be ready..."
        sleep 2
        DB_AVAILABLE=true
    elif docker-compose -f "$COMPOSE_FILE" up -d 2>/dev/null; then
        echo "Waiting for DB to be ready..."
        sleep 2
        DB_AVAILABLE=true
    else
        echo "Warning: docker compose failed. Database may not be available."
    fi
else
    echo "Docker is not running. Database and dashboard data will be unavailable."
    echo "  Start Docker Desktop (or the Docker daemon), then run this script again for DB + dashboard data."
fi

if [ "$DB_AVAILABLE" = true ]; then
    echo "Initializing Database Schema..."
    if python scripts/init_db.py 2>&1 | tee -a init_db.log; then
        echo "✓ Database schema initialized successfully"
    else
        echo "⚠ Database schema init returned non-zero (might already be initialized)"
    fi
else
    echo "⚠ Skipping database initialization (Docker not available)"
fi
echo ""

echo "Starting Dashboard on port $DASHBOARD_PORT..."
python -m uvicorn nepali_corpus.core.services.dashboard.app:app --host 0.0.0.0 --port "$DASHBOARD_PORT" > dashboard.log 2>&1 &
echo $! > .dashboard.pid
dashboard_started=false
for _ in {1..10}; do
    if is_port_in_use "$DASHBOARD_PORT"; then
        dashboard_started=true
        break
    fi
    if [ -f .dashboard.pid ]; then
        dash_pid=$(cat .dashboard.pid)
        if ! kill -0 "$dash_pid" 2>/dev/null; then
            break
        fi
    fi
    sleep 1
done

if [ "$dashboard_started" = true ]; then
    echo "✓ Dashboard running on port $DASHBOARD_PORT"
else
    echo "✗ Failed to start Dashboard (check dashboard.log)"
    tail -n 20 dashboard.log 2>/dev/null || true
fi
echo ""

echo "=== Services Ready ==="
echo "Database:  $DB_HOST:$DB_PORT $([ "$DB_AVAILABLE" = true ] && echo '✓' || echo '✗ (start Docker)')"
echo "Dashboard: http://localhost:$DASHBOARD_PORT"
echo ""
echo "Logs: dashboard.log, init_db.log"
echo "To stop: kill \$(cat .dashboard.pid 2>/dev/null)"
echo ""

if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Opening Dashboard in browser..."
    open "http://localhost:$DASHBOARD_PORT"
fi
