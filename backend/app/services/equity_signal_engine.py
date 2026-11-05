"""
Equity signal engine — OLBOS-style rule-based signal generation for stocks/ETFs.
Uses the `ta` library (Technical Analysis Library in Python) for indicator computation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from app.core.config import settings

logger = logging.getLogger(__name__)

# Lightweight provenance stamp for signal_outcomes.signal_engine_version —
# bump only when score_equity_signal()'s scoring rules materially change.
# No snapshot/versioning table exists for equity signals (unlike options'
# strategy_config_service) because there has never been more than one
# version in production; add one only if that stops being true.
EQUITY_SCORING_VERSION = "1.0.0"

# ── Indicator computation ───────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> dict:
    """
    Compute technical indicators from an OHLCV DataFrame.

    Expected columns: open, high, low, close, volume (all numeric).
    Returns a dict of latest indicator values.
    """
    try:
        import ta as ta_lib  # type: ignore
        from ta.trend import EMAIndicator, MACD, ADXIndicator
        from ta.momentum import RSIIndicator, StochasticOscillator
        from ta.volatility import BollingerBands, AverageTrueRange
        from ta.volume import OnBalanceVolumeIndicator
    except ImportError:
        logger.warning("ta library not installed — returning empty indicators")
        return {}

    if len(df) < 30:
        logger.warning("Insufficient bars for indicator computation: %d", len(df))
        return {}

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    # EMAs
    df["ema20"]  = EMAIndicator(close=close, window=20).ema_indicator()
    df["ema50"]  = EMAIndicator(close=close, window=50).ema_indicator()
    df["ema200"] = EMAIndicator(close=close, window=200).ema_indicator()

    # RSI
    df["rsi"] = RSIIndicator(close=close, window=14).rsi()

    # MACD
    macd_obj = MACD(close=close, window_fast=12, window_slow=26, window_sign=9)
    df["macd"]        = macd_obj.macd()
    df["macd_signal"] = macd_obj.macd_signal()
    df["macd_hist"]   = macd_obj.macd_diff()

    # Bollinger Bands
    bb_obj = BollingerBands(close=close, window=20, window_dev=2)
    df["bb_lower"] = bb_obj.bollinger_lband()
    df["bb_mid"]   = bb_obj.bollinger_mavg()
    df["bb_upper"] = bb_obj.bollinger_hband()
    # pct_b = (close - lower) / (upper - lower)
    bb_width = (bb_obj.bollinger_hband() - bb_obj.bollinger_lband()).replace(0, 1)
    df["bb_pct_b"] = (close - bb_obj.bollinger_lband()) / bb_width

    # ATR
    df["atr"] = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

    # ADX — trend strength. Previously only available as a single shared
    # market-wide reading (main.py's options scan read it off the regime
    # classifier for every symbol); computed per-symbol here so each stock's
    # own trend strength is available wherever this function's output is used.
    df["adx"] = ADXIndicator(high=high, low=low, close=close, window=14).adx()

    # OBV
    df["obv"] = OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume()

    # Stochastic K/D
    stoch_obj = StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3)
    df["stoch_k"] = stoch_obj.stoch()
    df["stoch_d"] = stoch_obj.stoch_signal()

    # VWAP approximation (cumulative from first bar — good enough for daily bars)
    df["vwap"] = (close * volume).cumsum() / volume.cumsum()

    # Volume ratio vs 20-bar average
    df["vol_avg20"]    = volume.rolling(20).mean()
    df["volume_ratio"] = volume / df["vol_avg20"].replace(0, 1)

    latest = df.iloc[-1]
    close  = float(latest.get("close", 0))
    ema20  = float(latest.get("ema20",  0) or 0)
    ema50  = float(latest.get("ema50",  0) or 0)
    ema200 = float(latest.get("ema200", 0) or 0)
    vwap   = float(latest.get("vwap",   0) or 0)

    return {
        "close":           close,
        "ema20":           ema20,
        "ema50":           ema50,
        "ema200":          ema200,
        "rsi":             float(latest.get("rsi", 50) or 50),
        "macd":            float(latest.get("macd", 0) or 0),
        "macd_signal":     float(latest.get("macd_signal", 0) or 0),
        "macd_hist":       float(latest.get("macd_hist", 0) or 0),
        "bb_pct_b":        float(latest.get("bb_pct_b", 0.5) or 0.5),
        "atr":             float(latest.get("atr", 1) or 1),
        "adx":             float(latest.get("adx", 20.0) or 20.0),
        "obv":             float(latest.get("obv", 0) or 0),
        "stoch_k":         float(latest.get("stoch_k", 50) or 50),
        "stoch_d":         float(latest.get("stoch_d", 50) or 50),
        "vwap":            vwap,
        "volume_ratio":    float(latest.get("volume_ratio", 1.0) or 1.0),
        "above_ema200":    close > ema200 if ema200 > 0 else False,
        "above_vwap":      close > vwap  if vwap  > 0 else False,
        "ema_aligned_bull": (ema20 > ema50 and close > ema20) if (ema20 and ema50) else False,
        "ema_aligned_bear": (ema20 < ema50 and close < ema20) if (ema20 and ema50) else False,
    }


# ── Signal scoring ──────────────────────────────────────────────────────────

@dataclass
class EquitySignalParams:
    """
    Tunable weights/thresholds for score_equity_signal() and
    compute_equity_trade_plan(). Every default below reproduces today's
    exact hardcoded behavior — passing None (or omitting `params`
    entirely) to either function is identical to passing
    EquitySignalParams(). No live caller passes non-default values yet;
    this exists so a future equity-side optimizer (mirroring
    strategy_optimizer.py's options-side grid search) has something real
    to sweep over. `max_position_pct` is deliberately NOT included here —
    it's already its own tunable kwarg on compute_equity_trade_plan(), not
    a hardcoded literal, so duplicating it here would just create a second
    source of truth for the same value.
    """

    # ── score_equity_signal() ── bull/bear share one weight field when the
    # original code used the identical point value on both sides; the
    # thresholds stay separate since oversold/overbought values differ.
    rsi_oversold_threshold:    float = 35.0
    rsi_overbought_threshold:  float = 65.0
    rsi_extreme_pts:           float = 1.5

    macd_above_signal_pts:     float = 0.8
    macd_cross_pts:            float = 2.0

    bb_oversold_threshold:     float = 0.10
    bb_overbought_threshold:   float = 0.90
    bb_extreme_pts:            float = 1.5

    volume_ratio_threshold:    float = 1.5
    volume_confirmed_pts:      float = 0.7

    above_ema200_pts:          float = 0.5
    ema_aligned_pts:           float = 0.8
    above_vwap_pts:            float = 0.6

    stoch_oversold_threshold:   float = 20.0
    stoch_overbought_threshold: float = 80.0
    stoch_extreme_pts:          float = 1.0

    orderflow_threshold:       float = 0.15
    orderflow_multiplier:      float = 1.2

    sentiment_threshold:       float = 0.2
    sentiment_multiplier:      float = 1.5

    strength_offset:            float = 1.0
    strength_divisor:           float = 8.0
    confidence_base:            float = 0.65
    confidence_strength_weight: float = 0.35
    min_margin_to_fire:         float = 0.06

    # ── compute_equity_trade_plan() ──
    # Read from settings rather than hardcoded, because these two decide how
    # far a signal has to travel to count as right and are meant to be chosen
    # from measured outcomes. default_factory, not a plain default: a bare
    # `= settings.x` would bind once at import and ignore the environment.
    # See config.py for why the RATIO is not where an edge comes from, and
    # why max_hold_days is what makes the target distance bite.
    stop_atr_multiplier:   float = field(
        default_factory=lambda: settings.equity_stop_atr_multiplier)
    target_atr_multiplier: float = field(
        default_factory=lambda: settings.equity_target_atr_multiplier)
    risk_pct_per_trade:        float = 0.02
    sentiment_scale_floor:     float = 0.70
    sentiment_scale_trigger:   float = 0.1


# Import-time snapshot, kept for score_equity_signal — whose weights are
# plain constants, so a snapshot is exactly right there. Do NOT use it where
# the ATR multipliers matter: those come from settings, and this object froze
# them at import. compute_equity_trade_plan builds its own for that reason.
DEFAULT_EQUITY_SIGNAL_PARAMS = EquitySignalParams()


def score_equity_signal(
    ind: dict,
    sentiment_score: float = 0.0,
    orderflow_score: float = 0.0,
    params: Optional[EquitySignalParams] = None,
) -> tuple[str, float, dict]:
    """
    Score equity indicators and return (action, confidence, reasons).

    action:     "BUY" | "SELL" | "HOLD"
    confidence: 0.0 – 1.0
    reasons:    dict of contributing factors and their point values
    """
    p = params or DEFAULT_EQUITY_SIGNAL_PARAMS
    bull_pts = 0.0
    bear_pts = 0.0
    reasons: dict = {}

    rsi        = ind.get("rsi", 50)
    macd       = ind.get("macd", 0)
    macd_sig   = ind.get("macd_signal", 0)
    bb_pct_b   = ind.get("bb_pct_b", 0.5)
    vol_ratio  = ind.get("volume_ratio", 1.0)
    stoch_k    = ind.get("stoch_k", 50)

    # ── Bull scoring ─────────────────────────────────────────────────────
    if rsi < p.rsi_oversold_threshold:
        bull_pts += p.rsi_extreme_pts
        reasons["rsi_oversold"] = p.rsi_extreme_pts

    if macd > macd_sig and macd_sig != 0:
        bull_pts += p.macd_above_signal_pts
        reasons["macd_above_signal"] = p.macd_above_signal_pts
    if macd > 0 and macd_sig < 0:  # bullish cross
        bull_pts += p.macd_cross_pts
        reasons["macd_bull_cross"] = p.macd_cross_pts

    if bb_pct_b < p.bb_oversold_threshold:
        bull_pts += p.bb_extreme_pts
        reasons["bb_oversold"] = p.bb_extreme_pts

    if vol_ratio > p.volume_ratio_threshold and ind.get("above_vwap"):
        bull_pts += p.volume_confirmed_pts
        reasons["volume_confirmed_bull"] = p.volume_confirmed_pts

    if ind.get("above_ema200"):
        bull_pts += p.above_ema200_pts
        reasons["above_ema200"] = p.above_ema200_pts

    if ind.get("ema_aligned_bull"):
        bull_pts += p.ema_aligned_pts
        reasons["ema_aligned_bull"] = p.ema_aligned_pts

    if ind.get("above_vwap"):
        bull_pts += p.above_vwap_pts
        reasons["above_vwap"] = p.above_vwap_pts

    if stoch_k < p.stoch_oversold_threshold:
        bull_pts += p.stoch_extreme_pts
        reasons["stoch_oversold"] = p.stoch_extreme_pts

    if orderflow_score > p.orderflow_threshold:
        pts = orderflow_score * p.orderflow_multiplier
        bull_pts += pts
        reasons["orderflow_bull"] = round(pts, 3)

    if sentiment_score > p.sentiment_threshold:
        pts = sentiment_score * p.sentiment_multiplier
        bull_pts += pts
        reasons["sentiment_bull"] = round(pts, 3)

    # ── Bear scoring ─────────────────────────────────────────────────────
    if rsi > p.rsi_overbought_threshold:
        bear_pts += p.rsi_extreme_pts
        reasons["rsi_overbought"] = -p.rsi_extreme_pts

    if macd < macd_sig and macd_sig != 0:
        bear_pts += p.macd_above_signal_pts
        reasons["macd_below_signal"] = -p.macd_above_signal_pts
    if macd < 0 and macd_sig > 0:  # bearish cross
        bear_pts += p.macd_cross_pts
        reasons["macd_bear_cross"] = -p.macd_cross_pts

    if bb_pct_b > p.bb_overbought_threshold:
        bear_pts += p.bb_extreme_pts
        reasons["bb_overbought"] = -p.bb_extreme_pts

    if vol_ratio > p.volume_ratio_threshold and not ind.get("above_vwap"):
        bear_pts += p.volume_confirmed_pts
        reasons["volume_confirmed_bear"] = -p.volume_confirmed_pts

    if not ind.get("above_ema200"):
        bear_pts += p.above_ema200_pts
        reasons["below_ema200"] = -p.above_ema200_pts

    if ind.get("ema_aligned_bear"):
        bear_pts += p.ema_aligned_pts
        reasons["ema_aligned_bear"] = -p.ema_aligned_pts

    if not ind.get("above_vwap"):
        bear_pts += p.above_vwap_pts
        reasons["below_vwap"] = -p.above_vwap_pts

    if stoch_k > p.stoch_overbought_threshold:
        bear_pts += p.stoch_extreme_pts
        reasons["stoch_overbought"] = -p.stoch_extreme_pts

    if orderflow_score < -p.orderflow_threshold:
        pts = abs(orderflow_score) * p.orderflow_multiplier
        bear_pts += pts
        reasons["orderflow_bear"] = round(-pts, 3)

    if sentiment_score < -p.sentiment_threshold:
        pts = abs(sentiment_score) * p.sentiment_multiplier
        bear_pts += pts
        reasons["sentiment_bear"] = round(-pts, 3)

    # ── Direction decision ────────────────────────────────────────────────
    total = bull_pts + bear_pts
    if total == 0:
        return "HOLD", 0.0, reasons

    directional_share = max(bull_pts, bear_pts) / total
    strength = min((max(bull_pts, bear_pts) - p.strength_offset) / p.strength_divisor, 1.0)
    confidence = directional_share * (p.confidence_base + p.confidence_strength_weight * max(strength, 0.0))

    # Require minimum edge margin to fire
    margin = abs(bull_pts - bear_pts) / max(total, 1)
    if margin < p.min_margin_to_fire:
        return "HOLD", round(confidence, 4), reasons

    action = "BUY" if bull_pts > bear_pts else "SELL"
    return action, round(confidence, 4), reasons


# ── Trade plan ──────────────────────────────────────────────────────────────

def compute_equity_trade_plan(
    ind: dict,
    action: str,
    portfolio_value: float,
    max_position_pct: float | None = None,
    sentiment_score: float = 0.0,
    params: Optional[EquitySignalParams] = None,
) -> dict:
    """
    Compute entry/stop/target and position size for an equity signal.

    Returns:
        entry_price, stop_price, target_price, position_size,
        shares, risk_reward, risk_dollars
    """
    # A FRESH params object, not DEFAULT_EQUITY_SIGNAL_PARAMS, and that is the
    # whole point of default_factory being here at all.
    #
    # The singleton is built at import, so its multipliers are a snapshot of
    # whatever settings held then. Every live caller — the scanner, Alpha Edge —
    # passes params=None, so falling back to the singleton would have left the
    # live path pinned to the import-time geometry while a test that
    # constructed its own params saw the configured one. Caught in review on
    # #61: the mutation test passed for exactly that reason, proving a property
    # the production path did not have.
    #
    # Constructing one small dataclass per plan is not worth optimising away.
    p = params if params is not None else EquitySignalParams()
    entry = ind.get("close", 0.0)
    atr   = ind.get("atr",   1.0) or 1.0

    if action == "BUY":
        stop   = entry - atr * p.stop_atr_multiplier
        target = entry + atr * p.target_atr_multiplier
    elif action == "SELL":
        stop   = entry + atr * p.stop_atr_multiplier
        target = entry - atr * p.target_atr_multiplier
    else:
        return {"entry_price": entry, "action": "HOLD"}

    risk_per_share = abs(entry - stop)
    if risk_per_share == 0:
        risk_per_share = atr

    # Base position size: risk_pct_per_trade of portfolio
    risk_dollars_base = portfolio_value * p.risk_pct_per_trade
    shares_raw = int(risk_dollars_base / risk_per_share)

    # Soft sentiment scaling: bearish news on BUY reduces size by up to 30%
    sentiment_scale = 1.0
    if action == "BUY" and sentiment_score < -p.sentiment_scale_trigger:
        sentiment_scale = max(p.sentiment_scale_floor, 1.0 + sentiment_score)
    elif action == "SELL" and sentiment_score > p.sentiment_scale_trigger:
        sentiment_scale = max(p.sentiment_scale_floor, 1.0 - sentiment_score)

    shares = int(shares_raw * sentiment_scale)
    # Allow 0 — caller / OMS skips zero-size instead of forcing a 1-share trade.
    shares = max(0, shares)

    # Cap at max_position_pct of portfolio (EQUITY_MAX_POSITION_PCT unless a caller passes one)
    if max_position_pct is None:
        max_position_pct = settings.equity_max_position_pct
    max_shares = int((portfolio_value * max_position_pct) / max(entry, 0.01))
    if shares > 0:
        shares = min(shares, max_shares)

    position_size = shares * entry
    risk_dollars  = shares * risk_per_share
    reward_dollars = shares * abs(target - entry)
    risk_reward   = round(reward_dollars / max(risk_dollars, 0.01), 2)
    # % move from entry to the ATR target — lets a caller judge a signal's
    # projected size (e.g. "only trade setups targeting 5%+") without having
    # to recompute it from entry_price/target_price everywhere it's shown.
    target_move_pct = round(abs(target - entry) / entry * 100, 2) if entry else 0.0

    return {
        "entry_price":     round(entry, 4),
        "stop_price":      round(stop, 4),
        "target_price":    round(target, 4),
        "target_move_pct": target_move_pct,
        "position_size":   round(position_size, 2),
        "shares":          shares,
        "risk_reward":     risk_reward,
        "risk_dollars":    round(risk_dollars, 2),
        "sentiment_scale": round(sentiment_scale, 3),
    }


# ── Earnings gate ───────────────────────────────────────────────────────────

_earnings_cache: dict[str, tuple[bool, datetime]] = {}
_CACHE_TTL_SECONDS = 3600


def earnings_gate(ticker: str, gate_days: int = 3) -> bool:
    """
    Returns True if earnings are within gate_days — signal should be skipped.
    Uses yfinance calendar; falls back to False (allow) on any error.
    """
    now = datetime.now(timezone.utc)
    if ticker in _earnings_cache:
        gated, cached_at = _earnings_cache[ticker]
        if (now - cached_at).total_seconds() < _CACHE_TTL_SECONDS:
            return gated

    try:
        import yfinance as yf  # type: ignore
        t = yf.Ticker(ticker)
        cal = t.calendar  # returns dict in yfinance >=0.2.x, DataFrame in older

        # Normalise: accept both dict and DataFrame
        earn_dates: list = []
        if cal is None:
            pass
        elif isinstance(cal, dict):
            # New yfinance API: {"Earnings Date": [Timestamp, ...], ...}
            earn_dates = cal.get("Earnings Date", []) or []
        else:
            # Legacy DataFrame
            try:
                if not cal.empty and "Earnings Date" in cal.index:
                    earn_dates = list(cal.loc["Earnings Date"])
            except Exception:
                pass

        for val in earn_dates:
            try:
                ts = pd.Timestamp(val)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                else:
                    ts = ts.tz_convert("UTC")
                days_away = (ts - pd.Timestamp(now)).days
                if abs(days_away) <= gate_days:
                    logger.info("Earnings gate triggered for %s — %d days away", ticker, days_away)
                    _earnings_cache[ticker] = (True, now)
                    return True
            except Exception:
                continue

    except Exception as exc:
        logger.warning("Earnings gate check failed for %s: %s", ticker, exc)

    _earnings_cache[ticker] = (False, now)
    return False
