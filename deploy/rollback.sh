#!/bin/sh
. "$(dirname -- "$0")/_lib.sh"

trap cleanup_build_tree EXIT INT TERM HUP

TARGET=${1:-}
[ -n "$TARGET" ] || die "usage: rollback.sh <commit-sha-to-run>"
require_env_file

cd "$REPO_ROOT"
git cat-file -e "${TARGET}^{commit}" 2>/dev/null || die "unknown commit: $TARGET
Fetch first if it is not in this clone:  git fetch origin"
TO=$(git rev-parse "$TARGET")
SHORT=$(git rev-parse --short "$TO")

RUNNING=""
if app_running; then
	RUNNING=$(docker inspect -f '{{.Config.Image}}' "$(app_container)" 2>/dev/null || true)
fi
say "currently running: ${RUNNING:-nothing}"
say "rolling to:        $TO"

CHECKOUT_BEFORE=$(checkout_state)
VOLUME=$(data_volume) || die "no data volume found"
VOL_CREATED=$(docker volume inspect -f '{{.CreatedAt}}' "$VOLUME")
TEAMS_BEFORE=""
if app_running; then
	say "taking a backup before the rollback"
	"$DEPLOY_DIR/backup.sh"
	TEAMS_BEFORE=$(team_count)
	say "before: volume=$VOLUME teams=$TEAMS_BEFORE"
fi

export SECCAP_IMAGE_TAG="$SHORT"
build_image_at "$TO" "$SHORT"

compose up -d                 # recreates the containers, reattaches the volume
wait_for_healthz 90 || die "rollback finished but the application is not healthy"

VOL_NOW=$(data_volume) || die "the data volume is gone after the rollback"
[ "$VOL_NOW" = "$VOLUME" ] || die "the volume changed: $VOLUME -> $VOL_NOW"
[ "$(docker volume inspect -f '{{.CreatedAt}}' "$VOLUME")" = "$VOL_CREATED" ] \
	|| die "the volume was recreated; this is not the data you had"

CHECKOUT_AFTER=$(checkout_state)
[ "$CHECKOUT_BEFORE" = "$CHECKOUT_AFTER" ] \
	|| die "the checkout moved: $CHECKOUT_BEFORE -> $CHECKOUT_AFTER"

if [ -n "$TEAMS_BEFORE" ]; then
	TEAMS_AFTER=$(team_count)
	[ "$TEAMS_BEFORE" = "$TEAMS_AFTER" ] \
		|| die "teams went from $TEAMS_BEFORE to $TEAMS_AFTER across a CODE rollback"
	say "after:  volume=$VOLUME teams=$TEAMS_AFTER (unchanged)"
fi

say ""
say "now running $TO as predwen-seccap:$SHORT"
say "the checkout is untouched, still at ${CHECKOUT_AFTER%% *}"
say "to go forward again:  ./deploy/rollback.sh <newer sha>"
