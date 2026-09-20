#!/bin/sh
. "$(dirname -- "$0")/_lib.sh"

require_env_file

BACKUP_PROGRAM="$REPO_ROOT/scripts/backup_db.py"
[ -f "$BACKUP_PROGRAM" ] || die "missing $BACKUP_PROGRAM"

MODE=live
case "${1:-}" in
	"") ;;
	--offline) MODE=offline ;;
	*) die "usage: backup.sh [--offline]" ;;
esac

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST_DIR=${SECCAP_BACKUP_DIR:-$REPO_ROOT/backups}
mkdir -p "$DEST_DIR"

n=1
while :; do
	DEST="$DEST_DIR/seccap-$STAMP-$n.db"
	[ -e "$DEST" ] || break
	n=$((n + 1))
	[ "$n" -lt 1000 ] || die "1000 backups in one second at $STAMP; something is looping"
done
INNER="/tmp/seccap-$STAMP-$n.db"

if [ "$MODE" = offline ]; then
	app_running || say "note: the application was already stopped"
	say "stopping the application for a quiescent copy"
	compose stop seccap
	INNER="/data/seccap-backup-$STAMP-$n.db"
	VOLUME=$(data_volume) || die "no data volume found"
	docker run --rm -i --user root \
		-v "$VOLUME":/data \
		-e SECCAP_DATABASE_URI="sqlite:////data/seccap.db" \
		--entrypoint python "$(app_image)" - "$INNER" < "$BACKUP_PROGRAM"
	cid=$(compose ps -aq seccap)
	[ -n "$cid" ] || die "no seccap container to copy from"
	docker cp "$cid:$INNER" "$DEST"
	docker run --rm --user root -v "$VOLUME":/data \
		--entrypoint rm "$(app_image)" -f "$INNER"
	say "starting the application again"
	compose start seccap
	wait_for_healthz 90 || die "the application did not come back after the backup"
else
	app_running || die "the application is not running.
Start it, or take the backup with --offline."
	cid=$(compose ps -q seccap)
	docker exec -i "$cid" python - "$INNER" < "$BACKUP_PROGRAM"
	docker cp "$cid:$INNER" "$DEST"
	docker exec "$cid" rm -f "$INNER"
fi

python3 - "$DEST" <<'PY'
import sqlite3, sys, os
path = sys.argv[1]
conn = sqlite3.connect(path)
try:
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    tables = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    teams = conn.execute("SELECT count(*) FROM teams").fetchone()[0]
    events = conn.execute("SELECT count(*) FROM score_events").fetchone()[0]
finally:
    conn.close()
assert tables > 0, "the copy has no tables"
print("backup: %s (%d bytes, %d tables, %d teams, %d score events)"
      % (path, os.path.getsize(path), tables, teams, events))
PY

say ""
say "backups on this host:"
ls -1t "$DEST_DIR" | head -10
say ""
say "Copy it off the VPS. A backup that only exists on the machine it protects"
say "is not a backup:"
say "    scp <user>@<host>:$DEST ."
