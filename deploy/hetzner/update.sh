#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# OlbosTrade — Pull latest code and redeploy (zero-downtime rolling restart)
#
# Run from /opt/olbostrade on the server:
#   bash deploy/hetzner/update.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

set -a
source backend/.env.prod
set +a

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  OlbosTrade — Updating"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo "[1/5] Pulling latest code..."
git pull origin main
echo "      ✅ Code updated"

echo "[2/5] Rebuilding containers..."
docker compose -f docker-compose.hetzner.yml build --no-cache backend frontend
echo "      ✅ Images rebuilt"

echo "[3/5] Restarting containers..."
docker compose -f docker-compose.hetzner.yml up -d
echo "      ✅ Containers restarted"

echo "[4/5] Running migrations..."
# Wait briefly for backend to come up
sleep 5
docker exec olbostrade-backend python3 -m alembic upgrade head
echo "      ✅ Migrations applied"

echo "[5/5] Reclaiming build cache..."
# The build above passes --no-cache, so BuildKit writes every layer it produces
# and then never reads any of it. Nothing collected that. By 2026-09-17 it had
# reached 40.83GB across 185 entries — 51GB of /var/lib/containerd on a 75GB
# disk, at 82% full, purely from deploys.
#
# `builder prune -af` is safe precisely BECAUSE of --no-cache: a cache the next
# build is explicitly told to ignore has no value to preserve.
#
# `image prune -f` is dangling-only, deliberately. `-a` would evict any image
# without a running container, and this host runs other compose projects
# (ibkr-gateway) whose images would be fair game if it happened to be stopped.
# Rebuilding a tag orphans the image it replaces, so the dangling-only sweep
# already collects exactly this deploy's garbage. Caddy was on that list until
# 2026-09-19 and is now ours, which makes the list shorter but not the reasoning
# weaker: one neighbour is enough for -a to be wrong.
#
# Non-fatal: the deploy succeeded at step 4. `set -e` is on, and failing the
# whole run over cleanup would report a working deployment as broken.
# `--filter until=72h`, not a bare -af. `docker builder prune` operates on the
# DEFAULT BUILDER's cache, which is shared by every compose project on this
# host — not just OlbosTrade. The --no-cache argument for discarding our own
# layers says nothing about ibkr-gateway's cache, and nuking it would force
# expensive full rebuilds elsewhere. 72h clears the accumulation (our
# garbage is regenerated every deploy and is never reused) while sparing
# anything a neighbour has touched recently. Caught in review on PR #64.
CLEANUP_OK=1
docker builder prune -af --filter until=72h \
  || { CLEANUP_OK=0; echo "      ⚠ build cache prune failed"; }
# Also age-filtered. `image prune -f` is dangling-only but still DAEMON-WIDE:
# ibkr-gateway's just-replaced image is dangling too, and someone may be
# holding it for a rollback. 72h matches the builder prune above — our own
# per-deploy garbage ages out, a neighbour's recent work does not.
docker image prune -f --filter until=72h \
  || { CLEANUP_OK=0; echo "      ⚠ dangling image prune failed"; }

# `|| true`: set -euo pipefail is on and this is a diagnostic. An unguarded
# failure here would exit non-zero and report a SUCCESSFUL deploy as failed —
# the exact thing the guards above exist to prevent.
docker system df || true

# Say what happened. An unconditional "✅ Reclaimed" after two failed prunes
# tells the operator the disk problem is handled when nothing was freed.
if [ "$CLEANUP_OK" -eq 1 ]; then
  echo "      ✅ Reclaimed"
else
  echo "      ⚠ Cleanup INCOMPLETE — deploy is fine, but disk was not reclaimed."
  echo "        Check the 'docker system df' output above."
fi

echo ""
echo "  ✅ Update complete"
echo "     docker logs olbostrade-backend -f   ← watch logs"
