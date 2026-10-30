# UX-1 / UX-5 desktop workspace correction

Base: 3928ad2d31f7390fcb188d6d5e0e39cbe6e1713d.
Branch: codex/desktop-shell-refinement.

The production screenshot exposed a mismatch: redesigned cards still sat in
an icon-only desktop shell with a miniature logo and a crowded ticker row.
This change gives the official full wordmark its own 68px header, separates
market quotes into their own row, keeps execution controls and failed-read
warnings visible, and opens labeled desktop navigation by default. Collapse
is reversible and preserves branding. Crossing into the phone breakpoint
closes the drawer; phone navigation and header retain their existing layout.

Home uses an asymmetric balance/attention row on large desktops, followed by
full-width trading status and a holdings/protection row. Below 1200px it stacks.
Advanced analytics remain available behind the existing disclosure, now closed
initially, rather than mounting an expanded second dashboard under Home.
No change to fetched data, account permissions, execution mode handlers,
confirmation, kill switch, order paths, risk limits or backend code. No new
migration or environment variable. No merge or deployment by this task.

Validation: full frontend units, TypeScript/build and diff checks; browser
matrix includes Home/Markets/Signals/Positions at 820/1280/1440/1920, branded
header and ticker separation, labeled navigation, reversible collapse, Home
layout and no API writes. Existing populated/unavailable capture matrix runs
against the changed shell as well. Browser tests use synthetic API responses,
not broker evidence. Local browser executable is unavailable; CI captures are
required before claiming geometry or visual acceptance. Record exact CI
results and screenshot review in the PR. Actual Safari/device review remains
outstanding. Roll back the frontend code/image via the release process.

## Desktop direction clarified by the user

The user wants Bloomberg-style desktop density, rather than the spacious card
interpretation. The same branch now uses compact rectangular navy panels,
amber monospace section headings and tabular numeric readouts; Home shows
balance/status/attention together at full desktop width. Desktop holdings are
restored to a semantic aligned table in a named, keyboard-focusable horizontal
scroll region. Phone cards remain unchanged. Account and Data & Integrations
now use distinct person and connections icons in both navigation models.
Browser acceptance asserts distinct icon paths and desktop table/row geometry.
Prior screenshots and CI counts predate this clarification; the PR records
new head checks and superseding captures. No order or mode handlers changed.

## Figma Command Center reference

The user approved a chart-first desktop reference: one compact summary strip,
a central performance chart with execution status beside it, then a full-width
holdings table. DesktopCommandCenter replaces the desktop MobileHome renderer;
phone Home remains unchanged. The existing advanced dashboard stays available
behind its disclosure. Shared Home read helpers are reused, preserving the
45-second display freshness limit and independent unavailable states.

Metrics use available backend fields: broker account value, Olbos day/total
P&L, buying power and daily loss-budget utilization. No net exposure value is
invented. The chart is explicitly cumulative reported P&L from at most 500
returned closed trades, sorted by actual exit dates. It is not intraday broker
equity, uses no backtest substitution, generated dates, synthetic production
points or clipping of negative P&L. A failed/malformed read removes the curve.
Ranges filter the latest dates in the returned history, retaining the original
cumulative baseline. This source limitation remains visible in the panel.

The header adds workspace search with slash-to-focus, Escape and unique-result
Enter navigation. It issues no trading/settings commands. Symbols are not
searched by this first version. A keyboard-focusable positions table retains
read-only quantities, cost, broker marks/tracking and separately read stop
coverage. Links open existing Trade Desk, approvals and Risk pages. The full
logo and distinct icons remain. No new backend endpoint, migration, environment
variable, authorization, risk-limit or order handler. Final head/CI and updated
browser screenshots are recorded in the PR; prior captures are superseded.

The user supplied Bloomberg-Style Trading Terminal.zip. Its App.tsx/CSS are
a visual prototype with hardcoded metrics/prices, a fixed SVG curve, local-only
mode toggles and a different logo. Adapt its panel hierarchy, palette and
summary/grid proportions within existing React 18/Vite dependencies, rather
than importing its app bootstrap or build configuration. Market Pulse uses
existing last-close snapshot GETs; Event Risk uses the existing 45-day calendar
GET. No decorative sparklines or prototype health values enter production.

Advanced analytics now mount on disclosure opening and unmount on close,
so the first desktop view does not run a second hidden dashboard or duplicate
its polling. Existing browser checks open the disclosure and verify it renders.

## Combined clean application reference

The user supplied Improve OlbosTrade App(1).zip and selected its simple layout
as the main visual reference. Retain the working Command Center chart, table,
broker flows and truthful reads from the previous iteration. Apply a shared
near-black navy/gold palette, restrained borders, readable sans headings and
aligned numeric data across primary pages, advanced workspaces and mobile.
The full approved logo remains; no prototype metrics, connection toggles,
security claims, audit events or model scores are imported.

Desktop starts with an 84px labeled Home/Markets/Signals/Positions/Risk/More
rail. More opens the existing full navigation, retaining signed-in Account
filtering and every advanced destination. Navigation selection closes the
expanded rail. The original kill-switch confirmation remains reachable.
Workspace search supports slash and Ctrl/Cmd+K; typing never executes trades.

At >=1600px, a right context panel reports independently read mode, environment,
broker state, entry checks, kill switch, approval counts and actionable
exceptions. Failed polls remove previous successful values. Read receipt ages
are not broker-as-of timestamps or audit evidence. Home's duplicate execution
panel is hidden only when these readings/links are present in the wide rail;
on narrower screens its existing execution panel remains. The rail is not
mounted on phones/tablets. No fabricated session health score or audit trail.

In the embedded desktop Trade Desk, redundant legacy tabs and execution
settings sit behind Additional desk tools, while the outer workspace tabs and
status remain. Opening preserves all original controls, including signal-cycle
submission; review tests open/close only, never submit. Phone and standalone
legacy execution controls keep their existing presentation.

Coverage adds shared-style/route/browser checks at 390/768/1440/1920, compact
navigation/full-menu reachability, context unavailable states, keyboard search,
auxiliary tool disclosure and no order/settings writes. Final CI/captures in
the PR supersede previous screenshots. No backend, migration, environment,
permission, execution handler or risk-limit change. No merge/deployment.
