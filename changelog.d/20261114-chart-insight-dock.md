## Unreleased — Chart page: research dock under the chart

- The Markets → Chart page gains a tabbed research dock under the chart (desktop only): **Signal**, **Levels**, **Volatility** and **Catalysts**. It reads data the page already loads; it adds no endpoint, migration or environment variable. Frontend-only deploy.
- The dock starts open on windows at least 1000px tall and collapsed on shorter ones, so the chart keeps its height (the existing "chart is tall" browser test still holds). One click on any tab opens it.
- Honesty rules carried into the dock: the model score is labelled "not a probability"; levels say whether they are the signal's own plan or chart-derived support/resistance ("Not a trade plan"); a failed volatility or catalyst request says so instead of showing an empty state; missing numbers are a dash, never zero.
- The server's "IV rank" is a 14-day-ATR realized-volatility rank, not implied volatility (`/api/market/iv-rank`, `method: atr_rv_proxy`). The dock calls it "Volatility rank" and says so. The older "IV Rank" tile on the compact desk chart is unchanged.
- Phones keep the existing layout (no dock).
