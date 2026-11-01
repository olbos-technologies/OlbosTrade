## Unreleased — Read-only research tooling for intraday VWAP mean reversion (QQQ/SPY)

- Two scripts, no trading code and no schema change: `intraday_bars_fetch.py`
  exports 5-minute regular-hours bars from the paper gateway over a read-only
  session, and `vwap_reversion_backtest.py` tests the pre-registered rules in
  `docs/research/0DTE_STRATEGY_BANK.md` with costs, a chronological holdout and
  the number of parameter cells tried.
- The backtest reports the same trades at four exposure caps because position
  size, not the signal, decides whether a percent-of-price edge can reach a
  percent-of-equity daily target.
- Nothing here is approved to trade and 0DTE autopilot stays disabled. A result
  is evidence about the past under stated assumptions, not a forecast.
