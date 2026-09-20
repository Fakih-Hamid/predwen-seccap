#!/bin/sh
. "$(dirname -- "$0")/_lib.sh"

trap cleanup_build_tree EXIT INT TERM HUP

EXPECTED=${1:-}
[ -n "$EXPECTED" ] || die "usage: deploy.sh <expected-commit-sha>"

require_env_file
command -v docker >/dev/null || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "docker compose v2 is required"

cd "$REPO_ROOT"
HEAD_SHA=$(git rev-parse HEAD)
case "$HEAD_SHA" in
	"$EXPECTED"*) ;;                 # a short SHA on the command line is fine
	*) die "refusing to deploy: this checkout is at
    $HEAD_SHA
but you asked for
    $EXPECTED
Check out the commit you meant, then run this again.
To run a DIFFERENT commit without moving this checkout, use rollback.sh." ;;
esac

if [ -n "$(git status --porcelain)" ]; then
	git status --short
	die "refusing to deploy: the working tree has uncommitted changes.
What is deployed must be exactly what is committed, or the SHA above is a
label rather than a fact."
fi
say "commit: $HEAD_SHA (clean)"

SHORT=$(git rev-parse --short "$HEAD_SHA")
export SECCAP_IMAGE_TAG="$SHORT"

[ -n "${SECCAP_SITE_ADDRESS:-}" ] || die "SECCAP_SITE_ADDRESS is empty in $ENV_FILE"
[ -n "${SECCAP_BASIC_AUTH_HASH:-}" ] || die "SECCAP_BASIC_AUTH_HASH is empty in $ENV_FILE
Generate it with:
    ./deploy/generate-secrets.sh"
[ -n "${SECCAP_SECRET_KEY:-}" ] || die "SECCAP_SECRET_KEY is empty in $ENV_FILE"
[ -n "${SECCAP_FACILITATOR_PASSWORD_HASH:-}" ] || die "SECCAP_FACILITATOR_PASSWORD_HASH is empty
in $ENV_FILE. That disables the facilitator console, which is the console the
whole exercise is run from."

case "$SECCAP_FACILITATOR_PASSWORD_HASH" in
	*'$'*'$'*) ;;
	*) die "SECCAP_FACILITATOR_PASSWORD_HASH does not look like a Werkzeug hash.
If it lost part of itself, the value in $ENV_FILE is missing its single quotes:
    SECCAP_FACILITATOR_PASSWORD_HASH='scrypt:32768:8:1\$SALT\$HEX'" ;;
esac

if grep -qi 'sakura-vpn-update' "$ENV_FILE"; then
	die "refusing to deploy: $ENV_FILE mentions sakura-vpn-update.
That domain is the COMPROMISED infrastructure inside the scenario. Serving the
platform from it would erase the distinction three missions exist to teach."
fi

compose config >/dev/null || die "compose configuration is invalid"

if app_running; then
	say "the stack is running; taking a backup before touching it"
	"$DEPLOY_DIR/backup.sh"
fi

BEFORE=$(checkout_state)
build_image_at "$HEAD_SHA" "$SHORT"

say "starting"
compose up -d

wait_for_healthz 90 || die "deployment failed: the application never became healthy"

AFTER=$(checkout_state)
[ "$BEFORE" = "$AFTER" ] || die "the checkout moved during the deploy: $BEFORE -> $AFTER"

say ""
say "deployed $HEAD_SHA as predwen-seccap:$SHORT"
say "verify from the outside with:  ./deploy/verify.sh"
