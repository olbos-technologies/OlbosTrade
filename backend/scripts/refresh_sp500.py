#!/usr/bin/env python3
"""
Refresh app/data/sp500.json, the S&P 500 list the screener scans.

Reads the constituents table (id="constituents") from Wikipedia's "List of
S&P 500 companies", validates it, and writes the file only if it passes. Run it
from backend/:

    python scripts/refresh_sp500.py            # fetch and write
    python scripts/refresh_sp500.py --check    # fetch and validate, write nothing

Then review the diff in git like any other change. Index membership changes
several times a year; the screener page shows this file's retrieval date.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

SOURCE = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
OUT = Path(__file__).resolve().parents[1] / "app" / "data" / "sp500.json"
SYMBOL = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
MIN_ROWS, MAX_ROWS = 490, 520   # the index holds ~503 lines (some firms have two share classes)


class _Table(HTMLParser):
    """Collect the text of each cell of the table with id="constituents"."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0          # >0 while inside the target table
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.depth:
                self.depth += 1
            elif dict(attrs).get("id") == "constituents":
                self.depth = 1
        elif self.depth == 1 and tag == "tr":
            self._row = []
        elif self.depth == 1 and tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self.depth:
            self.depth -= 1
        elif self.depth == 1 and tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif self.depth == 1 and tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self.depth == 1 and self._cell is not None:
            self._cell.append(data)


def parse_constituents(html: str) -> list[dict]:
    p = _Table()
    p.feed(html)
    if not p.rows:
        raise ValueError('no table with id="constituents" found')
    header = [h.lower() for h in p.rows[0]]
    try:
        i_sym, i_name, i_sec = header.index("symbol"), header.index("security"), header.index("gics sector")
    except ValueError as exc:
        raise ValueError(f"unexpected table header: {p.rows[0]}") from exc
    out = []
    for r in p.rows[1:]:
        if len(r) <= max(i_sym, i_name, i_sec):
            continue
        out.append({"symbol": r[i_sym].strip().upper(), "name": r[i_name].strip(), "sector": r[i_sec].strip()})
    return out


def validate(rows: list[dict]) -> list[str]:
    problems = []
    if not MIN_ROWS <= len(rows) <= MAX_ROWS:
        problems.append(f"{len(rows)} rows, expected {MIN_ROWS}-{MAX_ROWS}")
    syms = [r["symbol"] for r in rows]
    bad = [s for s in syms if not SYMBOL.match(s)]
    if bad:
        problems.append(f"malformed symbols: {bad[:10]}")
    dupes = sorted({s for s in syms if syms.count(s) > 1})
    if dupes:
        problems.append(f"duplicate symbols: {dupes[:10]}")
    if any(not r["name"] or not r["sector"] for r in rows):
        problems.append("rows with an empty name or sector")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="validate only; do not write")
    args = ap.parse_args(argv)
    req = urllib.request.Request(SOURCE, headers={"User-Agent": "olbostrade-universe-refresh/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", "replace")
    rows = parse_constituents(html)
    problems = validate(rows)
    if problems:
        print("NOT written:", "; ".join(problems), file=sys.stderr)
        return 1
    rows.sort(key=lambda r: r["symbol"])
    print(f"{len(rows)} constituents, {len({r['sector'] for r in rows})} sectors")
    if args.check:
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": SOURCE,
        "retrieved": datetime.now(timezone.utc).date().isoformat(),
        "count": len(rows),
        "constituents": rows,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
