"""The breadth report script: universe reading and the printout, with no network."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from app.services import market_breadth as mb

_spec = importlib.util.spec_from_file_location("market_breadth_report", Path(__file__).resolve().parent.parent / "scripts" / "market_breadth_report.py")
rep = importlib.util.module_from_spec(_spec)
sys.modules["market_breadth_report"] = rep
_spec.loader.exec_module(rep)


def test_default_universe_is_unique_upper_case_and_not_tiny():
    u = rep.read_universe(None)
    assert len(u) == len(set(u)) and len(u) >= 60
    assert all(s == s.upper() and s.isalpha() for s in u)


def test_universe_file_allows_comments_commas_and_duplicates(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("aapl, msft  # tech\n\n# a whole-line comment\nAAPL\nnvda\n")
    assert rep.read_universe(str(f)) == ["AAPL", "MSFT", "NVDA"]


def test_render_names_the_universe_and_reports_failed_symbols():
    n = 330
    dates = pd.bdate_range("2024-01-01", periods=n)
    closes = pd.DataFrame({f"S{i}": np.linspace(100, 300, n) for i in range(40)}, index=dates)
    t, r = mb.summarise(closes)
    text = rep.render(t, r, failed=["BAD1"])
    assert "40 with data" in text and "BAD1" in text
    assert "HEALTHY" in text or "EXTENDED" in text
    assert "not a trading signal" in text


def test_render_of_an_unknown_reading_prints_the_reason_not_numbers():
    n = 330
    dates = pd.bdate_range("2024-01-01", periods=n)
    closes = pd.DataFrame({f"S{i}": np.linspace(100, 300, n) for i in range(5)}, index=dates)   # 5 names: too few
    t, r = mb.summarise(closes)
    text = rep.render(t, r, failed=[])
    assert "UNKNOWN" in text and "need 30" in text
    assert "above 50-day today" not in text


def test_failed_symbols_count_against_coverage():
    """A feed that lost most names must read UNKNOWN, as main() adds failed symbols to the universe."""
    n = 120
    dates = pd.bdate_range("2025-01-01", periods=n)
    closes = pd.DataFrame({f"S{i}": np.linspace(100, 200, n) for i in range(40)}, index=dates)
    for i in range(40):
        closes[f"F{i}"] = float("nan")                      # 40 more asked for, none returned
    _, r = mb.summarise(closes)
    assert r.state == mb.UNKNOWN and "missing" in r.reasons[0]
