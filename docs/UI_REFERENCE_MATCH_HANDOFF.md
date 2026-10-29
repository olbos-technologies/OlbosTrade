# UX-1 / UX-5 reference alignment

Branch: `codex/design-reference-match`. Base main:
`e79e2b103141092dc23f833685aec7f1837d941e`. Integrates the existing unmerged
redesign from PR #158 at `27ce92705cf4e29cd3e0d20a7a52431c6c6e800a` without
modifying that branch. This PR includes its Home, Markets, Signals and Positions
changes plus the current broker onboarding/data views. Review the combined
change against main; do not interpret inherited work as newly implemented here.

Reference-specific changes: terminal-only silver O single-line lockup, mobile
header alignment, Home/Markets/Signals/Positions/More navigation, larger type,
navy/gold card styling, healthy Home account-first hierarchy and consistent
My Brokers cards. Desk and Portfolio & Risk remain reachable in the More drawer.
Risk warnings/unverified execution remain above balances. The existing mode
control and risk indicators remain visible/reachable; the design reference's
static PAPER badge and illustrative amounts are not hard-coded into product UI.
This is an implementation aligned to the references, not a pixel-identical
reproduction of generated concept images. Existing data capabilities and safety
states determine displayed fields; no available-cash figure is invented.

The desktop Signals browser failure exposed a missing-state rendering crash in
GlobalRiskStatus. Missing/non-numeric/non-finite daily-loss state now renders
unavailable instead of dereferencing undefined or assuming zero drawdown. Four
unit regression cases cover malformed responses.

Validation: frontend unit suite 606 passed; production TypeScript/Vite build and
browser results are recorded in the PR. Browser matrix covers 320/390/768/1440px,
populated/unavailable states, no unintended API writes, chart/signals/position
review, five-tab names, logo/menu bounds, navigation and screenshot captures.
Local browser binaries are unavailable; browser results must come from CI.
Captures use synthetic intercepted data and are not broker/account evidence.

No backend application change, migration, new setting, broker action, execution
permission/routing or risk-limit change. No merge/deployment by this work.
Rollback the frontend code/image through the reviewed release process. Next:
inspect CI screenshots against the approved references, resolve any failures,
then review integration/merge and deployment independently.
