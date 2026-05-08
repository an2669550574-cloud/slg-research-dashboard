#!/usr/bin/env bash
# Backup the SQLite DB used by docker-compose.prod.yml.
#
# Strategy:
#   - Use sqlite3 ".backup" for a consistent online snapshot (no need to stop the container).
#   - Compress with gzip and timestamp.
#   - Keep the most recent N backups (default: 14).
#
# Usage:
#   ./scripts/backup.sh                 # backups → ./backups
#   BACKUP_DIR=/var/backups/slg ./scripts/backup.sh
#   KEEP=30 ./scripts/backup.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${REPO_ROOT}/data/slg_research.db"
BACKUP_DIR="${BACKUP_DIR:-${REPO_ROOT}/backups}"
KEEP="${KEEP:-14}"

if [[ ! -f "$DB_PATH" ]]; then
  echo "DB not found: $DB_PATH" >&2
  echo "Run docker compose -f docker-compose.prod.yml up first to initialize it." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

ts=$(date -u +"%Y%m%dT%H%M%SZ")
out="${BACKUP_DIR}/slg_research-${ts}.db"

echo "[backup] snapshot → $out"
# 优先用宿主机的 sqlite3；没有则进容器内执行
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" ".backup '$out'"
else
  echo "[backup] sqlite3 not on host, using docker exec slg_backend"
  docker exec slg_backend sqlite3 /app/data/slg_research.db ".backup /app/data/_snapshot.db"
  cp "${REPO_ROOT}/data/_snapshot.db" "$out"
  rm -f "${REPO_ROOT}/data/_snapshot.db"
fi

gzip "$out"
echo "[backup] compressed → ${out}.gz"

# Rotate
echo "[backup] rotating; keeping latest $KEEP files"
ls -1t "$BACKUP_DIR"/slg_research-*.db.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r f; do
  echo "  - removing $f"
  rm -f "$f"
done

echo "[backup] done"
