#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Install a 5-minutely container memory sampler as a cron job.
#
# Why this exists: on 2026-09-17 the backend was found to have been OOM-killed
# five times since 09-14, at a consistent ~1530M against a 1500M cgroup cap,
# while the host had gigabytes free. The cap was raised to 3000M — but at rest
# the backend sits near 322M, so something grows it ~1.2G and ONE of those kills
# landed two minutes after a restart. That does not look like a slow leak, and
# a single snapshot cannot tell a leak from a heavy scheduled job.
#
# This log answers it. A steady ramp is a leak; flat-then-spike is a job.
# They need different fixes, and without this we would just be raising the
# number again.
#
# Run once on the server:
#   bash deploy/hetzner/install_mem_sampler.sh
#
# Read it after a day:
#   grep -A6 olbostrade-backend /var/log/olbostrade-mem.log | tail -60
#
# Remove with:
#   crontab -l | grep -v olbostrade-mem | crontab -
# ═══════════════════════════════════════════════════════════════════════════════
# Deliberately not `set -e`: an empty crontab makes `grep` exit 1, which would
# abort before installing anything. Errors are handled inline.
set -uo pipefail

LOG="/var/log/olbostrade-mem.log"
CRON_LINE="*/5 * * * * { date -u; docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.MemPerc}}'; } >> $LOG 2>&1"

if ! command -v crontab >/dev/null 2>&1; then
  echo "ERROR: crontab not installed.  apt-get install -y cron" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found on PATH — this belongs on the server, not in a container." >&2
  exit 1
fi

# Keep every existing entry (the IBKR watchdog and the DB backup live here too)
# and replace only ours. `|| true` so an empty crontab is not an error.
current="$(crontab -l 2>/dev/null || true)"
kept="$(printf '%s\n' "$current" | grep -v 'olbostrade-mem' | sed '/^[[:space:]]*$/d' || true)"
{ [ -n "$kept" ] && printf '%s\n' "$kept"; echo "$CRON_LINE"; } | crontab -
if [ $? -ne 0 ]; then
  echo "ERROR: failed to write crontab." >&2
  exit 1
fi

echo "Installed memory sampler — every 5 minutes, logging to $LOG"
echo "Full crontab now:"
crontab -l

# A cron entry that never fires is worse than none, because it reads as covered.
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet cron 2>/dev/null || systemctl is-active --quiet crond 2>/dev/null; then
    echo "cron daemon: active"
  else
    echo "cron daemon NOT active — attempting to start..."
    systemctl enable --now cron 2>/dev/null \
      || systemctl enable --now crond 2>/dev/null \
      || echo "  Could not auto-start cron. Check: systemctl status cron"
  fi
fi

# Rotation: this writes ~6 lines every 5 minutes forever. /var/log/journal had
# already reached 3.2G on this host from unrotated growth; do not add another.
if [ -d /etc/logrotate.d ]; then
  cat > /etc/logrotate.d/olbostrade-mem <<'ROTATE'
/var/log/olbostrade-mem.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
ROTATE
  echo "Installed logrotate policy: weekly, keep 4."
else
  echo "WARNING: no /etc/logrotate.d — $LOG will grow unbounded."
fi

echo ""
echo "Sample it now to confirm it works:"
echo "  { date -u; docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'; }"
