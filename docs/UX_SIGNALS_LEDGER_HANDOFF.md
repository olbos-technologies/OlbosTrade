# Signals ledger implementation handoff

Milestones: UX-1 / UX-3 / UX-5. Branch: codex/signals-ledger-ui.
Main baseline: 70ee90ec339d85f8a67ae16ee01a7716f2654491.
Builds on adaptive mobile PR #223 (e8fc2a737d29266adc52dee58a510f02dec6a3c8).

The equity feed uses native details/summary rows. Scores retain their producer's
OPP/CONF labels; no score becomes a probability of profit. Expanding exposes
trade levels, sizing, rationale, block context, generated time and technical
trace. Review idea opens the existing research drawer. Filter/scan controls use
a disclosure. Quote/scan/execution APIs and risk controls are unchanged.

Changed: EquitySignals.tsx, scoped signals-ledger.css, App.tsx import,
adaptive-mobile, terminal-redesign and mobile-signals-focus browser checks,
this handoff and changelog fragment. Adaptive mobile changes from #223 remain
in the branch and are a dependency.

No merge or deployment. Remaining: compare CI screenshots with approved concept,
real iOS Safari and Android device verification, options-feed redesign, and the
other concepts. Pixel identity cannot be promised across device/font sizes;
responsive content and correct data take precedence over raster geometry.
