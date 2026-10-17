"""
Run the equity exit-geometry sweep and print the results.

Context: production equity exits over 2026-08-01 → 2026-10-17 (adopted
positions excluded, 43 attributable closes) realised a reward:risk of 0.31
against a geometry that plans 2.0 — stop at 2.0 x ATR, target at 4.0 x ATR.
Maximum favourable excursion on the two groups held for any length of time
was 1.12 and 1.14 x ATR, so the target sits at roughly 3.5x the best the
price was observed to do while the stop is reached and pays exactly
-2.00 x ATR. See app/services/equity_exit_optimizer.py's docstring for the
full table.

That is an inference from 43 trades. This script replaces it with a
walk-forward backtest: grid-search both multipliers on a training window,
validate the winner out-of-sample, print the decision and the reasoning.

READ-ONLY. It writes no config, no database row, and submits no order. It
opens no broker connection either — OHLCV comes from yfinance via a stub
broker (see _YFinanceOnly below), so running this cannot touch the live
execution stack or consume an IBKR client id.

Nothing here changes a production parameter. The output is a
recommendation for a person to act on.

Usage (inside the backend container):
    python scripts/equity_exit_sweep.py
    python scripts/equity_exit_sweep.py --tickers MRVL,INTC,AVGO
    python scripts/equity_exit_sweep.py --full-grid --train-months 9
    python scripts/equity_exit_sweep.py --capital 250000 --end-date 2026-10-17
"""

from __future__ import annotations

import argparse
import asyncio
import sys

sys.path.insert(0, "/app")

# The names that actually produced equity stop-outs in production. A wider
# universe is better science — pass --tickers. These are the default only
# because they are the names the diagnosis was drawn from, so they are the
# ones where a geometry change would have bitten.
DEFAULT_TICKERS = ["MRVL", "MSTR", "INTC", "PANW", "AVGO", "ASML", "KHC"]

# Production sizes to ~$20k notional per position, which is the 8%
# max_position_pct cap binding on a ~$250k account. Defaulting to that
# capital keeps the backtest in the same cap-bound regime the diagnosis
# came from; a much smaller figure would let the risk budget bind instead
# and measure a different system.
DEFAULT_CAPITAL = 250_000.0


class _YFinanceOnly:
    """Stub broker: reports no bars so DataFetcher falls through to yfinance.

    Returning an empty list rather than raising keeps the clean
    `if bars:` fall-through instead of DataFetcher's exception path, so a
    research run produces no "broker fetch failed" warnings for something
    that was never meant to use a broker.
    """

    async def get_bars(self, *args, **kwargs):
        return []


def _fmt(point, baseline) -> str:
    g = point.geometry
    mark = " <- shipped" if g == baseline else ""
    if not point.is_scorable:
        return (f"  {g.label:34}  n={point.n_trades:<4} "
                f"(too few trades to score){mark}")
    return (
        f"  {g.label:34}  n={point.n_trades:<4} "
        f"target={point.target_hit_rate:5.0%} stop={point.stop_hit_rate:5.0%}  "
        f"R:R={point.realized_reward_risk:4.2f}  "
        f"Sharpe={point.sharpe:6.2f}  "
        f"P&L={point.total_pnl:>10,.0f}{mark}"
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--tickers", default=",".join(DEFAULT_TICKERS),
                    help="comma-separated (default: the production stop-out names)")
    ap.add_argument("--end-date", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--train-months", type=int, default=6)
    ap.add_argument("--validate-months", type=int, default=1)
    ap.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    ap.add_argument("--full-grid", action="store_true",
                    help="42 points instead of 12 — one backtest per point per ticker")
    args = ap.parse_args()

    from app.services.backtester import Backtester
    from app.services.data_fetcher import DataFetcher
    from app.services.equity_exit_optimizer import EquityExitOptimizer

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    opt = EquityExitOptimizer(
        Backtester(DataFetcher(_YFinanceOnly())),
        starting_capital=args.capital,
    )

    print(f"Sweeping {len(tickers)} tickers: {', '.join(tickers)}")
    print(f"Capital {args.capital:,.0f}  "
          f"train {args.train_months}mo  validate {args.validate_months}mo  "
          f"{'full' if args.full_grid else 'reduced'} grid\n")

    res = await opt.sweep(
        tickers,
        end_date=args.end_date,
        train_months=args.train_months,
        validate_months=args.validate_months,
        full_grid=args.full_grid,
    )

    print(f"Train    {res.train_start} .. {res.train_end}")
    print(f"Validate {res.validate_start} .. {res.validate_end}")
    print(f"\nTraining window — {res.grid_points_tested} geometries, "
          f"best Sharpe last:\n")
    for p in sorted(res.train_results, key=lambda p: p.sharpe):
        print(_fmt(p, res.baseline))

    if res.best_on_validate is not None and res.baseline_on_validate is not None:
        print("\nOut-of-sample:\n")
        print(_fmt(res.baseline_on_validate, res.baseline))
        # When the shipped geometry won the training window, sweep() reuses
        # its validation point as the candidate's rather than re-running an
        # identical backtest. Printing it twice, with a +0.00 gain, would
        # read as a comparison where none was made.
        if res.best_on_validate is not res.baseline_on_validate:
            print(_fmt(res.best_on_validate, res.baseline))
            print(f"\n  Sharpe gain vs shipped: "
                  f"{res.validation_sharpe_gain:+.2f} "
                  f"(need {opt.MIN_VALIDATION_SHARPE_GAIN:+.2f})")

    print("\n" + "=" * 68)
    if res.accepted:
        rec = res.recommended
        print(f"ACCEPTED: {rec.label}")
        print(f"  equity_stop_atr_multiplier   {rec.stop_atr_multiplier:g}")
        print(f"  equity_target_atr_multiplier {rec.target_atr_multiplier:g}")
        print("\nThis sweep's out-of-sample window confirmed the improvement on")
        print("these names over this period. That is evidence about this data,")
        print("not a prediction. Nothing has been changed — a person decides")
        print("whether to move the production parameters, with the trade counts")
        print("above in view.")
    else:
        print(f"NO CHANGE RECOMMENDED: {res.rejection_reason}")
        print(f"\nKeeping {res.baseline.label}.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
