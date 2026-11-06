## Unreleased — An out-of-sample evaluation for the signal scorer, run before trusting a model

- New `ml/evaluate_signal_scorer.py` (run locally: `python3 -m ml.evaluate_signal_scorer
  --trades-csv trades.csv`, or `--collect --save-trades trades.csv` to generate trades with the
  backtester first). It answers "does gating trades with this model beat not gating?" with purged
  walk-forward folds (trades overlapping the split are excluded, plus an embargo), the live gate's
  real cutoff (12% + the 5-point uncertainty band = 17%), a per-trade cost at 1x and 2x, and
  permutation tests against a random gate of the same size.
- The pass rule is fixed in constants at the top of the file before any result exists. Verdicts are
  `PASS` or `NOT_PROVEN` (no evidence of an edge, not proof of none). Directional accuracy is
  reported against the base rate and is not part of the rule.
- Reports constant features (today `vix_term_slope` and `earnings_days_away` carry no information).
- Labels are simulated backtest trades; a PASS is a reason to run a model paper-only, not proof of
  profit. Nothing in the running app changes: no backend behaviour, no deploy.
