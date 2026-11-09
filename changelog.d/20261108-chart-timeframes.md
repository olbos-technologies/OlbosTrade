## Unreleased — chart page: TradingView-style intervals and ranges, positions left, watchlist right

- The chart has two time controls, like TradingView. The **interval strip** at the top-left of the chart sets the candle size: 1m, 5m, 15m, 30m, 1h, 4h, D, W, M (was 5M, 15M, 1H, 1D at the right of the page header). The **range bar** at the bottom-right sets how much history is shown: 1D, 5D, 1M, 3M, 6M, YTD, 1Y, 5Y, All.
- Picking a range also picks a sensible candle size for it (for example 1Y shows daily candles); picking an interval keeps the range. Yahoo keeps limited history at fine intervals (about a week of 1-minute candles, 60 days of 5/15/30-minute, two years of hourly), so a range wider than that is cut down and the page says so: "Showing the last 7 days: 1 minute bars don't go back further."
- Open positions are in a panel on the left of the chart and the watchlist in a panel on the right, instead of tabs. On a phone the chart comes first, then the panels below it, and both strips scroll sideways with 44px targets.
- Backend: `GET /api/equity/chart/{ticker}` accepts the new intervals and an optional `range`, and reports `range` and `range_clamped`. Called without `range` it behaves exactly as before, so the Equity and Options desk charts are unchanged. 4-hour candles are built from hourly ones.

Needs a backend restart (new chart parameters) and a frontend rebuild. No migration, no new environment variables.
