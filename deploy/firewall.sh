#!/bin/sh
set -eu

SSH_PORT=${SSH_PORT:-22}

[ "$(id -u)" = "0" ] || { echo "run this with sudo" >&2; exit 1; }
command -v ufw >/dev/null || { echo "ufw is not installed: apt-get install -y ufw" >&2; exit 1; }

echo "resetting to a deny-by-default policy"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing

echo "allowing SSH on $SSH_PORT (rate-limited)"
ufw limit "$SSH_PORT"/tcp comment 'SSH'

echo "allowing HTTP and HTTPS for Caddy"
ufw allow 80/tcp   comment 'HTTP: ACME challenge and the redirect to HTTPS'
ufw allow 443/tcp  comment 'HTTPS'
ufw allow 443/udp  comment 'HTTP/3 (QUIC)'

ufw --force enable
ufw status verbose

cat <<'NOTE'

--------------------------------------------------------------------------
Two things this does NOT do, both worth knowing:

1. DOCKER PUBLISHES PORTS AROUND UFW. Docker writes its own iptables rules in
   the FORWARD chain, which ufw's INPUT policy never sees. A container that
   publishes a host port is reachable from the internet even though ufw says
   "deny incoming".

   That is fine here because ONLY Caddy publishes ports, and 80/443 are
   exactly what should be open. It stops being fine the moment somebody adds
   a `ports:` entry to the seccap service "just to check something" — that
   would put the application on the internet with no Basic Auth in front of
   it. deploy/compose.prod.yml uses `expose:` for the app for this reason,
   and deploy/verify.sh fails if a published port ever appears.

2. IT DOES NOT TOUCH THE PROVIDER FIREWALL. If a Vultr Firewall Group is
   attached to the instance, its rules apply before these do. Either leave
   the instance without one, or open 22, 80 and 443 there too.
--------------------------------------------------------------------------
NOTE
