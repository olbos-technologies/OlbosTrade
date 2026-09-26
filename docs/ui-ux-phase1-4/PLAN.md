# Status of this plan — read before implementing anything below

This file has been on `main` for a while and is byte-identical to the copy on
`codex/mobile-ux-and-tenant-foundation`, so the plan itself was adopted long ago.
What was missing was any record of which of it is still true — that is what this
preamble is. That branch's **code** was not merged; the reasons are at the bottom
of this preamble and are worth reading before anyone tries again.

The plan is kept because the product rules in it are good and worth holding to —
in particular "truth before density", "failed data must never resemble a safe
value", the semantic-colour restraint, and the 44px/320px verification matrix.

**But almost every batch it specifies was already implemented by the 41 commits
it predates.** Audited against `main` on 2026-09-25, file by file:

| Batch | PLAN item | Actual state on `main` |
|---|---|---|
| 1.1 | ChartWorkstation says `AUTOPILOT READY` | **Done.** Uses `formatExecutionModeDisplay(execMode, killSwitch?.active)` reading the real mode. |
| 1.1 | ENTRY and RESISTANCE share `#f4c64f` | **Done.** `chartLevelColors()` — entry `var(--amber)`, resistance `#a78bfa`, with a comment saying why. |
| 1.1 | `mode-badge` misused for BUY/SELL | **Done.** `SignalAttribution` / `.signal-badge` classes in use. |
| 1.2 | Sidebar kill switch navigates to Risk | **Done.** `<KillSwitchButton variant="sidebar" expanded={showLabels} />`. |
| 1.2 | Status bar hardcodes `TRADE DESK` | **Done.** No such string remains. |
| 1.3 | `trade:logs` label | **Done.** Nav leaf is "P&L Breakdown", with "Execution Monitor" as a separate leaf. |
| 1.3 | "Risk profile" → **Trading style** | **WAS THE ONE REAL GAP.** Five user-facing occurrences plus four stale comments. Fixed, and locked by `src/utils/__tests__/vocabulary.test.ts`. |
| 2.1 | ExecutiveSummary advanced collapse | **Done.** Persists to `olbos.execSummary.advanced`, exactly the key this plan specifies. |
| 2.3 | Welcome empty state | **Done.** `WelcomeBanner.tsx`. |
| 3.1 | MetricHint glossary | **Done.** `MetricHint.tsx` with `METRIC_HINTS` + alias map. |
| 3.1 | `Consec. Loss` spelled out | **Done here.** Dashboard label is now "Consecutive losses"; the `consec. loss` alias is retained. |
| 3.2 | Chart legend | **Done.** In `CandlestickChart.tsx` — SMA-20, VWAP (dashed), Volume, plus every level, `aria-label="Chart legend"`. |
| 3.2 | Loading skeleton | **Done.** `skeleton-block` / `skeleton-shimmer` with `aria-busy`. |
| 4.1 | Personal `mailto:` in Pro CTA | **Done.** No `mailto` on Landing; replaced by request-access. |
| 4.1 | Five `title="Not published"` footer stubs | **Not a defect.** One "Not published" remains as a track-record *value*, which this plan explicitly says to keep. |
| 5.1–5.3 | Mobile shell, 44px targets, ≤760px card reflow, hide moving ticker | **Done in #82**, independently. This is why the branch could not simply be merged. |

| 5.4 | Execution safety surface | **Done.** Server-confirmed vs requested state were already separated (no optimistic update; `execPending` rendered dashed, never selected). Added: an Autopilot confirmation gate, an acknowledgement of applied changes, a current-state sentence, and freshness — the poll's `.catch(() => {})` meant an unreachable backend and a confirmed MANUAL were pixel-identical for five minutes at a time. |

**Still genuinely open from this plan:** nothing. Phases 1–4 were delivered by the
commits this plan predates; Phase 5 by #82 and the 5.4 work above.

## Why the branch itself was not merged

1. **Duplicate alembic revision.** Its `0029_add_multitenant_foundation.py`
   declares `revision = "0029"`, `down_revision = "0028"` — identical to `main`'s
   `0029_add_options_scan_rejections.py`. Two files with one revision id makes
   `alembic upgrade head` fail, and `deploy/hetzner/update.sh` runs it. Merging
   would have broken the deploy.
2. **A second, incompatible auth/billing architecture.** It creates `users` keyed
   on `clerk_user_id` (main's `0030` already creates `users` with email +
   password hash), `broker_credentials` duplicating `broker_connections` from
   `0034`, and `subscriptions` with Stripe ids — adding `stripe` and
   `clerk-backend-api` as dependencies. That contradicts the shipped decisions:
   own auth, request-access waitlist, manual tiers, no billing.
3. **Phase 5 collided with #82 silently.** `index.css` auto-merged from 3 mobile
   `@media` blocks to 4 — two independent implementations of the same 44px and
   card-reflow rules concatenated rather than reconciled. jsdom does no layout, so
   `npm test` stays green while the cascade fights itself.

Treat the sections below as the product-rules document they are, not as a
work queue. Check each item against `main` before implementing it.

---

# OlbosTrade UI/UX — Phases 1–4 Implementation Plan

Frontend trust, clarity, and density work derived from
`ARCHITECTURE_AUDIT.md` + the UI/UX review canvas. **Reuse-first**: extend
existing components (`SignalAttribution`, `KillSwitchButton`, `GlobalRiskStatus`,
`TradingModeSelector`, `ExecModeBar` patterns) rather than rebuilding the
terminal shell.

**Same bar as prior frontend trust/UX revision (CHANGELOG Unreleased):**
- Frontend-only unless a batch explicitly lists a tiny backend display field
- No trading, risk, execution, sizing, or authentication logic changes
- Honest unknown/unavailable wording — never fabricate source/timeframe/authority
- Nothing deploys until explicitly approved by the operator

**Deploy path (Claude Code / Hetzner — unchanged):**
1. Implement + test locally (`frontend`: `npm test`, `npm run build`)
2. Update `CHANGELOG.md` under Unreleased (or a dated section)
3. Open PR / merge to `main` only when operator approves
4. On server: `bash deploy/hetzner/update.sh` (pulls `main`, rebuilds, restarts)

**Local run (align with `.claude/launch.json` + README):**
- Frontend: `cd frontend && BACKEND_URL=http://127.0.0.1:8001 npm run dev -- --port 3010 --host`
- Or full stack: `./start.sh` then `./health.sh`

**Out of scope (do not touch in these phases):**
- Capital-base unification, persist approvals/`/tmp` exec mode, 120-bar skew
- Watchlist coverage, regime staleness, position identity, iron_condor
- Auth / billing / Free-vs-Pro enforcement
- Full visual redesign, new design system, light theme
- Backend signal-engine consolidation (Phase 4 may *display* fields if already
  present; it must not invent engines or change AUTOPILOT)

---

## Vocabulary (use everywhere)

| Concept | Allowed labels | Values |
|---|---|---|
| Execution mode | **Execution mode** | Manual · Copilot · Autopilot |
| Trading style | **Trading style** (not “Risk profile”) | Conservative · Balanced · Aggressive · Scalper |
| Kill switch | **Kill switch** | Engaged / Armed (not engaged) |

Never call trading style “Risk profile” or execution mode “AI mode” in user-facing copy.

---

## Phase 1 — Trust labels (nothing on screen lies)

**Goal:** Fix misleading chrome so a regular user can trust what the UI says
about execution state, navigation, and signal origin.

**Acceptance:** Manual walkthrough on paper account — no false “AUTOPILOT READY”,
sidebar Kill Switch engages (confirm modal), status bar matches page, Execution
Logs lands on the log, Chart chips use `SignalAttribution`, TA plan title is honest.

### Batch 1.1 — ChartWorkstation honesty
**Files:** `frontend/src/pages/ChartWorkstation.tsx`

| Item | Change |
|---|---|
| Exec Mode strip | Replace `killSwitch?.active ? "HALTED" : "AUTOPILOT READY"` with real execution mode from the same API/source the header chips use (`manual` / `copilot` / `autopilot`), plus `HALTED` when kill switch is active. Label: **Execution mode**. Never imply Autopilot unless mode is autopilot. |
| Bare BUY/SELL list | Replace `mode-badge` / conservative|scalper class misuse with `<SignalAttribution … size="sm" />` (same mapping pattern as `EquitySignals.toAttribution` / TradeDesk approvals). |
| “TA Trade Plan” | If `selectedSignal` present: title **Trade plan** (or **Scanner trade plan**) + source from payload when available. If absent: keep current “No signal for SYMBOL — levels below are chart-derived…” and title **Chart levels** (not TA). |
| Level colors | ENTRY and RESISTANCE must not share `#f4c64f` — pick distinct token colors (`--cyan` / `--amber` / existing level colors). |

**Reuse:** `SignalAttribution`, existing chart “no `signals[0]` fallback” (do not reintroduce).

**Tests:** Extend or add Vitest coverage for attribution render on Chart if practical; otherwise assert helper mapping in a small pure helper extracted from the page.

### Batch 1.2 — TerminalLayout trust chrome
**Files:** `frontend/src/components/TerminalLayout.tsx`

| Item | Change |
|---|---|
| Sidebar Kill Switch | Replace the custom `onNav("risk")` button with existing `<KillSwitchButton variant="sidebar" expanded={showLabels} />` (component already implements confirm + engage). Do not invent a second kill API. |
| Status bar | `StatusBar` already receives `page` — remove the hardcoded second `<span>TRADE DESK</span>`. Show a human label from `NAV_MODEL` (lookup by key → group + leaf label), e.g. `MARKETS · CHART`. Keep broker / version segments. |
| Mode chips | Replace dual binary COPILOT/AUTOPILOT chips with a **single tri-state control** (Manual · Copilot · Autopilot) that calls the same `setExecutionMode` path TradeDesk uses. Tooltip or short legend: “Autopilot off returns to Copilot; use Manual to stop approvals.” Optional: keep chips but make state machine explicit — prefer one control. |

**Reuse:** `KillSwitchButton` (`variant="sidebar"`), TradeDesk `api.setExecutionMode`.

**Tests:** Update `TerminalLayout.test.tsx` if present; add cases for status label lookup + kill switch not navigating to risk.

### Batch 1.3 — Nav / tab label mismatches
**Files:** `frontend/src/App.tsx`, `frontend/src/components/TerminalLayout.tsx` (`NAV_MODEL`), `frontend/src/pages/TradeDesk.tsx` (tab titles only if needed)

| Item | Change |
|---|---|
| `trade:logs` | Either (a) `initialTab` that shows the real execution log (approvals tab / EXECUTION LOG section), **or** (b) rename nav leaf to **P&L Breakdown** and add a separate leaf **Execution Log** → approvals. Prefer (b) if both surfaces matter; prefer (a) if one leaf is enough. |
| Trade Desk tab “Risk profile” | Rename to **Trading style** (renders `TradingModeSelector`). |
| Strategies → Signals vs Trade Desk signals | Rename Trade Desk deep-link leaf currently “Orders” / signals tab to something accurate (**Signal queue** or **Desk signals**) so it does not collide with Strategies → Signals. |

**No backend changes.**

### Batch 1.4 — Phase 1 verification
```bash
cd frontend && npm test && npm run build
```
Manual: open Chart (Manual mode) → Execution mode must not say AUTOPILOT READY;
sidebar Kill Switch → confirm modal (not Risk page); change pages → status bar
updates; click Execution Log / P&L nav → correct tab.

**CHANGELOG:** document under Fixed / Changed — trust labels only.

---

## Phase 2 — First 60 seconds (density + wayfinding)

**Goal:** A new user opening `/terminal` is not hit with 25 panels and 30 nav
leaves before they know what to do.

**Acceptance:** Default Command Center shows a short summary; advanced analytics
collapsed; nav has Core vs Advanced (or equivalent progressive disclosure);
first-run empty state points to broker + Manual + one signal.

### Batch 2.1 — ExecutiveSummary default density
**Files:** `frontend/src/components/ExecutiveSummary.tsx`, optionally `Dashboard.tsx`

- Default visible: KPI cards + Portfolio Heat + short Performance strip (win rate,
  profit factor, max DD — or similar small set)
- Collapsed behind **Show advanced analytics**: Strategy Health, Meta-Strategy,
  Capital Allocation, Stress & VaR, System Health, extra ratios (Sharpe/Sortino/Calmar…)
- Persist expand preference in `localStorage` (key namespaced, e.g. `olbos.execSummary.advanced`)

**Reuse:** existing panels; do not delete metrics — only hide by default.

### Batch 2.2 — Nav progressive disclosure
**Files:** `frontend/src/components/TerminalLayout.tsx` (`NAV_MODEL`)

- **Core (always visible):** Command Center, Markets (Chart + Watchlists), Trade Desk,
  Strategies → Signals, Portfolio & Risk, System → Broker
- **Advanced (collapsed section or “More”):** Strategy Builder, Research lab leaves,
  Options Flow extras, Model Health, Backtests, Income Strategies, etc.
- Do not remove routes from `App.tsx` `PAGES` — only change default visibility.
- Keep deep-links working when advanced is expanded.

### Batch 2.3 — Welcome empty state
**Files:** `frontend/src/pages/Dashboard.tsx` (or small `WelcomeBanner.tsx`)

Show once when there are no closed trades / broker disconnected / first visit
(`localStorage` dismiss):
1. Connect / confirm broker (link System → Broker)
2. Stay in **Manual** execution mode
3. Open **Signals** and review one card

Dismissible; never block trading chrome.

### Batch 2.4 — Phase 2 verification
```bash
cd frontend && npm test && npm run build
```
Manual: fresh `localStorage` → slim dashboard + welcome; expand advanced;
collapse nav Advanced and confirm Core still reaches Chart + Trade Desk + Risk.

**CHANGELOG:** Added / Changed — density & onboarding.

---

## Phase 3 — Speak human (glossary + polish)

**Goal:** Expert metrics remain, but regular people can hover and understand;
visual polish without a redesign.

**Acceptance:** Key labels have one-line tooltips; Execution vs Trading style
wording consistent; chart has a legend; loading is less jarring.

### Batch 3.1 — Glossary tooltips
**Files:** prefer one helper `frontend/src/components/MetricHint.tsx` (or
`title=` wrappers) used in:
- `ExecutiveSummary.tsx`, `TradeDesk.tsx` (approvals + P&L columns), `Dashboard.tsx`

Glossary (one sentence each): Sharpe, Sortino, Calmar, Max DD, POP, EV, Kelly,
MAE, MFE, IV Rank, Net Theta, Consec. Loss → spell out **Consecutive losses**.

### Batch 3.2 — Chart legend + loading
**Files:** `ChartWorkstation.tsx`, shared CSS in `index.css` if needed

- Legend: SMA-20, VWAP, volume, entry/stop/target/support/resistance colors
- Replace bare `LOADING…` / `LOADING CHART…` with a simple skeleton block
  (CSS only — no new dependency)

### Batch 3.3 — Signal badge CSS hygiene
**Files:** `index.css`, ChartWorkstation / any leftover `mode-badge` for direction

Add `.signal-badge.bullish` / `.signal-badge.bearish` (or rely solely on
`SignalAttribution`). Stop styling BUY/SELL with conservative/scalper mode classes.

### Batch 3.4 — Phase 3 verification
```bash
cd frontend && npm test && npm run build
```
Hover POP/EV/Kelly on an approval card; open Chart → legend visible; no
mode-badge-as-direction left on Chart.

**CHANGELOG:** Added MetricHint / legend; Changed labels.

---

## Phase 4 — Landing trust + attribution display

**Goal:** Public front door matches the product’s honest tone; in-app attribution
badges become useful when the API already sends fields (no fake sources).

**Acceptance:** Pro CTA is not a personal mailto; footer links are honest or gone;
Free/Pro copy does not imply gated features that are not gated; divergence visible
on a primary journey if two sources exist; badges show real `source` when present.

### Batch 4.1 — Landing
**Files:** `frontend/src/pages/Landing.tsx`, `frontend/src/landing.css`,
`frontend/src/pages/__tests__/Landing.test.tsx`

| Item | Change |
|---|---|
| Pro CTA | Replace `mailto:mangpijasuan@…` with `/terminal` + clear “Request access” note, or a neutral `mailto:hello@…` / waitlist placeholder **without** a personal address. Keep honesty that signup is not wired. |
| Footer legal | Remove disabled Privacy/Terms/… **or** single “Disclosures coming soon” line. Do not leave five `title="Not published"` stubs. |
| Free vs Pro | Align card copy with reality (both open same unauthenticated terminal today — CHANGELOG known limitation). Prefer honest “same terminal today; Pro limits not enforced yet” over fake tier UX. |
| Keep | Honest track-record placeholders and risk disclosure paragraph. |

### Batch 4.2 — Attribution on primary surfaces
**Files:** `ChartWorkstation.tsx`, `EquitySignals.tsx`, optionally wire
`SignalDivergence` where two sources are already available (EquityScanPanel
already wires it — extend Chart or EquitySignals **only if** a second source is
reachable without new engines).

Rules:
- Continue reading `sig.source` from payload; never hardcode `"Equity Scan Engine"`
- If backend still omits source → show “Source unavailable” (existing
  `SignalAttribution` behavior)
- **Optional tiny backend** (only if operator approves mid-phase): ensure
  background scanner payloads set `source` / `engine` string on
  `/api/equity/signals` — display-only field, no scoring change. If not approved,
  skip and document under Known limitations.

### Batch 4.3 — Phase 4 verification
```bash
cd frontend && npm test && npm run build
```
Open `/` — no personal email in Pro CTA; footer clean; `/terminal` still works
behind existing Basic Auth (do not change nginx/auth in this phase).

**CHANGELOG:** Landing trust; attribution surface updates; Known limitations if
backend source field deferred.

---

## Cross-cutting (every phase)

| Rule | Detail |
|---|---|
| Reuse-first | Prefer `KillSwitchButton`, `SignalAttribution`, `SignalDivergence`, existing tokens in `index.css` |
| No money-path edits | Do not change `trade_desk.py` execute path, sizing, guardrails, or broker submit |
| Tests | `cd frontend && npm test` green; `npm run build` green before asking to deploy |
| A11y | Keep keyboard/tab patterns on TabBar; kill switch confirm must be cancelable |
| Mobile | Spot-check sidebar overlay + Chart stacks (`useIsMobile`) after TerminalLayout edits |
| Docs | Append to `CHANGELOG.md`; do not invent STRATEGY.md / DATA.md |
| Deploy | Operator says “deploy” → merge `main` → `bash deploy/hetzner/update.sh` → verify ticker + GlobalRiskStatus + Manual mode |

---

## Suggested Claude Code session prompts

Use one phase per session (or one batch if context is tight).

**Phase 1:**
> Implement `docs/ui-ux-phase1-4/PLAN.md` Phase 1 only (Batches 1.1–1.4).
> Frontend-only, reuse `KillSwitchButton` sidebar variant and `SignalAttribution`.
> Do not deploy. Run `cd frontend && npm test && npm run build`. Update CHANGELOG.

**Phase 2:**
> Continue `docs/ui-ux-phase1-4/PLAN.md` Phase 2 only. Collapse ExecutiveSummary
> advanced panels by default; Core vs Advanced nav; welcome banner. No backend.
> Tests + CHANGELOG. Do not deploy.

**Phase 3:**
> Continue plan Phase 3 — MetricHint glossary, chart legend, loading skeletons,
> signal-badge CSS. No backend. Tests + CHANGELOG. Do not deploy.

**Phase 4:**
> Continue plan Phase 4 — Landing trust + attribution on primary surfaces.
> Do not add auth. Backend `source` field only if I explicitly approve.
> Tests + CHANGELOG. Do not deploy until I say so.

**Deploy (operator-only):**
> Merge approved UI/UX branch to main, then on the server run
> `bash deploy/hetzner/update.sh` and verify paper terminal: Manual mode label,
> Kill Switch confirm from sidebar, Landing CTA, Command Center density.

---

## Done definition (all four phases)

- [x] Phase 1 acceptance checklist passed on paper *(code + tests green; operator paper walkthrough still recommended before deploy)*
- [x] Phase 2 slim default + welcome + Core nav *(code + tests green)*
- [x] Phase 3 tooltips + legend + no mode-badge-as-direction *(code + tests green)*
- [x] Phase 4 landing trust + attribution rules respected *(code + tests green; backend source field deferred)*
- [x] `frontend` tests + build green *(Phases 1–4)*
- [x] CHANGELOG updated *(Phases 1–4)*
- [ ] Operator-approved deploy via `deploy/hetzner/update.sh`
- [ ] Backend architecture audit HIGH items still deferred (explicitly out of scope)

---

## Phase 5 — Institutional mobile workstation

**Goal:** Make the phone experience an intentional operator workflow rather
than a compressed desktop terminal. This is a display and interaction phase;
it must not change execution, risk, sizing, broker, or authentication logic.

### Product rules

1. **Truth before density:** Paper/live, execution mode, kill state, data
   freshness, and risk availability must be readable before any performance
   metric. Failed data must never resemble a safe value.
2. **One decision per screen:** A screen identifies the current operational
   state, the highest-priority next action, and supporting detail—in that order.
3. **Semantic restraint:** Gold is brand/active navigation only; blue is
   selection; green/red communicate verified positive/negative or approval/
   rejection; amber is caution. Do not use color as the only state cue.
4. **Touch is the baseline:** Every mobile control has a 44 × 44px minimum hit
   area. Critical actions must remain usable at 320px wide and with the on-screen
   keyboard open.
5. **Progressive disclosure:** Show the position/signal/action summary first;
   tables, diagnostics, and configuration are expandable—not removed.

### Batch 5.1 — Mobile shell safety and ergonomics

**Files:** `frontend/src/components/TerminalLayout.tsx`, `frontend/src/index.css`,
`frontend/src/components/MobileBottomNav.tsx`

- Replace the compressed three-button execution-mode header control with a
  compact current-state trigger that reveals three 44px choices on demand.
- On phones, hide the continuously moving ticker and secondary clock from the
  fixed header. Preserve execution state, and leave market detail available in
  Markets—not as inaccessible moving chrome.
- Make sidebar group rows, children, and Advanced toggle 44px tall on mobile.
- Preserve the existing six bottom destinations until product analytics supports
  a reduction; do not silently remove a primary route.

**Acceptance:** At 320px and 390px, no clipped execution option; all mobile
shell controls are at least 44px tall; “More” remains dismissible and clears
the bottom navigation; keyboard focus is visible.

### Batch 5.2 — Operational dashboard hierarchy

**Files:** `frontend/src/pages/Dashboard.tsx`, `frontend/src/components/GlobalRiskStatus.tsx`,
`frontend/src/components/WelcomeBanner.tsx`

- Replace a collection of separate `UNKNOWN`/`UNAVAILABLE` chips with one
  compact system-health banner that states what failed, when it was last known,
  and has a Retry/action path where the API supports it.
- Add one state-driven next action above secondary analytics: Review queue,
  Scan equities, Scan options, Review risk, or Connect broker.
- Keep risk state and execution state visible in the first viewport; move
  secondary performance blocks below it.

### Batch 5.3 — Mobile data views

**Files:** position, order, execution queue, and scan result components

- At ≤760px, replace horizontal data-table dependency with expandable summary
  rows. Initial content: symbol, direction/status, primary price/P&L, risk or
  confidence, and the safe next action. Expose remaining fields in Details.
- Preserve desktop tables and all existing information.
- Use a clear empty state that names the next useful action; avoid a blank table
  as the only outcome.

### Batch 5.4 — Execution safety surface

**Files:** `TerminalLayout.tsx`, `KillSwitchButton.tsx`, existing modal/shared UI

- Give every execution-mode transition an explicit current-state sentence and a
  reason/guardrail summary. Autopilot requires a separate deliberate
  confirmation; returning to Manual is immediate and clearly acknowledged.
- Show server-confirmed state, requested state, and data freshness separately.
- Do not change APIs or make optimistic mode claims.

### Verification matrix

| Scenario | Expected outcome |
|---|---|
| 320px / 390px portrait | No horizontal page overflow; all shell controls ≥44px |
| Offline / partial API failure | One honest recovery banner; no false $0 or “clear” state |
| Open positions / queue | First screen gives a clear review path; details remain reachable |
| Manual → Copilot → Autopilot | Server-confirmed state only; Autopilot confirmation is explicit |
| Kill switch engaged | State is visible in fixed context and action stays cancelable |
| Reduced motion | No required information relies on animation or marquee movement |

### Reusable agent prompt (Codex, Claude Code, or Cursor)

> Implement one Batch from Phase 5 of `docs/ui-ux-phase1-4/PLAN.md` only.
> Preserve all trading, risk, broker, sizing, auth, and API behavior. Reuse
> existing tokens and components. Do not deploy. Add or update focused tests,
> run `cd frontend && npm test && npm run build`, and report screenshots or
> mobile verification at 320px and 390px.
