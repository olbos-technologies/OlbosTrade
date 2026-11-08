## Unreleased — chart page: chart-first layout with a tabbed dock (desktop)

- The chart page's heading, symbol tabs, four KPI cards, snapshot card and chart title row are replaced by one instrument bar: symbol picker, price and move, the last bar's open/high/low/close/volume, how fresh the data is, the signal badge, timeframes, Signal detail and Focus chart. The chart now starts near the top of the screen at full width instead of below the middle.
- The watchlist, setup readiness, signals, positions, trade plan and context, research evidence and safety panels move into a tabbed dock under the chart (flat tabs, gold underline on the selected one, arrow-key navigation). Watchlist and positions are dense tables. Execution mode, day P&L, portfolio and reconciliation, which the removed KPI cards showed, are in the Safety tab.
- Signal detail, Focus chart and Rescan are flat outlines instead of raised keys.
- Missing prices and unreadable positions show "—" or "unavailable", never zero.
- Phones, the compact Equity/Options desk chart and Focus chart keep their existing layout.
- Includes the earlier unmerged change: the chart legend is collapsed by default behind a Legend button, and a blocked setup shows its first reason on its row.

Frontend only: no backend restart, no migration, no new environment variables.
