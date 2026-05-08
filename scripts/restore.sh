#!/usr/bin/env bash
# Restore the SQLite DB from a backup created by backup.sh.
#
# Usage:
#   ./scripts/restore.sh backups/slg_research-20260601T020000Z.db.gz
#
# DESTRUCTIVE: replaces the current database file. The previous file is renamed
# with a .pre-restore suffix so you can roll back manually if needed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${REPO_ROOT}/data/slg_research.db"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <backup-file.db.gz>" >&2
  exit 1
fi

src="$1"
if [[ ! -f "$src" ]]; then
  echo "Backup file not found: $src" >&2
  exit 1
fi

echo "[restore] target: $DB_PATH"
echo "[restore] source: $src"
read -r -p "Replace existing database? [yes/NO] " ans
[[ "$ans" == "yes" ]] || { echo "aborted"; exit 1; }

# Stop backend so it doesn't write during restore
if docker ps --format '{{.Names}}' | grep -q '^slg_backend$'; then
  echo "[restore] stopping slg_backend"
  docker stop slg_backend >/dev/null
  started_with_docker=1
else
  started_with_docker=0
fi

if [[ -f "$DB_PATH" ]]; then
  pre="${DB_PATH}.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
  echo "[restore] saving previous DB → $pre"
  mv "$DB_PATH" "$pre"
fi

echo "[restore] decompressing"
gunzip -c "$src" > "$DB_PATH"

echo "[restore] verifying integrity"
sqlite3 "$DB_PATH" "PRAGMA integrity_check;" | grep -q '^ok$' || { echo "integrity check failed"; exit 1; }

if [[ "$started_with_docker" == "1" ]]; then
  echo "[restore] starting slg_backend"
  docker start slg_backend >/dev/null
fi

echo "[restore] done"
