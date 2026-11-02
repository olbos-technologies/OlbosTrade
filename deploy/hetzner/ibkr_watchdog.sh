#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# OlbosTrade — IB Gateway watchdog
#
# The gnzsnz/ib-gateway container can stay "Up" while the IB Gateway app inside it
# stops serving its API port (the classic IBKR daily-restart hang). When that
# happens the backend logs endless "TimeoutError / Failed to connect to IBKR".
#
# This script checks liveness by reading the backend's own public /health
# (already-running FastAPI process, no new socket to the gateway) and restarts
# the gateway container only when that snapshot looks dead — with a cooldown so
# it doesn't restart again during the ~90s it takes the gateway to log back in.
#
# PRIOR VERSION BUG (found 2026-08-25): this used to open a second, independent
# ib_insync connection (clientId=99) as its liveness probe. Correlating gateway
# logs against this cron's own schedule proved that connection was the direct
# cause of "remove Client 2" disconnects on the app's real session — IB Gateway
# kicked the existing clientId=2 session every single time this probe connected
# (~286-288 collisions/day while the cron ran, essentially zero when it didn't;
# see the plan doc's "Out-of-band incident" section for the full log analysis).
# The fix opening a second connection was supposed to catch was real, but the
# probe's own side effect was worse than the daily hang it existed to catch —
# so this version checks the app's already-open connection's self-reported
# state instead of ever dialing a second one.
#
# PRIOR VERSION BUG #2 (found 2026-08-27): the fix above also added a second
# condition to the probe — `queue_depth.P0 < 30` — on the theory that a deep
# queue meant the connection was unresponsive even if isConnected() hadn't
# noticed. That predicate is self-reinforcing and cannot clear itself:
# restarting the GATEWAY cannot drain an APP-side queue, it deepens it (the
# restart drops the connection, in-flight coordinator requests are shielded so
# they hang rather than cancel, and the scanners keep enqueueing). Once the
# queue crossed 30 the probe could never return OK again, so this watchdog
# restarted the gateway every COOLDOWN seconds indefinitely — 9 restarts
# between 02:20 and 07:00 alone, each exactly 15 minutes apart. Downstream that
# produced a 99.3% ACCOUNT_SUMMARY timeout rate, a fabricated Total Equity on
# the dashboard (the summary route silently falls back to capital + realized
# P&L when the broker read fails), and margin guardrails running blind while
# autopilot was on. Two lessons, both now encoded below:
#   1. Only restart on a condition the restart can actually fix. For a gateway
#      restart that is exactly one thing: the socket is down.
#   2. Any unattended remediation needs a circuit breaker. If N restarts in a
#      window all fail to restore health, restarting is not the answer —
#      stop and surface it rather than loop forever.
#
# Intended to run from cron every few minutes (see install_ibkr_watchdog.sh).
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
# Appended, not prepended: cron starts with a minimal PATH, which this fills in, but
# anything already on PATH keeps precedence (and a test can put a stub docker first).
export PATH="${PATH:+$PATH:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

VERBOSE=0
[ "${1:-}" = "--verbose" ] && VERBOSE=1

GATEWAY="ibkr-gateway"
BACKEND="olbostrade-backend"
COOLDOWN=900                    # seconds; don't restart again within 15 min
STATE_DIR="${WATCHDOG_STATE_DIR:-/tmp}"   # overridable so tests never touch the real files
HEALTH_URL="${WATCHDOG_HEALTH_URL:-http://127.0.0.1:8000/health}"
STAMP="$STATE_DIR/ibkr-watchdog-last-restart"
# Circuit breaker: if this many restarts inside the window have all failed to
# bring the probe back to OK, restarting plainly isn't the remedy — stop and
# escalate to a human instead of hammering the gateway forever. See the
# 2026-08-27 bug note in the header for why this exists. History is cleared on
# any OK probe, so the breaker only ever trips on *repeated ineffective*
# restarts, never on a series of genuine hangs that each recovered.
MAX_RESTARTS=4
BREAKER_WINDOW=21600            # seconds (6h)
HISTORY="$STATE_DIR/ibkr-watchdog-restart-history"

ts() { date -u +%FT%TZ; }
vsay() { [ "$VERBOSE" = 1 ] && echo "$(ts) $*"; return 0; }

_running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || echo false)" = "true" ]; }

mark_restart() {
  date +%s > "$STAMP"
  date +%s >> "$HISTORY"
}

in_cooldown() {
  [ -f "$STAMP" ] || return 1
  local last now; last=$(cat "$STAMP" 2>/dev/null || echo 0); now=$(date +%s)
  [ $((now - last)) -lt "$COOLDOWN" ]
}

# Count restarts still inside the breaker window, pruning older entries.
restarts_in_window() {
  [ -f "$HISTORY" ] || { echo 0; return 0; }
  local now cutoff kept
  now=$(date +%s); cutoff=$((now - BREAKER_WINDOW))
  kept=$(awk -v c="$cutoff" '$1 ~ /^[0-9]+$/ && $1 >= c' "$HISTORY" 2>/dev/null)
  printf '%s\n' "$kept" | grep -c '[0-9]' || true
  printf '%s\n' "$kept" > "$HISTORY"
}

# A healthy probe means whatever we did (or nothing) worked — forget the
# restart history so a later, unrelated hang gets its full budget again.
clear_history() { : > "$HISTORY"; }

# 1. Gateway container missing or stopped → just (re)start it.
if ! _running "$GATEWAY"; then
  if in_cooldown; then
    echo "$(ts) watchdog: $GATEWAY not running but within cooldown — waiting"
    exit 0
  fi
  echo "$(ts) watchdog: $GATEWAY not running — starting it"
  docker start "$GATEWAY" >/dev/null 2>&1 || docker restart "$GATEWAY" >/dev/null 2>&1 || true
  mark_restart
  exit 0
fi

# 2. Need the backend running to ask it; if it's down, skip (a dead backend
#    isn't this watchdog's problem to fix).
if ! _running "$BACKEND"; then
  echo "$(ts) watchdog: $BACKEND not running — skipping probe"
  exit 0
fi

# 3. Liveness check via the backend's own already-open connection. It reads the
#    PUBLIC /health route from inside the backend container (no new socket to
#    the gateway, so this can never itself disturb the app's live session) and
#    takes its `broker_connected` field, which is a local read with no I/O.
#
#    Three outcomes, and the third is the one that matters:
#      OK       broker_connected is true  -> healthy, forget restart history
#      DEAD     broker_connected is false -> the only condition a *gateway*
#                                            restart can remedy
#      UNKNOWN  anything else             -> the probe failed, say nothing
#                                            about the gateway; do NOTHING
#
#    This used to read /api/health/detail and treat every exception as DEAD.
#    Once AUTH_ENABLED made that route return 401, every probe "failed" and the
#    watchdog restarted a healthy gateway about 800 times. An error from the
#    probe is not evidence about the thing being probed.
#
#    The P0 queue depth is no longer read (it lives on the authenticated detail
#    route) and was never part of the verdict — see the 2026-08-27 bug note in
#    the header: queue depth is app-side backlog, and restarting the gateway
#    makes it strictly worse.
probe=$(docker exec -i -e HEALTH_URL="$HEALTH_URL" "$BACKEND" python3 - <<'PY' 2>/dev/null
import json, os, urllib.request
state = "UNKNOWN"
try:
    with urllib.request.urlopen(os.environ["HEALTH_URL"], timeout=8) as r:
        flag = json.load(r).get("broker_connected")
    if flag is True:
        state = "OK"
    elif flag is False:
        state = "DEAD"
    # anything else (null, missing field from an older backend) stays UNKNOWN
except Exception:
    # A 401, a 5xx, a timeout, unparseable JSON, a refused connection: none of
    # these says the gateway is down. They say the PROBE failed.
    state = "UNKNOWN"
print(state)
PY
)
probe=$(printf '%s' "$probe" | tr -d '[:space:]')

case "$probe" in
  OK)
    clear_history
    vsay "watchdog: gateway healthy (backend reports broker_connected=true)"
    exit 0   # healthy — nothing to do
    ;;
  DEAD)
    ;;       # the backend positively reports its broker socket is down: act below
  *)
    # UNKNOWN, or no output at all. Never restart on this, and never clear the
    # restart history: we learned nothing. 2026-11-02: a probe that read an
    # authenticated route turned every 401 into "DEAD" and restarted a healthy
    # gateway ~800 times, repeatedly dropping the broker connection during
    # market hours. Only a clear "disconnected" report may trigger a restart.
    echo "$(ts) watchdog: backend health probe was inconclusive (${probe:-empty}) — NOT restarting; only broker_connected=false triggers a restart"
    exit 0
    ;;
esac

# 4. Probe failed → restart the gateway, respecting cooldown + breaker.
if in_cooldown; then
  echo "$(ts) watchdog: backend reports the broker disconnected but within cooldown — gateway likely still logging in"
  exit 0
fi

recent=$(restarts_in_window)
if [ "${recent:-0}" -ge "$MAX_RESTARTS" ]; then
  echo "$(ts) watchdog: CIRCUIT BREAKER OPEN — ${recent} restarts in the last $((BREAKER_WINDOW/3600))h did not restore the connection. NOT restarting again; this needs manual investigation."
  exit 0
fi

echo "$(ts) watchdog: backend reports the broker disconnected — restarting $GATEWAY (${recent}/${MAX_RESTARTS} in window)"
docker restart "$GATEWAY" >/dev/null 2>&1 || true
mark_restart
