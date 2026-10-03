#!/usr/bin/env bash
# End-to-end test of the backup scripts shipped in the chart (charts/tickertape/files/backup.sh and restore-test.sh),
# against a throw-away Postgres container: no cluster and no dev database involved. Needs only Docker.
#   scripts/test-backup.sh            (make backup-test)
# It checks: a dump is created and verified, retention keeps the newest N, the weekly restore test passes on a good
# backup and FAILS on a missing, stale or corrupt one, the scratch database is always cleaned up, and the documented
# disaster-recovery command really brings the data back.
set -euo pipefail
cd "$(dirname "$0")/.."

img=${PG_IMAGE:-postgres:16-alpine}
name="tickertape-backup-test-$$"
net="$name"; srv="$name-db"; vol="$name-backups"
export MSYS_NO_PATHCONV=1   # Git Bash must not rewrite /scripts, /backups in docker arguments

cleanup() {
  docker rm -f "$srv" > /dev/null 2>&1 || true
  docker volume rm -f "$vol" > /dev/null 2>&1 || true
  docker network rm "$net" > /dev/null 2>&1 || true
}
trap cleanup EXIT

pass() { echo "  ok: $1"; }
die() { echo "  FAILED: $1" >&2; exit 1; }

sql() { docker exec -i "$srv" psql -U tickertape -d "${DB:-tickertape}" -v ON_ERROR_STOP=1 -qAt "$@"; }

# Run one of the chart's scripts the way the CronJob does: postgres image, uid 70, backups volume, libpq variables.
job() {
  local script=$1; shift
  docker run --rm --network "$net" --user 70:70 -v "$vol:/backups" -v "$PWD/charts/tickertape/files:/scripts:ro" \
    -e PGHOST="$srv" -e PGUSER=tickertape -e PGPASSWORD=test -e PGDATABASE=tickertape "$@" "$img" sh "/scripts/$script"
}
in_volume() { docker run --rm --user root -v "$vol:/backups" "$img" sh -c "$1"; }

echo "== starting a throw-away Postgres"
docker network create "$net" > /dev/null
docker volume create "$vol" > /dev/null
in_volume "chown 70:70 /backups"
docker run -d --name "$srv" --network "$net" -e POSTGRES_USER=tickertape -e POSTGRES_PASSWORD=test -e POSTGRES_DB=tickertape "$img" > /dev/null
for _ in $(seq 1 60); do   # the image restarts the server once after initialising: wait for the second start
  [ "$(docker logs "$srv" 2>&1 | grep -c 'ready to accept connections')" -ge 2 ] && break
  sleep 1
done
sql < charts/tickertape/files/schema.sql > /dev/null
sql -c "insert into items (uid, source, title) values ('u1','t','one'), ('u2','t','two'), ('u3','t','three')" \
    -c "insert into decisions (item_id, question, answer, confidence, model_rev) select id, 'sentiment', 'neu', 0.5, 'test' from items" \
    -c "insert into feed_state (feed_key) values ('f1')"
pass "database ready with 3 items, 3 decisions, 1 feed_state"

echo "== restore test with no backup must fail"
if job restore-test.sh > /tmp/rt.out 2>&1; then die "restore test passed without any backup"; fi
grep -q "no backup found" /tmp/rt.out && pass "fails with 'no backup found'"

echo "== backup"
out=$(job backup.sh)
echo "$out" | grep -q '"status": "ok"' || die "backup did not report ok: $out"
count=$(in_volume 'ls -1 /backups | grep -c "^tickertape-.*\.dump$"')
[ "$count" = 1 ] || die "expected 1 dump, found $count"
[ "$(in_volume 'ls -1a /backups | grep -c partial || true')" = 0 ] || die "a .partial file was left behind"
pass "one verified dump, no partial files"

echo "== restore test on a good backup must pass and clean up"
out=$(job restore-test.sh)
echo "$out" | grep -q '"status": "ok"' || die "restore test failed on a good backup: $out"
echo "$out" | grep -q '"items": 3' || die "restored row count is wrong: $out"
DB=postgres; [ "$(sql -c "select count(*) from pg_database where datname = 'restore_test'")" = 0 ] || die "scratch database was not dropped"
DB=tickertape
pass "restored 3 items into a scratch database, which was dropped afterwards"

echo "== retention keeps the newest N"
for _ in 1 2 3; do sleep 1.1; job backup.sh -e BACKUP_KEEP=2 > /dev/null; done
count=$(in_volume 'ls -1 /backups | grep -c "^tickertape-.*\.dump$"')
[ "$count" = 2 ] || die "expected 2 dumps after pruning, found $count"
pass "4 backups, retention 2 -> 2 left"

echo "== a stale backup must fail the restore test"
in_volume 'ts=$(date -u -d @$(( $(date +%s) - 259200 )) "+%Y-%m-%d %H:%M:%S"); touch -d "$ts" /backups/tickertape-*.dump'   # 3 days old (busybox touch)
if job restore-test.sh > /tmp/rt.out 2>&1; then die "restore test passed on stale backups"; fi
grep -q "backups have stopped" /tmp/rt.out && pass "fails with 'backups have stopped'"
job restore-test.sh -e BACKUP_MAX_AGE_HOURS=1000 | grep -q '"status": "ok"' && pass "passes when the age limit allows it"

echo "== a corrupt backup must fail the restore test"
in_volume 'rm -f /backups/tickertape-*.dump; head -c 3000 /dev/urandom > /backups/tickertape-20990101T000000Z.dump'
if job restore-test.sh > /tmp/rt.out 2>&1; then die "restore test passed on a corrupt backup"; fi
pass "fails on a corrupt dump"
DB=postgres; [ "$(sql -c "select count(*) from pg_database where datname = 'restore_test'")" = 0 ] || die "scratch database left behind after a failure"
DB=tickertape
pass "scratch database cleaned up even after a failure"

echo "== a backup of an empty database is refused"
DB=postgres sql -c "create database empty_db" > /dev/null
if job backup.sh -e PGDATABASE=empty_db > /tmp/bk.out 2>&1; then die "backup accepted a database without the tables"; fi
pass "backup refuses a dump that lacks the tables"

echo "== disaster recovery: the documented restore command brings the data back"
in_volume 'rm -f /backups/tickertape-*.dump'
job backup.sh > /dev/null
sql -c "truncate items cascade" -c "drop table feed_state"
[ "$(sql -c "select count(*) from items")" = 0 ] || die "test setup: items not empty"
dump=$(in_volume 'ls -1 /backups/tickertape-*.dump')
docker run --rm --network "$net" --user 70:70 -v "$vol:/backups:ro" -e PGHOST="$srv" -e PGUSER=tickertape -e PGPASSWORD=test -e PGDATABASE=tickertape \
  "$img" pg_restore --clean --if-exists --no-owner -d tickertape "$dump"
[ "$(sql -c "select count(*) from items")" = 3 ] || die "items were not restored"
[ "$(sql -c "select count(*) from decisions")" = 3 ] || die "decisions were not restored"
[ "$(sql -c "select count(*) from feed_state")" = 1 ] || die "feed_state was not restored"
pass "items, decisions and the dropped feed_state table are back"

echo "all backup checks passed"
