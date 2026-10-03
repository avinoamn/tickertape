#!/usr/bin/env bash
# Copy a fresh dump of the cluster database to this machine, i.e. off the node: scripts/backup-pull.sh [directory]
# The in-cluster backups live on the same disk as the database; this is the copy that survives losing the node.
# Read-only on the cluster (pg_dump through `kubectl exec`). The dump holds third-party news text: keep it private.
# Default directory: $TICKERTAPE_BACKUP_DIR or ~/tickertape-backups. Then sync that folder wherever you keep backups.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
dir="${1:-${TICKERTAPE_BACKUP_DIR:-$HOME/tickertape-backups}}"
mkdir -p "$dir"
file="$dir/tickertape-$(date -u +%Y%m%dT%H%M%SZ).dump"

"$root/scripts/kc.sh" exec -n tickertape postgres-0 -- pg_dump -U tickertape -Fc tickertape > "$file.partial"

# Check the archive is readable and holds every table before it is allowed to look like a backup.
listing=$(MSYS_NO_PATHCONV=1 docker run --rm -i postgres:16-alpine pg_restore --list < "$file.partial") \
  || { rm -f "$file.partial"; echo "the dump is not a readable archive" >&2; exit 1; }
for table in items decisions feed_state; do
  grep -q "TABLE DATA public $table " <<< "$listing" || { rm -f "$file.partial"; echo "the dump has no data for $table" >&2; exit 1; }
done
mv "$file.partial" "$file"
echo "saved $file ($(wc -c < "$file" | tr -d ' ') bytes), verified readable"
