## Unreleased — chart page: chart on the left, positions and watchlist on the right

- The chart page now splits the window side by side, two to one: the chart takes the left two thirds at full height, and a flat tabbed panel takes the right third, instead of leaving empty space under the chart.
- The panel has two tabs: Positions (symbol, quantity, average cost, unrealized P&L, days held) and Watchlist (last price and move, click a symbol to load it, × to remove). A missing price or an unreadable positions list shows "—" or "unavailable", never zero.
- On a phone the panel sits under the chart and you scroll to it, so the chart still starts near the top of the screen.
- Symbols are still added from the Symbol box in the bar above the chart.

Frontend only: no backend restart, no migration, no new environment variables.
