"""Market breadth report: how many stocks are above their own 50-day average, and what that says.

Read-only. It fetches daily closes (through the app's DataFetcher, like the other study scripts),
calls the pure arithmetic in app/services/market_breadth.py, and prints. It writes nothing to the
database, places nothing, and feeds no risk or execution path.

The default universe is a built-in list of liquid large caps across sectors. That is a PROXY for
the market, not the S&P 500: the printed name count says how many were really used. Pass
--universe FILE (one symbol per line, '#' starts a comment) to use a wider list.

How to read it: it describes breadth. The turning points are found in hindsight (a point needs
bars after it), so they show where past tops and bottoms were, never that one is happening now.
Whether breadth improves our trades is for the Edge Lab ledger to test, not for this script to say.

Usage (on the server, inside the backend container):
    python scripts/market_breadth_report.py [--days 700] [--universe symbols.txt] [--csv out.csv]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

warnings.filterwarnings("ignore")

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import market_breadth as mb  # noqa: E402

DEFAULT_UNIVERSE = """
AAPL MSFT NVDA AMZN GOOGL META AVGO ORCL ADBE CRM AMD INTC CSCO QCOM TXN IBM NOW INTU MU
JPM BAC WFC GS MS C BLK SCHW AXP V MA PYPL SPGI
UNH JNJ LLY PFE MRK ABBV TMO ABT DHR BMY AMGN GILD MDT CVS ISRG
XOM CVX COP SLB EOG OXY PSX MPC
PG KO PEP WMT COST MCD NKE SBUX TGT HD LOW DIS NFLX TSLA
CAT DE HON UPS BA GE LMT RTX MMM UNP FDX
NEE DUK SO D AEP LIN APD SHW FCX NEM PLD AMT CCI
""".split()


def read_universe(path: str | None) -> list[str]:
    if not path:
        return list(dict.fromkeys(DEFAULT_UNIVERSE))
    syms: list[str] = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip().upper()
        if line:
            syms.extend(line.replace(",", " ").split())
    return list(dict.fromkeys(syms))


async def fetch_closes(symbols: list[str], start: str, end: str) -> tuple[pd.DataFrame, list[str]]:
    """A date-by-symbol table of closes, and the symbols that returned nothing."""
    from app.services.data_fetcher import DataFetcher
    fetcher = DataFetcher(broker=MagicMock())   # no broker here: every symbol resolves via the yfinance fallback
    cols: dict[str, pd.Series] = {}
    failed: list[str] = []
    for i, sym in enumerate(symbols, 1):
        try:
            df = await fetcher.fetch_ohlcv(sym, start, end)
            if df is None or df.empty:
                failed.append(sym)
            else:
                cols[sym] = df["Close"]
        except Exception as exc:  # one bad symbol must not stop the run, but it must be reported
            failed.append(sym)
            print(f"  {sym}: FAILED {type(exc).__name__}", file=sys.stderr)
        if i % 20 == 0:
            print(f"  fetched {i}/{len(symbols)}", flush=True)
    return pd.DataFrame(cols), failed


def render(table: pd.DataFrame, reading: mb.BreadthReading, failed: list[str]) -> str:
    out: list[str] = []
    out.append(f"Market breadth as of {reading.as_of.date() if hasattr(reading.as_of, 'date') else reading.as_of}")
    out.append(f"  universe: {reading.n_universe} names asked for, {reading.n_valid} with data on the latest day"
               + (f", {len(failed)} returned nothing: {', '.join(failed[:12])}{' ...' if len(failed) > 12 else ''}" if failed else ""))
    out.append(f"  state: {reading.state.upper()}")
    for r in reading.reasons:
        out.append(f"    - {r}")
    if reading.state != mb.UNKNOWN:
        slow = f"{reading.slow:.0%}" if reading.slow is not None else "n/a (under 200 days of breadth)"
        out.append(f"  above 50-day today: {reading.pct_above:.0%}   8-day: {reading.fast:.0%}   200-day: {slow}")
    out.append("")
    tail = table.tail(10)
    out.append("  last 10 days   above50   8-day   valid")
    for d, r in tail.iterrows():
        pct = "  n/a" if pd.isna(r["pct_above"]) else f"{r['pct_above']:5.0%}"
        fast = "  n/a" if pd.isna(r["fast"]) else f"{r['fast']:5.0%}"
        out.append(f"  {d.date()}      {pct}    {fast}    {int(r['n_valid'])}")
    line, name, window, prom = (("slow", "200-day", 20, 0.10) if table["slow"].notna().sum() > 80
                                else ("fast", "8-day", 10, 0.15))
    pts = mb.turning_points(table[line], window=window, prominence=prom)
    out.append("")
    out.append(f"  turning points on the {name} line (found in hindsight; window {window} bars, swing at least {prom:.0%}):")
    out.append("    " + ("; ".join(f"{p.kind} {p.date.date()} {p.value:.0%}" for p in pts) if pts else "none in this history"))
    valid = table["fast"].dropna()
    if len(valid):
        wash = int((valid < mb.WASHOUT_BELOW).sum())
        out.append("")
        out.append(f"  washout days (8-day under {mb.WASHOUT_BELOW:.0%}): {wash} of {len(valid)} days with a reading")
    out.append("")
    out.append("This describes the market. It is not a trading signal, and no rule here has been tested for edge.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=700, help="calendar days of history (need ~365+ for the 200-day line)")
    ap.add_argument("--universe", help="file of symbols, one per line")
    ap.add_argument("--csv", help="also write the daily table here")
    a = ap.parse_args(argv)
    symbols = read_universe(a.universe)
    end = date.today()
    start = end - timedelta(days=a.days)
    print(f"Fetching {len(symbols)} symbols from {start} to {end} ...", flush=True)
    closes, failed = asyncio.run(fetch_closes(symbols, start.isoformat(), end.isoformat()))
    if closes.empty:
        print("No price data came back, so there is no reading.", file=sys.stderr)
        return 2
    # Count symbols that failed as part of the universe, so a feed that lost names cannot look complete.
    for sym in failed:
        closes[sym] = float("nan")
    table, reading = mb.summarise(closes)
    print(render(table, reading, failed))
    if a.csv:
        table.to_csv(a.csv)
        print(f"wrote {a.csv}")
    return 0 if reading.state != mb.UNKNOWN else 1


if __name__ == "__main__":
    raise SystemExit(main())
