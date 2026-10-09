# OlbosTrade — Operations Runbook

For the person at the keyboard when something is wrong, who may not be the
person who broke it.

Every procedure here has been run against production. Where something is
believed but unproven, it says so — an unverified step in a runbook is worse
than an absent one, because it gets trusted under pressure.

Production: `http://46.224.0.213` · Hetzner · IBKR **paper** account `****6720`

---

## First: read the state

```bash
curl -s http://46.224.0.213/api/health/detail | python3 -m json.tool
```

`status`, `database.connected`, `ibkr.connected`, `scanner.alive` and
`scanner.last_tick_age_seconds` answer most questions in one call. A scanner
tick age above a couple of minutes means the background loop is wedged.

```bash
curl -s http://46.224.0.213/api/portfolio/heat | python3 -m json.tool
```

Read `heat_overstated` alongside `portfolio_heat_pct`. When it is `true`, some
position lacks a trustworthy stop and its risk is reported as full notional —
the heat figure is an upper bound, not a measurement. `unstopped_position_count`
says how many.

---

## IBKR shows `connected: false`

Symptoms: `accounts: []`, and the log repeating

```
API connection failed: TimeoutError()
Broker reconnect failed: Is TWS/Gateway running on ibkr-gateway:4004?
```

**Restart the gateway alone first.**

```bash
ssh root@46.224.0.213 'docker restart ibkr-gateway'
```

Then wait and re-read `/api/health/detail`. Verified 2026-08-30: IBC completed
a clean login and the backend's own reconnect loop picked it up without a
backend restart — `connected: true`, 184 account values cached.

**Do not restart the backend first.** A backend restart drops and
re-establishes the very connection that keeps failing, and this system has a
recorded history of that making things worse. Only restart the backend if the
gateway is confirmed logged in and the app still cannot reach it.

### `remove Client 2` in the gateway log

Expect to see this **even on a healthy connection** — it reappears immediately
after a clean login. Root cause is unresolved.

A successful reconnect is not evidence it is fixed. On 2026-08-25 the same
signature began benign and escalated to a 1654-deep P0 request queue and a
genuinely blocked trade. If the P0 queue is climbing and timeouts are the
majority, treat it as the acute form and escalate.

---

## The equity scan is slow, or returns 504

**Do not raise the concurrency cap.** This is the trap.

`equity_scan_engine.SCAN_CONCURRENCY` is deliberately **8**. It was previously
an unbounded `asyncio.gather` over all ~100 watchlist tickers, which produced a
130s scan and an nginx 504 while the work carried on invisibly behind it.
Capping at 8 with a 20s per-ticker deadline took the same scan to **7.6s**.

Less parallelism is faster here because the providers are rate-limited:
100 simultaneous requests mostly sit queued and retrying, while 8 get served.
Raising the cap will make it slower again.

Time a scan with:

```bash
curl -s -o /tmp/scan.json -w "%{http_code} %{time_total}s\n" \
  -X POST http://46.224.0.213/api/equity/scan
```

A ticker that exceeds its deadline is dropped and logged by name
(`equity scan: XYZ exceeded 20s — skipped`). A partial scan is expected
behaviour, not a fault.

Note the background scanner runs this same code every 15 minutes.

---

## Orphaned protective orders

Resting stop orders with no matching position. They block
`GET /api/rotation/preflight` from reaching 13/13, which in turn blocks the
capital-rotation approval path.

Check:

```bash
curl -s http://46.224.0.213/api/portfolio/open-orders | python3 -m json.tool
curl -s http://46.224.0.213/api/rotation/preflight  | python3 -m json.tool
```

Cancel (needs the Operator API Key — set it in a shell variable so it stays
out of history):

```bash
curl -s -X POST http://46.224.0.213/api/portfolio/cancel-orders \
  -H 'Content-Type: application/json' -H "X-Api-Key: $OLBOS_KEY" \
  -d '{"order_ids": [122820]}' | python3 -m json.tool
```

**Do this during regular trading hours.** On 2026-08-29 a Saturday cancel of
all 20 changed nothing — every order was still live on the next refreshed
read, with no error. A single-order probe through this route returned
`STILL OPEN — IBKR did not cancel it`, so the refusal is IBKR-side.
The leading theory is that IBKR will not cancel a `PreSubmitted` stop outside
RTH. **Unproven** — a market-hours retry is the test.

Try one order before all of them, so a failure is diagnosable.

The route refuses to cancel any order protecting a live position. That guard
is deliberate: cancelling a resting stop is the one action here that increases
risk rather than reducing it.

---

## Deploying

```bash
ssh root@46.224.0.213 'cd /opt/olbostrade && nohup bash deploy/hetzner/update.sh > /tmp/deploy.log 2>&1 &'
```

Run it detached. A foreground deploy over SSH has repeatedly died mid-build on
a dropped connection, leaving you unsure whether it finished. Poll instead:

```bash
ssh root@46.224.0.213 'tail -5 /tmp/deploy.log'
```

Wait for `✅ Update complete`, then check `/api/health/detail`.

**Expect IBKR to need a moment after every deploy**, and occasionally a gateway
restart — the container restart drops the broker connection.

### Migrations run from the new image, before it starts serving

`update.sh` does this itself now, and the order is the point:

```
[2/6] build          ← the new image, containing the new revision
[3/6] alembic upgrade head   ← in a throwaway container from THAT image
[4/6] up -d          ← only now does new code start serving
[5/6] alembic current        ← verify, in case up -d recreated the database
```

It used to start containers at step 3 and migrate at step 4, so between them
the new code was live against the old schema. For the position-claims table
(`0040`) that meant Stage 3 failing closed — **new entries refused** — for the
length of a deploy. Exits, fill polling, reconciliation and the kill switch
were unaffected then and are unaffected now: nothing outside the entry path
consults claims, and `test_only_the_entry_path_consults_the_claim` pins that.

**Do not "pre-apply" a migration with `docker exec`.** An earlier version of
this runbook said to run:

```bash
docker exec olbostrade-backend python3 -m alembic upgrade head   # ← WRONG
```

That executes inside the container **still running the old image**, which does
not contain a revision that arrives with this deploy. It exits 0 having
applied nothing, and reports success. A command that looks like it closed the
window and did not is worse than no command, because it is believed. The
migration has to come from the image just built, which is what step 3 does:

```bash
docker compose -f docker-compose.hetzner.yml run --rm --no-deps backend \
  python3 -m alembic upgrade head
```

`--no-deps` because `olbostrade-db` is already up in a normal deploy and must
not be recreated underneath the running backend. If the database is *not* up,
start it first with `up -d olbostrade-db` rather than dropping `--no-deps`.

#### If the migration fails

The deploy stops at step 3, before any new container starts. `set -euo
pipefail` is on and that line is deliberately unguarded, so the old image
keeps serving the schema it was built for — a working system, not a degraded
one. Fix the migration and re-run; there is nothing to roll back, because
nothing was swapped.

If new code is *already* serving without its table (someone ran the old
ordering, or step 4 recreated the database), entries refuse with
`entry_guard_unavailable` while monitoring and exits keep working. That is the
safe direction but not a state to sit in: apply the migration, or roll the
image back.

#### Unresolved position claims

A claim in `submitted` or `unknown` blocks **new entries** on that position
(exits, fill polling, reconciliation and the kill switch are unaffected).
Two things clear them.

**Automatically**, if the broker can answer. A sweep runs at startup and every
5 minutes, asks the broker whether it holds an order carrying the claim's
`client_order_id`, and releases the claim only on an authoritative "no such
order" past a 120-second settle window. Everything else leaves it blocked.
Look for `Claim reconciliation sweep` in the logs.

It cannot answer for:

| case | why |
|---|---|
| IBKR claims | no client-id lookup, so absence is never established |
| **equity** claims | `place_equity_order` sends no client id, so nothing carries the key |
| broker unreachable, 5xx, 429, timeout | not an answer |
| the order is real | correctly held for position reconciliation |

When a sweep exhausts its retries it logs **CRITICAL** naming each blocked
position and pointing here.

**By hand**, for the ones it cannot. Authenticated, audited, and it records
who and why in `execution_events` (kind `claim_override`) in the same
transaction as the release:

```bash
# What is blocking entries
curl -s -b "$SESSION_COOKIE" https://<host>/api/admin/position-claims | jq

# Clear one, once the order's fate is actually known
curl -s -b "$SESSION_COOKIE" -X POST \
  https://<host>/api/admin/position-claims/<claim_token>/release \
  -H 'Content-Type: application/json' \
  -d '{"reason":"venue confirmed no fill by phone"}'
```

The reason is required and cannot be blank. Identity comes from the session,
never from the request body — **with `AUTH_ENABLED` off there is no identity
to record and the release is refused with 403**, because an override
attributed to nobody is not an audit trail. Releasing re-opens the position to
a new entry, so only do it once you know the order did not fill.

---

---

## Things that are true and easy to get wrong

- **`heat_overstated: true` means the heat number is an upper bound.** Do not
  quote the percentage without it.
- **A reconciler-adopted position has `entry == stop`.** Anything reading
  `Trade.long_strike` as a stop must go through
  `portfolio_engine.equity_stop_distance()`, which rejects that shape.
  Read literally, those rows report roughly zero risk.
- **`Unknown` is not a sector** and never blocks a concentration check. See
  `is_cappable_sector()`.
- **Capital Rotation can recommend but never execute.** The close functions
  refuse a `position_rotation`-labelled close without an approval token, and
  the only route that mints one is the explicit approve endpoint.
- **Options POP is real** (Black-Scholes `norm.cdf(d2)`), risk-neutral, and
  options-only. Equity signals have no POP.
