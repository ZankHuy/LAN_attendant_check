#!/bin/sh
# Daily backup loop for CheckNV SQLite database.
# Mounted into the `backup` container in docker-compose.yml.

if ! command -v sqlite3 >/dev/null 2>&1; then
  apk add --no-cache sqlite >/dev/null 2>&1 || true
fi

while true; do
  if [ -f /data/checknv.db ]; then
    ts=$(date +%Y%m%d_%H%M%S)
    if command -v sqlite3 >/dev/null 2>&1; then
      # .backup is atomic and safe even when the DB is being written to.
      sqlite3 /data/checknv.db ".backup /backups/checknv_${ts}.db"
    else
      # Fallback: plain file copy. Risk of partial writes if app is active,
      # acceptable for read-mostly LAN usage.
      cp /data/checknv.db "/backups/checknv_${ts}.db"
    fi
    echo "[backup] checknv_${ts}.db created"
    # Keep last 30 days.
    find /backups -name "checknv_*.db" -mtime +30 -delete
  else
    echo "[backup] /data/checknv.db not found, skipping"
  fi
  sleep 86400
done
