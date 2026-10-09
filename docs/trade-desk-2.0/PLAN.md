# OlbosTrade — Trade Desk 2.0 Implementation Plan (Phase A)

**Status:** Phases B–F + Step 8 complete **against the 12-step plan in §13
below**. Re-baselined 2026-07-19 against the fuller `MASTER_SPEC.md` — see
"Re-baseline vs Master Spec" section: several Phase D/E/F bullets in that
document are still open. Do not read "complete" here as satisfying every
bullet in `MASTER_SPEC.md`.
**UI default:** Trade Desk V2 flags are **opt-in (off)** until you enable them after a
paper walkthrough — legacy desk remains the default experience on deploy.
**Deploy only after paper E2E + explicit operator approval.**
Greeks delta/vega caps remain **off** by default. 0DTE Autopilot remains off.

---

## Re-baseline vs Master Spec (2026-07-19)

`docs/trade-desk-2.0/MASTER_SPEC.md` is the fuller 26-section brief the
operator provided after this plan's 12-step version was already built. It is
the canonical target from now on; this plan's §13 stays as sequencing, not
the acceptance bar. Verified by reading the actual code (not by re-trusting
old status labels):

**Confirmed done and matches spec intent:**
- Shell/header/tabs/panel layout/flags (§3, §22) — `frontend/src/trade-desk/`
- Command Overview (§4) — real queues (Opportunities/Reviews/Risk
  Actions/Execution Exceptions), not a stub
- Positions tab reuses legacy `TradeDesk` (§1's "don't duplicate systems") —
  `pages/TradeDeskV2.tsx:73-74`
- Options Desk reuses existing pages as tool-tabs instead of a parallel
  engine (§6) — `trade-desk/options/OptionsDesk.tsx`
- TradeIntent-style evaluation (§11) — `/evaluate-equity` / `/evaluate-options`
- Portfolio gate as backend-authoritative risk (§12) — Step 8, this file above

**Open gaps against Master Spec (not started or partial):**
- §6.4/§6 tree — no **Strategy Builder** tab (multi-leg spread construction UI)
  and no **Roll Manager** in `OptionsDesk.tsx`'s tool list (Chain/Chart/Scanner/
  Income/Flow/Signals/0DTE only)
- §6.2/§6.3 — no GEX/positioning, IV term structure, volatility smile,
  open-interest map; no data provider sourced for these yet
- §5.3/§8 — correlation clusters, stress & VaR, per-trade Portfolio Greeks
  impact not yet in `EquityIntelligenceRail` / `OptionsIntelligenceRail`
- §15 tree — `OrderStateBadge`, `OrderDetailDrawer`, `ExecutionTimeline`,
  `CopilotReviewCard`, `CopilotDecisionModal`, `PositionDetailDrawer` are not
  separate components; likely inlined in `OrdersWorkspace.tsx` /
  `ExecutionMonitor.tsx` / `CopilotQueue.tsx` (spec permits this — "don't force
  exact paths if equivalent modules exist" — but flagging so nobody assumes
  they exist as reusable units)
- §17 real-time event bus — not started; spec's own escape hatch ("don't
  introduce a complex event bus if the architecture can't support it safely")
  should be invoked — current polling approach stays until there's a concrete
  need
- §13/§14 accessibility and mobile responsive behavior — only partially
  verified (mobile flag exists in `featureFlags.ts`, not walked end-to-end)
- §24 acceptance criteria — treat as 16 separate phase-exit gates per item,
  not one bundled release gate (see `MASTER_SPEC.md` §24 note)

**Recommendation:** don't try to "complete" Master Spec in one pass. Pick the
next 1-2 gaps above as an explicit next phase (operator/product decision —
see conversation), keep paper-walking what already exists before growing
surface area further, and update this section as gaps close instead of
re-declaring a phase "complete" wholesale.

## Step 8 — money-path spec (approved)

| Gate | Behavior | Fail mode |
|------|----------|-----------|
| Max concurrent positions | Block when open ≥ `max_concurrent_positions` | Closed when state loaded |
| Single underlying ≤25% | Block when projected exposure exceeds cap | Closed when state loaded |
| Sector ≤40% | **Hard block** (aligned with options scan; not warn-only) | Closed when state loaded |
| Portfolio heat | Block when projected heat > 50% (`HEAT_HIGH`) | Closed when state loaded |
| Portfolio delta/vega | **Not enforced** unless `execution_enforce_portfolio_greeks=true` | N/A (miscalibrated) |
| Open-trade DB load error | Empty book assumed for this gate only | **Fail open** + warning (kill/guardrails still closed) |

**Module:** `app/services/execution_portfolio_gate.py`  
**Hook:** Stage 2b in `_execute_signal` (after guardrails, before duplicate/broker)  
**Rollback:** `EXECUTION_PORTFOLIO_GATE=false` (or settings `execution_portfolio_gate`)  
**Advisory:** evaluate-equity / evaluate-options also surface the same checks

Still a single OMS: no second submit path.

**Reuse-first.** Extend existing Trade Desk, chart, scanners, risk, portfolio, OMS
(`_execute_signal`), broker, journal. Do not rebuild those systems. Claude Code
owns integration audit, paper E2E hardening, and deploy prep after Cursor phases.

**Prioritization (operator decision, not defaulted by this doc):** The Landing
page correctly states paper validation is incomplete. Prefer a **thin UX track**
(shell + Command Overview + Equity/Options display assembly) while paper trading
builds a track record. Defer heavy **Orders / Execution Monitor / TradeIntent /
Replay** until after paper validation *or* explicitly prioritize them as a
separate product call. Do not treat the 12-step list as an automatic build order.

**In scope (when approved, phased):** Trade Desk shell + Command Overview + Equity
Desk + Options Desk (tools inside desk) + Copilot Queue + Positions; optionally
later Orders + Execution Monitor + Trade Replay + TradeIntent/Evaluation + feature
flags + paper-mode validation path.

**Deferred / non-goals:** See §17. Live futures, naked shorts, 0DTE Autopilot,
social/marketplace, LLM→broker, full institutional OMS replacement, broad
unrelated refactors.

> **Superseded 2026-10-05.** "Multi-tenant SaaS auth" was listed here as a
> non-goal. `docs/adr/0001-broker-connection-ownership.md` is now Accepted: an
> organization owns a broker connection, and every user gets a personal
> organization. Roles, invitations and multi-member organizations remain
> deferred — only the *ownership key* is decided. This plan is not authoritative
> on tenancy; `MASTER_ARCHITECTURE.md` and the ADRs are.

---

## Approval gates (not a blanket 12-step approve)

| Gate | What | Money path? |
|------|------|-------------|
| **Phase B** | Shell, flags, sidebar IA, Command Overview (read-only) | No |
| **Phase C/D** | Equity/Options desk UI assembly (display + paper composer display) | No submit changes without review |
| **Step 8 — own gate** | Portfolio concentration / max positions / heat on `_execute_signal` (**done**) | Yes — rolled back via `execution_portfolio_gate=false` |
| **Phase E rest** | Thin Copilot Queue / Orders / Execution Monitor (done) | No — visibility + existing approve path |
| **Phase F** | Trade Replay MVP + desk a11y/mobile (**done**) | No new submit |
| **Claude Code** | Integration audit + paper E2E docs + deploy prep (**docs done**; deploy gated) | Deploy only if green + explicit OK |

**Step 8 is never implied by “approve Phase B.”** It needs a standalone money-path
spec (gates list, fail-open vs fail-closed, test matrix, rollback) before any
edit to `_execute_signal`.

**Nav nuance:** UI/UX pass fixed *labels*; the deeper gap remains — there is still
no order-lifecycle table (submitted→acked→partial→filled→rejected→cancelled).
That is Orders/Execution Monitor work, not a label fix.

---

## Verdict

Feasible as an **incremental consolidation** around the existing single submit
gate `trade_desk._execute_signal`. Largest gaps: typed **TradeIntent**, dedicated
**Orders / Execution Monitor**, **Trade Replay**, and wiring evaluation engines
into enforcement. ChartWorkstation is the chart surface (no separate “Gold
Standard” codebase). Options UI already exists but lives outside Trade Desk nav.

---

## 1. Current Trade Desk architecture

| Layer | Today |
|-------|-------|
| Shell | `TerminalLayout` + `App` page-state routing (`/terminal/*`, not URL tabs) |
| Trade Desk page | `frontend/src/pages/TradeDesk.tsx` — tabs: signals (history), positions, approvals, pnl, mode |
| Nav group | `trade:*` — Copilot Review, Desk signals, Positions, P&L, Execution Log |
| Mode | Manual / Copilot / Autopilot via `execution_mode` + header `ExecutionModeControl` |
| Style | Conservative / Balanced / Aggressive via `TradingModeSelector` (`/api/mode`) |
| Submit | All live paths → `_execute_signal` → IBKR → `trade_recorder` |

**Nav mismatches:** `trade:orders` → trade **history** (not orders); `trade:execlog`
→ same Approvals tab as Copilot.

---

## 2. Existing components to reuse

| Component | Path |
|-----------|------|
| ApprovalsQueue / Positions / PnL | `TradeDesk.tsx` |
| SignalAttribution | `components/SignalAttribution.tsx` |
| GlobalRiskStatus, KillSwitchButton | `components/` |
| BrokerStatus, PortfolioGreeks | `components/` |
| ChartWorkstation + ChartCanvas | `pages/ChartWorkstation.tsx` |
| EquityScanPanel / OptionsScanPanel | `components/` |
| EquitySignals / OptionsSignals | `pages/` |
| Options Chain, CSP, Income, Flow | `pages/options/*`, `OptionsFlow.tsx` |
| TabBar, MetricHint, HoldToConfirmButton | `components/` |
| Journal | `pages/Journal.tsx` |

**Clean up (don’t fork):** duplicate ExecModeBar vs header; ConfidenceBar copy
between equity/options cards; Scan page chrome wrappers.

---

## 3. Existing backend services to reuse

| Service | Path | Use in v2 |
|---------|------|-----------|
| `_execute_signal` / `handle_signal` | `api/routes/trade_desk.py` | **Only** OMS submit |
| `ExecutionModeManager` | `services/execution_mode.py` | Mode routing |
| `GuardrailEngine` + KillSwitch | `services/guardrails.py`, `kill_switch.py` | Hard controls |
| `RiskManager` / `ProposedTrade` | `services/risk_manager.py` | Wire into Evaluation (today unused on path) |
| `portfolio_engine` | `services/portfolio_engine.py` | Heat / concentration |
| `options_intelligence` / decision / CSP gate | `services/*` | Options Evaluation |
| `equity_signal_engine` / `equity_scan_engine` | `services/` | Equity evidence |
| `TradeRecorder` + `ExecutionEvent` | recorder + models | Audit / fills |
| `IBKRClient` + `broker_factory` | `broker/` | Adapter |
| `account_guard` | `services/account_guard.py` | Paper/live match |

---

## 4. Current order path

```
Background scan | POST /signal | POST /manual-trade | POST /approve/{id}
  → handle_signal / approve / manual
  → _execute_signal (kill → hours → portfolio DB → frequency → health
       → GuardrailEngine → dupe guard → account_guard → margin → broker)
  → place_equity_order | place_order
  → trade_recorder.record_fill
```

| Mode | Behavior |
|------|----------|
| Manual | Scanner does not auto-execute; `manual-trade` forces |
| Copilot | Queue `pending_approval`; user approve/reject |
| Autopilot | Background `handle_signal` executes; **scan-panel autopilot still queues** |

---

## 5. Current risk and portfolio path

**Enforced on order:** kill switch, market hours, GuardrailEngine (loss/trade caps),
frequency (non-manual), strategy health (fail-open), account mode, margin.

**Not on live path:** `UnifiedRiskEngine`, `RiskManager.approve_trade` (Greeks
caps historically miscalibrated — comment in codebase).

**Portfolio:** `GET /api/portfolio/heat`, greeks via paper-trade summary;
options background scan applies concentration caps locally.

---

## 6. Current broker-adapter path

`get_broker()` → `IBKRClient` when `BROKER=ibkr`. Paper/live =
`IBKR_TRADING_MODE` + Gateway port/account; `verify_account_mode` blocks mismatch.
No separate paper broker class.

---

## 7. Current chart architecture

`ChartWorkstation` (`markets:chart`): SVG `ChartCanvas`, watchlist, positions,
intel panels (bias/alignment/structure), setup scanner, signal drawer
(**advisory — no broker submit**). Display helpers in
`utils/chartWorkstationDisplay.ts`. Secondary: `TradeMarkerChart`, Dashboard
equity curve.

---

## 8. Current options support

| Capability | Status |
|------------|--------|
| Chain | UI + broker |
| Spread scanner | OptionsScanPanel → trade-desk signal |
| Income / CSP | Screen + eligibility display |
| Flow | Evidence tape |
| Options signals store | `GET /api/options/signals` |
| Decision desk API | Evaluate only |
| 0DTE Autopilot | Must stay off |
| Iron condor / naked | Not executable / not approved |

---

## 9. Current sidebar and routing

Defined in `TerminalLayout.tsx` `NAV_MODEL` + `navLabels.filterNavForDisplay`
(Core vs Advanced). Groups: Command Center, Markets, Trade Desk, Strategies,
Options Desk (advanced), Portfolio & Risk, Research, Journal & Replay,
Performance, System. Kill Switch fixed at bottom.

Target IA (§2 of product spec) **rehomes** Options under Trade Desk and expands
Markets / Strategies / Performance — implement behind `trade_desk_v2` flag with
legacy nav fallback.

---

## 10. Missing requirements (vs product spec)

- Trade Desk shell (header strip, EQUITIES|OPTIONS|… tabs, 4-panel layout)
- Command Overview inside Trade Desk
- Unified Equity Desk / Options Desk workspaces
- Typed TradeIntent + TradeIntentEvaluation
- Orders workspace (working/pending/filled/…)
- Execution Monitor (timeline, slippage, reconciliation)
- Trade Replay (not Journal rename)
- Feature flags system
- Data-quality strip on every desk
- Mobile drawer / bottom nav for desk
- Eligibility always from backend (UI display only)
- Copilot modify → mandatory re-eval
- Counts/badges on queue/orders/positions

---

## 11. Security risks

1. Unauthenticated trade-desk mutate APIs (approve, mode, manual-trade, kill) —
   single-operator assumption; nginx Basic Auth only in prod.
2. Hardcoded kill-switch reset string in frontend RiskMonitor.
3. No multi-user / account isolation models.
4. Paper visibility settings can relax loss limits — must not apply on Live.
5. Scan `POST /signal` payload gaps vs approve sizing (equity defaults risk).
6. Default DB URL credentials in config defaults (ops hygiene).

**Phase B–F:** do not weaken auth; defer SaaS RBAC to explicit later phase.
Keep kill switch + paper/live badge always visible.

---

## 12. Migration risks

| Store | Risk |
|-------|------|
| `execution_events` JSONB | Flexible but untyped — TradeIntent may add tables |
| `trades` | Options-centric columns; equity encoded awkwardly |
| No `orders` table | Need additive Order entity + broker id / client order id |
| `positions.risk_approved` | Mostly unused — don’t rely without wiring |
| Historical fills/journal | **Never delete**; additive migrations only |

---

## 13. Maximum 12-step implementation plan

| Step | Cursor phase | Deliverable | Execution change | Gate |
|------|--------------|-------------|------------------|------|
| 1 | A | This plan (done) | No | — |
| 2 | B | Feature flags + Trade Desk routes/shell/header/tabs/panel layout + sidebar IA under flag | No | **approve Phase B** |
| 3 | B | Command Overview (read-only queues from existing APIs) | No | Phase B |
| 4 | C | Equity Desk: discovery + ChartWorkstation embed + intelligence rail (display) | No | approve Phase C |
| 5 | C | Equity order composer → evaluate endpoint; submit only via existing gate (paper) | Paper evaluate/submit | Phase C review |
| 6 | D | Options Desk shell: chain, builder, scanner, income, flow as internal tabs | No | approve Phase D |
| 7 | D | Options intelligence rail + 0DTE read-only / Copilot-only | No Autopilot | Phase D |
| 8 | E | TradeIntent + Evaluation; **wire Risk/Portfolio into `_execute_signal`** | **Yes** | **OWN GATE — money-path spec** |
| 9 | E | Copilot Queue v2 + audit; modify → re-eval | Approve path | approve Phase E-copilot |
| 10 | E | Orders workspace + Execution Monitor + client order id / dedupe | OMS visibility | Prefer **after paper track**; own approve |
| 11 | F | Trade Replay MVP + a11y + mobile desk behavior | No new submit | Prefer after paper; approve Phase F |
| 12 | Claude Code | Integration audit, paper E2E, release notes, deploy prep | Deploy only if green | Claude Code Stages |

**Sequencing recommendation:** Steps 2–7 improve operator UX without rewriting OMS.
Steps 8–11 are infrastructure-heavy; Step 8 is the only live-gate change and must
not ride along with UI phases. Steps 10–11 are most valuable *after* paper
validation proves the current path, unless product explicitly wants lifecycle UI
sooner for observability during paper.

---

## 14. File-by-file Phase 1 plan (Step 2 — Shared Shell)

**Phase 1 = Cursor Phase B only. No execution path changes.**

### New
- `frontend/src/trade-desk/TradeDeskShell.tsx` — shell layout
- `frontend/src/trade-desk/TradeDeskHeader.tsx` — account/env/mode/risk/broker/heat/P&L/kill
- `frontend/src/trade-desk/TradeDeskTabs.tsx` — EQUITIES | OPTIONS | COPILOT | …
- `frontend/src/trade-desk/PanelLayoutManager.tsx` — resize/collapse + localStorage
- `frontend/src/trade-desk/CommandOverview.tsx` — landing queues (read-only)
- `frontend/src/trade-desk/featureFlags.ts` — `trade_desk_v2` etc. (env + localStorage)
- `frontend/src/pages/TradeDeskV2.tsx` — page entry behind flag
- `docs/trade-desk-2.0/PLAN.md` — this file

### Edit
- `frontend/src/App.tsx` — map `trade:*` / new keys to V2 when flag on; keep legacy
- `frontend/src/components/TerminalLayout.tsx` — NAV_MODEL under flag (collapsible
  groups; Options under Trade Desk; Kill Switch fixed)
- `frontend/src/utils/navLabels.ts` — labels/status for new keys
- `frontend/src/api/client.ts` — only if flag endpoint added (optional)

### Tests
- `frontend/src/trade-desk/__tests__/TradeDeskShell.test.tsx` — tabs, flag fallback
- `frontend/src/components/__tests__/TerminalLayout.test.tsx` — extend nav under flag
- `frontend` build + existing suite green

### Explicitly not in Phase 1
- Order composer submit changes
- TradeIntent tables
- Replay
- Removing old TradeDesk

---

## 15. Test plan (whole program)

| Area | Focus |
|------|-------|
| Nav/layout | Groups, active route, flag fallback, panel persist |
| TradeIntent | Valid equity/options; expire; modify → re-eval; stale eval block |
| Risk | Size, daily loss, drawdown, concentration, event, BP, freshness, kill |
| Orders | Submit → ack → partial → fill → reject → cancel → replace → dedupe |
| Copilot | Approve/reject/snooze/modify/expired/risk-changed |
| Equity/Options desks | Eligibility labels; blocked reasons; no chart direct submit |
| Replay | Source labels; paper vs live separation |
| Security | No frontend secrets; live mode explicit; audit rows |
| Release | Existing `npm test` / `npm run build`; backend pytest for new modules |

Paper E2E checklist owned by Claude Code Stage 5 (spec §21).

---

## 16. Rollback plan

1. **Feature flag off** (`trade_desk_v2=false`) → legacy `TradeDesk.tsx` + current nav.
2. Do not delete legacy page until paper E2E accepted.
3. DB: additive migrations only; rollback = stop using new tables / flag off.
4. Deploy: existing `deploy/hetzner/update.sh` only after Claude Code Stage 7 gate;
   revert commit / prior image if smoke fails.
5. Kill switch remains independent of UI version.

---

## 17. Exact non-goals (first release)

- Live futures / CME data
- Prop-firm automation
- Multi-account copy trading
- Naked short options
- Automatic 0DTE Autopilot
- Social trading / public strategy marketplace
- Guaranteed-performance claims
- Direct LLM-to-broker execution
- Full institutional OMS replacement
- Unlicensed data redistribution
- Broad repository refactor unrelated to Trade Desk
- Multi-member orgs, RBAC and billing (roles/invitations still deferred; the
  ownership key itself is decided in ADR-0001, Accepted 2026-10-05)
- Replacing IBKR adapter or ChartWorkstation SVG engine
- Second submit path beside `_execute_signal`
- Complex event bus before REST polling + existing IBKR quote WS are insufficient

---

## Approval gate

- Reply **approve Phase B (shell)** to start Step 2 only (no money path).
- **Do not** blanket-approve Steps 8–11 with Phase B.
- Step 8 requires a separate money-path spec before any `_execute_signal` edit.
- Security fixes (kill-switch reset secret, trade-desk mutate API keys) may ship
  independently of Trade Desk 2.0.
