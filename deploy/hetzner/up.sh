#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# OlbosTrade — First-time start on Hetzner
#
# Run this ONCE after cloning the repo and filling in backend/.env.prod
# From /opt/olbostrade on the server:
#   bash deploy/hetzner/up.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# ── Checks ────────────────────────────────────────────────────────────────────
if [[ ! -f backend/.env.prod ]]; then
  echo "❌  Missing backend/.env.prod"
  echo "    Copy and fill in the template first:"
  echo "    cp deploy/hetzner/.env.example backend/.env.prod && nano backend/.env.prod"
  exit 1
fi

# Load env so docker compose can substitute variables
set -a
source backend/.env.prod
set +a

# Anything that makes this deployment less than fully working sets this. The
# script keeps going — the containers are up and tearing them down would be
# worse — but it must not end with a green banner and exit 0 when it isn't
# true. Reported as a failed deploy reads as something to investigate; a ✅
# reads as done.
DEPLOY_OK=1
PROBLEMS=()

problem() {
  DEPLOY_OK=0
  PROBLEMS+=("$1")
}

# docker_default is declared `external: true` in the compose file, so Compose
# will NOT create it — it errors out instead. It is external on purpose:
# ibkr-gateway belongs to a separate compose project and sits on this network,
# and IBKR_HOST=ibkr-gateway resolves over it, so `docker compose down` here
# must never remove it.
#
# It used to be created by the OlbosTerminal stack. That application has been
# deleted, so nothing recreates it if it is ever removed.
#
# Created rather than hard-failed, deliberately. The network EXISTING is not
# the property that matters — ibkr-gateway being ATTACHED to it is — and
# refusing to start because the network is absent would block a legitimate
# first deploy on a fresh host, where nothing has created it either. So it is
# created if missing, and the thing that actually matters is checked
# separately below.
if ! docker network ls --format '{{.Name}}' | grep -qE '^docker_default$'; then
  echo "      docker_default missing — creating it"
  docker network create docker_default >/dev/null
fi
echo "      Using network: docker_default"

# THE CHECK THAT MATTERS. IBKR_HOST=ibkr-gateway is a Docker DNS name that
# resolves only while that container is attached here. If it is not, the
# backend starts, serves, and reports healthy — with no route to the broker.
# A deploy that says ✅ in that state is the failure mode worth guarding:
# nothing looks wrong until an order does not go anywhere.
if docker network inspect docker_default \
     -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null \
     | grep -qw 'ibkr-gateway'; then
  echo "      ✅ ibkr-gateway attached"
else
  echo "      ⚠️  ibkr-gateway is NOT attached to docker_default."
  echo "         IBKR_HOST=${IBKR_HOST:-ibkr-gateway} will not resolve, so the"
  echo "         backend will come up with no route to the broker."
  echo "         Start the ibkr-gateway stack, then re-run this script."
  problem "ibkr-gateway not attached to docker_default — no broker connection"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  OlbosTrade — Starting on Hetzner"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Build and start containers ─────────────────────────────────────────────
echo "[1/4] Building and starting containers..."
docker compose -f docker-compose.hetzner.yml up -d --build
echo "      ✅ Containers started"

# ── 2. Wait for backend to be healthy ─────────────────────────────────────────
echo "[2/4] Waiting for backend to be ready (up to 90s)..."
for i in $(seq 1 30); do
  if docker exec olbostrade-backend curl -fsS http://127.0.0.1:8000/api/guardrails/status > /dev/null 2>&1; then
    echo "      ✅ Backend healthy"
    break
  fi
  if [[ $i -eq 30 ]]; then
    echo "      ❌ Backend did not start in time"
    echo "         Check logs: docker logs olbostrade-backend"
    exit 1
  fi
  sleep 3
done

# ── 3. Run database migrations ────────────────────────────────────────────────
echo "[3/4] Running database migrations..."
docker exec olbostrade-backend python3 -m alembic upgrade head
echo "      ✅ Migrations applied"

# ── 4. Confirm Caddy is serving ───────────────────────────────────────────────
# Nothing to paste any more: deploy/hetzner/Caddyfile is a tracked file in this
# repo, mounted read-only into the `caddy` service by the compose file above.
# Until 2026-09-19 this step printed a snippet for the operator to copy into a
# different project's Caddyfile, which is why the config and the app could
# disagree at all.
echo "[4/4] Checking Caddy..."
# Two distinct failures, reported distinctly: a container that is not running
# and a config that does not parse need different first moves, and one message
# covering both sends the operator to the wrong one.
CADDY_STATE="$(docker inspect -f '{{.State.Status}}' olbostrade-caddy 2>/dev/null || echo missing)"
if [[ "$CADDY_STATE" != "running" ]]; then
  echo "      ❌ olbostrade-caddy is '${CADDY_STATE}', not running."
  echo "         Usually a port conflict — something else holds 80 or 443:"
  echo "           ss -ltnp | grep -E ':80 |:443 '"
  echo "         On a server migrating from the old stack, the previous"
  echo "         container may still hold them:"
  echo "           docker stop olbos-caddy && docker rm olbos-caddy"
  echo "         Logs: docker logs olbostrade-caddy --tail 50"
  problem "Caddy container is '${CADDY_STATE}' — HTTPS is down"
elif ! docker exec olbostrade-caddy caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
  echo "      ❌ Caddy is running but its config did not validate."
  echo "         docker exec olbostrade-caddy caddy validate --config /etc/caddy/Caddyfile"
  echo "         Fix deploy/hetzner/Caddyfile IN GIT, pull, then reload:"
  echo "           docker exec olbostrade-caddy caddy reload --config /etc/caddy/Caddyfile"
  problem "Caddy config does not validate — HTTPS may be serving stale config"
else
  echo "      ✅ Caddy running with a valid config"
fi

# `caddy validate` checks syntax and that the container is up. It does NOT
# prove HTTPS works: that needs DNS pointing here and a certificate, neither of
# which exists yet on a first deploy. Step 7 of the README is where HTTPS is
# actually verified, which is why this script stops short of claiming it.

echo ""
if [[ "$DEPLOY_OK" -eq 1 ]]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ✅ OlbosTrade is running"
  echo ""
  echo "  Next: point trade.olbos.us → this server's IP"
  echo "  (DNS-only — disable any CDN proxy, ACME must reach this host),"
  echo "  then open https://trade.olbos.us"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ⚠️  OlbosTrade STARTED WITH PROBLEMS"
  echo ""
  for p in "${PROBLEMS[@]}"; do
    echo "   • $p"
  done
  echo ""
  echo "  The containers are up — they were deliberately left running, because"
  echo "  stopping them makes diagnosis harder and loses nothing. But this is"
  echo "  NOT a working deployment. Fix the above and re-run this script."
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  exit 1
fi
