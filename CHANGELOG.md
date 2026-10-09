# Changelog

## Unreleased — The duplicate-position guard is no longer a bare read

**Requires a migration (0040). `deploy/hetzner/update.sh` now applies it from
the newly built image before any new container serves — see docs/runbook.md.
No new environment variables.**

- `_execute_signal` Stage 3 asked the database whether an open or pending
  trade already existed for (underlying, asset class) and skipped if one did.
  That read is correct and **not sufficient**: the row it looks for is written
  only *after* the broker accepts, so two signals arriving inside that round
  trip both read zero rows, both pass, and both submit — one position, two
  economic orders.
- A **position claim** is now taken before submission. `INSERT ... ON CONFLICT
  DO NOTHING` against a composite primary key lets exactly one concurrent
  entry proceed; the loser skips with `entry_in_flight`.
- The trade read now runs **again after the claim is won**. The first read and
  the claim are separate round trips, so a caller could read "no trade",
  stall, and win a claim another caller had just released by recording its
  position. Winning the claim is what makes the second read conclusive.
- A claim has a **state, not just a lease**. Only `pending` claims — nothing
  sent to the broker — are reclaimable by time. Once intent is recorded the
  lease stops applying, because a timer expiring is not evidence that no order
  exists. A submission that times out leaves the claim `unknown`, which blocks
  re-entry until reconciled.
- **Intent is recorded before the broker call**, so a process killed mid-submit
  cannot leave an order placed and nothing persisted. On the options path the
  claim's key rides along as `client_order_id`, which Alpaca honours, making a
  retry safe there; `place_equity_order` accepts no such key, so on the equity
  path the broker has no dedup of its own — which is why a submitted claim
  must never free itself on a timer.
- `reconcile_unresolved()` asks the broker what became of each unresolved
  claim and distinguishes **three** answers, where it previously had two. The
  lookup returns a `BrokerVerdict`: `ABSENT` (the broker states positively
  that no such order exists), `PRESENT`, or `INDETERMINATE` — not visible yet,
  degraded, unrecognised. Only `ABSENT` releases. The old
  `Optional[order]` contract collapsed "there is nothing there" and "I cannot
  see one yet" into `None`, and order-by-client-id lookups are not immediately
  consistent, so an order accepted moments earlier could read as absent and
  free the claim guarding it.
- Even `ABSENT` is not believed inside a **settle window** (120s by default,
  measured database-side). A broker that has accepted an order can still answer
  "no such order" briefly; waiting costs one blocked entry, believing it costs
  a duplicate position.
- A lookup that raises still resolves nothing, and each outcome is counted
  separately — `released`, `still_unresolved`, `indeterminate`, `unreachable`,
  `too_fresh` — so a quiet broker cannot look like a clean sweep.
- **Reconciliation now runs.** It had no caller: the mechanism existed and
  nothing ever invoked it, so in production a crashed submit blocked its
  position until a human noticed. A sweep runs at startup — the case the
  lifecycle exists for, since the previous process may have died mid-submit —
  and every 5 minutes from the scheduler, with bounded retries (3, exponential
  backoff) that re-ask only while the broker is the thing not answering. A
  sweep that exhausts them logs CRITICAL naming each blocked position and the
  endpoint that clears it.
- **Broker lookup is per-broker and fails safe by default.**
  `BrokerInterface.find_order_by_client_order_id` returns UNDETERMINED unless
  a broker overrides it, so a broker is opted *in* to authoritative absence
  rather than opted out by omission. Alpaca overrides it against
  `/v2/orders:by_client_order_id`, which is indexed by client id — a 404 there
  is a statement about that id, not an empty page of a filtered list. A 5xx,
  429, timeout, transport error or unexpected status is UNDETERMINED. IBKR
  does not override it, so IBKR claims are never released automatically.
- **An equity claim is never resolved by lookup.** Only the options path sends
  the claim's key as `client_order_id`, so Alpaca's 404 for an equity key
  means "nothing was ever tagged with this" — true, and silent on whether an
  equity order exists. Releasing on it would free the claim on exactly the
  asset class with no broker-side dedup behind it. A guard test fails if
  `place_equity_order` gains a client id and the rule is not updated.
- **Waiting is never evidence.** The settle window only makes an authoritative
  NOT_FOUND believable; it does not turn silence, an unsupported lookup or an
  untransmitted key into absence.
- `force_release()` gives an operator a way to clear a claim the broker will
  never answer for. The delete and an `execution_events` row of kind
  `claim_override` are written in **one transaction**, so a release cannot land
  without a record of who did it and why; an override that matched no claim
  writes nothing. It is reachable over HTTP at
  `GET /api/admin/position-claims` and
  `POST /api/admin/position-claims/{claim_token}/release`, under `/api/admin/`
  because the session allowlist matches on path alone. Identity comes from
  authentication, never the request body, and a caller with no identity is
  refused rather than recorded as nobody; the reason is required and cannot be
  blank.
- Every state change is keyed on a **unique claim token**, so a worker whose
  claim was reclaimed cannot release or advance whoever holds the position
  now. Lease comparisons are **database-side** throughout; no timestamp
  travels from a worker's clock.
- Claims are **global** (`scope` column, always `'global'`). Positions are not
  yet owned — `trades` has no organization column — so scoping claims per
  tenant while the duplicate read beside them stays global would make the two
  disagree. The column exists so the batch that gives trades an owner can fill
  it in without reshaping a primary key.
- A claim that cannot be taken **fails closed**, matching the duplicate read
  beside it, and now says which of the two failed: `entry_guard_unavailable`
  for the claim, `duplicate_check_error` for the read. They previously shared
  one reason, so a test asserting "blocked" passed whether the guard worked or
  something unrelated broke — an accident counting as proof of the intended
  behaviour. Nothing outside the entry path consults claims, so exits, fill
  polling, reconciliation and the kill switch are unaffected when the table is
  missing — pinned by a test.
- A claim survives a process restart and still blocks, which is the case the
  whole lifecycle exists for; tests drive crash → restart → broker lookup →
  resolution through a genuinely new engine and session factory over the same
  rows, including an order that only becomes visible on the second sweep.
- The test suite no longer cancels background tasks indiscriminately. It stops
  three **named** services through their own shutdown APIs
  (`conftest.BACKGROUND_TASK_OWNERS`), because a blanket `asyncio.all_tasks()`
  sweep would silence the same warnings while hiding the lifecycle defect
  behind them — a service with no shutdown path would look clean in the suite
  and go on leaking in production.
- `deploy/hetzner/update.sh` is now **executed** in tests, not just parsed,
  against a real disposable PostgreSQL with Docker stubbed: ordering, a real
  `alembic upgrade head`, and a failed migration leaving the running service
  untouched.
- Deploy ordering is fixed rather than documented around. `update.sh` used to
  start containers and migrate afterwards, leaving new code serving an old
  schema in between, and the runbook's suggested pre-apply —
  `docker exec olbostrade-backend alembic upgrade head` — ran inside the
  container **still on the old image** and so applied nothing while reporting
  success. The migration now runs in a throwaway container from the image just
  built, before `up -d`, and a failure stops the deploy with the old image
  still serving a schema it matches.

## Unreleased — Autopilot is restored from the record again

Reverses the restart behaviour shipped in "Execution-mode changes state their
durability", which refused to restore Autopilot at all. That did close the
unpersisted-reduction hazard, but it disarmed automation on **every** deploy —
a cure firing on every restart for a fault firing on almost none.

- `rehydrate()` restores the recorded mode as recorded, Autopilot included,
  and logs a warning when it does so.
- What makes the record trustworthy instead: **a reduction that fails to
  persist is now retried** (2s, backing off to 60s, 20 attempts) until it
  lands. The window in which the newest row on disk is more permissive than
  the running mode now ends when the database comes back, rather than when
  somebody notices. A retry is abandoned if the mode moves on, so a superseded
  reduction cannot be re-recorded over a newer decision, and giving up is
  logged CRITICAL.
- **Known residual risk, stated rather than designed around:** if the process
  dies during that window — database down, reduction applied in memory, no
  retry landed — nothing durable records the reduction and startup restores
  the older Autopilot row. Recovering that would require the information the
  failed write is precisely what did not record. The kill switch persists
  separately and rehydrates fail-closed; it is the control that does not
  depend on this path.
- Fixes a bug from the same earlier change: `summary()` called with no
  arguments — which is what `GET /api/trade-desk/execution-mode` does —
  reported `persistence: "confirmed"` even while an unrecorded reduction was
  outstanding, hiding exactly what those fields exist to show. It now derives
  the value from state.

## Unreleased — Journal entries are owned by an organization

**Requires a migration (0039). No new environment variables.**

- `journal_entries` had **no ownership column at all** — not `organization_id`,
  not even `user_id` — and every journal route was a shared query. Listing
  returned everyone's entries; fetching or updating by id worked on any row
  regardless of who asked; the analytics endpoints aggregated across all
  tenants.
- Every journal read and write is now scoped to the caller's organization,
  resolved from the authenticated server context (`owner_scope`). Nothing a
  client sends takes part in deciding what it can see.
- **Cross-organization ids answer 404, not 403**, and with the same detail
  string a nonexistent id gets — a 403 would confirm the row exists.
  Ownership is part of the lookup rather than a check afterwards.
- **Migration 0039 does not guess.** `organization_id` is nullable and stays
  that way: a pre-ownership entry's author is not recoverable from the row.
  The backfill assigns existing entries only when there is **exactly one**
  personal organization (the single-operator case). With zero or several it
  leaves them unattributed and raises a NOTICE with the count, because handing
  one customer's trading journal to another is worse than leaving old rows
  unowned.
- **NULL is a scope, not a wildcard.** Unattributed rows match only the
  single-operator scope used when auth is disabled, so they are invisible to
  every organization-scoped query and fail closed.
- `list_entries` no longer swallows an auth failure into
  `{"entries": [], "error": ...}`. Rendering "you are not allowed to see this"
  as "you have nothing" reads as a safe value and is not one.

Isolation for `trades` is **not** in this change — the model is read by eight
route modules (analytics, portfolio, risk, rotation, strategy, paper_trade,
research, trade_desk) and warrants its own batch.

## Unreleased — Strategy validation requires traceable evidence

- **Hardcoded metrics can no longer validate a strategy.** The Research Lab UI
  posted literal values — `sharpe: 1.0`, `oos_sharpe: 0.9` — into the
  transition route, and the gates, which evaluate whatever dict they are
  handed, cleared them. A strategy could reach a validated stage without
  anything ever having been measured. Those literals are gone.
- **Clients may no longer supply metrics at all.** The route refuses
  `metrics` / `wf_metrics` in a request body and instead takes a
  `backtest_run_id`. The server loads that `BacktestRun`, checks it completed
  and that its strategy matches the experiment, reads the metrics from the
  stored row, and writes a `provenance` block (engine, run id, strategy,
  dataset window, starting capital, parameters including commissions and
  slippage, recorded-at). The `sharpe_ratio` → `sharpe` key mapping moved
  server-side too, so a client can no longer get it wrong — nor quietly right
  by sending its own number.
- Evidence without complete provenance is **unverified** and cannot clear a
  gate. Promotion additionally re-checks the backtest behind the experiment,
  so a demo-seeded experiment cannot become live-eligible on the strength of a
  real paper record alone.
- **Existing history is preserved, not rewritten.** Rows written before
  provenance existed keep their numbers and simply stop clearing gates, with
  an explanation naming what is missing. A rejected transition changes
  nothing.
- **The walk-forward → paper transition is disabled.** Nothing in this build
  computes out-of-sample metrics: `oos_sharpe` appears only in the gate and
  the route that fed it, and the sole producer was that literal object in the
  UI. Rather than keep judging whatever a caller sends, the transition now
  refuses and states what is missing. `evaluate_walkforward_gate` is retained
  and still tested, ready for a real engine.

## Unreleased — Broker-neutral emergency stop, with verified flatness

- **Cancellation now works on every broker.** It sat inside
  `if hasattr(self._broker, "ib")`, so on Alpaca the entire step was skipped —
  no orders cancelled, **no error recorded**, `orders_cancelled` left at 0. A
  caller could not tell "nothing to cancel" from "never tried", and working
  orders stayed live while the positions underneath them were flattened. A
  resting entry filling afterwards re-opens the exposure the stop existed to
  remove.
- New `BrokerInterface.cancel_all_open_orders()` sweeps the whole account and
  returns a `CancelSweep` of `requested` / `cancelled` / `unresolved` /
  `enumeration_error`. The existing per-symbol `cancel_open_orders` cannot
  substitute: it only visits symbols the caller already knows about, which
  during a stop means symbols that still have positions — a working order for
  a symbol with **no** position is invisible to it.
- **Flatness is now verified, not inferred.** `positions_flattened` counts
  every non-rejected result, so an accepted-but-unfilled market order, a
  partial fill and a venue cancellation all increment it. A reconciliation
  step re-reads the broker; `reconciliation.flat` is `True` only when no
  non-zero position and no unresolved order remain, and `None` — unknown, not
  `False` — when the read itself failed.
- **Re-engaging re-verifies instead of reassuring.** It returned
  `already_engaged` and nothing else, which reads as success while unresolved
  orders and residual positions sit untouched. It now reconciles and reports
  the exposure.
- **Options flatten orders now state that they close.** `position_intent` was
  hardcoded to `*_to_open` for every leg, so the kill switch asked the broker
  to open a naked short in the contract it was trying to close. `SpreadLeg`
  gains `intent` (`open` by default, so no existing caller changes behaviour);
  the flatten path sets `close`.

## Unreleased — Execution-mode changes state their durability

- Raising automation (Manual → Copilot → Autopilot) now **records the decision
  before activating it**. Previously `set_mode` mutated the runtime first and
  swallowed the write's failure, so a database outage produced a live Autopilot
  and an ordinary success response — the machine trading automatically on a
  decision nothing durably held. A failed write now leaves the mode unchanged
  and the route answers `503` instead of `200` with the old mode in the body.
- Reducing automation, and engaging an emergency stop, still take effect
  **immediately even during a database outage**. Making a safety reduction wait
  for a database that may be the broken thing would be the same mistake with
  the opposite sign. A reduction that could not be recorded is surfaced as
  `persistence: "unconfirmed"` rather than logged and forgotten.
- `summary()` now states durability explicitly: `confirmed`, `unavailable`
  (requested but **not** in force), `unconfirmed` (in force, not recorded) or
  `stale`, plus `requested_mode` when the two differ. A failed write used to be
  indistinguishable from a successful one.
- **Autopilot is no longer auto-restored on restart.** A reduction out of
  Autopilot that failed to persist leaves the older, more permissive row newest
  on disk, so restoring it would hand automation back silently as a side effect
  of a restart. It now restores as Copilot — every signal is kept, a human is
  still asked — with `restore_note` explaining why. **This is a behaviour
  change: Autopilot must be re-engaged explicitly after a deploy.**

## Unreleased — Copilot approval is a single-use atomic claim

- `_resolve_pending_approval` now claims a pending approval with one
  conditional `UPDATE ... WHERE status='pending' RETURNING`, replacing an
  unlocked `SELECT` followed by an ORM write. Under READ COMMITTED that read
  took no row lock, so two concurrent approvals of one signal could both see
  `pending`, both commit, and both return a payload — **two broker orders for
  one signal**. The rotation path next door already used `SELECT ... FOR
  UPDATE`; this path did not.
- A concurrent approve/reject pair now produces exactly one terminal decision
  instead of submitting and recording a rejection for the same signal.
- Approval and rejection record the authenticated actor (`approved_by_actor` /
  `rejected_by_actor`) alongside the existing role label, and the decision is
  merged into the stored payload. When auth is disabled the field is omitted
  rather than filled with a placeholder.
- Concurrency is now covered against a real PostgreSQL
  (`tests/test_approval_concurrency_pg.py`), and CI gained a `postgres:16`
  service so those tests run instead of skipping. The pre-existing mock-based
  tests passed throughout the window in which this bug was live.

## Unreleased — Instrument console UI (skeuomorphic-lite)

- Terminal chrome: raised bezels on panels/buttons, rack ticker + status bars,
  instrument chips on Trade Desk header, physical-style kill + mode cards
- Typography: IBM Plex Sans + JetBrains Mono (replaces Inter)
- Explicitly not glass/neumorphism/clay — crisp console, high contrast

## Unreleased — Trading Style in Trade Desk V2

- Desk Settings now includes Trading Style (Conservative / Balanced /
  Aggressive / Scalper) — was only on the legacy Trade Desk page
- Mode cards show min confidence + daily hard max from the frequency controller

## Unreleased — Position rotation at max concurrent

- When `POSITION_ROTATION_ON_MAX=true` and max open positions blocks a new
  **equity** entry, close N positions (default 2): highest unrealized P&L,
  then lowest `signal_score` (oldest if scores missing). Skips the incoming
  ticker; options closes deferred. Flag **off** by default.

## Unreleased — Trade Desk V2 default-on

- Product defaults: `trade_desk_v2` + equity/options/copilot/execution/replay/
  mobile flags **on**; experimental desks stay off
- Desk Settings copy: default V2 with localStorage rollback to legacy
- Paper E2E decision updated accordingly

## Unreleased — P1 identity / sizing / scan honesty

- Position identity + OMS duplicate guard key on `(underlying, equity|options)`
  so SPY stock and SPY spreads coexist (`trade_identity.py`, `paper_trade`,
  `_execute_signal` Stage 3)
- Equity sizing allows **0 shares** (skip) instead of forcing `max(1, …)`
- Scan panels: “Queue top for approval” (not Auto-execute); removed dead
  EXECUTE LADDER; options `/signal` requires `asset_type=options` + spread
- Paper E2E updated; **V2 flags stay opt-in** (no default-on)

## Unreleased — Feature flags opt-in (pre-deploy)

- Trade Desk V2 flags default **off** (`trade_desk_v2` and desk/monitor/replay/mobile)
  so deploy keeps legacy desk until operator enables V2 in Desk Settings / localStorage
- Desk Settings copy updated to describe opt-in; rollback still `olbos.flags.trade_desk_v2=0`

## Unreleased — Claude Code stage (integration / paper E2E / deploy prep)

- Docs: `docs/trade-desk-2.0/INTEGRATION_AUDIT.md`, `PAPER_E2E.md`, `DEPLOY_PREP.md`
- Hardening: equity `/signal` now carries `shares` + `trade_plan` (composer + scan)
- Read-only smoke: `scripts/paper_e2e_smoke.sh` (no orders; no production deploy)

## Unreleased — Trade Desk 2.0 Phase F (Replay + a11y/mobile)

- Trade Replay MVP: trade tape + detail (paper/live account label, source, journal snapshot)
- Desk shell: skip link, landmarks; scrollable tabs; mobile bottom tab strip (`mobile_trade_desk`)
- History API exposes `trading_mode`; flags `trade_replay_v2` + `mobile_trade_desk` default on

## Unreleased — Step 8 (money-path portfolio gate)

- `_execute_signal` Stage 2b: max positions, underlying/sector concentration, heat-high
- Module `execution_portfolio_gate.py`; Greeks caps off by default (miscalibrated)
- Rollback: `EXECUTION_PORTFOLIO_GATE=false`; evaluate-* endpoints surface the same checks

## Unreleased — Trade Desk 2.0 Phase E (thin Copilot / Orders / Execution)

- Copilot Queue v2: pending approvals + recent decisions audit (existing approve/reject APIs)
- Orders workspace: lifecycle filters from queue + execution log (honest: no broker ack table yet)
- Execution Monitor: timeline + submitted/blocked/skipped/rejected counts
- Flags `copilot_queue_v2` + `execution_monitor_v2` default on; `_execute_signal` unchanged

## Unreleased — Trade Desk 2.0 Phase D (Options Desk)

- Options Desk: discovery + Chain / Chart / Scanner / Income / Flow / Signals /
  0DTE (read-only) tool tabs; intelligence rail with analyze preview + Greeks
- `POST /api/trade-desk/evaluate-options` advisory gate (no `_execute_signal`);
  bans naked shorts / iron condor; 0DTE Autopilot explicitly off
- OptionsChain accepts controlled `symbol`; `options_desk_v2` default on

## Unreleased — Trade Desk 2.0 Phase C (Equity Desk)


- Equity Desk: discovery rail, compact ChartWorkstation embed, intelligence
  rail (bias/alignment/structure + portfolio heat + setup readiness)
- Order composer: `POST /api/trade-desk/evaluate-equity` (advisory, no
  `_execute_signal`) then submit via existing `/api/trade-desk/signal` queue
- ChartWorkstation accepts optional controlled `symbol` + `compact` mode
- Flag `equity_desk_v2` default on; no money-path gate changes

## Unreleased — Trade Desk 2.0 Phase B (shell)


- Feature flag `trade_desk_v2` (default on; rollback via Desk Settings or
  `olbos.flags.trade_desk_v2=0`)
- Trade Desk shell: header (Paper/Live labeled), tabs, Command Overview
  (read-only queues from pending / execution log / positions / risk)
- Sidebar IA under flag: Options under Trade Desk group; options tools stay
  under Strategies (advanced) until Phase D
- Copilot / Positions reuse legacy TradeDesk tabs; Equity/Options/Orders/Replay
  show honest Phase placeholders
- No `_execute_signal` or money-path changes

## Unreleased — Security: kill-switch reset + trade-desk mutate auth


- Kill-switch reset code moved to server env `KILL_SWITCH_RESET_CODE` (no
  default; reset disabled until configured). Frontend prompts for the code;
  the old hardcoded `OLBOSTRADE_MANUAL_RESET` string is removed from the bundle
  and from `POST /api/trade-desk/kill-switch` reset.
- Trade-desk mutate routes (`execution-mode`, `approve`, `reject`,
  `manual-trade`, `signal`) require `X-Api-Key` when `SECRET_KEY` is set.
  Operator pastes the key once per session (Risk → Operator API Key →
  sessionStorage). Engage kill switch remains FastAPI-open for emergencies
  (nginx Basic Auth still applies in production).

## Unreleased — Options Signal cards on Strategies → Signals


- Strategies → Signals: OPTIONS | EQUITIES toggle beside broker/Greeks strip
- Options cards mirror equity layout: strategy, BUY_SPREAD/SELL_SPREAD
  attribution, POP/confidence, IV rank + Greeks pills, credit/debit · max
  loss · breakeven, contracts/DTE
- Backend: in-memory store + `GET /api/options/signals` and
  `POST /api/options/signals/scan` (SPY/QQQ); background scanner persists
  into the same store

### Fixed
- **`POST /api/options/signals/scan` no longer dispatches to execution.**
  It originally called `_run_options_scan()` the same way the scheduled
  background scanner does, ending in `handle_signal()` — meaning a UI
  action that looks like "refresh the signal feed" could submit a real
  order if Autopilot happened to be the active execution mode, with no
  indication of that on the page. `_run_options_scan()` now takes an
  `execute` flag (default `True`, unchanged for the scheduler); the preview
  endpoint passes `execute=False`, computing and persisting the same
  strategy/strike recommendation but never reaching `handle_signal()`. Only
  the scheduled background scanner executes. Verified live against the test
  container in Autopilot mode. Known limitation: iron_condor still never
  appears (execution or display) — its 4-leg structure isn't wired into the
  current spread data model.

## Unreleased — UI/UX Phase 4: landing trust + attribution display

Frontend-only public-page honesty from `docs/ui-ux-phase1-4/PLAN.md` Phase 4.
No trading, risk, execution, sizing, or authentication logic changed. No backend
`source` field added (deferred pending operator approval).

### Changed
- **Landing Free/Pro:** both CTAs are **Open paper terminal** → `/terminal`.
  Removed personal `mailto:` Pro CTA. Capability lists match what the terminal
  actually exposes today; numeric limits labeled **(planned)** / not enforced.
- **Landing footer:** removed five disabled Privacy/Terms/… stubs; replaced with
  “Disclosures coming soon” + Sign In.
- **Landing divergence copy:** scoped to the equity scan panel (where
  `SignalDivergence` is actually wired), not a global promise.

### Known limitations
- Background scanner `/api/equity/signals` may still omit `source`; UI continues
  to show “unknown” / “Source unavailable” via `SignalAttribution` rather than
  inventing an engine name. Populating `source`/`engine` on the payload remains
  an optional backend follow-up.

## Unreleased — UI/UX Phase 3: speak human

Frontend-only glossary and polish from `docs/ui-ux-phase1-4/PLAN.md` Phase 3.
No trading, risk, execution, sizing, or authentication logic changed.

### Added
- **MetricHint** (`MetricHint.tsx`) — one-line hover glossary for Sharpe, Sortino,
  Calmar, Max DD, POP, EV, Kelly, MAE, MFE, IV Rank, Net Theta, consecutive
  losses, and related labels. Wired into Executive Summary tiles, Trade Desk
  approvals + P&L/position headers, Dashboard stats/guardrails, and Chart IV Rank.
- **Chart legend** on ChartWorkstation (SMA-20, VWAP, volume, price levels).
- **CSS loading skeleton** (`.skeleton-block` / `.skeleton-shimmer`) replacing
  bare `LOADING CHART…` text; respects `prefers-reduced-motion`.
- **`.signal-badge`** bullish/bearish/neutral classes so direction chips are not
  styled with trading-style `mode-badge` classes.

### Changed
- Dashboard **Net Theta / day** → **Net Theta (daily)**; **Consec. Loss**
  displays as **Consecutive losses** via the glossary helper.

## Unreleased — UI/UX Phase 2: first 60 seconds

Frontend-only density and wayfinding from `docs/ui-ux-phase1-4/PLAN.md` Phase 2.
No trading, risk, execution, sizing, or authentication logic changed.

### Added
- **Welcome banner** on Command Center (`WelcomeBanner.tsx`) — three steps
  (Broker → Manual mode → Signals), dismissible via `olbos.welcome.dismissed`.
- **TerminalNavContext** so pages can deep-link through the shell without
  prop-drilling.
- **Core vs Advanced nav** filter (`filterNavForDisplay`) with a sidebar
  **Show advanced** toggle (`olbos.nav.advanced`). Deep-linked advanced pages
  stay visible even when Advanced is collapsed.

### Changed
- **ExecutiveSummary** defaults to KPI cards + Portfolio Heat + a short
  Performance strip (Win Rate, Profit Factor, Max DD, Current DD). Strategy
  Health, Meta-Strategy, Capital Allocation, Stress & VaR, System Health, and
  extra ratios sit behind **Show advanced analytics**
  (`olbos.execSummary.advanced`).
- Sidebar Core defaults: Command Center, Markets (Chart + Watchlists), Trade
  Desk, Strategies → Signals, Portfolio & Risk, System → Broker. Options Desk,
  Research, Journal, Performance, and other leaves move under Advanced.

## Unreleased — UI/UX Phase 1: trust labels

Frontend-only honesty fixes from `docs/ui-ux-phase1-4/PLAN.md` Phase 1.
No trading, risk, execution, sizing, or authentication logic changed.

### Fixed
- **ChartWorkstation** no longer shows `AUTOPILOT READY` whenever the kill
  switch is off. The top strip reads the real execution mode
  (`MANUAL` / `COPILOT` / `AUTOPILOT`) and only shows `HALTED` when the kill
  switch is active.
- **ChartWorkstation** bare BUY/SELL chips (and the selected-signal cell)
  now use `SignalAttribution` instead of misusing `mode-badge`
  conservative/scalper classes. Cross-ticker `signals[0]` fallback remains
  removed.
- **“TA Trade Plan”** renamed to **Trade plan** when a scanner signal is
  present (with source when the payload provides it) or **Chart levels**
  when levels are support/resistance fallbacks. ENTRY and RESISTANCE no
  longer share the same color.
- **Sidebar Kill Switch** engages the existing `KillSwitchButton` confirm
  modal instead of navigating to the Risk page.
- **Status bar** shows the active workspace label (e.g. `MARKETS · CHART`)
  instead of a hardcoded `TRADE DESK` on every page.
- **Header execution control** is a single Manual / Copilot / Autopilot
  tri-state (same API as Trade Desk), replacing dual COPILOT/AUTOPILOT
  ON/OFF chips that hid the state machine.
- **Nav / tab labels:** Trade Desk “Orders” → **Desk signals**; “Execution
  Logs” (which opened P&L) → **P&L Breakdown**; new **Execution Log** leaf
  opens the approvals/execution-log surface; Trade Desk tab “Risk profile”
  → **Trading style**.

### Added
- Pure helpers + Vitest coverage for execution-mode display, chart
  attribution mapping, and status-bar nav labels
  (`frontend/src/utils/chartWorkstationDisplay.ts`,
  `frontend/src/utils/navLabels.ts`).

## Unreleased — Frontend trust/UX revision + public landing page

Improved presentation and visibility of existing trading, signal, and risk states.
No trading, risk, execution, or authentication logic was added or changed —
this is a frontend-only change set (see the implementation report delivered
alongside this change for the full evidence table).

### Added
- **Signal attribution** (`frontend/src/components/SignalAttribution.tsx`,
  `frontend/src/types/signal.ts`): a shared component that replaces bare
  BUY/SELL/HOLD badges with direction + source + timeframe + confidence +
  freshness + execution authority, using explicit "unknown"/"unavailable"
  wording for anything the frontend can't verify instead of omitting it.
  Wired into `EquitySignals.tsx`, `OptionsScanPanel.tsx`,
  `EquityScanPanel.tsx`, and the Copilot approvals queue in `TradeDesk.tsx`.
- **Signal divergence disclosure** (`frontend/src/components/SignalDivergence.tsx`):
  a reusable component that surfaces disagreement between two independently
  sourced signals for the same symbol (direction conflict, mixed
  confirmation, or timeframe disagreement) instead of resolving it visually.
  Never claims which signal is execution-authoritative unless that is
  already known from the data.
- **Persistent capital-at-risk status bar**
  (`frontend/src/components/GlobalRiskStatus.tsx`), mounted in
  `TerminalLayout.tsx` above the main content on every authenticated page.
  Shows live/paper environment, AUTOPILOT state, kill-switch state, daily
  drawdown, and remaining daily risk budget (derived only when both the
  current loss and the loss limit are available from the same verified
  source, in the same unit). Every field independently renders
  loading/available/unavailable/unknown — a failed or missing read is never
  displayed as a value that looks safe.
- **Public landing page** (`frontend/src/pages/Landing.tsx`,
  `frontend/src/landing.css`), mounted at `/` via `react-router-dom` (an
  existing, previously-unused dependency). The terminal is unchanged and now
  lives at `/terminal/*`; an unmatched route redirects to `/`. All copy is
  grounded in repository evidence (README, TRADING_POLICY.md,
  ARCHITECTURE_MEMO.md); every performance figure is explicitly labeled as
  unpublished/placeholder, and no guaranteed-return language is used.
- Route-level code splitting (`React.lazy`) for the landing page and the
  terminal bundle, so visiting either surface no longer downloads the other.
- Reduced-motion support: the ticker marquee and status-dot pulse animation
  now respect `prefers-reduced-motion`.
- Minimal test tooling (Vitest + Testing Library — new devDependencies, none
  existed before) and unit tests for `SignalAttribution`, `SignalDivergence`,
  `GlobalRiskStatus`, and `Landing`.

### Changed
- `TerminalLayout.tsx`: mounts `GlobalRiskStatus` between the ticker strip
  and the main content area; marquee marked for reduced-motion.
- `EquitySignals.tsx`, `OptionsScanPanel.tsx`, `EquityScanPanel.tsx`,
  `TradeDesk.tsx`: bare directional badges replaced with
  `SignalAttribution`.
- `index.tsx`: now renders a `BrowserRouter` with lazy-loaded `/` and
  `/terminal/*` routes instead of mounting `App` directly.

### Fixed
- **Terminal-wide crash on bad market data.** Found by running the frontend
  against a real, locally-provisioned backend instead of only a stopped one:
  `/api/market/snapshot/{symbol}` and `/api/market/regime` both omit their
  normal fields on error (a documented yfinance-failure condition, see
  `AUDIT_2026-06.md`), and the ticker strip called `.toFixed()` /
  `.includes()` / `.replace()` on the resulting `undefined` with no error
  boundary above it — blanking the *entire* terminal, not just the ticker.
  Fixed the two unsafe field checks and wrapped the ticker strip, risk-status
  bar, sidebar, and page content each in the existing `ErrorBoundary`
  component so one panel failing can no longer take the others down.
  Regression tests added.

### Known limitations
- This repository has no authentication system (confirmed by direct
  inspection — no router, no session/token handling, a hardcoded account
  name in the sidebar). The `/` vs `/terminal` split is a navigational
  separation only; it does not gate access. Production currently gates the
  *entire* app (including any new public route) behind HTTP Basic Auth at
  the nginx layer when `DASH_USER`/`DASH_PASS` are set
  (`frontend/docker-entrypoint.sh`) — making the landing page genuinely
  public in that deployment requires an infra change (exempting `/` from
  `auth_basic`) that is out of scope here.
- No live example of two reachable, simultaneously-visible panels showing
  opposing signals for the same symbol was found in the current codebase to
  wire `SignalDivergence` into directly; it ships as a tested, ready-to-use
  component for the first place that need arises.
- New devDependencies (`vitest`, `@testing-library/*`, `jsdom`) pull in
  `esbuild <=0.24.2`, flagged by `npm audit` for a dev-server-only
  vulnerability (GHSA-67mh-4wv8-2f99) that does not affect production
  builds or runtime code.
