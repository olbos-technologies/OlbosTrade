# Credential Rotation Runbook

**Why this exists.** On 2026-08-28 the production env file
(`/opt/olbostrade/backend/.env.prod`) was found at `0644` — world-readable —
alongside four backup copies, also `0644`, in the same directory. Permissions
have since been tightened to `0600` and the backups removed, but the five
credentials below were readable by any account on the host for an unknown
window and should be treated as exposed.

`SECRET_KEY` is **not** in this list: it was rotated on 2026-08-27, and the
superseded value was destroyed with the backup archive on 08-28. The live key
is unchanged and needs no action.

## Ground rules

- **No secret value appears in this document, in logs, in commits, or in
  terminal output.** Verify by hashing or by behaviour, never by printing.
- Rotate one credential at a time and verify before moving on. A failed
  rotation that goes unnoticed looks identical to a working one until the next
  scan.
- `.env.prod` is read at **container creation**, not at restart. `docker
  restart` keeps the old value. Use the recreate step below.
- Every provider-side step — signing in, generating, revoking — is the
  operator's. Nothing in this runbook should be executed by an assistant.

## Shared procedure

1. Generate the new credential in the provider's console.
2. Edit the value in `/opt/olbostrade/backend/.env.prod` (root, `0600`).
3. Recreate the backend so the new value is loaded:

```bash
ssh root@46.224.0.213 'cd /opt/olbostrade && set -a && . backend/.env.prod && set +a && docker compose -f docker-compose.hetzner.yml up -d --force-recreate backend'
```

The `set -a; . backend/.env.prod` preamble is required — compose interpolates
`${VAR}` from the shell environment, and without it the DB password
interpolation fails.

The verification commands below go through `docker exec` into the backend
rather than `curl http://localhost/...` on the host. They used the host URL
until 2026-09-19, which reached the app through Caddy's plain-HTTP `:80` site
block; that block served the whole terminal in clear text to anyone who knew
the server's IP and has been removed. `docker exec` does not traverse Caddy at
all, so these checks keep working and never depend on a plaintext route
existing.

4. Run the credential-specific verification below.
5. Revoke the old credential provider-side.
6. Confirm the old credential is dead (see per-credential notes).

## Per credential

### `IBKR_PASSWORD`

- **Rotate at:** IBKR Account Management → Settings → password.
- **Blast radius:** the highest of the five. The gateway authenticates with
  it on every IBC login; a wrong value means no market data, no positions,
  no orders. Rotate outside market hours.
- **Extra step:** the gateway container caches credentials. Recreate
  `ibkr-gateway` as well as the backend, and watch its log for a successful
  fresh login before trusting anything downstream.
- **Verify (no secret printed):**

```bash
ssh root@46.224.0.213 'docker exec olbostrade-backend curl -s http://127.0.0.1:8000/api/health/detail | grep -o "\"connected\":[a-z]*" | head -1'
```

Then confirm real account data flows — `GET /api/paper-trade/portfolio`
returning a non-empty `net_liquidation` with `broker_error: ""`.

- **Old-credential check:** IBKR invalidates the previous password on change;
  no separate revocation. Confirm by checking that no second session appears
  in Account Management's login history after the cutover.

### `OLBOSTRADE_DB_PASSWORD`

- **Rotate at:** Postgres itself. This one is *not* provider-side, and the
  order matters — change it in the database **and** the env file, or the app
  loses its own database.
- **Sequence:** `ALTER ROLE olbosquant WITH PASSWORD '…'` inside the
  `olbostrade-db` container → update `.env.prod` → recreate backend.
- **Take a backup first.** A mismatch here is the one failure in this list
  that can leave the app unable to read its own trade history.
- **Verify:**

```bash
ssh root@46.224.0.213 'docker exec olbostrade-backend curl -s http://127.0.0.1:8000/api/health/detail | grep -o "\"database\":{\"connected\":[a-z]*}"'
```

- **Old-credential check:** `ALTER ROLE` replaces rather than adds, so the old
  password stops working immediately. Confirm by attempting a connection with
  it — expect a failure, and do not paste it anywhere that logs.

### `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`

- **Rotate at:** Alpaca dashboard → API keys → regenerate. Both values change
  together; update both env lines in one edit.
- **Blast radius:** low. Alpaca is a secondary broker path
  (`alpaca_client.py`); the live desk runs on IBKR. Nothing should break.
- **Verify:** no dedicated health field. Confirm the backend starts clean and
  no Alpaca auth errors appear in `docker logs olbostrade-backend`.
- **Old-credential check:** Alpaca shows key status in the dashboard —
  confirm the previous key reads revoked/deleted.

### `GEMINI_API_KEY`

- **Rotate at:** Google AI Studio → API keys → delete and create.
- **Blast radius:** low, and worth confirming before rotating — grep for
  actual call sites first, since an unused key is a cleanup candidate rather
  than a rotation task.
- **Verify:** exercise whichever feature consumes it and confirm a real
  response, not a cached one.
- **Old-credential check:** deleted keys 403 immediately; the console lists
  remaining keys.

### `SENDGRID_API_KEY`

- **Rotate at:** SendGrid → Settings → API Keys → create, then delete the old.
- **Create the new key before deleting the old** so notifications never have a
  dead window.
- **Verify:** send one test notification and confirm delivery, not just a 2xx.
- **Old-credential check:** SendGrid's Activity feed shows which key served a
  request; confirm the old one stops appearing, and that it is deleted rather
  than merely unused.

## After all five

- Re-check permissions survived the edits:

```bash
ssh root@46.224.0.213 'ls -l /opt/olbostrade/backend/.env.prod'
```

Expect `-rw------- root root`.

- **Do not create backup copies of the edited file.** That is what produced
  the original exposure. If a rollback point is genuinely needed, keep it
  outside the deploy tree at `0600` and destroy it once the rotation is
  confirmed.

- Run the rotation preflight and confirm no regression:

```bash
ssh root@46.224.0.213 'docker exec olbostrade-backend curl -s http://127.0.0.1:8000/api/rotation/preflight | head -c 200'
```

`ibkr_connection`, `account_state_synchronized` and `audit_logging_functional`
all exercise credentials indirectly and will fail loudly if a rotation went
wrong.

## Open question worth deciding separately

These five live in a plaintext env file on one host. Rotating them does not
change that. A secrets manager, or at minimum Docker secrets, would mean the
next permission slip is not also a credential disclosure. Out of scope for
this rotation, but the reason it was needed.
