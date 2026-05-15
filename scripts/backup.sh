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

# ─── Offsite copy (optional, env-gated) ─────────────────────────────────────
# 设了 COS_BACKUP_DIR（cosfs 挂载点下的目录）就把本次 .gz 再放一份到对象存储。
# 关键：cosfs 运行中可能掉挂，掉挂后挂载点退化成本地空目录——绝不能把"异地副本"
# 静默写进本地磁盘（那等于没有异地备份却以为有）。所以先验证它确是 fuse.cosfs
# 活挂载，否则只告警、跳过；本地备份始终保留，不受影响。
if [[ -n "${COS_BACKUP_DIR:-}" ]]; then
  mnt="$(df -P "$COS_BACKUP_DIR" 2>/dev/null | awk 'NR==2{print $6}')" || true
  if [[ -n "$mnt" ]] && mountpoint -q "$mnt" && mount | grep -q "on ${mnt} type fuse.cosfs"; then
    mkdir -p "$COS_BACKUP_DIR"
    if cp "${out}.gz" "${COS_BACKUP_DIR}/"; then
      local_sz=$(stat -c%s "${out}.gz")
      remote_sz=$(stat -c%s "${COS_BACKUP_DIR}/$(basename "${out}.gz")" 2>/dev/null || echo 0)
      if [[ "$local_sz" == "$remote_sz" ]]; then
        echo "[backup] offsite OK → ${COS_BACKUP_DIR}/$(basename "${out}.gz") (${remote_sz} bytes)"
      else
        echo "[backup] offsite SIZE MISMATCH (local=$local_sz remote=$remote_sz) — check COS!" >&2
      fi
    else
      echo "[backup] offsite copy FAILED (cp error) — local backup kept" >&2
    fi
  else
    echo "[backup] offsite SKIPPED: $COS_BACKUP_DIR not a live cosfs mount — local backup kept" >&2
  fi
fi

echo "[backup] done"
