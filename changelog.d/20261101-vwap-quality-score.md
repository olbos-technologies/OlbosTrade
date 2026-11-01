## Unreleased — Candidate D+: walk-forward signal-quality filter (research backtest)

- `scripts/vwap_quality_score.py` asks whether a plain logistic regression,
  trained only on earlier trades and refit monthly, can pick better VWAP
  reversion trades than keeping the same number at random. It reports
  out-of-sample AUC, a permutation p-value, the coefficients, and the daily
  result at four exposure caps.
- Research tooling only: no trading code, no schema change. Its settings are
  fixed in advance (one configuration), and its tests include leakage guards and
  a power check. Nothing is approved to trade.
