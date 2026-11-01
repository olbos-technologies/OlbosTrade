"""C++ accelerator for scripts/vwap_reversion_backtest.py's simulate().

Research only. Compiles scripts/cpp/vwap_sim.cpp with the system g++ into a
cache directory (keyed by the source's hash, so editing the C++ rebuilds it) and
calls it through ctypes. No pybind11, no CMake, nothing installed.

The Python `simulate` is the reference. This returns the same DataFrame, row for
row, and tests/test_vwap_reversion_cpp.py fails if it ever does not. It is faster
only; it is not a second opinion.

Needs a C++17 compiler on the machine that runs the research. The production
backend image does not need one — export the bars with intraday_bars_fetch.py
and run the backtest anywhere.
"""
from __future__ import annotations

import ctypes
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parent / "cpp" / "vwap_sim.cpp"
BAR_MIN = 5
MAX_WINDOWS = 4
_REASONS = ["stop_gap", "stop", "target", "flatten", "time"]
_COLUMNS = ["symbol", "side", "entry_ts", "exit_ts", "entry", "exit",
            "net_pct", "r", "reason", "date"]

_lib: Optional[ctypes.CDLL] = None
_cache: dict = {}


class CompilerUnavailable(RuntimeError):
    pass


def compiler() -> Optional[str]:
    return shutil.which("g++") or shutil.which("c++") or shutil.which("clang++")


def build(force: bool = False, src: Path = SRC) -> Path:
    """Compile to <tmp>/olbos_vwap_sim_<hash>/libvwapsim.so and return its path."""
    cxx = compiler()
    if cxx is None:
        raise CompilerUnavailable("no g++/c++/clang++ on PATH")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    out_dir = Path(tempfile.gettempdir()) / f"olbos_vwap_sim_{digest}"
    out_dir.mkdir(exist_ok=True)
    lib = out_dir / "libvwapsim.so"
    if force or not lib.exists():
        # -ffp-contract=off: no fused multiply-add, so every expression rounds
        # exactly as the Python (and numpy) float64 arithmetic does.
        subprocess.run(
            [cxx, "-O2", "-std=c++17", "-fPIC", "-shared", "-ffp-contract=off",
             "-o", str(lib), str(src)],
            check=True, capture_output=True, text=True,
        )
    return lib


def _load(lib_path: Optional[Path] = None) -> ctypes.CDLL:
    global _lib
    if _lib is not None and lib_path is None:
        return _lib
    lib = ctypes.CDLL(str(lib_path or build()))
    dp, i32p, i64p = (ctypes.POINTER(t) for t in (ctypes.c_double, ctypes.c_int32, ctypes.c_int64))
    lib.vwap_simulate.restype = ctypes.c_int
    lib.vwap_simulate.argtypes = [
        ctypes.c_int, i32p, i32p,
        dp, dp, dp, dp, dp, dp, dp, dp, i32p, i64p,
        dp, ctypes.c_int,
        i32p, i32p, i32p, i32p, dp, dp, dp, dp, i32p,
    ]
    if lib_path is None:
        _lib = lib
    return lib


def _params_array(p) -> np.ndarray:
    windows = list(p.windows)
    if len(windows) > MAX_WINDOWS:
        raise ValueError(f"at most {MAX_WINDOWS} signal windows are supported")
    flat = [x for w in windows for x in w]
    flat += [0.0] * (2 * MAX_WINDOWS - len(flat))
    return np.array([
        p.z_entry, p.stop_pct, p.target_pct, float(p.time_stop_min),
        1.0 if p.trend_filter == "ema_slope" else 0.0,
        p.atr_lo, p.atr_hi, p.vol_min, p.be_trigger_pct, p.slippage, p.commission,
        p.cost_mult, p.tick, 1.0 if "long" in p.sides else 0.0,
        1.0 if "short" in p.sides else 0.0, float(len(windows)), *flat,
    ], dtype=np.float64)


class _Arrays:
    """Contiguous column arrays for one features frame, built once and reused."""

    def __init__(self, feats: pd.DataFrame):
        self.feats = feats                      # keep alive: the cache keys on id()
        f = feats
        self.n = len(f)
        col = lambda name: np.ascontiguousarray(f[name].to_numpy(np.float64))
        self.o, self.h, self.l, self.c = (col(k) for k in ("open", "high", "low", "close"))
        self.z, self.atr, self.vol, self.slope = (
            col(k) for k in ("z", "atr_ratio", "vol_ratio", "h_slope"))
        self.end_min = np.ascontiguousarray(f["end_min"].to_numpy(np.int32))
        # minutes since the epoch, for the time stop — integer, so no rounding.
        self.epoch_min = np.ascontiguousarray(
            (f["end"].dt.tz_convert("UTC").astype("int64") // 60_000_000_000).to_numpy(np.int64))
        key = f["symbol"].astype(str) + "|" + f["date"].astype(str)
        change = np.flatnonzero(key.to_numpy()[1:] != key.to_numpy()[:-1]) + 1 if self.n else np.array([], int)
        starts = np.concatenate([[0], change]).astype(np.int32) if self.n else np.array([], np.int32)
        self.day_start = np.ascontiguousarray(starts)
        self.day_end = np.ascontiguousarray(np.append(starts[1:], self.n).astype(np.int32)) if self.n else starts
        self.n_days = len(self.day_start)
        self.symbol = f["symbol"].to_numpy()
        self.end = f["end"].reset_index(drop=True)
        self.date = f["date"].to_numpy()


def _arrays(feats: pd.DataFrame) -> _Arrays:
    hit = _cache.get(id(feats))
    if hit is not None and hit.feats is feats:
        return hit
    if len(_cache) >= 4:
        _cache.pop(next(iter(_cache)))
    _cache[id(feats)] = a = _Arrays(feats)
    return a


def simulate(feats: pd.DataFrame, p, *, lib: Optional[ctypes.CDLL] = None) -> pd.DataFrame:
    """Drop-in for vwap_reversion_backtest.simulate. Same rows, same dtypes."""
    if feats.empty:
        return pd.DataFrame(columns=_COLUMNS)
    a = _arrays(feats)          # positional arrays: the frame's index does not matter
    lib = lib or _load()
    cap = max(a.n // 2 + 1, 1)
    out = {
        "day": np.empty(cap, np.int32), "j": np.empty(cap, np.int32), "k": np.empty(cap, np.int32),
        "side": np.empty(cap, np.int32), "entry": np.empty(cap), "exit": np.empty(cap),
        "net": np.empty(cap), "r": np.empty(cap), "reason": np.empty(cap, np.int32),
    }
    prm = _params_array(p)
    dp, i32p, i64p = (ctypes.POINTER(t) for t in (ctypes.c_double, ctypes.c_int32, ctypes.c_int64))
    d = lambda x: x.ctypes.data_as(dp)
    i = lambda x: x.ctypes.data_as(i32p)
    n = lib.vwap_simulate(
        a.n_days, i(a.day_start), i(a.day_end),
        d(a.o), d(a.h), d(a.l), d(a.c), d(a.z), d(a.atr), d(a.vol), d(a.slope),
        i(a.end_min), a.epoch_min.ctypes.data_as(i64p),
        d(prm), cap,
        i(out["day"]), i(out["j"]), i(out["k"]), i(out["side"]),
        d(out["entry"]), d(out["exit"]), d(out["net"]), d(out["r"]), i(out["reason"]),
    )
    if n < 0:
        raise RuntimeError("trade buffer overflow in the C++ simulator")
    if n == 0:
        return pd.DataFrame([], columns=_COLUMNS)    # built exactly as the reference builds it
    j, k = out["j"][:n], out["k"][:n]
    bar = pd.Timedelta(minutes=BAR_MIN)
    return pd.DataFrame({
        "symbol": a.symbol[j],
        "side": np.where(out["side"][:n] == 1, "long", "short"),
        "entry_ts": (a.end.iloc[j] - bar).to_numpy(),
        "exit_ts": a.end.iloc[k].to_numpy(),
        "entry": out["entry"][:n], "exit": out["exit"][:n],
        "net_pct": out["net"][:n], "r": out["r"][:n],
        "reason": np.array(_REASONS)[out["reason"][:n]],
        "date": a.date[j],
    }, columns=_COLUMNS)
