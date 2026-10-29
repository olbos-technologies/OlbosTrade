# Alpaca paper connection validation — SaaS-1 / R0

This is an operator procedure for a separately approved staging rollout. It has
not been executed against a real broker. Connecting and viewing data remain
read-only; none of these steps grants execution permission.

## Application setup

1. Register the Olbos application with Alpaca and complete applicable vendor
   approval. Record approval in the private operational evidence system.
2. Register the exact HTTPS callback ending in
   `/api/brokers/oauth/alpaca/callback`. Use the same value for
   `ALPACA_OAUTH_REDIRECT_URI`; no query, fragment or URL credentials.
3. Set `ALPACA_OAUTH_CLIENT_ID`, `ALPACA_OAUTH_CLIENT_SECRET`,
   `BROKER_ENCRYPTION_KEY` and `AUTH_ENABLED=true` in the deployment secret
   store. Use an existing dedicated encryption key when grants already exist:
   replacing it invalidates stored credentials. Do not paste secrets into chat,
   screenshots, shell arguments or PRs.

## Offline check

From the backend directory, using the intended deployment environment:

```bash
python -m app.broker_paper_preflight
```

For an already built Hetzner backend image, without starting services:

```bash
docker compose --env-file backend/.env.prod -f docker-compose.hetzner.yml run --rm --no-deps backend python -m app.broker_paper_preflight
```

This requires Docker/image availability and reads the configured backend
environment. It does not build, migrate, start the app, access the database or
call Alpaca. Run it from the repository root. Never use `docker compose config`
or dump the environment to share diagnostics: those can expose secrets.

Exit 0 means loaded settings pass the offline checks; 1 means a check failed;
2 means settings could not be safely loaded/validated. Output contains fixed
check names and booleans, no values. `oauth_configuration_valid` reuses the
application's provider-availability rules, including the callback shape.
`encryption_key_valid` also checks the Fernet key format. A valid format does
not prove that the key decrypts existing grants. No result verifies vendor
approval, secret correctness at Alpaca, callback registration, DNS/TLS, database
schema, proxy configuration or successful broker sign-in.

## Controlled staging rollout and paper drill

Use the repository's existing release process and explicit deployment approval.
Record the deployed SHA, image identity, previous rollback image, health checks
and Alembic revision. Required migrations are 0043 and 0044 (or descendants).
Deploy the callback-redacting nginx configuration with the backend, and verify
upstream ingress/APM redaction before sign-in. Never capture raw callback URLs,
authorization codes, tokens, request bodies or full account identifiers.

With an authorized test user who has Elite broker access and an isolated Alpaca
paper account:

| Check | Expected result |
| --- | --- |
| Connect Alpaca | Broker-hosted login; paper access; no trading scope requested; Trading off |
| Cancel or expire sign-in | Fixed failure/cancel state; retry starts a new sign-in |
| Replay callback | Rejected; no duplicate grant or token exchange |
| Check account | Point-in-time health and timestamp for the selected account |
| View account data | Compare balances, signed/fractional quantities and open orders to the same paper account during the collection interval |
| No open orders/positions | Empty state only after a successful read, never after failure |
| Restriction/read failure | Restricted/unavailable/reconnect state; unknown values remain unknown |
| Refresh or switch account | Old values clear; late results cannot replace the selected account |
| Disconnect during read | Older result cannot restore data or overwrite revoked state |
| Broker-side revoke, then check | Reconnect required; no continuing success claim |
| Reconnect | Health resets and must be checked again |
| Another tenant/session | Cannot list, verify, read or disconnect the first tenant's connection |
| Logs | No codes, tokens, callback query strings or raw broker errors |

For replay, tenant and race checks use an approved private test harness; never
store raw callback requests as evidence. Record pass/fail/not-run, sanitized
timestamps, deployment SHA and synthetic connection labels. Do not mark the
drill complete if steps were skipped. Non-atomic reads and a limited open-order
query do not prove reconciliation, stop coverage or complete history.

If any check fails, halt onboarding rollout, preserve sanitized evidence, and
use the reviewed rollback image/process. Do not rotate the encryption key as a
rollback technique. Disconnect only clears Olbos's local token; revoke the app
at Alpaca separately when required. Schema downgrades can destroy grant data
and require a separately reviewed backup/rollback plan.

## Handoff

- Milestone: SaaS-1 / R0 configuration and validation preparation.
- Base: `e79e2b103141092dc23f833685aec7f1837d941e`.
- Branch: `codex/broker-paper-preflight`.
- Changes: offline CLI, CLI regression tests, this runbook, merged prerequisite
  bookkeeping in the roadmap/account-data documentation and changelog.
- No new setting, schema, API, broker call, order, risk/mode change or deployment.
- Next owner: application owner for registration/secrets; authorized release
  operator for staging; reviewer for actual paper evidence. Automated execution
  remains blocked on the separate R2 and per-account authorization gates.

Broker contract reference (registration requirements must be checked at setup):
https://docs.alpaca.markets/us/docs/using-oauth2-and-trading-api
