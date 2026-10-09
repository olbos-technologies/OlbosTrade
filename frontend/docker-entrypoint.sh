#!/bin/sh
# OlbosTrade frontend entrypoint.
#
# Writes the nginx config at startup and — when DASH_USER / DASH_PASS are set —
# password-protects the WHOLE app (dashboard + proxied /api) with HTTP Basic Auth.
# This is the access control for direct IP exposure: one browser login covers
# everything reachable on the published port. /health is exempt so the container
# healthcheck still works. If no credentials are set, the app serves open (with a
# loud warning) to preserve local/dev behaviour.
set -e

# ── Real client IP behind an upstream proxy ───────────────────────────────────
# The /api block below sets X-Forwarded-For from $remote_addr, which is the
# address nginx actually observed — deliberately NOT $proxy_add_x_forwarded_for,
# so a caller cannot forge it. That is correct when the caller reaches this
# container directly, and wrong when Caddy is in front: $remote_addr is then
# CADDY's address for every request, so the backend's login rate limiter sees
# one client and ten failed logins lock out everybody.
#
# set_real_ip_from fixes that without reopening the forgery hole: nginx rewrites
# $remote_addr from X-Forwarded-For ONLY when the peer is a listed trusted
# proxy, and ignores the header from anyone else.
#
# Defaults to empty = trust nobody = exactly the behaviour before this block
# existed. Set TRUSTED_PROXY_CIDR to Caddy's Docker subnet to complete the
# chain. Leaving it unset is safe; setting it too broadly is not — never
# include a range that a client could originate from.
#
# USE COMMAS for more than one CIDR, not spaces. Spaces are still accepted
# here, but the documented deploy path cannot carry them: deploy/hetzner/up.sh
# does `set -a; source backend/.env.prod`, and bash reads
#   TRUSTED_PROXY_CIDR=172.18.0.0/16 172.19.0.0/16
# as "assign the first, then RUN the second as a command". The deploy fails
# before Compose starts, with an error naming a subnet rather than a quoting
# problem. Commas avoid it outright; quotes would too, but only if the operator
# remembers, and nothing here can check for them.
REAL_IP_BLOCK=""
if [ -n "$TRUSTED_PROXY_CIDR" ]; then
    for cidr in $(echo "$TRUSTED_PROXY_CIDR" | tr ',' ' '); do
        REAL_IP_BLOCK="${REAL_IP_BLOCK}    set_real_ip_from ${cidr};
"
    done
    # recursive on: with a proxy chain, step back through X-Forwarded-For past
    # every trusted hop instead of stopping at the first.
    REAL_IP_BLOCK="${REAL_IP_BLOCK}    real_ip_header X-Forwarded-For;
    real_ip_recursive on;"
    echo "[entrypoint] Trusting upstream proxy for real client IP: $TRUSTED_PROXY_CIDR"
else
    echo "[entrypoint] TRUSTED_PROXY_CIDR not set — X-Forwarded-For from an upstream proxy is ignored."
    echo "[entrypoint]   Direct callers are rate-limited per client correctly."
    echo "[entrypoint]   Behind Caddy, all callers share one bucket: set TRUSTED_PROXY_CIDR to Caddy's subnet."
fi

# Proves to the backend that a request came through THIS proxy, so it can
# believe the X-Forwarded-For set just below. See backend/app/api/rate_limit.py
# client_ip() and issue #60: the backend shares the external docker_default
# network with Caddy and the IBKR gateway, so peer address cannot tell the
# frontend apart from anything else on it. A secret can.
#
# Unset = the backend ignores X-Forwarded-For and buckets every caller
# together. docker-compose.hetzner.yml requires the variable for that reason.
SECRET_HEADER=""
if [ -n "$TRUSTED_PROXY_SECRET" ]; then
    SECRET_HEADER="proxy_set_header X-Olbos-Proxy-Secret \"$TRUSTED_PROXY_SECRET\";"
    echo "[entrypoint] Proxy secret set — backend will trust this proxy's X-Forwarded-For."
else
    echo "[entrypoint] WARNING: TRUSTED_PROXY_SECRET not set — the backend will ignore"
    echo "[entrypoint]   X-Forwarded-For, so every caller shares one login rate-limit bucket."
fi

AUTH_BLOCK=""
if [ -n "$DASH_USER" ] && [ -n "$DASH_PASS" ]; then
    htpasswd -bc /etc/nginx/.htpasswd "$DASH_USER" "$DASH_PASS" >/dev/null 2>&1
    AUTH_BLOCK='auth_basic "OlbosTrade"; auth_basic_user_file /etc/nginx/.htpasswd;'
    echo "[entrypoint] Dashboard auth ENABLED (user: $DASH_USER)"
else
    echo "[entrypoint] WARNING: DASH_USER/DASH_PASS not set — dashboard is OPEN to anyone who can reach this port."
fi

cat > /etc/nginx/conf.d/default.conf <<EOF
server {
    listen 3000;
    root /usr/share/nginx/html;
    index index.html;
${REAL_IP_BLOCK}
    ${AUTH_BLOCK}

    # Healthcheck must stay open (container probe has no credentials).
    location = /health { auth_basic off; return 200 "ok"; add_header Content-Type text/plain; }

    # nginx's default proxy_read_timeout (60s) is shorter than the backend's
    # own OPTION_CHAIN coordinator timeout (120s, market_data.py) — without
    # this, nginx would 504 the connection before the backend's own timeout
    # (and its clean error body) ever gets a chance to fire.
    location /api         { proxy_pass http://olbostrade-backend:8000; proxy_set_header Host \$host; proxy_set_header X-Forwarded-For \$remote_addr; ${SECRET_HEADER} proxy_read_timeout 130s; proxy_send_timeout 130s; }
    location /docs        { proxy_pass http://olbostrade-backend:8000; }
    location /openapi.json { proxy_pass http://olbostrade-backend:8000; }
    location /ws {
        proxy_pass http://olbostrade-backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    # Vite's HASHED output only. The filename changes every build, so a copy
    # cached forever can never be the wrong one.
    #
    # ^~ so this wins over the regex below without depending on their order.
    location ^~ /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files \$uri =404;
    }

    # Files copied verbatim out of public/ — the favicons and the hero image.
    # These keep the SAME NAME across builds, so immutable would pin whatever
    # was cached first for a year: replace the hero image and nobody sees the new
    # one until 2027. The old rule matched them with the hashed assets and the
    # comment above only ever described /assets/.
    location ~* \.(png|jpg|jpeg|gif|svg|ico|webp|woff2?)\$ {
        add_header Cache-Control "public, max-age=3600, must-revalidate";
        try_files \$uri =404;
    }
    location / {
        add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        try_files \$uri \$uri/ /index.html;
    }
}
EOF

# Parse-check before starting. The config is generated at runtime from env, so
# a bad TRUSTED_PROXY_CIDR (or a missing realip module) would otherwise show up
# as an opaque container crash-loop. `nginx -t` turns that into one readable
# line naming the file and the offending directive.
if ! nginx -t; then
    echo "[entrypoint] FATAL: generated nginx config is invalid — see the error above." >&2
    echo "[entrypoint] TRUSTED_PROXY_CIDR was: '${TRUSTED_PROXY_CIDR:-<unset>}'" >&2
    exit 1
fi

exec nginx -g 'daemon off;'
