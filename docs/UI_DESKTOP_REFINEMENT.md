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
