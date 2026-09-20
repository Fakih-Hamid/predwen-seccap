set -eu

DEPLOY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$DEPLOY_DIR/.." && pwd)
COMPOSE_FILE="$DEPLOY_DIR/compose.prod.yml"
ENV_FILE=${SECCAP_ENV_FILE:-$DEPLOY_DIR/.env.production}

die() { echo "$@" >&2; exit 1; }
say() { echo "$@"; }

require_env_file() {
	[ -f "$ENV_FILE" ] || die "no environment file at $ENV_FILE
Generate one, on the server:
    ./deploy/generate-secrets.sh"

	mode=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo "")
	case "$mode" in
		600|400) ;;
		"") ;;                       # stat unavailable; not worth failing over
		*) say "warning: $ENV_FILE is mode $mode; chmod 600 it" ;;
	esac

	PYTHON=$(command -v python3 || command -v python || true)
	[ -n "$PYTHON" ] || die "python3 is required to validate $ENV_FILE"
	"$PYTHON" "$DEPLOY_DIR/validate_env.py" "$ENV_FILE" \
		|| die "refusing to load $ENV_FILE"

	set -a
	. "$ENV_FILE"
	set +a
}

compose() {
	docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

app_container() {
	compose ps -q seccap 2>/dev/null || true
}

app_running() {
	id=$(compose ps --status running -q seccap 2>/dev/null || true)
	[ -n "$id" ]
}

BUILD_ROOT=""
BUILD_TREE=""

cleanup_build_tree() {
	if [ -n "$BUILD_TREE" ]; then
		git -C "$REPO_ROOT" worktree remove --force "$BUILD_TREE" >/dev/null 2>&1 || true
		git -C "$REPO_ROOT" worktree prune >/dev/null 2>&1 || true
	fi
	if [ -n "$BUILD_ROOT" ] && [ -d "$BUILD_ROOT" ]; then
		rm -rf "$BUILD_ROOT"
	fi
	BUILD_TREE=""
	BUILD_ROOT=""
}

build_image_at() {
	_sha=$1
	_tag=$2
	git -C "$REPO_ROOT" cat-file -e "${_sha}^{commit}" 2>/dev/null \
		|| die "unknown commit: $_sha
If it is not in this clone yet:  git fetch origin"

	BUILD_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/seccap-build-XXXXXX")
	BUILD_TREE="$BUILD_ROOT/src"
	git -C "$REPO_ROOT" worktree add --detach "$BUILD_TREE" "$_sha" >/dev/null

	say "building predwen-seccap:$_tag from $_sha (temporary worktree)"
	docker build -t "predwen-seccap:$_tag" "$BUILD_TREE"
	cleanup_build_tree
}

checkout_state() {
	printf '%s %s' \
		"$(git -C "$REPO_ROOT" rev-parse HEAD)" \
		"$(git -C "$REPO_ROOT" symbolic-ref -q HEAD || echo DETACHED)"
}

data_volume() {
	cid=$(compose ps -aq seccap 2>/dev/null | head -1 || true)
	if [ -n "$cid" ]; then
		vol=$(docker inspect -f \
			'{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' \
			"$cid" 2>/dev/null || true)
		if [ -n "$vol" ]; then echo "$vol"; return 0; fi
	fi
	vol="${COMPOSE_PROJECT_NAME:-predwen-seccap}_seccap-data"
	docker volume inspect "$vol" >/dev/null 2>&1 || return 1
	echo "$vol"
}

app_image() {
	cid=$(compose ps -aq seccap 2>/dev/null | head -1 || true)
	if [ -n "$cid" ]; then
		img=$(docker inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null || true)
		if [ -n "$img" ]; then echo "$img"; return 0; fi
	fi
	echo "predwen-seccap:${SECCAP_IMAGE_TAG:-rc}"
}

on_volume() {
	_vol=$(data_volume) || die "no data volume found"
	_img=$(app_image)
	docker run --rm --user root --entrypoint sh -v "$_vol":/data "$_img" -c "$1"
}

wait_for_healthz() {
	tries=${1:-60}
	i=0
	while [ "$i" -lt "$tries" ]; do
		if compose exec -T seccap python -c \
			"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)" \
			>/dev/null 2>&1; then
			say "healthz: ok after ${i}s"
			return 0
		fi
		i=$((i + 1))
		sleep 1
	done
	say "healthz: never became available after ${tries}s"
	compose logs --tail 60 seccap || true
	return 1
}

team_count() {
	compose exec -T seccap python -c \
		"import urllib.request,json;print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=5))['teams'])" \
		2>/dev/null | tr -d '\r' | tail -1
}
