#!/bin/sh
# Prove that the newest backup restores: restore it into a scratch database on the same server, check that the tables
# have data, and that the backup is recent. Exits non-zero otherwise, so the Job fails and kube-state-metrics shows it.
# Connection settings come from the standard libpq variables: PGHOST, PGUSER, PGPASSWORD, PGDATABASE.
set -eu

dir=${BACKUP_DIR:-/backups}
max_age_hours=${BACKUP_MAX_AGE_HOURS:-36}
scratch=restore_test

fail() {
  echo "{\"svc\": \"restore-test\", \"status\": \"error\", \"error\": \"$1\"}"
  exit 1
}

latest=$(ls -1t "$dir"/tickertape-*.dump 2>/dev/null | head -n 1 || true)
[ -n "$latest" ] || fail "no backup found in $dir"

age_hours=$(( ( $(date +%s) - $(stat -c %Y "$latest") ) / 3600 ))
[ "$age_hours" -le "$max_age_hours" ] || fail "newest backup $(basename "$latest") is $age_hours h old (limit $max_age_hours h): backups have stopped"

admin() { PGOPTIONS="-c client_min_messages=warning" PGDATABASE=postgres psql -v ON_ERROR_STOP=1 -qAt "$@"; }
trap 'admin -c "DROP DATABASE IF EXISTS $scratch" > /dev/null 2>&1 || true' EXIT

admin -c "DROP DATABASE IF EXISTS $scratch" > /dev/null
admin -c "CREATE DATABASE $scratch" > /dev/null

pg_restore --no-owner -d "$scratch" "$latest" || fail "pg_restore of $(basename "$latest") failed"

rows=""
for table in items decisions feed_state; do
  restored=$(PGDATABASE=$scratch psql -v ON_ERROR_STOP=1 -qAt -c "select count(*) from $table") || fail "table $table is missing in the restored database"
  live=$(psql -v ON_ERROR_STOP=1 -qAt -c "select count(*) from $table")
  if [ "$live" -gt 0 ] && [ "$restored" -eq 0 ]; then
    fail "table $table has $live rows but none in the backup"
  fi
  rows="${rows}${rows:+, }\"$table\": $restored"
done

echo "{\"svc\": \"restore-test\", \"status\": \"ok\", \"file\": \"$(basename "$latest")\", \"age_hours\": $age_hours, \"rows\": {$rows}}"
