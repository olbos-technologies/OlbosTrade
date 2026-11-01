## Unreleased — Candidate E: intraday momentum model (research backtest)

- `scripts/intraday_momentum_backtest.py` tests a published intraday-momentum
  predictor (early-session and 15:00-15:30 returns forecasting the last 25
  minutes) on QQQ/SPY 5-minute bars, with a rolling regression fitted on prior
  sessions only, always-long/always-short baselines, a 60/40 holdout, 2x costs
  and results at four exposure caps.
- Research tooling only: no trading code, no schema change. The published
  effect is about 0.03% a day gross, so this is a low-exposure component to
  test, not a route to a 0.3-0.5% daily target. Nothing is approved to trade.
