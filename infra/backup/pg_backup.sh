#!/bin/sh
# Respaldo periódico de PostgreSQL (pg_dump comprimido). Variables: PGHOST, PGUSER, PGPASSWORD, PGDATABASE,
# BACKUP_DIR (/backups), INTERVAL_HOURS (6), KEEP (14).
set -u
DIR="${BACKUP_DIR:-/backups}"; KEEP="${KEEP:-14}"
mkdir -p "$DIR"
while true; do
  f="$DIR/jobhunter-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
  if pg_dump --no-owner "$PGDATABASE" | gzip > "$f.tmp" && gzip -t "$f.tmp"; then
    mv "$f.tmp" "$f"; echo "[backup] ok $(basename "$f")"
    ls -1t "$DIR"/jobhunter-*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
  else
    rm -f "$f.tmp"; echo "[backup] ERROR en pg_dump" >&2
  fi
  [ -n "${ONCE:-}" ] && exit 0
  sleep "$(( ${INTERVAL_HOURS:-6} * 3600 ))"
done
