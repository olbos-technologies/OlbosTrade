# Broker sign-in onboarding — SaaS-1 / R2 prerequisite

This proposal implements customer broker-hosted sign-in, not automated execution.
An existing Olbos login and Elite broker access are required. Users can skip
connecting and keep using their existing Olbos account.

## Implemented

- Account → My Brokers offers Alpaca, Interactive Brokers, and Charles Schwab
  (thinkorswim). IBKR and Schwab are visibly unavailable pending approved
  integration work. No broker passwords or customer API keys are requested by
  the new sign-in flow. The existing advanced API-key form remains available.
- Alpaca Connect redirects to the broker with explicit `env=paper`, no writable
  scopes, and random single-use state. State is hashed, expires in ten minutes,
  and binds the organization, user and current Olbos login session.
- Atomic SQL DELETE RETURNING consumes state before code exchange. Replay,
  wrong-owner, wrong-session and expired callbacks fail closed, including
  after broker/network failure. Restart sign-in after any failed exchange.
- The server exchanges the code, rejects writable scope grants, verifies an
  ACTIVE account through the fixed Alpaca paper endpoint, then encrypts its
  token with the existing dedicated Fernet key. No live endpoint probing.
- Separate `broker_oauth_grants` storage supports multiple paper accounts with
  a unique per-organization/broker/environment/account identity. It cannot be
  mistaken for existing API-key execution credentials. Listings never decrypt
  tokens; local disconnect clears the ciphertext while retaining a revoked row.
- The screen labels read-only, paper, trading off, and account data not synced.
  Connecting never changes execution mode or grants order permission. Existing
  operator/worker routing is not changed by this proposal.
- Callback redirects contain fixed status words, never code, token or broker
  error text. App middleware strips callback queries from the ASGI request
  target before Uvicorn access logging. Nginx's exact callback location disables
  request/error logging there; general access logs omit query strings.

## Deployment prerequisites (not performed)

1. Register the Olbos application with Alpaca Connect and obtain the applicable
   commercial/vendor approval. Official contract:
   https://docs.alpaca.markets/us/docs/using-oauth2-and-trading-api
2. Configure `ALPACA_OAUTH_CLIENT_ID`, `ALPACA_OAUTH_CLIENT_SECRET`,
   `ALPACA_OAUTH_REDIRECT_URI=https://YOUR_HOST/api/brokers/oauth/alpaca/callback`
   and the existing `BROKER_ENCRYPTION_KEY`. Register that exact callback with
   Alpaca. Secrets belong in the deployment secret store, never the frontend.
   Auth must be enabled; the callback requires the same active Olbos session.
3. Apply Alembic revision 0043 after 0042. No automatic backfill or conversion
   of API-key connections. Rollback removes OAuth grants and pending attempts;
   retain the dedicated encryption key throughout any normal upgrade.
4. Apply the nginx config alongside the backend. Confirm any upstream ingress,
   CDN, APM or error-reporting service also redacts OAuth callback query strings
   and request bodies. Do not enable body/HTTP debug logging for token exchange.
5. Complete a real paper-account connect/cancel/reconnect/disconnect drill,
   verify account identity, inspect logs for secrets, and verify login-session
   expiry and tenant separation. Synthetic tests are not broker evidence.
6. Local disconnect only removes Olbos's token. Users can revoke the app at
   Alpaca as well; broker-side grant revocation is not claimed here.

## Remaining before automated execution

- Complete R2 authenticated history/reconciliation and durable entry authority;
  active proposals #149 and #159 remain independent and are not superseded.
- Add separately consented trading scopes, token health/revocation handling,
  selected-account routing at every actual submission boundary, and enforce
  exact organization/account/environment identity without operator fallback.
- Add per-account Manual/Copilot/Autopilot authorization, authorization audits,
  limits and freshness checks. UI mode selection cannot authorize trading.
- Validate idempotency, partial fills, uncertain submissions, reconnects,
  protective stops, cancellations and kill/exit behavior against a real isolated
  paper broker. Only then review live activation as a separate milestone.
- Obtain and implement IBKR and Schwab commercial connectivity. Existing IBKR
  Gateway use does not implement public third-party customer OAuth.

No merge, deployment, live activation or broker orders are part of this change.
