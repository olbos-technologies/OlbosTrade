# OlbosTrade — Deploy on Hetzner

Runs OlbosTrade on a Hetzner server, with its own Caddy for HTTPS.

## Architecture

```
Internet
    │
    ▼  :80 (ACME + 308 redirect), :443
 olbostrade-caddy          ← this repo's compose file owns it
    │
    ▼  docker_default network, NOT a host port
 olbostrade-frontend       ← nginx: Basic Auth + proxies /api, /ws to backend
    │
    ▼  internal network only
 olbostrade-backend ──► olbostrade-db (postgres)
         │
         ▼  docker_default
   ibkr-gateway            ← separate compose project; IBKR_HOST=ibkr-gateway
```

Caddy reaches the frontend **over `docker_default` by container name**, not
through a published host port. That is why the frontend's own `:8080` is bound
to loopback (step 7b) without breaking anything.

> **History worth knowing.** Until 2026-09-19 Caddy belonged to a separate
> `OlbosTerminal` project whose application had already been deleted. This
> stack's TLS, its ports 80/443, and the network it reaches `ibkr-gateway`
> over all depended on a compose file for software that no longer existed —
> and the obvious cleanup command in that directory, `docker compose down`,
> would have removed the `docker_default` network and cut the broker
> connection. Caddy now lives in `docker-compose.hetzner.yml` here.
>
> The network keeps its historical `docker_default` name and stays declared
> `external: true`. `ibkr-gateway` sits on it and belongs to a third project,
> so this stack's `down` must never take it away — and renaming a network means
> recreating every container attached to it.
Its database and Redis are isolated on `olbostrade_internal` — separate from olbos.

NOTE: the Postgres database is `olbostrade`; the role stays `olbosquant`
deliberately (see the Migration section below for why). Docker container,
network, and directory names are fully rebranded.

---

## First deploy (do this once)

### 1. SSH into the server
```bash
ssh root@<YOUR_HETZNER_IP>
```

### 2. Clone OlbosTrade
```bash
cd /opt
git clone https://github.com/mangpijasuan/OlbosTrade.git olbostrade
cd olbostrade
```

### 3. Create the env file
```bash
cp deploy/hetzner/.env.example backend/.env.prod
nano backend/.env.prod
```

Fill in these required values:
| Variable | How to get it |
|----------|--------------|
| `OLBOSTRADE_DB_PASSWORD` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `OLBOS_API_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `TRUSTED_PROXY_SECRET` | `openssl rand -hex 32` |

`TRUSTED_PROXY_SECRET` is a shared secret between nginx and the backend, and
the stack will not start without it — `docker-compose.hetzner.yml` declares it
`${TRUSTED_PROXY_SECRET:?...}`, so Compose aborts rather than booting into a
state where the login rate limiter cannot tell callers apart. nginx stamps it
onto every proxied request; the backend honours `X-Forwarded-For` only when
that header matches, and otherwise falls back to the socket peer. It has to be
set on BOTH the backend and frontend services, which the compose file already
does from this one variable — set it once here.

`OLBOSTRADE_DB_PASSWORD` is the database password variable referenced by
`docker-compose.hetzner.yml` — it authenticates as the `olbosquant` role
(kept deliberately; see "Migration" below) against the `olbostrade`
database. The password value itself is unchanged from before the rename.

Leave `DATABASE_URL` and `REDIS_URL` blank — docker-compose fills them in.

### 3b. Before you point a public name at this — check it needs a password

```bash
grep -E '^(DASH_USER|DASH_PASS|AUTH_ENABLED)=' backend/.env.prod
```

Until now this instance has been reachable only as an unadvertised `IP:8080`,
which is obscurity, not access control. A domain removes even that.

**Both** `DASH_USER` *and* `DASH_PASS` must be non-empty. The entrypoint gates
on `[ -n "$DASH_USER" ] && [ -n "$DASH_PASS" ]` — set only one and Basic Auth
is silently off while the config *looks* filled in. Treat exactly-one-set as a
failed check, not a partial win.

If Basic Auth is off **and** `AUTH_ENABLED` is false, the frontend serves the
whole terminal — kill switch, position closing, execution mode — to anyone who
resolves the name. Confirm which state you are in from the container's own
report rather than from the env file:

```bash
docker logs olbostrade-frontend 2>&1 | grep -iE "Dashboard auth|DASH_USER"
```

`Dashboard auth ENABLED` means both were set. The `WARNING: DASH_USER/DASH_PASS
not set` line means the app is open — including when you filled in one of them.

Set one of these first:

* `DASH_USER`/`DASH_PASS` put nginx Basic Auth in front of everything,
  including `/api` (see `frontend/docker-entrypoint.sh`). Simplest.
* `AUTH_ENABLED=true` uses real accounts from `scripts/create_user.py`.

These are not interchangeable with `SECRET_KEY`. That one guards *mutating*
API routes and is entered per-session in the browser; it does nothing to stop
someone reading the terminal, and the kill-switch engage route deliberately
does not require it at all.

### 4. Add a DNS record

At your domain registrar, add an A record:
```
trade.olbos.us  →  <YOUR_HETZNER_IP>
```

Wait ~60 seconds for DNS to propagate.

### 5. Start OlbosTrade
```bash
bash deploy/hetzner/up.sh
```

The script will:
- Build and start all containers, Caddy included
- Run database migrations

### 6. Caddy

Nothing to paste. `deploy/hetzner/Caddyfile` is a tracked file in this repo,
mounted read-only into the `caddy` service, so `up.sh` starts a correctly
configured proxy.

Edit it **here and deploy with git**, never on the server: `update.sh` begins
with `git pull origin main`, so a host-side edit to a tracked file either stops
the next deploy with a conflict or is silently reverted.

Apply a Caddyfile change without restarting the container:
```bash
docker exec olbostrade-caddy caddy reload --config /etc/caddy/Caddyfile
```
A clean reload prints only its two "adapted config" lines. **If it errors, the
running config is kept** — nothing goes down while you fix it.

Caddy obtains its certificate over the ACME **HTTP** challenge, so port 80 must
be reachable from the internet. Do not add a `:80` site block to take it over:
with none, Caddy uses port 80 for challenges and a 308 to HTTPS, which is what
you want. See the note at the bottom of the Caddyfile for what happened the
last time one existed.

If your DNS is behind a proxying CDN (Cloudflare's orange cloud, for example),
**turn the proxy off** for this record. The ACME HTTP challenge has to reach
this server, and a proxied record resolves to the CDN instead — issuance then
fails or loops.

### 6b. One-time cutover (existing servers only)

**Skip this on a fresh deploy** — `up.sh` already starts the right container.

A server set up before 2026-09-19 is running `olbos-caddy` from the deleted
OlbosTerminal project. Compose cannot adopt it: that container carries the
other project's labels, so this stack will try to create its own and fail on
the port conflict. It has to be removed first.

> **Read this before starting.** Ports 80 and 443 are unavailable for the few
> seconds between the two commands, and if the new container does not come up,
> HTTPS stays down — with `:8080` now bound to loopback, the browser has no
> fallback. Your way back in is
> `ssh -L 8080:localhost:8080 root@<YOUR_HETZNER_IP>` then
> `http://localhost:8080`. Have that terminal open before you begin.

```bash
cd /opt/olbostrade && git pull origin main

# Keep a copy of the old config — it is not in this repo's history.
cp /root/OlbosTerminal/docker/Caddyfile /root/Caddyfile.pre-migration.bak

docker stop olbos-caddy && docker rm olbos-caddy

set -a; source backend/.env.prod; set +a
docker compose -f docker-compose.hetzner.yml up -d caddy
```

Verify, from your laptop rather than the server:

```bash
curl -sI http://<YOUR_HETZNER_IP>/  | head -2     # want 308 → https://
curl -sI https://trade.olbos.us     | head -2     # want 401
curl -s -o /dev/null -w '8081: %{http_code}\n' http://<YOUR_HETZNER_IP>:8081/
```

The certificates survive because the compose file pins the same volumes the old
stack used (`docker_caddy-data`, `docker_caddy-config`) rather than creating new
ones — a fresh volume would mean re-issuing from Let's Encrypt, whose per-name
rate limit is measured in hours. Confirm with `docker volume ls | grep caddy`:
you should see the two `docker_`-prefixed volumes and no new ones.

Port **8081** is gone deliberately: the old stack published it, it served the
app over plain HTTP on every interface, and its number is the one
`ARCHITECTURE_AUDIT.md` recorded as the production URL — a misattribution that
cost an afternoon during the 2026-09-17 outage.

**Rollback**, if the new container will not serve:

```bash
docker stop olbostrade-caddy && docker rm olbostrade-caddy
docker run -d --name olbos-caddy --restart unless-stopped \
  --network docker_default -p 80:80 -p 443:443 \
  -v /root/Caddyfile.pre-migration.bak:/etc/caddy/Caddyfile:ro \
  -v docker_caddy-data:/data -v docker_caddy-config:/config \
  caddy:2-alpine
```

Once HTTPS is confirmed on the new container, the old project directory can go:

```bash
rm -rf /root/OlbosTerminal        # nothing runs from it any more
```

Nothing on this host depends on it after the cutover — but verify with
`docker ps` first that no container's name starts with `olbos-` rather than
`olbostrade-`.

### 7. Verify
```bash
curl -s https://trade.olbos.us/api/guardrails/status
# → {"trading_allowed":true,"trading_mode":"normal",...}
```

Open **https://trade.olbos.us** in your browser.

**Without a domain — or when the domain is down — there is no public port to
open.** Earlier revisions of this guide sent you to the server's own address on
port 8080; that stopped being true when the frontend's host port moved to a
loopback bind
(`ports: ["127.0.0.1:8080:3000"]`, see step 7b). Port 8080 now answers only from
*on* the server, so reaching it from your laptop means an SSH tunnel:

```bash
ssh -L 8080:localhost:8080 root@<YOUR_HETZNER_IP>
# then open http://localhost:8080 — terminal at http://localhost:8080/terminal
```

> The browser says `http://`, and that is fine here: the traffic never touches
> the network unencrypted, because SSH carries it. This is the one route on
> which entering the Operator API Key (`SECRET_KEY`) or the
> `DASH_USER`/`DASH_PASS` credentials is safe without HTTPS — and it is safe
> *because of the tunnel*, not because the URL looks local.
>
> Prefer `https://trade.olbos.us` whenever it is up. The tunnel is for when it
> is not: a broken Caddy config, an expired certificate, a DNS problem.

The port number comes from the `ports:` entry on the `frontend` service in
`docker-compose.hetzner.yml`; if you change it there, change it here — and keep
the `127.0.0.1:` prefix, which is what makes it private. The backend is NOT
published to the host at all — it is reachable only over the internal Docker
network, which is why `curl localhost:8000` on the server returns nothing and
`docker exec olbostrade-backend curl localhost:8000/health` works.

### 7b. Close the direct HTTP port

Once HTTPS works, the published `:8080` is no longer needed — Caddy reaches the
frontend over the Docker network, not the host port.

Do this **after** step 7 passes, not before: until Caddy serves the domain,
`:8080` is the only way in, and closing it first locks you out of your own
server.

It is more urgent than it looks once Basic Auth is on. HTTP Basic sends
`user:password` base64-encoded, which is reversible by anyone reading the
traffic — so a `DASH_USER`/`DASH_PASS` prompt served over plain `http://` leaks
the credentials it exists to enforce.

> **`ufw deny 8080` does NOT close it.** Docker publishes ports with its own
> DNAT and FORWARD rules, which are traversed before UFW's, so a published
> container port stays reachable from the internet no matter what `ufw status`
> says. An earlier revision of this guide recommended exactly that, which is
> worse than saying nothing — it reads as done.

The bind is loopback, and it is **already in the repo** —
`docker-compose.hetzner.yml` carries `ports: ["127.0.0.1:8080:3000"]` on the
`frontend` service. Docker then listens only on the loopback interface, so
nothing external can reach it and no firewall rule is involved. The SSH tunnel
below still works, because it connects from *on* the host.

Earlier revisions of this guide told you to make that edit on the server. Do
not: `update.sh` begins with `git pull origin main`, so a local modification to
a tracked file either stops the next deploy with a conflict or gets reverted
without anyone noticing. Pull it instead:

```bash
cd /opt/olbostrade && git pull origin main
set -a; source backend/.env.prod; set +a
docker compose -f docker-compose.hetzner.yml up -d frontend
```

`up -d` is enough — changing `ports:` recreates the container, and no rebuild is
needed for a binding change.

**Verify from another machine, not from the server** — checking locally
succeeds either way and proves nothing:

```bash
# from your laptop
curl --connect-timeout 5 -sS -o /dev/null http://<YOUR_HETZNER_IP>:8080 \
  && echo "STILL REACHABLE — not closed" \
  || echo "closed"
curl -sI https://trade.olbos.us | head -3     # still fine
```

This matters beyond tidiness. While `:8080` is open there is a plain-HTTP route
into the same app, and the Trade Desk's own 403 message tells an operator to go
enter the Operator API Key (`SECRET_KEY`) on the Risk Monitor page. Follow that
over `http://` and the key crosses the network in clear text. Closing the port
removes the unsafe path rather than relying on everyone remembering which URL
they are on.

For direct triage when the domain is down, tunnel — this works with the
loopback binding above and needs no change to expose anything:

```bash
ssh -L 8080:localhost:8080 root@<YOUR_HETZNER_IP>
```

### 8. Set up automated backups

`backup_db.sh` dumps the database daily but does nothing until its cron
entry is actually installed — run this once:
```bash
bash deploy/hetzner/install_backup_cron.sh
```
Configure an off-site target too (`BACKUP_RCLONE_REMOTE` or
`BACKUP_SCP_TARGET` in `backend/.env.prod`) — local-only backups don't
survive a disk failure.

---

## Updating after a code change

```bash
cd /opt/olbostrade
bash deploy/hetzner/update.sh
```

This pulls latest code, rebuilds, restarts, and runs any new migrations.

**Upgrading a stack created before `TRUSTED_PROXY_SECRET` existed:** add it to
`backend/.env.prod` before running `update.sh`, or Compose refuses to start and
the app goes down on what looks like a routine update:

```bash
echo "TRUSTED_PROXY_SECRET=$(openssl rand -hex 32)" >> backend/.env.prod
```

---

## Useful commands

```bash
# View live logs
docker logs olbostrade-backend -f
docker logs olbostrade-frontend -f

# Check all container status
docker compose -f docker-compose.hetzner.yml ps

# Stop everything (does not delete data)
docker compose -f docker-compose.hetzner.yml down

# Open a shell in backend
docker exec -it olbostrade-backend bash

# Run a migration manually
docker exec olbostrade-backend python3 -m alembic upgrade head

# Check database (role stays olbosquant deliberately — see Migration section)
docker exec -it olbostrade-db psql -U olbosquant -d olbostrade
```

---

## IBKR Gateway (Docker)

OlbosTrade talks to IBKR through the [gnzsnz/ib-gateway](https://github.com/gnzsnz/ib-gateway-docker) image via `ib_insync` (socket API, not Client Portal).

### Start the gateway (same server)

```bash
docker run -d --name ibkr-gateway --restart unless-stopped \
  --network docker_default \
  -e TWS_USERID=your_ibkr_username \
  -e TWS_PASSWORD=your_ibkr_password \
  -e TRADING_MODE=paper \
  -e GATEWAY_OR_TWS=gateway \
  -e READ_ONLY_API=no \
  -e TWOFA_TIMEOUT_ACTION=restart \
  -e EXISTING_SESSION_DETECTED_ACTION=primaryoverride \
  -e TRUSTED_IPS=127.0.0.1,172.18.0.0/16 \
  -p 4002:4004 \
  -v ibkr-gateway_ibkr_settings:/home/ibgateway/Jts \
  ghcr.io/gnzsnz/ib-gateway:stable
```

Important:

| Setting | Value | Why |
|---------|-------|-----|
| `IBKR_HOST` | `ibkr-gateway` | Docker DNS on `docker_default` network |
| `IBKR_PORT` | `4004` | gnzsnz **socat** publishes paper API on container port **4004** (not 4002) |
| Host port map | `4002:4004` | Host clients use 4002; in-network clients use 4004 |
| Workers | `1` | IBKR allows only one connection per `IBKR_CLIENT_ID` |

After gateway restart, approve **2FA** on the IBKR mobile app if prompted. Check logs:

```bash
docker logs ibkr-gateway --tail 30    # expect "Login has completed"
docker logs olbostrade-backend --tail 20   # expect "Broker connected successfully"
```

Test from the backend container:

```bash
docker exec olbostrade-backend python3 -c "
import asyncio, os
from ib_insync import IB
async def t():
    ib = IB()
    await ib.connectAsync('ibkr-gateway', int(os.environ['IBKR_PORT']), clientId=99, timeout=20)
    print('accounts', ib.managedAccounts())
    ib.disconnect()
asyncio.run(t())
"
```

## Migration

The production database was renamed live via `ALTER DATABASE olbosquantdb
RENAME TO olbostrade` (instant — no dump/restore needed, no data touched).

The Postgres **role** deliberately stays `olbosquant`. Renaming a role
requires connecting as a *different* superuser (Postgres refuses to let a
session rename its own login role), which means creating a temporary
superuser on production — real privilege escalation for a value that's
never visible outside this repo's own config (not in the UI, not in any
API response). Not worth it for a cosmetic-only rename. `docker-compose.hetzner.yml`
reflects this: `POSTGRES_USER`/`DATABASE_URL` use `olbosquant`, `POSTGRES_DB`
uses `olbostrade`.

If a fresh deployment ever needs the role renamed too (no existing data at
risk), it's the same trick as the database — just needs a second superuser
to issue the command:

```bash
docker exec -it olbostrade-db psql -U olbosquant -d postgres \
  -c "CREATE ROLE rename_helper WITH LOGIN SUPERUSER PASSWORD 'temp';"
docker exec -it olbostrade-db psql -U olbosquant -d postgres \
  -c "ALTER ROLE olbosquant RENAME TO olbostrade;"
docker exec -it olbostrade-db psql -U olbostrade -d postgres \
  -c "DROP ROLE rename_helper;"
```
Then update `DATABASE_URL`/`POSTGRES_USER`/the healthcheck in
`docker-compose.hetzner.yml` to `olbostrade` and redeploy.

---

## RAM usage estimate

| Container | RAM |
|-----------|-----|
| olbostrade-backend | ~400–800 MB |
| olbostrade-frontend | ~100 MB |
| olbostrade-db | ~150–300 MB |
| olbostrade-redis | ~50 MB |
| **Total** | **~700 MB – 1.3 GB** |

Your existing olbos app uses ~2–3 GB.
A **CX32 (8 GB RAM, ~€17/mo)** comfortably runs both.
If your server is already 8 GB, upgrade to CX32 before deploying.
