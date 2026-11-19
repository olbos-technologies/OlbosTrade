"""
The S&P 500 constituent list the screener scans.

Stored as a dated, sourced file (app/data/sp500.json) rather than fetched at
request time, so the screener never depends on a third-party page being up and
the list in use can be reviewed in git. Refresh it with
`python scripts/refresh_sp500.py`, which also validates it. Index membership
changes several times a year, so the page shows the list's retrieval date.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "sp500.json"


def yahoo_symbol(symbol: str) -> str:
    """Yahoo writes share classes with a dash: BRK.B -> BRK-B."""
    return symbol.replace(".", "-")


@lru_cache(maxsize=1)
def load_universe() -> dict:
    """{"source", "retrieved", "constituents": [{"symbol", "name", "sector"}]}. Empty list if missing."""
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"source": None, "retrieved": None, "constituents": []}
    rows = [c for c in data.get("constituents", []) if isinstance(c, dict) and c.get("symbol")]
    return {"source": data.get("source"), "retrieved": data.get("retrieved"), "constituents": rows}
