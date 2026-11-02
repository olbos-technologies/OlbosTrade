# UX-5: adaptive financial cards and mobile hierarchy

Base: 70ee90ec339d85f8a67ae16ee01a7716f2654491
Branch: codex/adaptive-mobile-layout

Preserve current terminal palette, fonts, APIs and trading controls. Reflow account P&L and signal trade-plan fields by available component width, keep numeric values intact, wrap sizing metadata, compact signal card headers and scan/search controls, put mobile account information before the introductory panel, and retain a single mobile instrument quote summary. Desktop chart title is retained.

Files: frontend/src/adaptive-mobile.css, App.tsx, pages/EquitySignals.tsx, pages/MobileHome.tsx, e2e/adaptive-mobile.spec.ts.

Local build/typecheck and unit tests: passed, 85 files / 672 tests. Browser assertions cover populated synthetic values at 320/375/390/430px and narrow containers with enlarged P&L text. Browser CI and screenshot review are release gates; do not infer visual correctness from unit tests. Real iOS Safari, keyboard/browser toolbar interactions, full text-enlargement and remaining-page audits are not certified by this pass.

No migration, backend, permission or risk-rule change. Not merged/deployed. Rollback: revert this focused change. Next owner: Codex to record exact CI head/results in PR; reviewer before merge.
