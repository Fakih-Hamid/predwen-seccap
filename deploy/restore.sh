#!/bin/sh
. "$(dirname -- "$0")/_lib.sh"

SOURCE=${1:-}
[ -n "$SOURCE" ] || die "usage: restore.sh <backup.db>"
[ -f "$SOURCE" ] || die "no such backup: $SOURCE"
require_env_file

SOURCE_ABS=$(CDPATH= cd -- "$(dirname -- "$SOURCE")" && pwd)/$(basename -- "$SOURCE")

STAGED=".restore-incoming.db"

SWAP='
set -e
cd /data
[ -f '"$STAGED"' ] || { echo "staged copy is missing"; exit 1; }
rm -f seccap.db.prev seccap.db-wal.prev seccap.db-shm.prev
if [ -f seccap.db ];     then mv seccap.db     seccap.db.prev;     fi
if [ -f seccap.db-wal ]; then mv seccap.db-wal seccap.db-wal.prev; fi
if [ -f seccap.db-shm ]; then mv seccap.db-shm seccap.db-shm.prev; fi
mv '"$STAGED"' seccap.db
chown 10001:10001 seccap.db
chmod 640 seccap.db
echo "swap: done"
'

REVERT='
set -e
cd /data
rm -f '"$STAGED"'
if [ -f seccap.db.prev ]; then
	rm -f seccap.db seccap.db-wal seccap.db-shm
	mv seccap.db.prev seccap.db
	if [ -f seccap.db-wal.prev ]; then mv seccap.db-wal.prev seccap.db-wal; fi
	if [ -f seccap.db-shm.prev ]; then mv seccap.db-shm.prev seccap.db-shm; fi
	chown 10001:10001 seccap.db
	chmod 640 seccap.db
	echo "revert: the previous database is back"
else
	echo "revert: nothing to put back"
fi
'

COMMIT='
set -e
cd /data
rm -f seccap.db.prev seccap.db-wal.prev seccap.db-shm.prev '"$STAGED"'
echo "commit: previous copy removed"
'

RESTORE_DONE=0
APP_WAS_STOPPED=0

on_exit() {
	if [ "$RESTORE_DONE" = "1" ]; then return 0; fi
	if [ "$APP_WAS_STOPPED" != "1" ]; then return 0; fi

	say ""
	say "RESTORE FAILED — putting the previous database back and restarting."
	if on_volume "$REVERT"; then
		say "the previous database is in place"
	else
		say "WARNING: the revert itself failed. The previous database is on the"
		say "volume as /data/seccap.db.prev and has not been deleted."
	fi
	compose up -d >/dev/null 2>&1 || true
	if wait_for_healthz 120; then
		say "the application is back up on the previous database."
	else
		say "WARNING: the application did not come back. Check:"
		say "    docker compose -f $COMPOSE_FILE --env-file $ENV_FILE logs seccap"
	fi
}
trap on_exit EXIT INT TERM HUP

python3 - "$SOURCE_ABS" <<'PY'
import sqlite3, sys
path = sys.argv[1]
conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
try:
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("restore: %s fails integrity_check" % path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = {"event_sessions", "teams", "members", "score_events"} - names
    if missing:
        raise SystemExit("restore: %s is not a Predwen database (missing %s)"
                         % (path, ", ".join(sorted(missing))))
    teams = conn.execute("SELECT count(*) FROM teams").fetchone()[0]
    events = conn.execute("SELECT count(*) FROM score_events").fetchone()[0]
finally:
    conn.close()
print("restore: candidate looks sound (%d teams, %d score events)" % (teams, events))
PY

VOLUME=$(data_volume) || die "no data volume found; has the stack ever been started?"
IMAGE=$(app_image)
say "target volume: $VOLUME"

ACTIVE=no
REASON=""
if app_running; then
	ACTIVE=yes
	REASON="the application is running"
else
	existing=$(docker run --rm --user root --entrypoint python \
		-v "$VOLUME":/data "$IMAGE" -c "
import os, sqlite3
p = '/data/seccap.db'
if not os.path.exists(p):
    print(0)
else:
    c = sqlite3.connect(p)
    try:
        print(c.execute('SELECT count(*) FROM teams').fetchone()[0])
    except Exception:
        print(0)
    finally:
        c.close()
" 2>/dev/null | tr -d '\r' | tail -1)
	if [ -n "$existing" ] && [ "$existing" != "0" ]; then
		ACTIVE=yes
		REASON="the volume already holds $existing teams"
	fi
fi

if [ "$ACTIVE" = yes ]; then
	say ""
	say "REFUSING SILENTLY IS THE POINT: $REASON."
	say "Restoring $SOURCE_ABS will discard everything recorded since it was taken."
	if [ "${SECCAP_RESTORE_CONFIRM:-}" = "OVERWRITE" ]; then
		say "SECCAP_RESTORE_CONFIRM=OVERWRITE given; proceeding."
	elif [ -t 0 ]; then
		printf 'Type OVERWRITE to continue: '
		read -r answer
		[ "$answer" = "OVERWRITE" ] || die "not confirmed; nothing was changed"
	else
		die "not confirmed, and there is no terminal to ask on; nothing was changed.
Re-run with SECCAP_RESTORE_CONFIRM=OVERWRITE if this is really what you want."
	fi
fi

if [ "$ACTIVE" = yes ]; then
	say "taking a backup of the CURRENT database before replacing it"
	if app_running; then
		"$DEPLOY_DIR/backup.sh"
	else
		"$DEPLOY_DIR/backup.sh" --offline
	fi
fi

say "staging the candidate onto the volume"
docker run --rm --user root --entrypoint sh \
	-v "$VOLUME":/data \
	-v "$SOURCE_ABS":/restore-source.db:ro \
	"$IMAGE" -c '
		set -e
		cp /restore-source.db /data/'"$STAGED"'
		chown 10001:10001 /data/'"$STAGED"'
		chmod 640 /data/'"$STAGED"'
	'

say "stopping the application"
compose stop seccap
APP_WAS_STOPPED=1

case "${SECCAP_RESTORE_INJECT_FAILURE:-}" in
	after-stop) die "injected failure: after the stop, before the swap" ;;
esac

on_volume "$SWAP"

case "${SECCAP_RESTORE_INJECT_FAILURE:-}" in
	after-swap) die "injected failure: after the swap, before the start" ;;
esac

say "starting the application"
compose up -d
wait_for_healthz 120 || die "the restored database did not come up healthy"

on_volume "$COMMIT"
RESTORE_DONE=1

say ""
compose exec -T seccap python -c "
import urllib.request, json
b = json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5))
print('after restore: teams=%s members=%s session_active=%s'
      % (b['teams'], b['members'], b['session_active']))
"
say "restored from $SOURCE_ABS"
