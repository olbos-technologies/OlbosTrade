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
| Chart (always in the centre) | `api.getEquityChart`, `CandlestickChart` (MA, VWAP, MACD, volume, **volume profile**, **drawing tools**), `IntervalStrip`, `RangeBar` |
| Watchlist | `WatchlistTable`; the **same list as the Chart page** (`olbos.chart.watchlist`) |
| Positions | `PositionsTable` via `usePaperTrade` |
| Unusual options | `OptionsFlow` |
| News & catalysts | `NewsCatalysts` |
| Scanner | `ScanCenter` (options and equity) |
| Setup scanner | `SetupScannerPanel` |
| Backtest | `BacktestCenter` |

Presets: **Trader** (default), **Research**, **Chart only**. Toggles show or hide the
side panels and the bottom dock; **Reset layout** returns to Trader.

## Volume profile

A horizontal histogram at the right edge of the price pane showing how much volume
traded at each price, for the bars **currently in view**. It recomputes as you zoom
and scroll. The busiest price (the point of control, POC) gets a dashed line across
the pane, and the value area (the band around the POC holding 70% of the volume) is
drawn slightly stronger. A line under the chart reads, for example,
`Volume profile · 160 bars · POC 116.64 · value area 114.65–117.12`. The **Volume
profile** button in the chart bar turns it off; the choice is remembered in this
browser. It is on by default in the workspace.

**It is approximate.** It is built from OHLCV bars, not from individual trades. A
bar says how much traded in total and the range it covered, not where inside that
range, so each bar's volume is spread evenly across the price bins its high-low range
touches. A coarser bar interval makes the profile coarser. The line's tooltip says so.
It shows where trading happened. It is not a signal and does not predict price.

How it is built: `components/chart/volumeProfile.ts` is the plain, tested maths (volume
is conserved exactly; invalid bars are skipped; a flat window is one level).
`VolumeProfilePrimitive.ts` draws it as a charting-library series primitive attached to
the candle series, so it uses the candles' own price scale and stays aligned through
zoom, scroll and resize. It is behind the candles and neutral grey with a blue POC line:
a profile shows where volume traded, not gain or loss, so it does not use the green and
red that mean profit and loss. `CandlestickChart` takes `volumeProfile` (off by default, so
every user of the chart that does not ask for it is unchanged). The workspace chart and the
Chart page (Markets > Chart) ask for it; the compact desk charts do not. The Chart page has the
same **Volume profile** button, in its chart toolbar, and shares this saved choice: turning the
profile off in one turns it off in both. Its readout is in the chart legend there.

Known: when the chart is removed while a redraw is pending, the charting library logs
"Object is disposed". That already happens on the Chart page (checked on a clean
checkout of `main`), and whether it fires in a given browser run is timing. So no browser
test counts it (one that did failed 7 runs in 30 on clean `main` under CPU load). The part
that is not timing, the ORDER of teardown (the profile and the drawings let go before the
chart is removed), is pinned on a fake chart in `CandlestickChartTeardown.test.tsx` and
`drawings/useDrawings.test.tsx`; the browser tests check that nothing other than the
library's own message is ever thrown.

## Drawing tools

A rail beside the chart: **Select**, **Trend line**, **Horizontal line**, **Vertical line**,
**Rectangle** and **Fib retracement** (levels 0, 23.6, 38.2, 50, 61.8, 78.6 and 100%), a
**Magnet**, a **Style** button (six colours, three line widths; it shows the current colour
and unfolds in place), and **Undo**, **Redo**, **Delete** and **Clear all**.

- **Drawing.** Pick a tool, then click the first point, move, and click the second; or press,
  drag and release. One-point tools (horizontal and vertical line) take a single click. When a
  drawing is finished it is selected and the tool returns to Select. **Escape** cancels a
  half-placed drawing, then leaves the tool, then deselects.
- **Editing.** Click a drawing to select it. Drag its body to move it, or drag one of its
  handles to move just that end. **Delete** removes it. **Ctrl/Cmd+Z** undoes, **Ctrl/Cmd+Shift+Z**
  or **Ctrl+Y** redoes. One drag is one undo step. Clear all can be undone. Choosing a colour
  or width recolours the selected drawing and sets the style for new ones.
- **Magnet.** Pulls a new point or a handle onto the nearest bar and its nearest open, high, low
  or close, when the pointer is within 12px.
- **Anchored to time and price**, not to pixels or bar numbers, so a drawing stays on the same
  candle and price through zoom, scroll, resize and a change of bar interval. A point may sit
  between two bars or in the future past the last bar (positions there are extended using the
  median gap between bars, so a weekend does not distort it).
- **Saved per symbol in this browser only.** Not in the account, and not on other devices. Up
  to 200 drawings per symbol and 40 symbols; the oldest symbols go first. Opening a symbol writes
  nothing back; only an edit saves. Stored data is validated on load (browser storage can be
  edited): unknown kinds, malformed or out-of-range numbers, bad colours, duplicate ids and
  hostile keys such as `__proto__` are dropped or repaired.
- **Mouse and pen only.** The charting library listens for touch with handlers that cannot be
  intercepted, so drawing on a touch screen would fight the chart's own panning. The rail is not
  offered on the stacked phone layout (900px and under); saved drawings still show there but
  cannot be edited.
- **Notes, not orders.** Drawings place no orders, set no stops and feed no risk or execution
  logic. A horizontal line is a mark on the chart, not a stop or a limit price. Colours leave out
  pure green and red, which mean profit and loss elsewhere.

How it is built (`frontend/src/workspace/drawings/`): `model.ts` (kinds, validation, storage),
`history.ts` (undo and redo), `time.ts` (bar position to and from time, with interpolation and
extension), `geometry.ts` (pixel shapes and hit testing; the same shapes are drawn and picked),
`snap.ts` (magnet), `engine.ts` (the tool, selection and gesture state machine, with no DOM or
chart in it), `DrawingsPrimitive.ts` (a charting-library series primitive that paints on the
candle series and uses the chart's own scales), `useDrawings.ts` (attaches it, listens to the
mouse and keyboard, saves edits) and `DrawingToolbar.tsx`. `CandlestickChart` gained an opt-in
`onReady` handle; without it nothing changes, so the Chart page is untouched.

The drawings are detached, and their mouse listeners removed, before the chart is removed
(the chart runs registered disposers first). That order is pinned by deterministic unit tests
(see the volume profile note above for the library's own "Object is disposed" message, which
is timing and is not counted by any browser test).

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
- **More drawing tools.** Text notes, rays and extended lines, parallel channels, Fibonacci
  extensions, a measure tool, and per-drawing visibility. Drawings saved to the account or
  shared across devices. Touch drawing.
- **Drawing tools on the Chart page**, and **session or fixed-range profiles** (it
  covers the bars in view). **Tick-accurate profiles** need trade data we do not load.
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
