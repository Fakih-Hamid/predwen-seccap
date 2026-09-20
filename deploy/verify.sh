#!/bin/sh
. "$(dirname -- "$0")/_lib.sh"

require_env_file
FAILURES=0
fail() { echo "FAIL  $*" >&2; FAILURES=$((FAILURES + 1)); }
pass() { echo "ok    $*"; }

echo "--- disk"
df -h / | tail -n +1
USED=$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')
AVAIL_KB=$(df -P / | awk 'NR==2 {print $4}')
AVAIL_MB=$((AVAIL_KB / 1024))
echo "root filesystem: ${USED}% used, ${AVAIL_MB} MB free"
if [ "$USED" -ge 90 ]; then
	fail "disk is ${USED}% full. Reclaim space before the event: docker image prune -f"
elif [ "$USED" -ge 80 ]; then
	echo "warn  disk is ${USED}% full; 'docker image prune -f' clears old builds"
	pass "disk has ${AVAIL_MB} MB free"
elif [ "$AVAIL_MB" -lt 1024 ]; then
	fail "only ${AVAIL_MB} MB free; an event needs headroom for the database and a backup"
else
	pass "disk has ${AVAIL_MB} MB free (${USED}% used)"
fi

if VOLUME=$(data_volume); then
	echo "data volume: $VOLUME at $(docker volume inspect -f '{{.Mountpoint}}' "$VOLUME")"
fi

echo ""
echo "--- containers"
compose ps
app_running || fail "the seccap container is not running"

echo ""
echo "--- /healthz, from inside the application"
if compose exec -T seccap python -c "
import urllib.request, json, sys
b = json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5))
print(json.dumps(b))
sys.exit(0 if b['status'] == 'ok' and b['database'] == 'ok' else 1)
"; then
	pass "the application is healthy and its database is reachable"
else
	fail "/healthz did not report a healthy database"
fi

ADDR=${SECCAP_SITE_ADDRESS:-}
[ -n "$ADDR" ] || fail "SECCAP_SITE_ADDRESS is not set in $ENV_FILE"

case "$ADDR" in
	:*) BASE="http://127.0.0.1$ADDR" ;;      # the CI shape; no TLS, no ACME
	*)  BASE="https://$ADDR" ;;
esac
echo ""
echo "--- through Caddy at $BASE"

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/" || echo 000)
case "$code" in
	401) pass "an unauthenticated request is refused (401)" ;;
	000) fail "no answer from $BASE — is DNS pointed here, and are 80/443 open?" ;;
	*)   fail "an unauthenticated request returned $code, not 401. BASIC AUTH IS NOT PROTECTING THIS HOST." ;;
esac

if [ -n "${SECCAP_BASIC_AUTH_PLAINTEXT:-}" ]; then
	USER=${SECCAP_BASIC_AUTH_USER:-seccap}
	code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
		-u "$USER:$SECCAP_BASIC_AUTH_PLAINTEXT" "$BASE/" || echo 000)
	if [ "$code" = "200" ]; then
		pass "the shared credentials open the platform (200)"
	else
		fail "the shared credentials returned $code, not 200"
	fi

	body=$(curl -s --max-time 15 -u "$USER:$SECCAP_BASIC_AUTH_PLAINTEXT" "$BASE/healthz" || echo "")
	echo "$body"
	if echo "$body" | grep -q '"status": *"ok"'; then
		pass "/healthz answers through Caddy"
	else
		fail "/healthz through Caddy did not report ok"
	fi

	if curl -sI --max-time 15 -u "$USER:$SECCAP_BASIC_AUTH_PLAINTEXT" "$BASE/" \
		| grep -qi '^strict-transport-security: *max-age=31536000'; then
		pass "HSTS is set (max-age=31536000, no includeSubDomains, no preload)"
	else
		fail "no Strict-Transport-Security header"
	fi
else
	echo "note  set SECCAP_BASIC_AUTH_PLAINTEXT to also test the authenticated path"
fi

echo ""
echo "--- direct exposure"
if curl -s -o /dev/null --max-time 5 http://127.0.0.1:8000/healthz; then
	fail "the application answered on port 8000 directly. It must only be reachable through Caddy."
else
	pass "port 8000 is not published; the application is behind Caddy only"
fi
published=$(compose ps --format '{{json .Publishers}}' seccap 2>/dev/null || true)
if echo "$published" | grep -Eq '"PublishedPort": *[1-9]'; then
	fail "compose publishes a host port for seccap: $published"
else
	pass "compose publishes no host port for seccap"
fi

echo ""
if [ "$FAILURES" -eq 0 ]; then
	echo "verify: everything checked out."
else
	echo "verify: $FAILURES check(s) failed."
	exit 1
fi
