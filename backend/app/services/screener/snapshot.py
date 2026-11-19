"""
A cached, background-built snapshot of daily metrics for the whole universe.

One refresh downloads daily bars for every constituent in a few batched Yahoo
requests and computes the metrics. It runs as a background task, so a request
never waits on ~500 symbols (the proxies in front of the API time out at 130s):
the route returns the current snapshot at once, with `refreshing` set while a
newer one is being built, and the page polls.

Failure is never presented as data. A symbol with no usable bars is listed in
`missing`; a refresh that fails outright keeps the previous snapshot and
records the error, and with no previous snapshot the state is "error".
"""

from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timezone
from typing import Callable, Optional

from app.services.screener.metrics import clean_bars, completed_sessions, session_metrics
from app.services.screener.universe import load_universe, yahoo_symbol
from app.utils.logger import get_logger

logger = get_logger(__name__)

TTL_S = 15 * 60          # rebuild at most every 15 minutes
BATCH = 100              # symbols per Yahoo request
PERIOD = "3mo"           # ~63 sessions; 21 are needed


def frame_to_bars(df, yahoo_sym: str) -> list[dict]:
    """Bars for one symbol from a yfinance download frame (multi- or single-ticker columns)."""
    try:
        import pandas as pd  # noqa: F401
        if df is None or getattr(df, "empty", True):
            return []
        cols = df.columns
        if getattr(cols, "nlevels", 1) > 1:
            if yahoo_sym not in cols.get_level_values(0):
                return []
            sub = df[yahoo_sym]
        else:
            sub = df
        out = []
        for ts, row in sub.iterrows():
            out.append({
                "date": ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10],
                "open": row.get("Open"), "high": row.get("High"), "low": row.get("Low"),
                "close": row.get("Close"), "volume": row.get("Volume"),
            })
        return out
    except Exception as exc:  # a malformed frame is a missing symbol, not a crash
        logger.warning("screener: could not read bars for %s: %s", yahoo_sym, exc)
        return []


def _download(symbols: list[str]):
    import yfinance as yf  # type: ignore
    return yf.download(tickers=" ".join(symbols), period=PERIOD, interval="1d", group_by="ticker",
                       auto_adjust=True, threads=True, progress=False)


def build_rows(constituents: list[dict], download: Callable[[list[str]], object],
               now: Optional[datetime] = None) -> tuple[list[dict], list[str]]:
    """Rows and missing symbols for the universe. `download` is injectable for tests."""
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    missing: list[str] = []
    for i in range(0, len(constituents), BATCH):
        chunk = constituents[i:i + BATCH]
        ysyms = [yahoo_symbol(c["symbol"]) for c in chunk]
        try:
            df = download(ysyms)
        except Exception as exc:
            logger.warning("screener: batch %d failed: %s", i // BATCH, exc)
            df = None
        for c, ys in zip(chunk, ysyms):
            bars = completed_sessions(clean_bars(frame_to_bars(df, ys)), now)
            m = session_metrics(bars)
            if m is None:
                missing.append(c["symbol"])
                continue
            rows.append({"symbol": c["symbol"], "name": c.get("name"), "sector": c.get("sector"), **m})
    return rows, missing


class Snapshot:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.missing: list[str] = []
        self.computed_at: Optional[str] = None
        self.built_mono: float = 0.0
        self.error: Optional[str] = None
        self.task: Optional[asyncio.Task] = None

    @property
    def stale(self) -> bool:
        return not self.computed_at or (_time.monotonic() - self.built_mono) > TTL_S

    @property
    def refreshing(self) -> bool:
        return self.task is not None and not self.task.done()

    async def _refresh(self, constituents: list[dict]) -> None:
        loop = asyncio.get_running_loop()
        try:
            rows, missing = await loop.run_in_executor(None, build_rows, constituents, _download)
            if not rows:
                raise RuntimeError("no symbol returned usable daily bars")
            self.rows, self.missing = rows, missing
            self.computed_at = datetime.now(timezone.utc).isoformat()
            self.built_mono = _time.monotonic()
            self.error = None
        except Exception as exc:
            logger.warning("screener refresh failed: %s", exc)
            self.error = "The market-data refresh failed."

    def ensure_fresh(self, constituents: list[dict]) -> None:
        if constituents and self.stale and not self.refreshing:
            self.task = asyncio.get_running_loop().create_task(self._refresh(constituents))

    def view(self, universe: dict) -> dict:
        n = len(universe["constituents"])
        if not n:
            state = "no_universe"
        elif self.computed_at:
            state = "ready"
        elif self.error:
            state = "error"
        else:
            state = "warming"
        return {
            "state": state,
            "refreshing": self.refreshing,
            "error": self.error,
            "computed_at": self.computed_at,
            "universe": {"name": "S&P 500", "count": n, "source": universe["source"], "retrieved": universe["retrieved"]},
            "data": "Yahoo Finance daily bars, delayed; last completed session",
            "rows": self.rows,
            "missing": self.missing,
        }


SNAPSHOT = Snapshot()


def current_view() -> dict:
    universe = load_universe()
    SNAPSHOT.ensure_fresh(universe["constituents"])
    return SNAPSHOT.view(universe)
