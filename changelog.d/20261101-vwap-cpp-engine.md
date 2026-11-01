## Unreleased — Optional C++ engine for the VWAP-reversion research backtest

- `scripts/vwap_reversion_backtest.py --engine cpp` runs the trade simulation
  in C++ instead of Python. The Python version remains the reference: the C++
  twin must produce byte-identical trades, and `tests/test_vwap_reversion_cpp.py`
  fails if it does not (it also proves a deliberately broken build is detected).
- Research tooling only: no trading code, no schema change, no change to the
  default engine. Needs a C++ compiler on the machine that runs the research;
  the production backend image does not need one. If none is installed the
  differential test is skipped and says so.
