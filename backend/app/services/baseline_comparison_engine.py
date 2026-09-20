"""
Strategy Comparison — our real equity signal engine vs. simple rule-based
baselines (Buy & Hold, SMA cross, RSI threshold, MACD cross) over one
symbol's price history.

Pure module — no DB, no broker. Read-only research tool, not an execution
path. All five models share one position simulator (long/flat only, buy on
BUY when flat, close on SELL when long) so the comparison is about signal
quality, not execution sophistication: only the signal source differs
between models.

Indicators are computed over the full fetched history so early bars in the
requested window aren't warmup-starved (the same class of bug fixed
elsewhere this session for the live scanner — see equity_scan_engine.py),
but no model is allowed to act (buy/sell) before the requested start date —
each one starts flat exactly at the window's first bar.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

from app.utils.metrics import calculate_all_metrics

STARTING_CAPITAL_DEFAULT = 25000.0


@dataclass
class Trade:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl: float
    hold_days: int


def _load_daily_bars(symbol: str) -> pd.DataFrame:
    """Fetch ~3y of daily bars — generous warmup margin for any reasonable
    requested date range, matching the pattern in forecasts.py."""
    import yfinance as yf

    history = yf.Ticker(symbol).history(period="3y", auto_adjust=True)
    if history.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    history = history.rename(columns=str.lower)
    history = history[["open", "high", "low", "close", "volume"]].dropna()
    history = history[(history["close"] > 0) & (history["high"] > 0) & (history["low"] > 0)]
    if history.index.tz is not None:
        history.index = history.index.tz_localize(None)
    return history


def simulate_positions(
    dates: list[str], closes: list[float], actions: list[str],
    starting_capital: float = STARTING_CAPITAL_DEFAULT,
) -> tuple[list[Trade], list[float]]:
    """
    Long/flat simulator shared by every model: BUY opens a position (if flat),
    SELL closes it (if long), HOLD is a no-op. Equity is marked to market
    every bar. A position still open at the last bar is force-closed there so
    Buy & Hold (which never sells) still produces a realized trade for the
    metrics — otherwise calculate_all_metrics would see zero trades and
    report a flat 0% return despite the equity curve having moved.
    """
    trades: list[Trade] = []
    equity_curve: list[float] = []
    cash = starting_capital
    shares = 0.0
    position_open = False
    entry_price = 0.0
    entry_date = ""
    entry_idx = 0
    cash_before_entry = starting_capital

    for i, (d, price, action) in enumerate(zip(dates, closes, actions)):
        if not position_open and action == "BUY" and price > 0:
            shares = cash / price
            entry_price = price
            entry_date = d
            entry_idx = i
            cash_before_entry = cash
            cash = 0.0
            position_open = True
        elif position_open and action == "SELL":
            exit_cash = shares * price
            trades.append(Trade(
                entry_date=entry_date, exit_date=d,
                entry_price=entry_price, exit_price=price,
                pnl=exit_cash - cash_before_entry, hold_days=i - entry_idx,
            ))
            cash = exit_cash
            shares = 0.0
            position_open = False

        equity_curve.append(cash + shares * price)

    if position_open and dates:
        final_price = closes[-1]
        exit_cash = shares * final_price
        trades.append(Trade(
            entry_date=entry_date, exit_date=dates[-1],
            entry_price=entry_price, exit_price=final_price,
            pnl=exit_cash - cash_before_entry, hold_days=len(dates) - 1 - entry_idx,
        ))

    return trades, equity_curve


# ── Baseline signal generators ───────────────────────────────────────────────
# Each returns one action per bar of the FULL series (for alignment), but only
# bars from start_idx onward can be non-HOLD — every model starts flat at the
# window's first bar, regardless of what it would have done earlier.

def _buy_and_hold_actions(n: int, start_idx: int) -> list[str]:
    actions = ["HOLD"] * n
    if start_idx < n:
        actions[start_idx] = "BUY"
    return actions


def _cross_actions(indicator_above: pd.Series, start_idx: int) -> list[str]:
    """Shared cross-detection state machine for SMA/MACD: BUY when the series
    crosses from below to above its reference, SELL on the reverse cross.
    Only starts tracking state at start_idx (flat entering the window)."""
    n = len(indicator_above)
    actions = ["HOLD"] * n
    prev_above: Optional[bool] = None
    for i in range(start_idx, n):
        a = indicator_above.iloc[i]
        if pd.isna(a):
            prev_above = None
            continue
        if prev_above is None:
            pass  # first usable bar — nothing to compare against yet
        elif a and not prev_above:
            actions[i] = "BUY"
        elif not a and prev_above:
            actions[i] = "SELL"
        prev_above = bool(a)
    return actions


def _sma_cross_actions(close: pd.Series, start_idx: int, window: int = 20) -> list[str]:
    sma = close.rolling(window=window).mean()
    return _cross_actions(close > sma, start_idx)


def _macd_cross_actions(close: pd.Series, start_idx: int) -> list[str]:
    from ta.trend import MACD
    macd_obj = MACD(close=close, window_fast=12, window_slow=26, window_sign=9)
    return _cross_actions(macd_obj.macd() > macd_obj.macd_signal(), start_idx)


def _rsi_threshold_actions(
    close: pd.Series, start_idx: int, period: int = 14,
    buy_below: float = 30.0, sell_above: float = 70.0,
) -> list[str]:
    """Mean-reversion baseline: buy when RSI drops below buy_below (oversold),
    sell when it rises above sell_above (overbought). Wilder's RSI, same math
    as data_fetcher.py::calculate_rsi."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    n = len(close)
    actions = ["HOLD"] * n
    in_position = False
    for i in range(start_idx, n):
        r = rsi.iloc[i]
        if pd.isna(r):
            continue
        if not in_position and r < buy_below:
            actions[i] = "BUY"
            in_position = True
        elif in_position and r > sell_above:
            actions[i] = "SELL"
            in_position = False
    return actions


def _ours_actions(df: pd.DataFrame, start_idx: int, end_idx: int) -> list[str]:
    """Delegates to the real production signal engine (equity_signal_engine.py)
    run bar-by-bar on an expanding window — this is what our system would
    actually have said historically, not a reimplementation."""
    from app.services.equity_signal_engine import compute_indicators, score_equity_signal

    actions = []
    for i in range(start_idx, end_idx + 1):
        window = df.iloc[: i + 1]
        if len(window) < 30:
            actions.append("HOLD")
            continue
        ind = compute_indicators(window)
        if not ind:
            actions.append("HOLD")
            continue
        action, _confidence, _reasons = score_equity_signal(ind)
        actions.append(action if action in ("BUY", "SELL") else "HOLD")
    return actions



# ── Random-entry baseline ────────────────────────────────────────────────────
#
# WHY THIS EXISTS AND WHY THE OTHER FOUR DO NOT ANSWER IT.
#
# Buy & Hold, SMA Cross, RSI Threshold and MACD Cross are all STRATEGIES. Each
# has its own edge or anti-edge, so beating them says "better than that rule",
# not "better than nothing". The question that has to be settled before any
# filter work is cruder: does the signal carry information at all, or would
# entries thrown at the same tape have done as well?
#
# That question has a specific shape, and getting the shape wrong is the usual
# way a random baseline ends up meaningless:
#
#   TRADE COUNT MUST MATCH. A random model that trades three times as often is
#   being compared on frequency, not on selection. It takes the number of
#   entries the reference model made.
#
#   HOLD DURATION MUST MATCH. If the reference holds forty days and random
#   holds five, the comparison confounds duration with selection — on a
#   drifting tape, time in the market is itself a return. Random draws its
#   holds from the reference's own durations.
#
#   ONE DRAW PROVES NOTHING. A single random run is a coin flip, and reporting
#   it as "the random baseline returned X" invites reading noise as a result.
#   What answers the question is the DISTRIBUTION: where the real model's
#   return falls among many draws. A model at the 50th percentile of random is
#   indistinguishable from chance no matter how good its absolute return
#   looks; one at the 4th percentile is actively losing to it.
#
# Seeded, so a reported percentile can be reproduced and argued with.

RANDOM_BASELINE_DRAWS = 500
RANDOM_BASELINE_SEED = 20260920


def _random_entry_actions(
    n: int, durations: list[int], rng: "np.random.Generator",
) -> list[str]:
    """Place len(durations) non-overlapping long positions at random.

    The placement is UNIFORM over all valid layouts, not "pick a start for each
    trade and retry on collision" — that biases toward the middle of the
    window and quietly changes what is being measured. The standard bijection
    is used instead: choose the gaps between trades, then lay the trades out
    end to end. Sampling k sorted positions from the free space and expanding
    is equivalent and cheaper.

    Returns fewer trades than asked for only when they cannot fit, which the
    caller reports rather than hides.
    """
    actions = ["HOLD"] * n
    if n <= 1 or not durations:
        return actions

    # A trade needs a bar to enter and a later bar to exit, so a duration of d
    # occupies d + 1 bars. Anything that cannot fit is dropped from the end.
    usable = [max(1, int(d)) for d in durations if d]
    while usable and sum(d + 1 for d in usable) > n:
        usable.pop()
    if not usable:
        return actions

    k = len(usable)
    occupied = sum(d + 1 for d in usable)
    free = n - occupied

    # The bijection: k sorted offsets in [0, free], each shifted by the space
    # the EARLIER trades occupy. `consumed` therefore accumulates lengths only
    # — never the gaps, which the sorted offsets already carry. Adding the
    # gaps here too double-counts them, which pushes later trades past the end
    # of the window and silently drops them: the first version did exactly
    # that and placed 3 of 7 trades, so the count matching this whole
    # comparison rests on was quietly false.
    offsets = np.sort(rng.integers(0, free + 1, size=k)) if free > 0 else np.zeros(k, int)
    lengths = [usable[i] for i in rng.permutation(k)]

    consumed = 0
    for offset, d in zip(offsets, lengths):
        entry = int(offset) + consumed
        exit_i = entry + d
        if exit_i >= n:
            break
        actions[entry] = "BUY"
        actions[exit_i] = "SELL"
        consumed += d + 1
    return actions


def random_baseline_distribution(
    dates: list[str], closes: list[float], reference_trades: list,
    starting_capital: float = STARTING_CAPITAL_DEFAULT,
    draws: int = RANDOM_BASELINE_DRAWS,
    seed: int = RANDOM_BASELINE_SEED,
) -> Optional[dict]:
    """Return where `reference_trades` sits among `draws` random layouts.

    None when the reference made no trades: there is nothing to match the
    count and duration of, and a baseline built from an empty reference would
    be comparing against zero trades while looking like a real result.
    """
    if not reference_trades:
        return None

    durations = [max(1, int(t.hold_days)) for t in reference_trades]
    reference_return = sum(t.pnl for t in reference_trades) / starting_capital * 100.0

    rng = np.random.default_rng(seed)
    returns: list[float] = []
    trade_counts: list[int] = []
    for _ in range(draws):
        actions = _random_entry_actions(len(closes), durations, rng)
        trades, _curve = simulate_positions(dates, closes, actions, starting_capital)
        returns.append(sum(t.pnl for t in trades) / starting_capital * 100.0)
        trade_counts.append(len(trades))

    arr = np.array(returns, dtype=float)
    # Strictly-less-than, so a model that merely ties the draws does not get
    # credit for beating them.
    percentile = float((arr < reference_return).mean() * 100.0)

    return {
        "draws": draws,
        "seed": seed,
        "reference_return_pct": round(reference_return, 2),
        "reference_trades": len(reference_trades),
        "random_trades_median": int(np.median(trade_counts)),
        "random_return_mean_pct": round(float(arr.mean()), 2),
        "random_return_p05_pct": round(float(np.percentile(arr, 5)), 2),
        "random_return_p50_pct": round(float(np.percentile(arr, 50)), 2),
        "random_return_p95_pct": round(float(np.percentile(arr, 95)), 2),
        "percentile_of_random": round(percentile, 1),
        "verdict": _verdict(percentile),
    }


def _verdict(percentile: float) -> str:
    """Plain words, because a percentile alone gets read as a score.

    The bands are deliberately wide. With 500 draws the sampling error on a
    percentile is a couple of points, and a narrower band would invite
    treating 57 and 63 as different findings when they are the same one.
    """
    if percentile >= 95.0:
        return "beats random entries at the 5% level"
    if percentile >= 75.0:
        return "better than most random entries, not conclusively"
    if percentile > 25.0:
        return "indistinguishable from random entries"
    if percentile > 5.0:
        return "worse than most random entries"
    return "loses to random entries at the 5% level — the signal is inverted or harmful"

MODEL_NAMES = ("Buy & Hold", "SMA Cross", "RSI Threshold", "MACD Cross", "Ours")


def _model_actions(name: str, full_df: pd.DataFrame, start_idx: int, end_idx: int) -> list[str]:
    close = full_df["close"]
    if name == "Buy & Hold":
        return _buy_and_hold_actions(len(full_df), start_idx)[start_idx:end_idx + 1]
    if name == "SMA Cross":
        return _sma_cross_actions(close, start_idx)[start_idx:end_idx + 1]
    if name == "RSI Threshold":
        return _rsi_threshold_actions(close, start_idx)[start_idx:end_idx + 1]
    if name == "MACD Cross":
        return _macd_cross_actions(close, start_idx)[start_idx:end_idx + 1]
    if name == "Ours":
        return _ours_actions(full_df, start_idx, end_idx)
    raise ValueError(f"unknown model {name!r}")


def generate_comparison(
    symbol: str, start: str, end: str,
    starting_capital: float = STARTING_CAPITAL_DEFAULT,
) -> dict:
    """Run all MODEL_NAMES over [start, end] for `symbol` and return a
    comparison report: metrics table + per-model trades/equity curve."""
    ticker = symbol.strip().upper()
    if not ticker or not ticker.replace(".", "").replace("-", "").isalnum():
        raise ValueError("invalid symbol")

    full_df = _load_daily_bars(ticker)
    if full_df.empty:
        raise ValueError(f"no price history available for {ticker}")

    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    in_window = (full_df.index >= start_ts) & (full_df.index <= end_ts)
    if int(in_window.sum()) < 30:
        raise ValueError("date range must include at least 30 trading days")

    window_positions = np.flatnonzero(in_window)
    start_idx, end_idx = int(window_positions[0]), int(window_positions[-1])
    if start_idx < 1:
        raise ValueError("not enough price history before the start date for indicator warmup")

    dates = [d.date().isoformat() for d in full_df.index[start_idx:end_idx + 1]]
    closes = full_df["close"].iloc[start_idx:end_idx + 1].tolist()

    models = []
    ours_trades: list[Trade] = []
    for name in MODEL_NAMES:
        actions = _model_actions(name, full_df, start_idx, end_idx)
        trades, equity_curve = simulate_positions(dates, closes, actions, starting_capital)
        if name == "Ours":
            ours_trades = trades

        pnl_series = [t.pnl for t in trades]
        hold_days = [t.hold_days for t in trades]
        metrics = calculate_all_metrics(
            pnl_series=pnl_series, hold_days=hold_days,
            starting_capital=starting_capital, total_commissions=0.0,
            equity_curve=pd.Series(equity_curve),
        )

        markers = (
            [{"date": t.entry_date, "action": "BUY", "price": t.entry_price} for t in trades]
            + [{"date": t.exit_date, "action": "SELL", "price": t.exit_price} for t in trades]
        )
        markers.sort(key=lambda m: m["date"])

        models.append({
            "name": name,
            "metrics": {
                "cumulative_return_pct": round(metrics.total_return_pct * 100, 2),
                "annual_return_pct": round(metrics.annualized_return_pct * 100, 2),
                "sharpe_ratio": metrics.sharpe_ratio,
                "max_drawdown_pct": round(metrics.max_drawdown_pct * 100, 2),
            },
            "trades": [asdict(t) for t in trades],
            "markers": markers,
            "equity_curve": [{"date": d, "value": round(v, 2)} for d, v in zip(dates, equity_curve)],
        })

    return {
        "symbol": ticker,
        "start": dates[0],
        "end": dates[-1],
        "starting_capital": starting_capital,
        "bars": [{"date": d, "close": c} for d, c in zip(dates, closes)],
        "models": models,
        # The question the other four models cannot answer: does the signal
        # beat entries thrown at the same tape? None when "Ours" made no
        # trades — see random_baseline_distribution.
        "random_baseline": random_baseline_distribution(
            dates, closes, ours_trades, starting_capital),
        "disclaimer": "Research only — no execution path. Long/flat simulation, no commissions or slippage modeled.",
    }
