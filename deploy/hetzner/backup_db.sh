#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# OlbosTrade — Daily database backup (with optional off-site copy)
#
# Dumps the Postgres database from the olbostrade-db container, compresses and
# timestamps it, prunes old local dumps, and — if an off-site target is
# configured — copies the dump off the box.
#
# Run manually:        bash deploy/hetzner/backup_db.sh
# Run daily via cron:  see "Cron setup" at the bottom of this file.
#
# Off-site copy (optional) — set ONE of these in backend/.env.prod:
#   BACKUP_RCLONE_REMOTE="myremote:olbostrade-backups"   # rclone (S3/B2/GDrive/…)
#   BACKUP_SCP_TARGET="user@host:/path/olbostrade-backups" # scp over SSH
# Without either, backups are kept locally only (better than nothing, but a disk
# failure loses them — configure an off-site target before live capital).
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Load config (DB password, optional off-site target)
if [[ -f backend/.env.prod ]]; then
  set -a; source backend/.env.prod; set +a
fi

# NOTE: the database was renamed to `olbostrade`; the Postgres ROLE stays
# `olbosquant` deliberately (see the NOTE in docker-compose.hetzner.yml —
# renaming it needs a temporary superuser, not worth it for a value that's
# never user-visible). If your deployment differs, override via env.
DB_CONTAINER="${DB_CONTAINER:-olbostrade-db}"
DB_USER="${POSTGRES_USER:-olbosquant}"
DB_NAME="${POSTGRES_DB:-olbostrade}"
BACKUP_DIR="${BACKUP_DIR:-/opt/olbostrade-backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

umask 077
mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/olbostrade-${STAMP}.sql.gz"
TMP="$(mktemp "$BACKUP_DIR/.olbostrade-${STAMP}.XXXXXX")"
trap 'rm -f -- "$TMP"' EXIT

if [[ -e "$OUT" ]]; then
  echo "Refusing to overwrite existing backup: $OUT" >&2
  exit 1
fi

echo "[1/4] Dumping $DB_NAME from $DB_CONTAINER..."
# pg_dump inside the container; gzip on the host. pipefail makes a dump failure
# fail the whole script. The unpublished temporary file is removed on failure.
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$TMP"
echo "[2/4] Verifying and publishing the new dump..."
gzip -t "$TMP"
mv -- "$TMP" "$OUT"
SIZE="$(du -h "$OUT" | cut -f1)"
echo "      ✅ Wrote $OUT ($SIZE)"

echo "[3/4] Off-site copy..."
if [[ -n "${BACKUP_RCLONE_REMOTE:-}" ]]; then
  rclone copy "$OUT" "$BACKUP_RCLONE_REMOTE"
  echo "      ✅ rclone → $BACKUP_RCLONE_REMOTE"
elif [[ -n "${BACKUP_SCP_TARGET:-}" ]]; then
  scp -q "$OUT" "$BACKUP_SCP_TARGET"
  echo "      ✅ scp → $BACKUP_SCP_TARGET"
else
  echo "      ⚠ No off-site target set (BACKUP_RCLONE_REMOTE / BACKUP_SCP_TARGET) — local only."
fi

echo "[4/4] Pruning local backups older than ${RETENTION_DAYS} days..."
# Never remove the previous good copy until this dump has been verified and
# the configured off-site transfer has succeeded.
find "$BACKUP_DIR" -name 'olbostrade-*.sql.gz' -type f -mtime "+${RETENTION_DAYS}" -delete
echo "      ✅ Pruned"

echo ""
echo "  ✅ Backup complete: $OUT"

# ───────────────────────────────────────────────────────────────────────────────
# Cron setup (run once on the server):
#   crontab -e
# then add — daily at 06:10 UTC, logging to a file:
#   10 6 * * * /usr/bin/bash /opt/olbostrade/deploy/hetzner/backup_db.sh >> /var/log/olbostrade-backup.log 2>&1
#
# IMPORTANT: if you had an existing crontab entry pointing at the old
# legacy app path, update it after the directory rename — cron does not
# follow renames.
#
# Do not use the live database for a restore drill. Restore into a disposable
# isolated database and record recovery evidence first; see
# docs/PAPER_PILOT_READINESS.md. A production restore requires a separate
# incident decision, verified target, and broker reconciliation plan.
# ───────────────────────────────────────────────────────────────────────────────
