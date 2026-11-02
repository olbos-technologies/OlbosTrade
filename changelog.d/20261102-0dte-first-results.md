## Unreleased — First real-data results for the 0DTE/intraday research: no edge found

- Recorded the first full run (QQQ and SPY, 5-minute bars, 2022-12-27 to
  2026-10-30) in `docs/research/0DTE_STRATEGY_BANK.md`: candidates D (VWAP
  reversion), E (intraday momentum) and D+ (quality filter) are all rejected under
  the pre-registered gates.
- The D report's verdict no longer calls a holdout gain "positive" when even the
  best of the grid's cells lost money on the selection period, or when it does not
  survive doubled costs.
- Documentation and research tooling only: no trading code, no schema change.
