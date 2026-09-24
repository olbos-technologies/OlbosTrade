"""
Intelligence Hub routes — /api/intel

Read-only research endpoints: event calendar, smart watchlists, data quality,
and the deterministic "why is it moving?" assembler. None of these touch the
order path, risk engine, guardrails, or kill switch.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_api_key

from app.services.event_risk_service import _days_to_next_earnings
from app.services.intel import watchlist_service as wl
from app.services.intel.catalyst_calendar import get_calendar
from app.services.intel.provider_registry import registry
from app.services.intel.why_moving import MoveInputs, explain_move
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


# ── Event & Catalyst Calendar ────────────────────────────────────────────────
@router.get("/calendar")
async def calendar(symbols: str | None = None, days_ahead: int = 45):
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    return get_calendar(symbols=syms, days_ahead=days_ahead)


# ── Smart Watchlists ─────────────────────────────────────────────────────────
class CreateWatchlist(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)
    symbols: list[str] = Field(default_factory=list)


class AddSymbol(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    asset_class: str = "equity"


@router.get("/watchlists")
async def watchlists():
    return {"watchlists": await wl.list_watchlists()}


@router.get("/watchlists/{slug}")
async def watchlist_detail(slug: str):
    w = await wl.get_watchlist(slug)
    if not w:
        raise HTTPException(404, "Watchlist not found")
    return w


@router.post("/watchlists", dependencies=[Depends(require_api_key)])
async def create_watchlist(body: CreateWatchlist):
    return await wl.create_watchlist(body.name, body.description, body.symbols)


@router.post("/watchlists/{slug}/symbols", dependencies=[Depends(require_api_key)])
async def add_symbol(slug: str, body: AddSymbol):
    w = await wl.add_symbol(slug, body.symbol, body.asset_class)
    if not w:
        raise HTTPException(404, "Watchlist not found")
    return w


@router.delete("/watchlists/{slug}/symbols/{symbol}", dependencies=[Depends(require_api_key)])
async def remove_symbol(slug: str, symbol: str):
    w = await wl.remove_symbol(slug, symbol)
    if not w:
        raise HTTPException(404, "Watchlist not found")
    return w


@router.delete("/watchlists/{slug}", dependencies=[Depends(require_api_key)])
async def delete_watchlist(slug: str):
    ok = await wl.delete_watchlist(slug)
    if not ok:
        raise HTTPException(400, "Cannot delete (not found or system list)")
    return {"deleted": True}


# ── News & Filings feeds ─────────────────────────────────────────────────────
async def _gather(providers, method: str, symbol: str, limit: int) -> list[dict]:
    """Run each provider's blocking fetch in a thread; merge enveloped results."""
    loop = asyncio.get_running_loop()
    items: list[dict] = []
    for p in providers:
        try:
            envs = await loop.run_in_executor(None, lambda p=p: getattr(p, method)(symbol, limit))
            items.extend(e.to_dict() for e in envs)
        except Exception as exc:
            logger.warning("%s.%s failed for %s: %s", getattr(p, "name", "?"), method, symbol, exc)
    items.sort(key=lambda d: d["as_of"], reverse=True)
    return items[:limit]


@router.get("/news/{symbol}")
async def news(symbol: str, limit: int = 20):
    items = await _gather(registry._news, "fetch_news", symbol.upper(), limit)
    return {"symbol": symbol.upper(), "items": items, "count": len(items)}


@router.get("/filings/{symbol}")
async def filings(symbol: str, limit: int = 20):
    items = await _gather(registry._filings, "fetch_filings", symbol.upper(), limit)
    return {"symbol": symbol.upper(), "items": items, "count": len(items)}


# ── News-to-Risk classification ──────────────────────────────────────────────
@router.get("/classify/{symbol}")
async def classify_symbol(symbol: str, limit: int = 15):
    """Fetch news + filings and attach a deterministic risk classification to
    each. Classification is advisory only — it changes no gate."""
    from app.services.intel.news_classifier import classify

    sym = symbol.upper()
    news = await _gather(registry._news, "fetch_news", sym, limit)
    filings = await _gather(registry._filings, "fetch_filings", sym, limit)

    items = []
    for n in news:
        c = classify(title=n["payload"].get("title", ""))
        items.append({"type": "news", **n, "classification": c.to_dict()})
    for f in filings:
        p = f["payload"]
        c = classify(title=p.get("title", ""), form_type=p.get("form_type"), is_filing=True)
        items.append({"type": "filing", **f, "classification": c.to_dict()})

    items.sort(key=lambda d: d["as_of"], reverse=True)
    return {"symbol": sym, "items": items[:limit], "count": len(items)}


# ── Insider & filing intelligence ────────────────────────────────────────────
@router.get("/insider/{symbol}")
async def insider(symbol: str, limit: int = 40):
    from app.services.intel.insider_intel import summarize_insider

    sym = symbol.upper()
    filings = await _gather(registry._filings, "fetch_filings", sym, limit)
    return summarize_insider(sym, filings).to_dict()


# ── Data quality ─────────────────────────────────────────────────────────────
@router.get("/data-quality/{symbol}")
async def data_quality(symbol: str):
    return registry.data_quality(symbol.upper()).to_dict()


# ── Why is it moving? ─────────────────────────────────────────────────────────
async def _snapshot(symbol: str) -> dict:
    """Best-effort change_pct + relative volume via yfinance (delayed, free)."""
    loop = asyncio.get_running_loop()

    def _fetch():
        try:
            import yfinance as yf  # type: ignore
            hist = yf.Ticker(symbol).history(period="1mo", interval="1d", auto_adjust=True)
            if hist.empty or len(hist) < 2:
                return {}
            closes = hist["Close"].dropna()
            vols = hist["Volume"].dropna()
            change_pct = float((closes.iloc[-1] / closes.iloc[-2] - 1) * 100)
            rel_vol = None
            if len(vols) >= 6:
                avg = float(vols.iloc[-21:-1].mean()) if len(vols) > 21 else float(vols.iloc[:-1].mean())
                if avg > 0:
                    rel_vol = float(vols.iloc[-1] / avg)
            return {"change_pct": change_pct, "relative_volume": rel_vol}
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("why-moving snapshot failed for %s: %s", symbol, exc)
            return {}

    return await loop.run_in_executor(None, _fetch)


@router.get("/why-moving/{symbol}")
async def why_moving(symbol: str):
    sym = symbol.upper()
    snap = await _snapshot(sym)
    days_earn = _days_to_next_earnings(sym)
    # A sourced news item existing is a fact; its content/cause is not asserted.
    news_items = await _gather(registry._news, "fetch_news", sym, 5)
    result = explain_move(MoveInputs(
        symbol=sym,
        change_pct=snap.get("change_pct"),
        relative_volume=snap.get("relative_volume"),
        days_to_earnings=days_earn,
        news_detected=len(news_items) > 0,
        # Sector/VWAP/options context are wired in a later phase — omitted
        # inputs surface honestly under `unknown`, never fabricated.
    ))
    return result.to_dict()
