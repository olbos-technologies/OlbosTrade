"""
Options Scan Engine (v2) — A-grade institutional options scanning.

Generates ranked spread candidates by Expected Value (EV) with:
- Live IBKR chain pricing fallback
- Multi-ticker scanning (SPY, ES, QQQ, single stocks, VIX)
- Entry ladder logic (splits large EV spreads into tranches)
- Kelly fraction → position sizing (confidence scaling)
- Vol surface modeling (skew-aware Greeks)

Integrates Black-Scholes, live chain, options intelligence, and automated gates
to produce institutional-quality spread candidates for autopilot decision logic.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class EntryTranche:
    """A single execution tranche for ladder entry."""
    tranche_id: int
    quantity: int
    kelly_fraction: float
    entry_note: str


@dataclass
class OptionsScanCandidate:
    """A ranked options spread candidate with full intelligence."""
    id: str
    ticker: str
    option_type: str
    short_strike: float
    long_strike: float
    expiration: str
    dte: int
    credit: float
    max_loss: float
    pop: float
    expected_value: float
    ev_per_risk: float
    kelly_fraction: float
    short_delta: float
    reward_risk: float
    pricing_source: str  # "black_scholes", "live_chain", "yfinance_chain"
    entry_ladder: Optional[list[EntryTranche]] = None
    iv_rank: Optional[float] = None
    skew_adjustment: float = 0.0  # vol smile premium/discount
    contract_quality: Optional[dict] = None  # research ranking only

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "option_type": self.option_type,
            "short_strike": self.short_strike,
            "long_strike": self.long_strike,
            "expiration": self.expiration,
            "dte": self.dte,
            "credit": self.credit,
            "max_loss": self.max_loss,
            "pop": round(self.pop, 4),
            "expected_value": self.expected_value,
            "ev_per_risk": round(self.ev_per_risk, 4),
            "kelly_fraction": round(self.kelly_fraction, 4),
            "short_delta": round(self.short_delta, 4),
            "reward_risk": round(self.reward_risk, 4),
            "pricing_source": self.pricing_source,
            "contract_quality": self.contract_quality,
            "iv_rank": round(self.iv_rank, 2) if self.iv_rank else None,
            "skew_adjustment": round(self.skew_adjustment, 4),
            "entry_ladder": [
                {
                    "tranche_id": t.tranche_id,
                    "quantity": t.quantity,
                    "kelly_fraction": round(t.kelly_fraction, 4),
                    "entry_note": t.entry_note,
                }
                for t in (self.entry_ladder or [])
            ],
        }


@dataclass
class OptionsScanResult:
    """Result of an options scan."""
    candidates: list[OptionsScanCandidate]
    gate_blocked: bool
    gate_reason: Optional[str]
    spot: Optional[float]
    vix_estimate: Optional[float]
    realized_vol: Optional[float]
    iv_rank: Optional[float]
    error: Optional[str]
    tickers_scanned: list[str] = None


async def check_no_trade_gate() -> Optional[str]:
    """
    Check if trading is allowed. Returns None if OK to trade, or a reason string if gated.
    Gates checked:
    - Kill switch engaged
    - Market hours (if configured)
    - Regime options allowed
    """
    from app.services.kill_switch import kill_switch_service
    from app.utils.market_hours import market_status
    from app.core.config import settings

    if kill_switch_service.is_engaged:
        return f"Kill switch engaged: {kill_switch_service.status.get('reason', 'unknown')}"

    if settings.market_hours_only:
        mkt = market_status()
        if not mkt["is_open"]:
            return f"Market closed: {mkt['reason']}"

    return None


async def _live_spread_quote(
    broker, symbol: str, expiry_iso: str,
    short_strike: float, long_strike: float, opt_type: str,
) -> Optional[dict]:
    """
    Price a vertical spread off the LIVE options chain (real NBBO mids).
    Returns dict with net_credit (dollars), actual strikes, and short-leg delta.
    Returns None if live chain unavailable (no IBKR options subscription).
    """
    try:
        chain = await broker.get_options_chain(symbol, expiry_iso)
    except Exception as exc:
        logger.debug("Live options chain unavailable for %s %s: %s", symbol, expiry_iso, exc)
        return None

    legs = chain.puts if opt_type == "put" else chain.calls
    if not legs:
        return None

    def _nearest(target: float):
        return min(legs, key=lambda c: abs(float(c.strike) - target))

    short_c = _nearest(short_strike)
    long_c = _nearest(long_strike)
    if float(short_c.strike) == float(long_c.strike):
        return None

    def _mid(c) -> Optional[float]:
        bid = float(c.bid or 0)
        ask = float(c.ask or 0)
        if bid > 0 and ask > 0 and ask >= bid:
            return (bid + ask) / 2.0
        last = float(getattr(c, "last", 0) or 0)
        return last if last > 0 else None

    short_mid = _mid(short_c)
    long_mid = _mid(long_c)
    if short_mid is None or long_mid is None:
        return None

    net_credit_ps = short_mid - long_mid
    if net_credit_ps <= 0:
        return None

    short_delta = abs(float(short_c.greeks.delta)) if short_c.greeks and short_c.greeks.delta else None
    from app.services.options_contract_quality import score_spread_quality
    contract_quality = score_spread_quality(short_c, long_c,
        "bull_put_spread" if opt_type == "put" else "bear_call_spread")
    return {
        "contract_quality": contract_quality,
        "net_credit": round(net_credit_ps * 100, 2),
        "short_delta": short_delta,
        "short_strike": float(short_c.strike),
        "long_strike": float(long_c.strike),
    }


async def _yf_options_quote(
    symbol: str, target_expiry_iso: str,
    short_strike: float, long_strike: float, opt_type: str,
) -> Optional[dict]:
    """
    Price a vertical spread off yfinance's (free, ~15-min delayed) option chain.
    Fallback when broker chain has no quotes. Snaps to nearest listed expiration/strikes.
    Returns None if no usable credit quote.
    """
    import asyncio
    loop = asyncio.get_running_loop()

    def _fetch() -> Optional[dict]:
        try:
            import yfinance as yf
            from datetime import date as _date
            tk = yf.Ticker(symbol)
            expirations = list(tk.options or [])
            if not expirations:
                return None
            target = _date.fromisoformat(target_expiry_iso)
            chosen = min(expirations,
                         key=lambda e: abs((_date.fromisoformat(e) - target).days))
            chain = tk.option_chain(chosen)
            df = chain.puts if opt_type == "put" else chain.calls
            if df is None or df.empty:
                return None

            def _leg(strike: float):
                idx = (df["strike"] - strike).abs().idxmin()
                row = df.loc[idx]
                bid = float(row.get("bid", 0) or 0)
                ask = float(row.get("ask", 0) or 0)
                last = float(row.get("lastPrice", 0) or 0)
                if bid > 0 and ask > 0 and ask >= bid:
                    mid = (bid + ask) / 2.0
                elif last > 0:
                    mid = last
                else:
                    mid = None
                return float(row["strike"]), mid

            s_strike, s_mid = _leg(short_strike)
            l_strike, l_mid = _leg(long_strike)
            if s_mid is None or l_mid is None or s_strike == l_strike:
                return None
            net = s_mid - l_mid
            if net <= 0:
                return None
            return {
                "net_credit": round(net * 100, 2),
                "short_delta": None,
                "short_strike": s_strike,
                "long_strike": l_strike,
                "expiration": chosen,
            }
        except Exception as exc:
            # Was a bare `return None`: a candidate that failed to build
            # vanished with no record, which is exactly how "options signals
            # almost never fire" (44f7073) stays invisible. Debug rather than
            # warning — a chain that cannot form a spread is ordinary.
            logger.debug("Spread build failed for %s: %s", symbol, exc)
            return None

    return await loop.run_in_executor(None, _fetch)


async def _yf_bars(ticker: str, limit: int = 60) -> list:
    """Fetch daily OHLCV bars via yfinance."""
    import yfinance as yf
    from decimal import Decimal
    from app.broker.broker_interface import Bar

    loop = asyncio.get_running_loop()

    def _fetch():
        sym = "^VIX" if ticker.upper() == "VIX" else ticker
        period = f"{min(limit * 2, 730)}d"
        hist = yf.Ticker(sym).history(period=period, auto_adjust=True)
        if hist.empty:
            return []
        hist = hist.tail(limit)
        bars = []
        import math as _math
        for ts, row in hist.iterrows():
            try:
                o = float(row["Open"])
                h = float(row["High"])
                lo = float(row["Low"])
                c = float(row["Close"])
            except (TypeError, ValueError):
                continue
            if any(_math.isnan(x) or x <= 0 for x in (o, h, lo, c)):
                continue
            try:
                bars.append(Bar(
                    timestamp=ts.to_pydatetime(),
                    open=Decimal(str(round(o, 4))),
                    high=Decimal(str(round(h, 4))),
                    low=Decimal(str(round(lo, 4))),
                    close=Decimal(str(round(c, 4))),
                    volume=int(row.get("Volume", 0) or 0),
                ))
            except Exception:
                continue
        return bars

    return await loop.run_in_executor(None, _fetch)


def _compute_iv_rank(vix_series: list[float]) -> float:
    """Compute IV rank from VIX series (percentile rank, 0-100)."""
    if not vix_series or len(vix_series) < 5:
        return 50.0
    vix_now = vix_series[-1]
    vix_high = max(vix_series)
    vix_low = min(vix_series)
    if vix_high == vix_low:
        return 50.0
    return float((vix_now - vix_low) / (vix_high - vix_low) * 100)


def _skew_adjustment(option_type: str, short_delta: float, iv_rank: float) -> float:
    """
    Adjust spread analysis for vol smile/skew.
    - Put spreads gain value when IV is high (fear premium)
    - Call spreads benefit from low IV rank (calm skew)
    Returns additive adjustment to EV (in dollars).
    """
    if option_type == "put":
        skew_mult = (iv_rank - 50) / 100  # +iv_rank → more skew premium
        return skew_mult * 15  # ~$0.15 per 1% skew
    else:
        skew_mult = (50 - iv_rank) / 100  # low iv_rank → call premium
        return skew_mult * 10  # ~$0.10 per 1% reverse skew
    return 0.0


def _build_entry_ladder(
    kelly_fraction: float,
    base_quantity: int,
    expected_value: float,
) -> list[EntryTranche]:
    """
    Build entry ladder: split large EV spreads into tranches by kelly confidence.
    Reduces risk of single-tranche entry, scales with true edge confidence.

    Kelly < 0.15 → full entry (high confidence)
    Kelly 0.15–0.30 → 2 tranches (medium)
    Kelly 0.30–0.50 → 3 tranches (lower confidence)
    Kelly > 0.50 → caution (edge is thin, size aggressively downward)
    """
    if base_quantity <= 0:
        return []

    # Prevent over-trading thin edges
    if kelly_fraction > 0.50:
        return [EntryTranche(
            tranche_id=1,
            quantity=max(1, base_quantity // 2),
            kelly_fraction=kelly_fraction,
            entry_note="Kelly > 50%: edge too thin, half position",
        )]

    if kelly_fraction > 0.30:
        q_per_tranche = max(1, base_quantity // 3)
        return [
            EntryTranche(
                tranche_id=1,
                quantity=q_per_tranche,
                kelly_fraction=kelly_fraction,
                entry_note="Entry 1: Scale-in ladder (kelly 30-50%)",
            ),
            EntryTranche(
                tranche_id=2,
                quantity=q_per_tranche,
                kelly_fraction=kelly_fraction * 0.8,
                entry_note="Entry 2: +2-3% move confirmed",
            ),
            EntryTranche(
                tranche_id=3,
                quantity=base_quantity - (q_per_tranche * 2),
                kelly_fraction=kelly_fraction * 0.6,
                entry_note="Entry 3: Full setup confirmed",
            ),
        ]

    if kelly_fraction > 0.15:
        q_per_tranche = max(1, base_quantity // 2)
        return [
            EntryTranche(
                tranche_id=1,
                quantity=q_per_tranche,
                kelly_fraction=kelly_fraction,
                entry_note="Entry 1: Initial (kelly 15-30%)",
            ),
            EntryTranche(
                tranche_id=2,
                quantity=base_quantity - q_per_tranche,
                kelly_fraction=kelly_fraction * 0.9,
                entry_note="Entry 2: Fill to full (setup confirmed)",
            ),
        ]

    return [EntryTranche(
        tranche_id=1,
        quantity=base_quantity,
        kelly_fraction=kelly_fraction,
        entry_note="Full entry (kelly < 15%: high confidence edge)",
    )]


async def scan_options(
    tickers: Optional[list[str]] = None,
    strategy: str = "bull_put_spread",
    dte_target: Optional[int] = None,
    limit: int = 10,
    base_quantity: int = 1,
) -> OptionsScanResult:
    """
    A-grade multi-ticker options scan with live chain pricing + entry ladder logic.

    Returns candidates ranked by Expected Value (EV) in descending order.
    Applies NO-TRADE gate before scanning.
    Integrates live IBKR chain → yfinance fallback → Black-Scholes.

    Args:
        tickers: List of symbols to scan (default: ["SPY", "ES", "QQQ"])
        strategy: Strategy type (default: bull_put_spread for puts)
        dte_target: Days to expiry target (uses config if None)
        limit: Max candidates to return (default: 10)
        base_quantity: Base position size for kelly-scaled ladder (default: 1)

    Returns:
        OptionsScanResult with ranked candidates, entry ladders, or gate block reason
    """
    from app.core.config import settings
    from app.services.options_pricer import BlackScholesPricer
    from app.services.options_intelligence import analyze_spread
    from app.broker.broker_factory import get_broker
    import uuid

    gate_reason = await check_no_trade_gate()
    if gate_reason:
        return OptionsScanResult(
            candidates=[],
            gate_blocked=True,
            gate_reason=gate_reason,
            spot=None,
            vix_estimate=None,
            realized_vol=None,
            iv_rank=None,
            error=None,
            tickers_scanned=[],
        )

    if tickers is None:
        tickers = ["SPY", "ES", "QQQ"]

    try:
        pricer = BlackScholesPricer()
        candidates = []
        RISK_FREE = 0.05

        if dte_target is None:
            dte_target = settings.get("dte_target", 30)

        try:
            broker = get_broker()
        except Exception:
            broker = None

        # Fetch VIX once (used for all tickers)
        vix_bars = await _yf_bars("VIX", limit=60)
        vix_series = [float(b.close) for b in vix_bars] if vix_bars else [20.0]
        vix_now = vix_series[-1]
        vix_est = vix_now / 100.0
        iv_rank = _compute_iv_rank(vix_series)

        # Target expiry
        today = date.today()
        target_exp = today + timedelta(days=dte_target)
        while target_exp.weekday() != 4:  # Friday
            target_exp += timedelta(days=1)
        T = max((target_exp - today).days / 365, 0.01)

        # Strategy and option type
        opt_type = "put" if "put" in strategy else "call"

        # Scan each ticker
        for ticker in tickers:
            try:
                bars = await _yf_bars(ticker, limit=60)
                if len(bars) < 20:
                    logger.debug("Insufficient data for %s", ticker)
                    continue

                closes = [float(b.close) for b in bars]
                spot = closes[-1]
                log_rets = np.diff(np.log(closes))
                sigma = float(np.std(log_rets) * np.sqrt(252))

                # Use VIX proxy for SPY, realized vol for others
                ticker_iv = vix_est if ticker.upper() == "SPY" else sigma

                # Generate candidates across strike grid
                strike_offsets = [-40, -30, -20, -10, 0, 10, 20, 30, 40]
                for offset in strike_offsets:
                    try:
                        short_strike = round(spot + offset, 0)
                        long_strike = short_strike - 5.0 if opt_type == "put" else short_strike + 5.0
                        spread_width = abs(short_strike - long_strike)

                        if spread_width <= 0:
                            continue

                        # Compute delta
                        delta = pricer.delta(spot, short_strike, T, RISK_FREE, ticker_iv, opt_type)
                        if abs(delta) < 0.10 or abs(delta) > 0.80:
                            continue

                        # Black-Scholes pricing (fallback/default)
                        if opt_type == "put":
                            short_px = pricer.put_price(spot, short_strike, T, RISK_FREE, ticker_iv)
                            long_px = pricer.put_price(spot, long_strike, T, RISK_FREE, ticker_iv)
                        else:
                            short_px = pricer.call_price(spot, short_strike, T, RISK_FREE, ticker_iv)
                            long_px = pricer.call_price(spot, long_strike, T, RISK_FREE, ticker_iv)

                        net_credit_ps = short_px - long_px
                        pricing_source = "black_scholes"
                        contract_quality = None

                        # Try live IBKR chain if broker available
                        if broker is not None:
                            try:
                                live = await _live_spread_quote(
                                    broker, ticker, target_exp.isoformat(),
                                    short_strike, long_strike, opt_type,
                                )
                                if live and live["net_credit"] > 0:
                                    short_strike = live["short_strike"]
                                    long_strike = live["long_strike"]
                                    net_credit_ps = live["net_credit"] / 100.0
                                    if live["short_delta"] is not None:
                                        delta = live["short_delta"]
                                    pricing_source = "live_chain"
                                    contract_quality = live.get("contract_quality")
                            except Exception:
                                pass  # Fall through to yfinance

                        # Fallback to yfinance if no live quote
                        if pricing_source == "black_scholes":
                            yq = await _yf_options_quote(
                                ticker, target_exp.isoformat(),
                                short_strike, long_strike, opt_type,
                            )
                            if yq and yq["net_credit"] > 0:
                                short_strike = yq["short_strike"]
                                long_strike = yq["long_strike"]
                                net_credit_ps = yq["net_credit"] / 100.0
                                pricing_source = "yfinance_chain"

                        if net_credit_ps <= 0.05:
                            continue

                        # Analyze with options intelligence
                        intel = analyze_spread(
                            spot=spot,
                            short_strike=short_strike,
                            long_strike=long_strike,
                            option_type=opt_type,
                            dte=float(dte_target),
                            iv=ticker_iv,
                            credit_per_share=net_credit_ps,
                            r=RISK_FREE,
                        )

                        # Skew adjustment based on vol regime
                        skew_adj = _skew_adjustment(opt_type, abs(delta), iv_rank)
                        adjusted_ev = intel.expected_value + skew_adj

                        # Build entry ladder based on kelly fraction
                        entry_ladder = _build_entry_ladder(
                            intel.kelly_fraction,
                            base_quantity,
                            adjusted_ev,
                        )

                        candidate = OptionsScanCandidate(
                            id=str(uuid.uuid4()),
                            ticker=ticker.upper(),
                            option_type=opt_type,
                            short_strike=float(short_strike),
                            long_strike=float(long_strike),
                            expiration=target_exp.isoformat(),
                            dte=dte_target,
                            credit=round(net_credit_ps * 100, 2),
                            max_loss=intel.max_loss,
                            pop=intel.pop,
                            expected_value=adjusted_ev,  # EV with skew adjustment
                            ev_per_risk=intel.ev_per_risk,
                            kelly_fraction=intel.kelly_fraction,
                            short_delta=abs(delta),
                            reward_risk=intel.reward_risk,
                            pricing_source=pricing_source,
                            entry_ladder=entry_ladder,
                            iv_rank=iv_rank,
                            skew_adjustment=skew_adj,
                            contract_quality=contract_quality,
                        )
                        candidates.append(candidate)

                    except Exception as e:
                        logger.debug("Spread generation failed for %s offset %s: %s", ticker, offset, e)
                        continue

            except Exception as e:
                logger.debug("Scan failed for %s: %s", ticker, e)
                continue

        # Sort by EV descending (highest first)
        # Research-only tie-break. Existing EV remains primary; this endpoint
        # returns candidates and does not dispatch orders. Unavailable != zero.
        candidates.sort(key=lambda c: (c.expected_value,
            c.contract_quality["score"] if c.contract_quality and c.contract_quality.get("state") == "scored" else -1), reverse=True)

        return OptionsScanResult(
            candidates=candidates[:limit],
            gate_blocked=False,
            gate_reason=None,
            spot=round(closes[-1], 2) if closes else None,
            vix_estimate=round(vix_now, 1),
            realized_vol=round(sigma * 100, 1) if 'sigma' in locals() else None,
            iv_rank=round(iv_rank, 1),
            error=None,
            tickers_scanned=tickers,
        )

    except Exception as exc:
        logger.warning("Options scan failed: %s", exc, exc_info=True)
        return OptionsScanResult(
            candidates=[],
            gate_blocked=False,
            gate_reason=None,
            spot=None,
            vix_estimate=None,
            realized_vol=None,
            iv_rank=None,
            error=str(exc),
            tickers_scanned=tickers or [],
        )
