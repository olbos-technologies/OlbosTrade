# Trade Desk 2.0 — Deploy Prep (gate only)

**Do not run `deploy/hetzner/update.sh` until paper E2E is accepted** and this checklist is green.

## Pre-deploy env (`backend/.env.prod`)

| Variable | Required | Notes |
|----------|----------|-------|
| `SECRET_KEY` | **Yes (prod)** | Mutate API key; operator pastes same in UI |
| `KILL_SWITCH_RESET_CODE` | **Yes (prod)** | Separate from SECRET_KEY |
| `EXECUTION_PORTFOLIO_GATE` | Recommended `true` | Rollback = `false` |
| `EXECUTION_ENFORCE_PORTFOLIO_GREEKS` | Keep `false` | Until Greeks recalibrated |
| `IBKR_TRADING_MODE` | Confirm | `paper` until live go/no-go |

Also retain existing DB/Redis/broker vars per `deploy/hetzner/README.md`.

## Update path (when approved)

```bash
# On server, from /opt/olbostrade
git pull --ff-only origin main    # the script does not pull — docs/runbook.md
bash deploy/hetzner/update.sh
# → verify checkout, rebuild backend+frontend, alembic upgrade head from the
#   new image, then up -d
```

Post-update smoke:

```bash
docker logs olbostrade-backend --tail 100
curl -fsS https://trading.<yourdomain>/api/health   # or internal health URL
```

UI: Paper badge, Trade Desk Overview, Copilot, kill switch visible.

## Rollback

1. UI: Desk Settings → Trade Desk 2.0 **off**
2. Env: `EXECUTION_PORTFOLIO_GATE=false` (portfolio gate only)
3. Code: prior git hash + rebuild via compose (see `DEPLOY_V3.md` rollback)
4. Kill switch remains independent of UI version

## Go / no-go

| Gate | Owner |
|------|-------|
| Paper E2E checklist green | Operator |
| `SECRET_KEY` + kill reset set in prod | Ops |
| Explicit “deploy now” approval | Operator |

Without explicit deploy approval, stop after docs + local verify.
