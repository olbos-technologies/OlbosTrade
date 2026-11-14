# Pro Workspace

**Status:** first slice. A dockable, resizable multi-panel view of the terminal at
`/terminal/markets/workspace` (sidebar: Markets → Pro Workspace). Presentation only.

## What it is

A chart in the middle, a side column and a bottom dock. Each slot holds one panel
the terminal already has, chosen from a menu in the slot header. Splitters resize
the regions by mouse, touch or keyboard, and the layout is remembered in this
browser. It is an additional page: every existing page is unchanged, and the same
workspace serves every brand and picks up each brand's colours from the shared
design tokens.

| Panel | Reuses |
| --- | --- |
| Chart (always in the centre) | `api.getEquityChart`, `CandlestickChart` (MA, VWAP, MACD, volume), `IntervalStrip`, `RangeBar` |
| Watchlist | `WatchlistTable`; the **same list as the Chart page** (`olbos.chart.watchlist`) |
| Positions | `PositionsTable` via `usePaperTrade` |
| Unusual options | `OptionsFlow` |
| News & catalysts | `NewsCatalysts` |
| Scanner | `ScanCenter` (options and equity) |
| Setup scanner | `SetupScannerPanel` |
| Backtest | `BacktestCenter` |

Presets: **Trader** (default), **Research**, **Chart only**. Toggles show or hide the
side panels and the bottom dock; **Reset layout** returns to Trader.

## What it never does

It places no orders and writes nothing to the server by itself (a test asserts that
browsing, switching presets and changing symbol send no non-GET request). It does
not change the execution mode, risk limits, broker connection or account in use
(AGENTS.md rule 3). A panel whose data fails shows its own unavailable state; the
chart never draws a made-up series, and an unknown price shows "—", never 0
(rule 5). Each panel sits in an error boundary, so one failing panel leaves the rest
working.

## Layout rules

`frontend/src/workspace/layout.ts` is plain logic with tests.

- Sizes are clamped: chart column 40–85% of the width, chart area 35–80% of the
  height (the dock always keeps at least 20%), side split 20–80%. A region cannot be
  dragged to nothing and lost.
- A saved layout comes from browser storage, which anyone can edit, so it is
  validated on load: wrong version, unknown panels (including `__proto__`),
  non-numbers and out-of-range sizes fall back to safe values.
- Splitters are WAI-ARIA window splitters: focusable, arrow keys (Shift for a larger
  step), Enter or double-click to reset.
- At 900px and under, the regions stack into one panel at a time, chosen from a
  strip. Three resizable regions do not fit a phone.

## Not in this slice (deliberately)

- **Seasonality.** Needs years of price history and a confirmed data source and
  rights. No placeholder numbers are shown.
- **Volume profile and drawing tools on the chart.** The chart has MA, VWAP, MACD and
  volume today. A volume profile can be computed from bars already loaded; drawing
  tools are a larger piece of work.
- **Free-form docking** (dragging a panel to a new edge) and **layouts that follow
  the account across devices.** Layouts are per browser. A server-side saved layout
  would need a backend table and is a separate decision.
- Signal levels on the workspace chart (the Chart page draws them); the workspace
  chart passes none today.
- The embedded pages keep their own page-style headings, so a few panels look
  taller than a dedicated terminal panel would.

## Adding a panel

Add its id and title to `PANEL_IDS` and `PANEL_TITLES` in `layout.ts`, render it in
`renderPanel` in `panels.tsx` (it is wrapped in an error boundary automatically), and
the layout tests and the slot menus pick it up.
