# Multi-timeframe

**Where:** Markets > Multi-timeframe (`/terminal/markets/multi`). One symbol on two to four charts, each at its own
bar interval, so the big trend and the entry chart can be read side by side. Presentation only: it never changes the
execution mode, risk limits or account, and places no orders.

## What it shows

- **Two to four charts** (a selector at the top, default three: Daily, 1h, 15m). Each chart has its own interval
  selector. Higher timeframes come first by default. Changing the count keeps the charts you chose and adds new ones
  from the defaults for that size, skipping any interval already on screen (adding a chart never adds a copy).
- **One symbol for all of them.** A symbol box, the first eight symbols of the shared watchlist
  (`olbos.chart.watchlist`, the same list as the Chart page and the Workspace), and `?symbol=` in the address.
- **Where price sits against its averages**, per chart (`MA20 ▲`, `MA50 ▼`, with a tooltip and an accessible name in
  words) and as one sentence above the charts ("Price is above its 50-bar average on all 3 timeframes." or "Mixed:
  above the 50-bar average on 2 of 3, below on 1."). **This is description, not a signal**: it reports what has
  already happened. It uses arrows and words, never the green and red that mean profit and loss elsewhere. A chart
  with too few bars (fewer than 50 for the 50-bar average) shows a dash and is left out of the sentence, which says
  how many were compared.
- **Indicators.** One **Indicators** button applies to every chart. The tiles are small, so they start lighter than the
  main chart (MA20, MA50, volume) and keep their own saved choice (`olbos.multitf.indicators`), separate from the
  Chart page and Workspace choice.
- **Each interval asks for a range that suits it** (Daily 1Y, Weekly 5Y, 4h 6M, 1h 1M, 15m 5D, ...), all ones the
  backend serves without clamping. If it ever has to cut a range down, the chart says so.

## What it does not do

- **The charts are not synchronised** in time or zoom: different intervals do not share a time axis. The page says so.
- **Each chart stands on its own.** A chart whose request fails says so for itself and the others still draw. A chart
  that returns no bars says so. Nothing is ever drawn from made-up data.
- No drawing tools and no volume profile here (they are on the Workspace and the Chart page). No live refresh: like
  the Chart page, a chart loads when its symbol or interval changes.
- It cannot make delayed quotes live. The bars come from the same source as the Chart page.

## Layout

Three charts sit in a row from 1280px up; four make a two-by-two grid; between 760px and 1279px the grid is two
columns; at 760px and under each chart takes a row of its own, 380px tall, and the page scrolls. The layout (the
number of charts and each interval) is saved in this browser (`olbos.multitf.layout`) and validated on read.

## How it works

`pages/MultiTimeframe.tsx` (the page and its tiles), `chart/useChartBarsMany.ts` (one independent request per chart;
only what is new is fetched, so changing one interval refetches only that chart, and a slower, older answer never
overwrites the newest pick), `chart/trendState.ts` (the averages comparison and the sentence),
`chart/multiTimeframePrefs.ts` (the saved layout and the range for each interval). It reuses `CandlestickChart` and
the indicator picker.
