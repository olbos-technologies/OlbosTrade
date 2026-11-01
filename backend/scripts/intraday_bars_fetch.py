"""Export 5-minute regular-hours bars for the VWAP-reversion research.

Read-only. Connects to the IBKR gateway with `readonly=True` — the API session
is refused the ability to place or modify orders — and writes a CSV that
`scripts/vwap_reversion_backtest.py` reads. It never touches the database or any
trading code, and it uses its own client id so it cannot collide with the
backend's session.

    python scripts/intraday_bars_fetch.py bars.csv --client-id 17 \
        --symbols QQQ SPY --start 2023-01-01 --end 2026-10-30

Run it on the server, against the paper gateway, outside market hours if you can.

IBKR limits (verify against your own gateway — these are from the API's
documented pacing rules and were NOT exercised in the environment this was
written in): no more than 60 historical requests per 10 minutes, and identical
requests are throttled. This asks for one week of 5-minute bars per request and
sleeps between requests, so 3 years of two symbols is roughly 300 requests and
takes about an hour. It can be interrupted and re-run: a week that already has
bars in the CSV for that symbol is not requested again (each week is written in
one piece, so a week is either complete or absent). Run it with `python -u` so
progress reaches a log file as it happens; redirected output is otherwise
buffered and a crash leaves an empty log.

Needs historical-data permission for US ETFs on the account. Bars come back as
TRADES (not adjusted for splits or dividends), which is fine for QQQ/SPY
intraday; do not reuse this for names that have split inside the window.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

FIELDS = ["symbol", "ts", "open", "high", "low", "close", "volume"]


def week_ends(start: date, end: date) -> list[date]:
    """Request end-dates, newest first, one per week, covering [start, end]."""
    if end < start:
        return []
    out, d = [], end
    while d >= start:
        out.append(d)
        d -= timedelta(days=7)
    return out


def bars_to_rows(symbol: str, bars: Iterable) -> list[list]:
    """Flatten ib_insync bars. `bar.date` is the bar START (UTC-aware with formatDate=2)."""
    rows = []
    for b in bars:
        ts = b.date if isinstance(b.date, datetime) else datetime.fromisoformat(str(b.date))
        rows.append([symbol, ts.isoformat(), b.open, b.high, b.low, b.close, int(b.volume)])
    return rows


def read_existing(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    with path.open(newline="") as f:
        return {(r["symbol"], r["ts"]) for r in csv.DictReader(f)}


def append_rows(path: Path, rows: list[list], seen: set[tuple[str, str]]) -> int:
    """Append rows not already present. Returns how many were new."""
    fresh = [r for r in rows if (r[0], r[1]) not in seen]
    if not fresh:
        return 0
    new_file = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(FIELDS)
        w.writerows(fresh)
    seen.update((r[0], r[1]) for r in fresh)
    return len(fresh)


def _say(msg: str) -> None:
    print(msg, flush=True)           # progress must reach a log file even if the process is killed


def have_week(seen: set[tuple[str, str]], symbol: str, week_end: date) -> bool:
    """True if any bar for `symbol` falls in the 7 calendar days ending `week_end`.

    A request for "1 W" ending on `week_end` returns those 7 days and is written
    in one piece, so any bar from the window means the whole week was saved.
    """
    lo = week_end - timedelta(days=6)
    for sym, ts in seen:
        if sym == symbol and lo <= date.fromisoformat(ts[:10]) <= week_end:
            return True
    return False


def fetch_symbol(
    request: Callable[[str, str], list], symbol: str, start: date, end: date,
    path: Path, *, sleep: float = 11.0, log: Callable[[str], None] = _say,
) -> int:
    """Walk backwards a week at a time. `request(symbol, end_datetime)` returns bars."""
    seen = read_existing(path)
    total = 0
    for d in week_ends(start, end):
        if have_week(seen, symbol, d):
            log(f"  {symbol} week ending {d}: already saved, skipped")
            continue
        end_dt = d.strftime("%Y%m%d") + " 23:59:59"
        try:
            bars = request(symbol, end_dt)
        except Exception as exc:                      # one bad week must not lose the rest
            log(f"  {symbol} {d}: FAILED {type(exc).__name__}: {exc}")
            time.sleep(sleep)
            continue
        n = append_rows(path, bars_to_rows(symbol, bars), seen)
        total += n
        log(f"  {symbol} week ending {d}: {len(bars)} bars, {n} new")
        time.sleep(sleep)
    return total


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out_csv")
    ap.add_argument("--client-id", type=int, required=True,
                    help="a client id NOT used by the backend (the backend uses IBKR_CLIENT_ID)")
    ap.add_argument("--symbols", nargs="+", default=["QQQ", "SPY"])
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=11.0)
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ib_insync import IB, Stock                    # noqa: E402  (not needed to import the helpers)
    from app.core.config import settings               # noqa: E402

    ib = IB()
    ib.connect(args.host or settings.ibkr_host, args.port or settings.ibkr_port,
               clientId=args.client_id, readonly=True, timeout=20)
    try:
        def request(symbol: str, end_dt: str) -> list:
            contract = Stock(symbol, "SMART", "USD")
            ib.qualifyContracts(contract)
            return ib.reqHistoricalData(
                contract, endDateTime=end_dt, durationStr="1 W", barSizeSetting="5 mins",
                whatToShow="TRADES", useRTH=True, formatDate=2,
            )

        out = Path(args.out_csv)
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        for sym in args.symbols:
            _say(f"{sym}:")
            _say(f"  {fetch_symbol(request, sym, start, end, out, sleep=args.sleep)} new bars")
    finally:
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
