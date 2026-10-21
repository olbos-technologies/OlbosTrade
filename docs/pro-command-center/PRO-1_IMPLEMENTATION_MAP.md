# PRO-1 — Command Center architecture and data audit

Status: audit complete; implementation and release gates remain open. This is a
read-only planning artifact for UX-1/UX-2/UX-3, not approval to trade or deploy.
Audited base: `8dee824` (upstream `main`, 2026-10-21).

## Repository baseline

- `AGENTS.md` and `docs/MASTER_UPGRADE_PLAN.md` are absent on this checkout.
  Draft PR #125 proposes both; do not treat that draft as merged or duplicate
  its master plan in a competing PR. Reconcile this map with it before UX-1
  implementation and update its milestone ledger after that PR lands.
- `CLAUDE.md`, `docs/MASTER_ARCHITECTURE.md`,
  `docs/trade-desk-2.0/MASTER_SPEC.md`, and
  `docs/ui-ux-phase1-4/PLAN.md` establish the safety and truthfulness rules.
  The Phase 1–4 plan's status preamble says its old batches are complete; it is
  not an unfinished work queue.
- Draft PR #126 changes the portfolio entry gate (R1). Keep its backend files
  and tests outside all Command Center PRs.
- A separate local working tree has committed Positions work and uncommitted
  mobile/Pro prototype work. Do not sweep it into a PRO milestone or discard
  it during branch setup.

## Integration decision

Extend the existing Command Center route (`Dashboard.tsx`) with reusable,
read-only analytics panels. Keep `TradeDeskV2.tsx` and its `CommandOverview`,
`EquityDesk`, `OptionsDesk`, `CopilotQueue`, `OrdersWorkspace`,
`ExecutionMonitor`, `TradeReplay`, and `DeskSettings` as the execution
workspace. `TerminalLayout.tsx` remains the single global shell, navigation,
account/menu, environment, execution-mode and kill-switch display. Do not add
a second order composer, approval queue, execution control, or kill switch.

Reuse `ExecutiveSummary`, `CommandAttention`, `RiskCenter`/`RiskMonitor`,
`usePaperTrade`, `useRisk`, existing panel primitives, and the shared API
client. Create typed, read-only presentation adapters before adding charts.
Mobile keeps its purpose-built `MobileHome` until each new panel is designed
and browser-tested for phone widths; do not render the desktop grid on phones.

The attached reference is a conceptual, low-resolution image of four modules,
not a source of data or a pixel-perfect desktop specification. Use original
OlbosTrade visuals, vocabulary, and accessible color semantics.

## Data contracts and provenance

| Need | Current source | Audit decision |
| --- | --- | --- |
| Account equity, cash, buying power | `GET /api/paper-trade/portfolio` | Broker-wide account values; tenant-scoped DB P&L is different. Label provenance and hide broker values when `broker_error` is set. |
| Broker positions and metadata | `GET /api/paper-trade/positions` | Broker holdings can be untracked or owned by another tenant; preserve reconciliation/source warnings and do not claim every row is Olbos-managed. |
| Portfolio heat | `GET /api/portfolio/heat` | Scoped display of tracked trades, explicitly not the desk-wide execution gate. Never label it as total broker exposure. |
| Risk state and limits | `GET /api/risk/portfolio-state`, existing guardrail/reconciliation APIs | Reuse backend calculations. This endpoint substitutes starting capital on broker read failure, so the UI must check `broker_error` before presenting account values; contract hardening belongs to a separate reviewed backend change. |
| Correlation | `GET /api/portfolio/correlation` | Already aligns market bars for scoped open-trade underlyings and returns `insufficient_data`/`error`. It is not a selectable-period broker-holdings matrix and does not currently expose sample size/frequency. Do not fill missing cells. |
| Strategy allocation | `GET /api/portfolio/allocation` | Target strategy weights, **not** actual sector exposure. Do not use for a sector donut. |
| Signals and explanations | `GET /api/equity/signals`, `GET /api/strategy/signals/{id}/explanation` | Reuse for links and evidence; never relabel model confidence or return-on-risk as probability of profit. |
| Equity history | Closed-trade history and backtest history exist | Neither is verified historical account equity. Current `Dashboard.tsx` falls back from trade-derived curve to backtest curve; replace this with explicitly separated datasets/empty state, not a synthetic account curve. |
| P&L waterfall | Closed-trade/position data is partial | No verified account-level attribution contract identified. Defer chart pending account/tenant scoped definitions for realized, unrealized, adopted and unknown-cost positions. |
| Sector exposure | `portfolio_engine.sector_for` used by heat | Current heat describes tracked-trade risk, not broker-verified dollar market value. A sector allocation chart needs a validated read-only aggregation and explicit unknown classification. |

All new response types should carry source, account/scope, as-of timestamp,
freshness/completeness, and unavailable reason where applicable. Unknown is
never zero. Do not merge paper account, live account, closed-trade-derived,
and backtest series into one unlabeled chart.

## Experience-level boundary

Beginner, Intermediate, and Pro are **presentation preferences**, separate
from paid Free/Pro/Elite entitlements and separate from Manual/Copilot/Autopilot
execution mode. A preference change must not grant broker access, turn on live
trading, change risk limits, or submit orders. The separate local, uncommitted
`ProDesktopDashboard.tsx` prototype is selected by `auth.user.tier === "pro"`;
that wiring is not an implementation of the requested experience preference.
Treat it as a prototype to refactor or discard after review, not a release gate.

## Incremental PR boundaries

1. **PRO-1 (this map):** document integration, authority, API gaps, conflicts,
   screenshot interpretation and release plan. Reconcile with draft PR #125.
2. **PRO-2:** typed read-only panel shell and summary strip in Command Center;
   preserve existing Trade Desk and mobile routes. No new backend or fake data.
3. **PRO-3:** verified performance/risk adapters and charts, separate account
   history from trade history and backtests. Add a read-only backend contract
   only if necessary, with scoping and failure tests.
4. **PRO-4:** broker-aware positions monitor, then sector/correlation panels
   only where provenance/sample data supports them. Defer waterfall until
   attribution accounting is defined and tested.
5. **PRO-5:** experience preference and signal explanation layers. Paid tier
   remains an independent entitlement boundary.
6. **PRO-6:** TypeScript, Vitest, build, Playwright on 320px phone, larger phone,
   tablet, 1440px and 1920px desktop; inspect real screenshots, keyboard and
   chart descriptions, CI, handoff and release review. No automatic merge or
   deployment.

Each implementation PR needs a base SHA, changed-file list, exact commands and
results, screenshots, API provenance, safety impact, known gaps and rollback
notes. Release claims require a green CI and browser evidence, not jsdom alone.

## PRO-1 verification and handoff

This PR changes documentation only: no API, migration, frontend route, order
path, execution mode or risk limit. Verify the Markdown diff and confirm the
branch contains only this file. Frontend/browser/backend test suites are not
evidence for a documentation-only architecture map; they become mandatory in
the implementation PRs. No screenshots are produced by PRO-1. Next owner:
reconcile this map with PR #125, then begin PRO-2 on a fresh branch.
