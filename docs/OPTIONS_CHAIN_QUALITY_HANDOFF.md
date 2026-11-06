# Options chain quality handoff

Milestone: ML-1 (experimental research evidence and its data-read prerequisite).
Base: b734357017fcd678ac6e24bd8d9ee4e6abde52fb.
Branch: codex/options-chain-quality.

## Behavior

The Alpaca adapter previously submitted every returned contract symbol in one
snapshot request and read only the first contracts/snapshots pages. Collection
now follows both page types, batches at most 100 symbols, deduplicates identical
records, rejects conflicting or out-of-scope records, and bounds pages, contract
count and total time. Failed/incomplete collection raises instead of returning a
partial successful chain. All requests are GETs. Existing caller fallbacks to
Black-Scholes/yfinance remain; this is not a new fail-closed execution policy.

`spread-quality-v1` evaluates both leg quotes and strategy shape, then combines
liquidity (60 points, using the worse relative leg spread) with delta fit (40).
Credit strategies target the short leg at absolute 0.20 delta. Supported debit
strategies target the long leg at 0.55. These are experimental research targets,
not calibrated probabilities. Gamma/theta and a universal low-IV bonus are not
used because they are not comparable across strategy, DTE and underlying price.

Research scan results expose `contract_quality`; EV remains the primary sort
key, with quality breaking exact EV ties. Automatic credit candidates record
quality in evidence but retain their existing scorer, ranking, sizing and
execution controls. Automated debit quoting still uses its existing theoretical
path and has no verified two-leg quality evidence. No order permissions are
created by this score: `execution_authorized` is always false.

Unknown/indicative feed, missing source time, stale/future quotes or missing delta
produce an unavailable score, never a fabricated zero. Invalid quotes, shapes or
economics receive a rejected state and reason. The score requires both legs to
have verified feed provenance and source quotes no older than 60 seconds.

`ALPACA_OPTIONS_FEED` is optional (`opra` or `indicative`). Leaving it unset
preserves provider-default requests but leaves quality unverified. Explicit OPRA
requires the account's data entitlement; failures do not silently downgrade to
indicative. IBKR records its market-data type but does not manufacture a bid/ask
timestamp from a request or arbitrary ticker update: quality remains unavailable
until a trustworthy source timestamp exists. No new SDK dependency is required.

Regime blocks, portfolio-state errors and worker failures now enter the existing
rejection aggregation. Scan cycle counts/candidate counts enter observability.
The rejection report explicitly distinguishes lack of evidence from downstream
execution rejection; it is not a complete order funnel.

## Verification and boundaries

Tests cover 251-contract adapter batching, both pagination types, malformed and
conflicting pages, unavailable snapshots, explicit feed/time metadata, deadline
cancellation, all four strategy shapes, invalid data, both scanner helper call
sites, early rejection recording and no dispatch on a failed scan. Dedicated CI
requires at least 95% coverage of the two new modules.

No frontend changes, migration, credential changes, risk-limit changes, order
placement or production deployment. Synthetic tests are not live broker proof.
No root cause for the production account's missing executions is established by
this patch. Existing theoretical-price fallbacks and automatic debit quoting
remain separate follow-up work.

After an operator-approved deployment, use the read-only diagnostic command:

```
docker exec -w /app olbostrade-backend python scripts/options_rejection_report.py --days 7
```

Compare it with deployed commit, scheduler state, mode, capacity and execution
logs before changing any gates. Do not force trades to test it. Rollback is the
prior application image; the metadata additions need no database rollback.

Local verification on 2026-11-06: focused eight-file pytest run: 114 passed;
statement/branch coverage: 100% for both new modules. Additional IBKR adapter,
alert scan wiring, shared modules, claim lookup and conflict-marker regressions:
83 passed. JUnit reports cross-checked; no skipped tests in either run.
`git diff --check` passed. CI results are recorded on the pull request.
