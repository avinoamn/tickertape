#!/bin/sh
# Dump the database into $BACKUP_DIR and keep the newest $BACKUP_KEEP dumps. Runs in the postgres image (busybox sh).
# Connection settings come from the standard libpq variables: PGHOST, PGUSER, PGPASSWORD, PGDATABASE.
set -eu

dir=${BACKUP_DIR:-/backups}
keep=${BACKUP_KEEP:-14}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
partial="$dir/.tickertape-$stamp.partial"
final="$dir/tickertape-$stamp.dump"

# A failed run must not leave a half-written file that looks like a backup.
trap 'rm -f "$partial" "$partial.list"' EXIT

pg_dump -Fc -f "$partial"

# The archive must be readable and contain the data of every table, before it is allowed to replace anything.
pg_restore --list "$partial" > "$partial.list"
for table in items decisions feed_state; do
  grep -q "TABLE DATA public $table " "$partial.list" \
    || { echo "{\"svc\": \"backup\", \"status\": \"error\", \"error\": \"dump has no data section for $table\"}"; exit 1; }
done

mv "$partial" "$final"

# Retention: newest first, drop everything after the first $keep.
removed=0
for old in $(ls -1t "$dir"/tickertape-*.dump | tail -n +"$((keep + 1))"); do
  rm -f "$old"
  removed=$((removed + 1))
done

kept=$(ls -1 "$dir"/tickertape-*.dump | wc -l | tr -d ' ')
bytes=$(wc -c < "$final" | tr -d ' ')
echo "{\"svc\": \"backup\", \"status\": \"ok\", \"file\": \"$(basename "$final")\", \"bytes\": $bytes, \"kept\": $kept, \"removed\": $removed}"
