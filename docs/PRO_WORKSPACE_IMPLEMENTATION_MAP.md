# PRO-1 implementation map — 2026-11-12

**Milestone:** PRO-1, benchmark-led research workspace. **Reviewed base:**
`f03a395`. This is an implementation map, not evidence that the R0/R1 paper
gate or a Pro release is complete. Do not start a production rollout from it.

## Existing surfaces to reuse

| Surface | Current implementation | Reuse decision |
| --- | --- | --- |
| Chart and instrument selection | `frontend/src/pages/ChartWorkstation.tsx` already accepts controlled `symbol` and `onSymbolChange`; its standalone view contains a chart, watchlist, positions, and signal detail drawer | Use its controlled selection contract. Keep chart rendering and interval/range logic; avoid a second chart implementation. |
| Discovery | `frontend/src/pages/markets/Watchlists.tsx` has saved symbols, search, sorting, 60-second snapshots, and a chart link that carries `?symbol=` | Reuse the link/selection pattern. Saved symbols are device-local and do not establish account scope. |
| Signals | `frontend/src/pages/SignalsCenter.tsx` owns Live Signals, History, Strategy Library, and Health; the chart already matches a signal by exact ticker | Link to the existing signal detail. No signal for the selected symbol must remain an explicit empty state. |
| Positions and risk | `frontend/src/components/chart/PositionsTable.tsx`, `frontend/src/pages/RiskCenter.tsx`, and `frontend/src/components/ProPortfolioInsights.tsx` already display these domains | Reuse read-only projections with their existing unavailable states. Do not infer broker verification from an internally tracked position. |
| Execution | `frontend/src/trade-desk/equity/EquityDesk.tsx` combines the controlled chart with discovery, intelligence, and `EquityOrderComposer` | Do **not** mount the Equity Desk as the Pro research shell: it includes an order composer. Link to Trade Desk only through the existing route and authorization flow. |

## Smallest first implementation PR, after R0/R1 evidence

1. Add a read-only Pro research route or panel within the existing terminal
   navigation. Reuse `ChartWorkstation` in controlled mode and a single selected
   instrument state. The existing chart route remains compatible.
2. Synchronize selection among chart, watchlist and a read-only signal summary.
   Use a canonical symbol/contract identifier from the returned data; reject
   malformed deep-link values. A selection change may fetch data but must not
   evaluate, approve, submit or queue a trade intent.
3. Show independent source, observation time, fetch time, stale/error/empty
   state and environment/account scope for each panel. Do not label a last
   close as a live quote, a model score as profit probability, or a missing
   exposure as zero. Preserve an explicit unavailable state when an API read
   fails; do not silently replace failed signals with an empty list.
4. Reuse the existing chart, signal drawer and positions/risk services. Do not
   add an order route, modify risk formulas, or create a second execution-mode
   control. Keep Beginner and Intermediate views usable with the same data.
5. Verify keyboard selection, focus and labels; empty/stale/error states;
   320/390/768/1440px layout in real browsers; and a guard that symbol
   selection never reaches any order/approval API. Run frontend typecheck,
   unit tests, browser tests and build. Capture desktop and mobile screenshots.

## Contracts to verify before follow-up PRs

- **Options scenario:** exact contract identity, multiplier, legs, bid/ask
  timestamp, fees, max loss and assignment assumptions. Unknown quotes block
  numeric payoff claims.
- **Backtest comparison:** point-in-time source, corporate actions, fills,
  commission/slippage, sample size and out-of-sample window. Never join a
  hypothetical return series to broker account equity without labeling both.
- **Portfolio analytics:** broker/account/environment identity, reconciliation
  state and as-of time for each position and aggregate. An incomplete broker
  read does not produce a verified allocation or risk utilization percentage.

## Release dependency and evidence

The current paper-pilot register in `docs/PAPER_PILOT_READINESS.md` still asks
for a recorded order lifecycle, disconnected/stale/ambiguous-response drills,
and a disposable restore. The November 12 master-plan snapshot did not inspect
production evidence. Record those results and a paper-only go/no-go before the
first PRO-1 UI implementation PR is treated as release-ready. Merge, deployment,
and broker-connected paper verification remain separate decisions.
