#!/bin/bash
# OlbosTrade startup script
# Run this once before market open: ./start.sh
# Or set it up as a launchd service (see README)

set -e
PROJECT="/Users/mangpijasuan/Projects/olbostrade"
LOG_DIR="$PROJECT/logs"
mkdir -p "$LOG_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  OlbosTrade — System Startup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Check IB Gateway / TWS is reachable ───────────────────────────────────
# Read port from .env (default 4002 for IB Gateway paper; use 7497 for TWS paper)
IBKR_PORT=$(grep -E "^IBKR_PORT=" "$PROJECT/backend/.env" 2>/dev/null | cut -d= -f2 | tr -d ' ')
IBKR_PORT="${IBKR_PORT:-4002}"
echo -n "[1/6] IB Gateway / TWS (port $IBKR_PORT)... "
if python3 -c "import socket; s=socket.socket(); s.settimeout(2); r=s.connect_ex(('127.0.0.1',$IBKR_PORT)); s.close(); exit(r)" 2>/dev/null; then
  echo "✅ CONNECTED"
else
  echo "❌ NOT REACHABLE"
  echo "    → Open IB Gateway (port 4002) or TWS (port 7497) and log in."
  echo "    → Make sure API is enabled: Configure → API → Enable ActiveX and Socket Clients"
  echo "    → Set IBKR_PORT=$IBKR_PORT in backend/.env to match your setup."
  exit 1
fi

# ── 2. Kill any stale processes ───────────────────────────────────────────────
echo -n "[2/6] Clearing stale processes... "
pkill -f "uvicorn app.main" 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
lsof -ti :8000 | xargs kill -9 2>/dev/null || true
lsof -ti :3000 | xargs kill -9 2>/dev/null || true
sleep 1
echo "✅ DONE"

# ── 3. Trim regenerable caches ────────────────────────────────────────────────
# Frees disk (a full disk has stopped the desk before). Runs only after the old
# processes are stopped, touches an allowlist of caches that regenerate, and never
# models, .env files or node_modules — see scripts/clean_caches.sh. A failure here
# must not stop the desk from starting, so it is advisory.
echo -n "[3/6] Trimming caches... "
if CLEAN_OUT="$("$PROJECT/scripts/clean_caches.sh" --apply 2>&1)"; then
  echo "✅ $(echo "$CLEAN_OUT" | tail -1)"
else
  echo "⚠️  skipped (startup continues)"
fi

# ── 4. Start backend ──────────────────────────────────────────────────────────
echo -n "[4/6] Starting backend (port 8000)... "
cd "$PROJECT/backend"
nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

# Wait for backend to be ready (max 30s)
for i in $(seq 1 30); do
  if curl -s http://localhost:8000/health > /dev/null 2>&1 || \
     curl -s http://localhost:8000/api/market/broker > /dev/null 2>&1; then
    echo "✅ RUNNING (PID $BACKEND_PID)"
    break
  fi
  if [ $i -eq 30 ]; then
    echo "❌ FAILED — check $LOG_DIR/backend.log"
    exit 1
  fi
  sleep 1
done

# ── 5. Start frontend ─────────────────────────────────────────────────────────
echo -n "[5/6] Starting frontend (port 3000)... "
cd "$PROJECT/frontend"
nohup npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
sleep 3
echo "✅ RUNNING (PID $FRONTEND_PID)"

# ── 6. Health check ───────────────────────────────────────────────────────────
echo -n "[6/6] System health check... "
# Query backend directly (port 8000) — not the Vite proxy — for reliability
BROKER=$(curl -s http://localhost:8000/api/market/broker 2>/dev/null)
SPY=$(curl -s http://localhost:8000/api/market/snapshot/SPY 2>/dev/null)

BROKER_STATUS=$(echo "$BROKER" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null)
SPY_PRICE=$(echo "$SPY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('mid','?'))" 2>/dev/null)

echo "✅ DONE"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ OlbosTrade is LIVE"
echo "  Broker : $BROKER_STATUS (IBKR paper)"
echo "  SPY    : \$$SPY_PRICE"
echo "  UI     : http://localhost:3000"
echo "  Logs   : $LOG_DIR/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Save PIDs for stop script
echo "$BACKEND_PID" > "$LOG_DIR/backend.pid"
echo "$FRONTEND_PID" > "$LOG_DIR/frontend.pid"
