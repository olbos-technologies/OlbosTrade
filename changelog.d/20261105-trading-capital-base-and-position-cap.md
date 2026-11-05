## Unreleased — The desk can trade a smaller capital than the paper account, with a configurable position cap

- New `TRADING_CAPITAL_BASE` (default `0` = off, behaviour unchanged). When set, position
  sizing, the entry gate's loss/drawdown percentages and the drawdown peak are all taken
  against `min(live account, base)`. A stored peak larger than the base is clamped when
  read (the stored row is untouched), so a 25k desk is not read as a ~90% drawdown against
  an old ~276k peak.
- New `EQUITY_MAX_POSITION_PCT` (default `0.08`, the figure that was hardcoded). The largest
  single equity position as a fraction of trading capital. The 2%-risk-per-trade rule and
  the 25% single-underlying limit are unchanged.
- `/api/risk/state` and `/api/paper/portfolio` report P&L and return against the desk's
  capital and expose `account_value_live` / `trading_capital_base`.

Deploy: backend rebuild only — no migration. To run a 25k desk with 10% positions set
`STARTING_CAPITAL=25000`, `TRADING_CAPITAL_BASE=25000`, `EQUITY_MAX_POSITION_PCT=0.10`
together; setting `STARTING_CAPITAL` alone recreates the capital-base divergence described
in `guardrails.py`. Open positions sized at the old scale are not closed.
