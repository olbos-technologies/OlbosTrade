# Architecture Audit — OlbosTrade

Date: **2026-08-20 (revision 3)** · Full-app re-audit against current `main`.  
Prior: 2026-07-19 (rev 2), 2026-07-16 (original).  
Canvas: `olbostrade-audit-2026-08-20.canvas.tsx`

---

## Verdict (2026-08-20 → patched)

**Paper: mechanically sound.** Single OMS (`_execute_signal`), Step 8 portfolio
gate on-path (Greeks off), mutate auth when `SECRET_KEY` set, kill reset via
env code, Trade Desk V2 **default on** (legacy rollback via Desk Settings).

**Live capital: not yet.** Kill engage remains unauthenticated at FastAPI (by
design / nginx).

### Closed this pass (was open 2026-08-20)

| Was | Issue | Fix |
|-----|--------|-----|
| P1 | Positions + dup guard keyed on `underlying` only | `(underlying, equity\|options)` via `trade_identity.py` |
| P1 | Equity size floor `max(1, shares)` | `compute_equity_trade_plan` allows 0 (OMS skip) |
| P2 | Misleading scan Auto-execute / dead EXECUTE LADDER | Relabeled queue UX; ladder button removed |
| P2 | Options `/signal` equity-shaped | Require `asset_type=options` + spread or 400 |

### Still open

| Sev | Issue | Where |
|-----|--------|--------|
| P1 | Kill engage unauthenticated at FastAPI | `trade_desk.py` (by design; nginx) |
| P2 | Duplicated delta/vega constants | `risk_manager` vs `portfolio_greeks` |
| P3 | Vestigial equity approve; TRADING_POLICY SPY-only; App bundle | cleanup |

### Next

1. Run / sign off `docs/trade-desk-2.0/PAPER_E2E.md` on paper  
2. Only then consider V2 default-on or live prep  
3. Remaining P2/P3 cleanup as capacity allows  

---

### Process note (2026-08-20, later same day) — `main` was red for 9 commits

`backend-tests` failed on **every** push from `7cd5c70` (the identity-key
commit) through `c87013d` — 9 commits, none of them checked with
`gh run list` / `gh run watch` before the next one landed. `gh run list
--branch main` shows the full red streak.

**Cause:** not a production bug. `test_paper_trade_positions.py`'s
`_broker_position()` helper builds a bare `MagicMock()` without setting
`.asset_type`. A real `Position` always has it (`ibkr_client.py` /
`alpaca_client.py` set it explicitly on every returned position), but a
bare `MagicMock()` auto-vivifies `.asset_type` as a Mock object on access
instead of raising `AttributeError` — so `getattr(pos, "asset_type",
"option")`'s fallback default never fires, the `7cd5c70` identity-keying
change silently mismatched, and the tracked-equity test fell through to
the untracked branch. Fixed in `4c248ef`: the helper now sets a real
`asset_type` per call site, matching the actual `Position` contract.

**Ask:** run `gh run list --branch main --limit 1` (or watch the run) after
every push to `main`, the same way this file's own "verify → commit → push
→ CI → deploy → live-verify" pipeline already expects. A red `main` that
nobody checks is how a real regression would ship unnoticed next time —
this particular one happened to be test-only, but the next one might not
be.

---

# Prior revision (2026-07-19)

Date: 2026-07-19 (revision 2) · Read-only pass · Verified against actual code,
not against status labels. Original pass: 2026-07-16 (below, preserved).

**This file was never committed after the 2026-07-16 pass** — it sat untracked
in the working tree for 3 days. That's itself a finding: an audit that isn't
committed isn't part of the record and can be silently lost. Commit this file
once reviewed.

---

## Verdict (2026-07-19): does the app run smoothly?

**Yes, mechanically.** Verified directly, not assumed:

| Check | Result |
|---|---|
| Backend test suite (`pytest`, Docker/py3.11) | **Green** — full suite passes, exit 0 |
| Frontend type check (`tsc --noEmit`) | **Clean** — no errors |
| Frontend test suite (`vitest`) | **Green** — 9 files / 64 tests pass |
| Frontend production build | **Succeeds** — see bundle-size note below |
| Production server (`https://trade.olbos.us`) | **Healthy** — `/api/health` ok, kill switch clear, no new errors in `docker logs` beyond pre-existing IBKR market-data-subscription warnings (unrelated to app code) |

> **Correction (2026-09-17):** the production row above read `:8081` until
> today. The published port has been `8080:3000` in `docker-compose.hetzner.yml`
> since that file was first committed, and the running server publishes 8080 —
> so this line was wrong when written, not stale. It cost real time during an
> outage: the operator's bookmark pointed at `:8081`, the app appeared dead from
> the browser, and the wrong port also led the investigation toward "the server
> config has diverged from the repo" when it had not — this document had.
> `backend/tests/test_docs_use_the_published_port.py` now fails on any doc that
> names a port the compose file does not publish.
>
> **Second correction (2026-09-19):** the row then named the server's public IP
> with port 8080, which was accurate when written and is not any more.
> (Spelling that address out here would trip the guard below, correctly — it
> cannot distinguish a URL quoted as history from one offered as instruction,
> and an exemption for quoted history is exactly the hole real drift would hide
> in. Rewording costs a sentence; a bypass costs the guard.) The frontend's host port
> is now bound to loopback (`127.0.0.1:8080:3000`), so that address answers only
> from on the server — a reader following it from anywhere else gets a hang, the
> same "app looks dead" failure as `:8081`, arrived at from the opposite
> direction: right port, no longer reachable. The row names the HTTPS URL
> instead, which is what a reader should actually open. The guard was extended
> at the same time to distinguish a published port from a loopback-bound one,
> because the original version could not tell them apart and would have gone on
> passing this line.

**But "runs smoothly" and "safe for real capital" are different questions.**
The six P0 findings from the 2026-07-16 pass were genuinely fixed (verified
in code below, not just trusted from status labels) — good news, that gate
holds. Three MED-severity findings from that same pass are **still open**
today, unchanged, and matter specifically once concurrent positions or mixed
equity/options-on-the-same-symbol happen: position identity, the duplicate
guard's asset-type blindness, and duplicated (not unified) risk-limit
constants. None have bitten yet only because paper trading so far hasn't hit
the edge case — one position per underlying, no simultaneous equity+options
on the same ticker in practice.

Separately, this session added a large new surface (Trade Desk V2, portfolio
gate, API-key auth, kill-switch reset code) — all flag-gated/off-by-default,
so it hasn't changed the live-AUTOPILOT risk profile, but it **has not been
paper-walked yet**. See `docs/trade-desk-2.0/PLAN.md`'s re-baseline section
for that gap list; not duplicated here since it's Trade-Desk-2.0-specific
rather than whole-app.

---

## What's fixed since 2026-07-16 (verified in code, not re-argued)

| # | Finding | Verified fix |
|---|---|---|
| 1 | Capital base was 3 different facts (static `starting_capital` vs live net-liq) | `app/services/account_state.py::get_account_value()` — single shared service, imported in `main.py`, `portfolio.py`, `trade_desk.py` |
| 2 | Approvals queue / execution log were in-process dicts, lost on every deploy | Persisted via `ExecutionEvent` model (`app/models/execution_event.py`), queried/written through `trade_desk.py` — survives restarts |
| 3 | Execution mode persisted to `/tmp` inside the container | `execution_mode.py` now writes to DB and has a `rehydrate()` restoring last-set mode on startup |
| 4 | Signal source hardcoded client-side; ChartWorkstation silently substituted another ticker's signal via `\|\| signals[0]` | `EquitySignals.tsx:69` reads `sig.source`; `ChartWorkstation.tsx:435-437` has explicit "no fallback to signals[0]" with a code comment recording why |
| 5 | Background scanner only ever covered `watchlist[:5]` — 8 of 13 charter symbols never scanned | `main.py:558-567` rotates the scan window across ticks (comment documents the prior bug directly) |
| 6 | Regime never checked for staleness — could silently drive strategy selection indefinitely | `main.py:512-549` — explicit age check (`classified_at`), resets to `UNKNOWN` past a max age |

**Also independently resolved (not in the original 6, flagged MED/structural):**
- `SignalDivergence` — previously built but wired nowhere; now consumed in
  `EquityScanPanel.tsx:1115`, rendering when the scan-panel and background-scanner
  signals disagree.
- `iron_condor` structural gap — previously a silent single-leg execution risk;
  now an explicit guarded skip with logging (`main.py:928-933`) plus a
  documented reason (`main.py:893-896`) rather than relying on it just not
  being picked (`REGIME_CONFIG` doesn't guarantee it's never first).

---

## Still open (verified unchanged in code)

| Sev | Subsystem | Location | Issue | Status |
|---|---|---|---|---|
| MED | Reconciliation | `app/api/routes/paper_trade.py:99` | `{t.underlying: t}` still clobbers a second open trade on the same symbol | **Unchanged** — first thing that breaks with multiple strategies per symbol |
| MED | Duplicate guard | `app/api/routes/trade_desk.py:823-838` | Guard keys only on `Trade.underlying == ticker`, no asset-type filter — an open SPY option spread still blocks a SPY *equity* signal and vice versa | **Unchanged** |
| MED | Risk limits | `risk_manager.py:57-58` vs `portfolio_greeks.py:37-38` | `MAX_PORTFOLIO_DELTA`/`MAX_VEGA_EXPOSURE` (0.30/0.15) duplicated verbatim in two services instead of one shared module | **Unchanged** — currently consistent by coincidence, not by design; a future edit to one and not the other silently diverges |
| LOW | Dead endpoint | `app/api/routes/equity.py:107-114` | `POST /api/equity/signals/{id}/approve` stamps `approved_at` on an in-memory dict and executes nothing — a second, vestigial "approval" concept beside the real queue | **Unchanged** |
| LOW | Docs drift | `TRADING_POLICY.md:34-35` | Still states "Options spreads are SPY-only" — false since QQQ options scanning shipped (`options.py`'s scan route covers SPY and QQQ) | **Unchanged** |
| LOW | Scale | `main.py` serial per-ticker scan loop; `equity_scan_engine.py` serial per-ticker yfinance calls | Still fully serial behind 90s `_guarded` timeouts on unauthenticated yfinance | **Unchanged** — matters more as watchlist rotation (fix #5) now actually reaches all 13 symbols instead of always the same 5 |
| NEW-LOW | Frontend bundle | `frontend/dist/assets/App-*.js` | 477.8 kB / 108.4 kB gzip, single bundle, no route-based code splitting | Not urgent today; will grow as Trade Desk V2 desks add more code — worth revisiting once V2 goes default-on |

---

## Recommended order (unchanged priorities, next up)

The six P0 items are done. Next tier, in order of blast radius if a real
multi-position/multi-asset-type paper session actually exercises them:

1. **Position identity** (`paper_trade.py:99`) and **duplicate guard**
   (`trade_desk.py:823`) share one root cause — identity keyed by underlying
   string only. Fix together: key on `(underlying, asset_type)` or
   `(underlying, strategy)`. This is the one most likely to produce a
   confusing real bug the moment you hold, say, SPY shares and a SPY option
   spread at the same time.
2. **Unify risk-limit constants** into one module `risk_manager.py` and
   `portfolio_greeks.py` both import from — currently identical by luck.
3. **Docs/dead-code cleanup** (TRADING_POLICY.md SPY-only line, the vestigial
   equity approve endpoint) — cheap, no behavior risk, just correctness of
   the record.
4. **Commit this file.** An uncommitted audit is a lost audit.

Trade-Desk-2.0-specific gaps (Strategy Builder, Roll Manager, GEX/IV data,
paper walkthrough) are tracked separately in `docs/trade-desk-2.0/PLAN.md` —
intentionally not merged into this list since that surface is still
flag-gated and off by default.

---

# Original audit (2026-07-16) — preserved for record

Date: 2026-07-16 · Read-only pass · Findings only, no fixes applied.

## Scope corrections (prompt vs. repository)

The audit brief described a stack this repository does not have. Audited against
what actually exists:

| Brief said | Repository reality |
|---|---|
| C++20 execution engine | No C++ anywhere. Execution is Python: FastAPI + `ib_insync` 0.9.86 (`backend/app/broker/ibkr_client.py`) |
| `ib_async` | `ib_insync` (pre-fork) — `backend/requirements` / import at `ibkr_client.py:16` |
| Redis | No Redis in any compose file or dependency; state is PostgreSQL + in-process memory |
| XGBoost regime classifier | Regime classifier is **rule-based** VIX/ADX (`regime_classifier.py:51-109`). XGBoost is the *signal scorer* (`signal_scorer.py`), and it is currently running its **heuristic fallback** — no trained model exists on disk |
| STRATEGY.md / RED_TEAM.md / DATA.md / CAPITAL_PLAN.md / AUDIT.md | Only close analogues exist: `TRADING_POLICY.md` (charter), `AUDIT_2026-06.md` + `backend/AUDIT.md` (security-correctness passes), `ARCHITECTURE_MEMO.md`. No DATA.md or CAPITAL_PLAN.md equivalent exists — data-contract and capital-plan expectations could not be diffed against a spec |

Items already closed in `backend/AUDIT.md` / `AUDIT_2026-06.md` / `CHANGELOG.md` are
**not** re-flagged here: single fail-closed `_execute_signal` pipeline, dispatch-id
idempotency, per-share limit price, kill-switch restart rehydration, bracket
parent-ack race (`080f991`), pending-order grace-period restart reset (`274a7ed`),
and the dormant PaperTrader/ExecutionDispatcher stack (since removed from
`app/services/`). The **no-API-authentication** finding remains OPEN but is owned by
`AUDIT_2026-06.md` ("Open" section) — security scope, not re-argued here.

**2026-07-19 update: the no-API-authentication finding is now CLOSED** —
`app/api/deps.py::require_api_key` / `require_api_key_configured`, gated on
`SECRET_KEY`, applied to all trade-desk mutate routes and kill-switch trigger.

---

## Verdict: the signal question

### Which signal does AUTOPILOT actually execute on?

**Exactly one pipeline executes. Everything else is display.** As the code stands:

- AUTOPILOT executes only signals produced by the **background scanners** in
  `backend/app/main.py` — `_run_equity_scan` (main.py:479, using
  `equity_signal_engine.score_equity_signal` on 250 daily yfinance bars,
  main.py:504) and `_run_options_scan` (main.py:706, delegating to the
  `strategy_engine.py` classes). Both route through `handle_signal`
  (trade_desk.py:671) → `_execute_signal`, the single fail-closed gate
  (kill switch → market hours → guardrails → frequency controller →
  strategy health → duplicate guard → sizing → broker).
- The **scan panels** (`equity_scan_engine.py`, `options_scan_engine.py`, exposed at
  `POST /api/equity/scan` and `POST /api/options/scan`) **cannot reach AUTOPILOT**:
  `submit_scan_signal`'s autopilot branch is explicitly disabled pending tests
  (trade_desk.py:777-796) — everything queues for approval.
- **Chart Intelligence** (`services/chart/`, `/api/chart`) is declared and verified
  evidence-only (chart.py:4-6) — never touches the order path.
- The new **Options Decision Engine** (PR #35) is an unadopted additive endpoint —
  not in any execution or scan path yet.

So there is **one canonical executing source of truth**. The conflict is not two
pipelines competing for execution — it is **four separate BUY/SELL computations on
screen, with the recent labeling pass attributing them to the wrong sources**:

1. **The labels name the wrong engine.** `EquitySignals.tsx:61` hardcodes
   `source: "Equity Scan Engine"` onto signals that come from `/api/equity/signals`
   → `_recent_signals` → the **background scanner** (`equity_signal_engine`) — i.e.
   the label names the *other, non-executing* module (`equity_scan_engine.py`).
   `TradeDesk.tsx:187` does the same for the approvals queue (also fed by the
   background scanners), and `EquityScanPanel.tsx:551` uses `"equity_scan_engine"`
   for the panel that genuinely is that engine. Two different computations now
   share one name in the UI. The labels are real strings, but they are **client-side
   constants, not wired to the producing engine** — a label on top, exactly what the
   brief asked to check for.
2. **The two equity computations genuinely disagree, systematically.** The scan
   panel's engine calls the *same* `score_equity_signal`
   (equity_scan_engine.py:331) but feeds it only **120 bars**
   (equity_scan_engine.py:244, 296). EMA200 needs 200 bars; with 120 it is NaN, so
   `above_ema200` is forced False (equity_signal_engine.py:111), which both drops
   a +0.5 bull point (line 163-165) **and adds a −0.5 bear point** (line 209-211).
   The panel is therefore ~1.0 point more bearish than the pipeline AUTOPILOT
   trades, on identical market data. This is the exact bug `AUDIT_2026-06.md`
   ("Equity signals skewed bearish — FIXED") closed — but the fix was applied to
   only one of the two call sites (main.py:504 comment documents the 250-bar fix).
3. **The "TA Trade Plan" panel is neither TA nor necessarily the right ticker.**
   `ChartWorkstation.tsx:540` renders entry/stop/target/R:R from
   `selectedSignal.trade_plan` — the background scanner's plan, not a chart-derived
   one — and `selectedSignal` falls back to `signals[0]`
   (ChartWorkstation.tsx:351-354): **chart symbol X with no recent signal, and the
   panel silently shows another ticker's trade plan**, mixed with symbol-X
   support/resistance fallbacks (lines 358-363). The bare `Signal: BUY/SELL` chip at
   line 495 and the right-rail watchlist tags at 616-617 come from the same store
   with no attribution — the "bare badge is a defect" rule from PR #39's CHANGELOG
   was applied to only 4 files; this page was missed.
4. **`SignalDivergence` — the component built to disclose exactly this
   disagreement — is wired nowhere** (only its own file and test reference it),
   matching the CHANGELOG's own "known limitation."

**2026-07-19 update: all four sub-findings above are now CLOSED.** See "What's
fixed since 2026-07-16" table at the top of this file.

### Recommended resolution (structural, not a patch)

Make signal identity a **server-side, first-class fact** — the direction
`ARCHITECTURE_MEMO.md` already names ("signal-to-order linkage as a first-class
entity"):

- One shared signal-computation service: single bar-fetch (one depth: 250),
  single indicator pass, single scorer invocation. The background scanner and the
  scan panel *consume* the same persisted signal record instead of independently
  recomputing it at different depths and times.
- Every signal payload carries `engine`, `engine_version`, `bar_window`,
  `computed_at`, and a persistent `signal_id`. UI panels render `source` **from the
  payload** — no hardcoded strings anywhere in the frontend.
- The Chart Workstation panel either becomes genuinely chart-derived (and says so)
  or displays the persisted scanner signal under its real name; the
  `|| signals[0]` cross-ticker fallback is removed outright ("no signal for
  SYMBOL" is the honest render).
- Wire `SignalDivergence` at the one place both computations are visible, until the
  consolidation above makes it moot.

**Note:** AUTOPILOT is not blocked today — it is **ON in production (paper)** and
opened NVDA/AMZN/AAPL positions on 2026-07-14. The findings below are what should be
true before that stops being a paper account.

---

## Findings

| Sev | Subsystem | Location | Issue | Recommended direction | 2026-07-19 status |
|---|---|---|---|---|---|
| HIGH | Frontend / Chart | `ChartWorkstation.tsx:351-354, 358-363, 495, 540, 616` | "TA Trade Plan" shows the background scanner's plan labeled as TA, silently substitutes another ticker's signal via `\|\| signals[0]`, and renders bare unattributed BUY/SELL chips | Remove cross-ticker fallback; attribute source from payload; rename or re-derive the panel | **FIXED** |
| HIGH | Signal engines | `equity_scan_engine.py:244, 296` vs `main.py:504` | Scan panel feeds 120 bars into a scorer needing 200 (EMA200) → systematic ~1.0-pt bearish skew vs the executing pipeline; prior audit's fix applied to one of two call sites | One shared bar-fetch/indicator service; delete the second fetch path | Not re-verified this pass — recommend re-checking bar depth at both call sites before next capital-risk decision |
| HIGH | Attribution | `EquitySignals.tsx:61`, `TradeDesk.tsx:187`, `EquityScanPanel.tsx:551` | Source labels are client-side constants and misname the executing pipeline; two engines share one display name | `source` set by the producing engine, carried in the payload end-to-end | **FIXED** (`EquitySignals.tsx:69` now reads `sig.source`) |
| HIGH | State ownership | `trade_desk.py:149-150` | Copilot approval queue (`_pending_approvals`) and execution log (`_execution_log`) are in-process dicts — every deploy/restart silently drops pending approvals and the audit trail | Persist both (memo's canonical order/audit lineage) | **FIXED** |
| HIGH | State ownership | `main.py:528` vs `main.py:1044` vs `api/routes/portfolio.py:33-35` vs `trade_desk.py:95-103` | Capital base is three different facts | One account-state service owning net-liq / buying power | **FIXED** |
| HIGH | Scheduler / coverage | `main.py:497` + `config.py:64-66` | `watchlist[:5]` — 8 of 13 charter symbols never scanned | Rotate the window per tick | **FIXED** |
| MED | Regime | `main.py:380-397`; `regime_classifier.py:151-155` | No staleness check on regime age | Max-age gate; degrade to UNKNOWN | **FIXED** |
| MED | Regime | `main.py:331-346` vs `main.py:722` | Fail-open (equities) vs fail-closed (options) on unknown regime — two policies for one condition | Pick one, document it | **Not re-verified this pass** |
| MED | Risk limits | `risk_manager.py:57-61` vs `portfolio_greeks.py:37-38, 131` | Same delta/vega limits duplicated in two services | Single limits module | **OPEN — unchanged** |
| MED | Reconciliation | `paper_trade.py` + `trade_identity.py` | Position identity keyed by `(underlying, equity\|options)` | Normalized position identity | **CLOSED** |
| MED | Duplicate guard | `trade_desk.py` Stage 3 | Guard keys on asset class with underlying | Key on (underlying, asset_type) | **CLOSED** |
| MED | Options | `main.py:757-763`; `trade_desk.py:579-592` | `iron_condor` regime-allowed but structurally 2-leg-only unexecutable | Wire 4-leg combo or remove from config | **FIXED** — explicit guarded skip, logged, documented |
| MED | Divergence UX | `components/SignalDivergence.tsx` (no consumers) | Built, tested, unused while the disagreement it targets is live | Wire it | **FIXED** — now consumed in `EquityScanPanel.tsx` |
| LOW | Docs drift | `TRADING_POLICY.md:34-37` | "SPY-only" claim false since QQQ shipped | Update charter | **OPEN — unchanged** |
| LOW | Docs drift | audit brief itself | Stack description matches no code in this repo | Correct source doc | N/A (external) |
| LOW | Dead endpoint | `equity.py:107-114` | Vestigial in-memory "approve" beside the real queue | Remove or route into real queue | **OPEN — unchanged** |
| LOW | Scale | `main.py:349-360`; `equity_scan_engine.py:430s` | Everything serial behind 90s timeouts on unauthenticated yfinance | Work queue with per-tick budget | **OPEN — unchanged**, now matters more since watchlist rotation reaches all 13 symbols |

## Fix before enabling AUTOPILOT (beyond paper) — original list, all six done

1. ~~Capital base unification~~ — **done**
2. ~~Persist the approvals queue and execution log~~ — **done**
3. ~~Persist execution mode off `/tmp`~~ — **done**
4. ~~Signal source of truth + attribution from payload~~ — **done**
5. ~~Watchlist coverage honesty~~ — **done**
6. ~~Regime staleness gate~~ — **done**

All six were prerequisites for trusting any live-capital session and the
signals it executes. Verified genuinely fixed, not just marked complete —
see "What's fixed" table at top. Next tier is the three still-open MED items
above (position identity, duplicate guard, risk-limit duplication).
