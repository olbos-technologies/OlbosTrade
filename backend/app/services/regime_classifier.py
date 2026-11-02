"""
Regime Classifier.

Identifies the current market volatility regime and gates strategy selection.

Regimes:
  LOW_VOL_TRENDING    — Calm, directional market. Best for debit spreads.
  NORMAL_MEAN_REVERT  — Range-bound, moderate IV. Best for Iron Condors.
  HIGH_VOL_TRENDING   — Elevated vol + direction. Best for credit spreads.
  CRISIS              — Vol spike, dislocated market. Trade nothing.

This directly controls which strategies are allowed, position size multipliers,
and signal score thresholds — overriding the blunt ADX<20 / VIX<25 rules
in strategy_engine.py with a more nuanced multi-factor classification.

Integration:
    regime = classifier.classify(surface, rsi, adx, spy_returns)
    if regime.strategies_allowed:
        for strategy in regime.strategies_allowed:
            signal = strategy.generate_signal(...)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from app.services.iv_surface import IVSurfaceSnapshot
from app.utils.logger import get_logger

#: The regime job has no options chain, so it builds "IV rank" from VIX itself:
#: where today's VIX sits within its own recent range. The range must be long
#: enough to mean anything. `IVSurfaceSnapshot.iv_rank` is documented as a 1-year
#: rank, but the job fed this a 60-session window. Over a calm 60 sessions an
#: ordinary VIX of 19 is the TOP of the range (rank ~90), and the classifier's
#: CRISIS trigger is `iv_rank >= 85` on its own — so a VIX of 19.4, against an
#: absolute CRISIS trigger of 40, shut every equity and options entry (observed
#: 2026-11-02: "IV_rank=90.1 >= 85.0", VIX=19.4).
VIX_RANK_WINDOW_BARS = 252     # ~1 trading year
VIX_RANK_MIN_BARS = 126        # below ~6 months a "rank" is just noise
NEUTRAL_IV_RANK = 30.0         # what the job already assumed when VIX is unavailable


def vix_iv_rank(closes) -> Optional[float]:
    """0-100 rank of the latest VIX close within the last VIX_RANK_WINDOW_BARS closes.

    Returns None — not a number — when there is not enough history for the rank to
    mean anything, so the caller must choose a neutral value on purpose instead of
    quietly ranking against a short, calm window (the bug this replaces).
    Non-finite and non-positive closes are ignored.
    """
    import math
    vals = []
    for c in closes:
        try:
            x = float(c)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and x > 0:
            vals.append(x)
    vals = vals[-VIX_RANK_WINDOW_BARS:]
    if len(vals) < VIX_RANK_MIN_BARS:
        return None
    lo, hi = min(vals), max(vals)
    return float((vals[-1] - lo) / max(hi - lo, 0.01) * 100)

logger = get_logger(__name__)


class RegimeType(str, Enum):
    LOW_VOL_TRENDING   = "low_vol_trending"
    NORMAL_MEAN_REVERT = "normal_mean_revert"
    HIGH_VOL_TRENDING  = "high_vol_trending"
    CRISIS             = "crisis"
    UNKNOWN            = "unknown"


# ── Regime definitions ────────────────────────────────────────────────────────
# Each regime controls: which strategies run, size multiplier, signal threshold

REGIME_CONFIG: dict[RegimeType, dict] = {
    RegimeType.LOW_VOL_TRENDING: {
        "description": "Calm trending market — low IV, directional",
        "strategies_allowed": ["bull_call_debit_spread"],  # Debit spreads only
        "equity_strategies_allowed": ["momentum_long"],
        "size_multiplier": 0.75,      # Smaller size — less premium to collect
        "equity_size_multiplier": 1.0,
        "options_size_multiplier": 0.75,
        "signal_threshold_override": None,  # Use default
        "iron_condor_allowed": False,
        "credit_spread_allowed": False,
    },
    RegimeType.NORMAL_MEAN_REVERT: {
        "description": "Range-bound moderate IV — ideal for premium selling",
        "strategies_allowed": [
            "bull_put_spread", "bear_call_spread", "iron_condor"
        ],
        "equity_strategies_allowed": ["momentum_long", "momentum_short"],
        "size_multiplier": 1.0,       # Full size
        "equity_size_multiplier": 0.75,
        "options_size_multiplier": 1.0,
        "signal_threshold_override": None,
        "iron_condor_allowed": True,
        "credit_spread_allowed": True,
    },
    RegimeType.HIGH_VOL_TRENDING: {
        "description": "Elevated vol with directional bias — single-direction credit spreads",
        "strategies_allowed": ["bull_put_spread", "bear_call_spread"],
        "equity_strategies_allowed": ["momentum_long"],   # reduced size
        "size_multiplier": 0.75,      # Reduced size — higher risk
        "equity_size_multiplier": 0.6,
        "options_size_multiplier": 0.75,
        "signal_threshold_override": 0.72,  # Tighter gate
        "iron_condor_allowed": False,  # Too directional for condors
        "credit_spread_allowed": True,
    },
    RegimeType.CRISIS: {
        "description": "Vol spike / market dislocation — no new positions",
        "strategies_allowed": [],      # NOTHING trades in crisis
        "equity_strategies_allowed": [],
        "size_multiplier": 0.0,
        "equity_size_multiplier": 0.0,
        "options_size_multiplier": 0.0,
        "signal_threshold_override": 1.1,  # Effectively impossible threshold
        "iron_condor_allowed": False,
        "credit_spread_allowed": False,
    },
    RegimeType.UNKNOWN: {
        "description": "Insufficient data to classify regime",
        "strategies_allowed": ["bull_put_spread", "bear_call_spread"],
        "equity_strategies_allowed": ["momentum_long", "momentum_short"],
        "size_multiplier": 0.5,       # Half size when uncertain
        "equity_size_multiplier": 0.3,
        "options_size_multiplier": 0.5,
        "signal_threshold_override": 0.75,
        "iron_condor_allowed": False,
        "credit_spread_allowed": True,
    },
}


@dataclass
class RegimeFeatures:
    """Raw features fed into the regime classifier."""
    iv_rank:            float
    iv_percentile:      float
    vix:                float
    vrp:                float           # IV - RV
    adx_14:             float           # Average Directional Index
    rsi_14:             float
    realized_vol_20d:   float
    term_in_backwardation: bool
    skew_elevated:      bool            # Put skew above 20% normalized
    spy_return_5d:      float           # 5-day SPY return
    spy_return_20d:     float           # 20-day SPY return
    vol_of_vol:         float           # Rolling std of daily IV changes
    # ── Options-flow features (Options Intelligence module) ───────────────
    # Default to neutral so regimes classify identically until flow data
    # exists. Populated via compute_flow_features() when ingest is running.
    flow_sentiment_score:        float = 0.5   # 30-min bullish_premium / total
    flow_large_sweep_bullish_count: int = 0    # large bullish SPY sweeps, 2h


@dataclass
class RegimeState:
    """
    Current regime classification with all downstream parameters.
    Passed into paper_trader.run_signal_cycle() to control execution.
    """
    regime:                    RegimeType
    description:               str
    confidence:                float           # 0.0–1.0 classifier confidence
    strategies_allowed:        list[str]
    equity_strategies_allowed: list[str]
    size_multiplier:           float
    equity_size_multiplier:    float
    options_size_multiplier:   float
    signal_threshold:          Optional[float]  # None = use system default
    iron_condor_allowed:       bool
    credit_spread_allowed:     bool
    classified_at:             datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    features_used:             Optional[RegimeFeatures] = None
    reasoning:                 list[str] = field(default_factory=list)

    @property
    def is_tradeable(self) -> bool:
        return (len(self.strategies_allowed) > 0 or len(self.equity_strategies_allowed) > 0) \
               and self.size_multiplier >= 0

    @property
    def equity_allowed(self) -> bool:
        return len(self.equity_strategies_allowed) > 0 and self.equity_size_multiplier > 0

    @property
    def options_allowed(self) -> bool:
        return len(self.strategies_allowed) > 0 and self.options_size_multiplier > 0


class RegimeClassifier:
    """
    Multi-factor regime classifier for SPY options trading.

    Uses a rule-based scoring approach rather than ML because:
    1. Regime labels are inherently subjective and hard to train on
    2. Rules are transparent and auditable
    3. We have limited training data for regime transitions

    Classification hierarchy:
        1. Crisis check first (hard override)
        2. Trending vs mean-reverting (ADX + return momentum)
        3. Vol level (IV rank + VRP)
        4. Combine into final regime

    Each factor votes, weighted by confidence.
    """

    # ── Crisis thresholds ─────────────────────────────────────────────────────
    CRISIS_VIX_THRESHOLD    = 40.0   # VIX above this = crisis
    CRISIS_IV_RANK_MIN      = 85.0   # IV rank this extreme = crisis
    CRISIS_BACKWARDATION_IV = 0.35   # ATM IV > 35% in backwardation = crisis
    CRISIS_VOL_OF_VOL       = 0.08   # Vol-of-vol above 8% = unstable regime

    # ── Trending vs mean-reverting ────────────────────────────────────────────
    TREND_ADX_THRESHOLD     = 22.0   # ADX above this = trending
    TREND_RETURN_5D         = 0.03   # 5-day return > 3% = strong trend
    MEAN_REVERT_ADX         = 18.0   # ADX below this = range-bound

    # ── Vol level thresholds ──────────────────────────────────────────────────
    LOW_VOL_IV_RANK         = 25.0
    HIGH_VOL_IV_RANK        = 60.0

    def classify(
        self,
        surface: IVSurfaceSnapshot,
        rsi: float,
        adx: float,
        spy_returns: pd.Series,
        flow_sentiment_score: float = 0.5,
        flow_large_sweep_bullish_count: int = 0,
    ) -> RegimeState:
        """
        Classify the current market regime.

        Args:
            surface:     IV surface snapshot (from IVSurfaceEngine)
            rsi:         14-day RSI
            adx:         14-day ADX
            spy_returns: Daily SPY returns (last 30 days minimum)
            flow_sentiment_score: 30-min SPY bullish_premium / total_premium
                         ratio from the options_flow table (0.5 = neutral).
            flow_large_sweep_bullish_count: count of large bullish SPY sweeps
                         in the last 2 hours.

        Returns:
            RegimeState with full downstream parameters

        Note: these flow features are carried on ``features_used`` so the
        signal scorer can consume them; they do not yet alter the rule-based
        regime decision (kept stable until walk-forward retraining picks up
        the new columns).
        """
        reasoning: list[str] = []

        # ── Build feature set ─────────────────────────────────────────────────
        spy_return_5d  = float(spy_returns.iloc[-5:].sum()) if len(spy_returns) >= 5 else 0.0
        spy_return_20d = float(spy_returns.iloc[-20:].sum()) if len(spy_returns) >= 20 else 0.0
        vol_of_vol     = float(spy_returns.rolling(5).std().std()) if len(spy_returns) >= 10 else 0.0

        features = RegimeFeatures(
            iv_rank=surface.iv_rank,
            iv_percentile=surface.iv_percentile,
            vix=surface.atm_iv * 100,
            vrp=surface.vrp,
            adx_14=adx,
            rsi_14=rsi,
            realized_vol_20d=surface.realized_vol_20d,
            term_in_backwardation=surface.term_structure_in_backwardation,
            skew_elevated=surface.fear_premium_elevated,
            spy_return_5d=spy_return_5d,
            spy_return_20d=spy_return_20d,
            vol_of_vol=vol_of_vol,
            flow_sentiment_score=flow_sentiment_score,
            flow_large_sweep_bullish_count=flow_large_sweep_bullish_count,
        )

        # ── Step 1: Crisis check (hard override — beats everything else) ──────
        crisis_regime = self._check_crisis(features, surface, reasoning)
        if crisis_regime is not None:
            return self._build_state(crisis_regime, 0.95, features, reasoning)

        # ── Step 2: Trending vs mean-reverting ────────────────────────────────
        is_trending, trend_confidence = self._classify_trending(features, reasoning)

        # ── Step 3: Vol level classification ─────────────────────────────────
        vol_regime, vol_confidence = self._classify_vol_level(features, reasoning)

        # ── Step 4: Combine ───────────────────────────────────────────────────
        regime, confidence = self._combine(
            is_trending, trend_confidence,
            vol_regime, vol_confidence,
            features, reasoning,
        )

        return self._build_state(regime, confidence, features, reasoning)

    def _check_crisis(
        self,
        f: RegimeFeatures,
        surface: IVSurfaceSnapshot,
        reasoning: list[str],
    ) -> Optional[RegimeType]:
        """Returns CRISIS if any hard threshold is breached, else None."""
        crisis_flags = []

        if f.vix >= self.CRISIS_VIX_THRESHOLD:
            crisis_flags.append(f"VIX={f.vix:.1f} >= {self.CRISIS_VIX_THRESHOLD}")

        if f.iv_rank >= self.CRISIS_IV_RANK_MIN:
            crisis_flags.append(f"IV_rank={f.iv_rank:.1f} >= {self.CRISIS_IV_RANK_MIN}")

        if (f.term_in_backwardation
                and surface.atm_iv >= self.CRISIS_BACKWARDATION_IV):
            crisis_flags.append(
                f"Backwardation with ATM_IV={surface.atm_iv:.1%} >= "
                f"{self.CRISIS_BACKWARDATION_IV:.1%}"
            )

        if f.vol_of_vol >= self.CRISIS_VOL_OF_VOL:
            crisis_flags.append(f"Vol-of-vol={f.vol_of_vol:.3f} >= {self.CRISIS_VOL_OF_VOL}")

        if crisis_flags:
            for flag in crisis_flags:
                reasoning.append(f"CRISIS: {flag}")
            logger.warning(
                "CRISIS REGIME detected — %d flags: %s",
                len(crisis_flags), "; ".join(crisis_flags),
            )
            return RegimeType.CRISIS

        return None

    def _classify_trending(
        self, f: RegimeFeatures, reasoning: list[str]
    ) -> tuple[bool, float]:
        """
        Returns (is_trending, confidence 0-1).
        Multiple factors vote: ADX, recent returns, RSI extremes.
        """
        votes: list[tuple[bool, float]] = []

        # ADX vote
        if f.adx_14 >= self.TREND_ADX_THRESHOLD:
            votes.append((True, 0.35))
            reasoning.append(f"Trending: ADX={f.adx_14:.1f} >= {self.TREND_ADX_THRESHOLD}")
        elif f.adx_14 <= self.MEAN_REVERT_ADX:
            votes.append((False, 0.35))
            reasoning.append(f"Range-bound: ADX={f.adx_14:.1f} <= {self.MEAN_REVERT_ADX}")
        else:
            votes.append((True, 0.15))  # Weak trend signal

        # Return momentum vote
        if abs(f.spy_return_5d) >= self.TREND_RETURN_5D:
            votes.append((True, 0.30))
            reasoning.append(
                f"Trending: 5d return={f.spy_return_5d:.1%} >= {self.TREND_RETURN_5D:.1%}"
            )
        else:
            votes.append((False, 0.20))

        # RSI extreme vote (RSI > 70 or < 30 = trending)
        if f.rsi_14 > 70 or f.rsi_14 < 30:
            votes.append((True, 0.20))
            reasoning.append(f"Trending: RSI={f.rsi_14:.1f} at extreme")
        else:
            votes.append((False, 0.15))

        # Term structure vote (backwardation = stress = often trending)
        if f.term_in_backwardation:
            votes.append((True, 0.15))
            reasoning.append("Trending: IV term structure in backwardation")

        # Weighted vote
        true_weight  = sum(w for v, w in votes if v)
        false_weight = sum(w for v, w in votes if not v)
        total = true_weight + false_weight

        is_trending = true_weight > false_weight
        confidence  = max(true_weight, false_weight) / total if total > 0 else 0.5
        return is_trending, round(confidence, 2)

    def _classify_vol_level(
        self, f: RegimeFeatures, reasoning: list[str]
    ) -> tuple[str, float]:
        """
        Returns ("low"|"normal"|"high", confidence).
        """
        if f.iv_rank < self.LOW_VOL_IV_RANK:
            reasoning.append(f"Low vol: IV_rank={f.iv_rank:.1f} < {self.LOW_VOL_IV_RANK}")
            confidence = 1.0 - (f.iv_rank / self.LOW_VOL_IV_RANK) * 0.3
            return "low", round(confidence, 2)

        if f.iv_rank >= self.HIGH_VOL_IV_RANK:
            reasoning.append(f"High vol: IV_rank={f.iv_rank:.1f} >= {self.HIGH_VOL_IV_RANK}")
            confidence = min((f.iv_rank - self.HIGH_VOL_IV_RANK) / 30 + 0.60, 0.95)
            return "high", round(confidence, 2)

        reasoning.append(
            f"Normal vol: IV_rank={f.iv_rank:.1f} in [{self.LOW_VOL_IV_RANK}, {self.HIGH_VOL_IV_RANK})"
        )
        # Confidence higher when near middle of range
        dist_from_center = abs(f.iv_rank - 42.5) / 17.5
        confidence = 0.70 - dist_from_center * 0.20
        return "normal", round(confidence, 2)

    def _combine(
        self,
        is_trending: bool,
        trend_conf: float,
        vol_level: str,
        vol_conf: float,
        f: RegimeFeatures,
        reasoning: list[str],
    ) -> tuple[RegimeType, float]:
        """Combine trending/vol signals into final regime."""
        combined_conf = (trend_conf + vol_conf) / 2

        if vol_level == "low" and is_trending:
            return RegimeType.LOW_VOL_TRENDING, combined_conf

        if vol_level == "low" and not is_trending:
            # Low vol, range-bound — still possible to trade but thin edge
            reasoning.append("Low vol range-bound — minimal premium available")
            return RegimeType.LOW_VOL_TRENDING, combined_conf * 0.8

        if vol_level == "normal" and not is_trending:
            return RegimeType.NORMAL_MEAN_REVERT, combined_conf

        if vol_level == "normal" and is_trending:
            # Normal vol but trending — single-direction credit spreads OK
            return RegimeType.HIGH_VOL_TRENDING, combined_conf * 0.85

        if vol_level == "high":
            return RegimeType.HIGH_VOL_TRENDING, combined_conf

        return RegimeType.UNKNOWN, 0.4

    def _build_state(
        self,
        regime: RegimeType,
        confidence: float,
        features: RegimeFeatures,
        reasoning: list[str],
    ) -> RegimeState:
        config = REGIME_CONFIG[regime]
        from app.core.config import settings
        default_threshold = settings.effective_signal_score_threshold

        state = RegimeState(
            regime=regime,
            description=config["description"],
            confidence=confidence,
            strategies_allowed=config["strategies_allowed"],
            equity_strategies_allowed=config.get("equity_strategies_allowed", []),
            size_multiplier=config["size_multiplier"],
            equity_size_multiplier=config.get("equity_size_multiplier", 0.5),
            options_size_multiplier=config.get("options_size_multiplier", config["size_multiplier"]),
            signal_threshold=config["signal_threshold_override"] or default_threshold,
            iron_condor_allowed=config["iron_condor_allowed"],
            credit_spread_allowed=config["credit_spread_allowed"],
            features_used=features,
            reasoning=reasoning,
        )

        logger.info(
            "Regime: %s (%.0f%% confidence) | strategies=%s | size=%.0f%% | "
            "threshold=%.2f",
            regime.value, confidence * 100,
            state.strategies_allowed,
            state.size_multiplier * 100,
            state.signal_threshold or 0,
        )
        return state


# ── Module-level helpers ────────────────────────────────────────────────────

def classify_equity_allowed(regime_state: RegimeState) -> bool:
    """Return True if equity trading is permitted in this regime."""
    return regime_state.equity_allowed


def classify_options_allowed(regime_state: RegimeState) -> bool:
    """Return True if options trading is permitted in this regime."""
    return regime_state.options_allowed


# ── Options-flow feature extraction (Options Intelligence module) ────────────
async def compute_flow_features(ticker: str = "SPY") -> dict:
    """
    Extract the two flow features used by the regime classifier / signal scorer
    from the options_flow table:

        flow_sentiment_score:
            rolling 30-min  bullish_premium / total_premium  ratio (0.5 neutral)
        flow_large_sweep_bullish_count:
            count of large bullish sweeps in the last 2 hours

    The live Options Flow (OPRA) module has been removed — it required a paid
    market-data subscription. This returns neutral defaults (0.5 / 0), exactly how
    the classifier ran by default, so callers can keep passing the result straight
    into ``classify(...)`` unconditionally.
    """
    return {"flow_sentiment_score": 0.5, "flow_large_sweep_bullish_count": 0}
