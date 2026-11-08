## Unreleased — the chart page is just the chart, on desktop and on phones

- The research dock under the chart (Setup readiness, Watchlist, Signals, Positions, Plan & context, Research, Safety), the page heading and explanation, the KPI cards, the snapshot card and the Focus chart toggle are gone. The page is one compact instrument bar and a chart that fills the window.
- On a phone the bar is four short rows: symbols (scroll sideways), price and symbol box, timeframes and Signal Detail, and the last bar's numbers. The chart now starts around the top of the screen instead of below it (about y=380 on a 390×844 phone, down from 678). Every control keeps a 44px target and nothing overflows down to 320px wide.
- A Symbol box in the bar loads any symbol and pins it to the picker; an × next to the current symbol removes it and moves to the next one. This replaces the Watchlist tab.
- Setup Readiness is no longer shown anywhere (it only lived on this page). The positions, safety and plan values are available on the Trade Desk and Command Center pages.
- Signal Detail is a flat outline in every chart layout. The chart legend starts collapsed behind a Legend button.
- The compact chart inside the Equity and Options desks is unchanged.

Frontend only: no backend restart, no migration, no new environment variables.
