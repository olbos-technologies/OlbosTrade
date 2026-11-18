# Pro Workspace

**Status:** this is now the **Chart page**, at `/terminal/markets/chart` (sidebar: Markets → Chart).
It replaced the earlier single-chart page and the separate "Pro Workspace" entry; the old address
`/terminal/markets/workspace` redirects here and keeps `?symbol=`. The compact chart inside the
Equity and Options desks is unchanged. Presentation only.

## What it is

A chart in the middle, a side column and a bottom dock. Each slot holds one panel
the terminal already has, chosen from a menu in the slot header. Splitters resize
the regions by mouse, touch or keyboard, and the layout is remembered in this
browser. It is an additional page: every existing page is unchanged, and the same
workspace serves every brand and picks up each brand's colours from the shared
design tokens.

| Panel | Reuses |
| --- | --- |
| Chart (always in the centre) | `api.getEquityChart`, `CandlestickChart` (the **indicator picker**: moving averages, VWAP, Bollinger, volume, MACD, RSI, ATR; **volume profile**; **drawing tools**), `IntervalStrip`, `RangeBar` |
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
profile off in one turns it off in both. Its readout is in the chart legend there, kept to one
line (it pushes the chart down otherwise) and dropped at 340px and under.

Known: when the chart is removed while a redraw is pending, the charting library logs
"Object is disposed". That already happens on the Chart page (checked on a clean
checkout of `main`), and whether it fires in a given browser run is timing. So no browser
test counts it (one that did failed 7 runs in 30 on clean `main` under CPU load). The part
that is not timing, the ORDER of teardown (the profile and the drawings let go before the
chart is removed), is pinned on a fake chart in `CandlestickChartTeardown.test.tsx` and
`drawings/useDrawings.test.tsx`; the browser tests check that nothing other than the
library's own message is ever thrown.

## Indicators

An **Indicators** button in the chart bar (the Workspace chart bar and the Chart page's chart toolbar) opens a
panel of checkboxes: what the chart draws. Overlays go on the price chart and panels get their own pane
below it. The choice is remembered in this browser and is **one shared choice for the Chart page and the
Workspace**: turn one on in either and it is on in both. **Reset to default** restores what the chart drew
before the picker existed.

| Overlays (on the price chart) | Panels (below it) |
| --- | --- |
| MA5, MA10, MA20, MA50, MA200 (simple averages) | Volume |
| EMA9, EMA21, EMA50 (exponential averages) | MACD (12, 26, 9) |
| VWAP | RSI (14, with guides at 30 and 70) |
| Bollinger bands (20, 2 standard deviations) | ATR (14) |

- **Up to 10 at once.** A phone has little room and every panel takes a slice of the chart. At the cap the
  unticked boxes are disabled and the panel says why. Candles only (none ticked) is valid.
- **Accuracy.** RSI and ATR use Wilder's smoothing. The RSI is checked in a test against the published values
  of the classic worked example (all 19, to two decimals); ATR, Bollinger and the averages are checked against
  small cases worked by hand. A line that needs more bars than are loaded (MA200 on a short range) reads as a
  dash, never as a made-up number. VWAP is cumulative over the bars loaded, not reset each session, and its
  description says so.
- **Not signals.** The panel says "Indicators describe past prices. They are not signals." Colours avoid the
  green and red that mean profit and loss elsewhere in the app (MACD's histogram keeps its signed colouring).
- **How it works.** `chart/indicators.ts` (what exists, the maths, readout and legend text),
  `chart/indicatorSeries.ts` (draws them), `chart/IndicatorPicker.tsx` (the panel, in a portal so the scrolling
  phone toolbar cannot clip it; a sheet above the bottom nav on a phone), `chart/indicatorPrefs.ts` (the saved
  choice, validated on read). `CandlestickChart` takes `indicators`; left out it draws exactly what it always did,
  so the compact Equity and Options desk charts are unchanged. A different selection rebuilds the chart and
  keeps the zoom.
- **Not here yet.** User-set periods (every indicator has one fixed period), more indicators (Stochastic,
  OBV and others), saving named sets, and tying the count to a plan tier (see `PLAN_BENCHMARK.md`).

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
- **Touch.** The same gestures work with one finger: press, drag and release (or tap twice) to
  draw; drag a line or a handle to edit. A fingertip is less exact, so touch picks a line within
  14px and a handle within 20px (a mouse: 6px and 7px). The hook listens in the capture phase
  with non-passive handlers and keeps a press from the chart only when the engine used it, so
  one finger on empty space still pans; a second finger cancels the gesture so pinch-zoom works
  (verified with real touch events in `e2e/chart-drawing.spec.ts`).
- **Where.** On a desktop (900px and wider) the rail sits beside the chart. On a phone it is behind a
  **Draw** switch in the chart bar: off by default so the chart behaves as before (one finger pans,
  two pinch), on it opens a finger-sized horizontal bar (44px buttons) above the chart. The phone
  layout is chart-first: the chart takes about 80% of the screen height, the OHLC line is dropped
  from the bar, the bar's buttons are 44px, and the range buttons scroll in one row.
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
