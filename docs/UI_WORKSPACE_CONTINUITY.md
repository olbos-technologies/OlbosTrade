# UX-1 / UX-5 — remaining workspace presentation

Base: `e7c397ac05667e4e60d8449ed15f7dc67de78e89` (merged PR #166).
Branch: `codex/ui-workspace-continuity`. No merge or deployment by this task.

## Scope

A shared WorkspaceFrame adds page headings, readable descriptions, navy cards,
gold selected tabs, consistent forms and 44px mobile controls to Risk,
Guardrails, Journal, Analytics, Backtesting, all five Research tabs, all three
System integration tabs, Profile, Password and Sessions. Existing primary-page
and My Brokers headings are retained. Unknown routes do not inherit metadata
from Object.prototype. The official Olbos Trade logo remains unchanged.

Risk/Research/Analytics content grids stack on narrow screens; stat strips use
two columns. The Backtesting form stacks above results. Dense tables remain
semantic tables inside horizontal scroll containers. Journal controls wrap and
tag performance rows retain their alignment inside their own scroll container.
Password inputs use 16px type on mobile to avoid Safari focus zoom. Credential
screens keep their existing official logo, routes, validation and notice tones,
with matching navy/gold tokens and sans-serif headings.

This covers the named workspaces, not certification of every advanced panel.
Options chain/income/flow, crypto, strategy builder and specialist research
subpanels still need individual populated-state design review and device tests.
No claim of entire-app completion or real-device Safari verification.

## Truthful unavailable states

Risk summary P&L/counts and loss/count/margin meters no longer substitute zero
for missing/non-finite numbers. Unknown meters have no filled bar. Unknown
entry status is visibly UNKNOWN, and negative P&L carries a minus sign. Loss
limits and backend enforcement remain unchanged. HTTP failures in Risk's
additional reads are rejected and follow its existing unavailable handling.
Journal clears its prior entries on refresh and shows an alert on failed initial
reads, instead of claiming an empty journal. Subtab performance/review error
semantics are not certified complete by this change.

## Validation and artifacts

- Local full frontend unit suite: 71 files / 613 tests passed, including three
  risk/journal read-state regressions. No broker mutations in those tests.
- TypeScript/Vite production build and git diff checks recorded in the PR.
- Browser coverage: 16 workspace routes at 320/390/768/1440px, populated Risk
  summaries, unavailable Journal, and sign-in/request-access/claim at all four
  widths. Checks include no overflow, no render boundary failures, stacked
  Backtesting layout, password input geometry, Journal tab reachability and no
  API writes. Captures use intercepted synthetic data, never real accounts.
- Browser binaries are unavailable locally; CI is the rendering evidence. See
  the PR for exact head/run, pass/fail results and screenshot artifact IDs.
- No backend application changes, new setting, migration, permission, broker
  action, execution routing or risk-limit change. Rollback frontend code/image
  through the existing release process.

Next: inspect CI screenshots, resolve failures, review remaining specialist
panels and actual Safari/accessibility behavior. Merge and deployment are
separate from this branch's implementation and validation.
