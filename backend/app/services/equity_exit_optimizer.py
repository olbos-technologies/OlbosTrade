"""
Walk-forward sweep over equity exit geometry.

Grid-searches the two parameters that decide where an equity trade's stop
and target sit — `stop_atr_multiplier` and `target_atr_multiplier` on
EquitySignalParams — scoring each candidate against a REAL walk-forward
backtest (Backtester.run_equity()) over a training window, then validating
the winner out-of-sample on a following window. A candidate is accepted
only if the out-of-sample window confirms it.

This is the "eventual equity-side optimizer" that run_equity()'s
`equity_params` argument was added for and documented as waiting on; until
now nothing passed a non-default value, so the injection point was inert.

Why this exists
---------------
Production equity exits, 2026-08-01 → 2026-10-17, adopted positions
excluded (43 attributable closes). Converted into ATR units via
planned_risk = 2 x ATR x quantity, so one ATR of P&L = planned_risk / 2:

    exit_reason          n   days   peak unrealized      realized
    manual              13    8.5   1.12 x ATR           +1.05 x ATR
    closed_at_broker     6   13.2   1.14 x ATR           +0.10 x ATR
    position_rotation   22    0.6   0.06 x ATR           -0.11 x ATR
    stop_hit             2   11.0   0.23 x ATR           -2.00 x ATR

Both groups that were held for any length of time peaked at 1.12 and
1.14 x ATR, independently and within 2% of each other. The shipped target
sits at 4.0 x ATR — roughly 3.5x the best the price was ever observed to
do — while the stop sits at 2.0 x ATR and is reached, delivering exactly
-2.00 x ATR when it is. An unreachable target paired with a reachable stop
is negative expectancy from the geometry alone, independent of whether the
entry signal has any skill.

That is an inference from 43 trades, which is far too few to conclude
anything. This module exists so the multipliers can be chosen from a
backtest instead of from that inference. It does NOT change any default:
settings.equity_stop_atr_multiplier / equity_target_atr_multiplier are
untouched, and nothing here writes to config, the database, or a broker.

What it deliberately does not do
--------------------------------
  - **No live-path edits.** Read-only with respect to production. The
    output is a recommendation for a human to act on, nothing more.
  - **No max_position_pct axis.** That cap is a kwarg on
    compute_equity_trade_plan(), not a field on EquitySignalParams (see
    that dataclass's own docstring on why it is kept separate), and
    run_equity() does not thread it. It therefore stays at the production
    default of 0.08 for every grid point — which is the regime being
    measured, since all seven production stop-outs sized to ~$20k notional
    and so had that cap bind. Varying it needs a backtester change first.
  - **No claim about profit.** A geometry that scores better out-of-sample
    on this data is evidence about this data. See `accepted`'s docstring.

Targets below the stop are allowed in the grid on purpose. It looks wrong
and it is the entire hypothesis: if favourable excursion really is ~1.1 x
ATR, the profitable geometry may well take profit nearer than it stops out.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import product
from typing import Any, Optional, Sequence, TYPE_CHECKING

from app.services.equity_signal_engine import EquitySignalParams
from app.utils.logger import get_logger
from app.utils.metrics import calculate_all_metrics

if TYPE_CHECKING:  # pragma: no cover - import-cycle / heavy-dep avoidance
    # Runtime-importing Backtester would pull in data_fetcher -> `ta`, which
    # does not build in the cloud container (see CLAUDE.md). Keeping it
    # behind TYPE_CHECKING is the same convention strategy_optimizer.py
    # uses, and is what lets this module and its tests collect there.
    from app.services.backtester import BacktestResult

logger = get_logger(__name__)

# run_equity() force-closes any still-open position at the end of the
# window with this reason and a hardcoded pnl of 0.0. That row is an
# artifact of the window ending, not a trade outcome, and scoring it as a
# real break-even would drag every mean toward zero — more so for exactly
# the wide-target geometries under test, which leave positions open
# longest. Every metric below is computed on resolved trades only.
UNRESOLVED_EXIT_REASON = "backtest_end"
TARGET_EXIT_REASON = "target"
STOP_EXIT_REASON = "stop"

# Scoring a handful of trades produces a Sharpe that is mostly noise.
# Mirrors strategy_optimizer._score_params' own floor.
MIN_TRADES_TO_SCORE = 3
UNSCORABLE = -999.0


@dataclass(frozen=True)
class ExitGeometry:
    """Where the stop and target sit, in ATR multiples.

    Frozen so a geometry can be a dict key and cannot be mutated after a
    score has been attached to it.
    """

    stop_atr_multiplier: float
    target_atr_multiplier: float

    def __post_init__(self) -> None:
        # compute_equity_trade_plan() divides by |entry - stop|, falling
        # back to a raw ATR when that is zero; a non-positive multiplier
        # would put the stop at or through entry and make the fallback,
        # not the parameter, decide position size. Reject rather than
        # silently score a geometry the engine would not honour.
        if self.stop_atr_multiplier <= 0:
            raise ValueError(
                f"stop_atr_multiplier must be > 0, got {self.stop_atr_multiplier}"
            )
        if self.target_atr_multiplier <= 0:
            raise ValueError(
                f"target_atr_multiplier must be > 0, got {self.target_atr_multiplier}"
            )

    @property
    def label(self) -> str:
        return (
            f"stop={self.stop_atr_multiplier:g}xATR/"
            f"target={self.target_atr_multiplier:g}xATR"
        )

    @property
    def planned_reward_risk(self) -> float:
        """The reward:risk the geometry claims, before any fill reality.

        Shipped defaults claim 2.0 here. The production table in this
        module's docstring realised 0.31. The gap, not this number, is
        the thing being investigated.
        """
        return self.target_atr_multiplier / self.stop_atr_multiplier

    def apply(self, base: Optional[EquitySignalParams] = None) -> EquitySignalParams:
        """This geometry over `base`, leaving every other tunable alone.

        dataclasses.replace rather than a fresh EquitySignalParams(), so a
        caller sweeping geometry on top of already-tuned scoring weights
        keeps those weights.
        """
        return dataclasses.replace(
            base if base is not None else EquitySignalParams(),
            stop_atr_multiplier=self.stop_atr_multiplier,
            target_atr_multiplier=self.target_atr_multiplier,
        )


@dataclass
class GridPoint:
    """One geometry's pooled result over one window."""

    geometry: ExitGeometry
    sharpe: float
    n_trades: int
    n_unresolved: int
    win_rate: float
    expectancy: float
    profit_factor: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    target_hit_rate: float
    stop_hit_rate: float
    avg_hold_days: float

    @property
    def realized_reward_risk(self) -> float:
        """avg_win / |avg_loss| — the ratio actually achieved.

        This is the number the production data put at 0.31 against a
        planned 2.0, so it is the one to compare a candidate against.
        Zero when there were no losses to divide by, which with real
        trade counts means the sample is too small to read.
        """
        if not self.avg_loss:
            return 0.0
        return abs(self.avg_win) / abs(self.avg_loss)

    @property
    def is_scorable(self) -> bool:
        return self.n_trades >= MIN_TRADES_TO_SCORE


@dataclass
class ExitSweepResult:
    """Outcome of one walk-forward sweep."""

    tickers: list[str]
    train_start: str
    train_end: str
    validate_start: str
    validate_end: str
    grid_points_tested: int

    baseline: ExitGeometry
    best_on_train: Optional[GridPoint]
    train_results: list[GridPoint]

    baseline_on_validate: Optional[GridPoint]
    best_on_validate: Optional[GridPoint]

    accepted: bool
    rejection_reason: Optional[str] = None
    completed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def recommended(self) -> ExitGeometry:
        """The geometry to actually use — the candidate only if accepted.

        `accepted` means this sweep's out-of-sample window confirmed the
        improvement on this ticker set over this period. It is not a
        prediction, and it is not authority to change a production
        parameter; a person decides that, with the sample sizes in view.
        """
        if self.accepted and self.best_on_train is not None:
            return self.best_on_train.geometry
        return self.baseline

    @property
    def validation_sharpe_gain(self) -> Optional[float]:
        if self.best_on_validate is None or self.baseline_on_validate is None:
            return None
        return self.best_on_validate.sharpe - self.baseline_on_validate.sharpe


class _MemoFetcher:
    """Delegating proxy that memoizes fetch_ohlcv for one sweep.

    A grid point is a backtest, and every backtest over the same window
    re-fetches byte-identical OHLCV: a 12-point grid over 7 tickers is 84
    fetches of 7 distinct frames. DataFetcher has no in-memory cache
    (cache_to_db is a separate, explicit DB write), so the sweep memoizes
    here instead.

    Everything other than fetch_ohlcv falls through by __getattr__, so this
    stays a proxy rather than a reimplementation and does not have to track
    DataFetcher's surface.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._ohlcv: dict[tuple[str, str, str], Any] = {}
        self.hits = 0
        self.misses = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def fetch_ohlcv(self, ticker: str, start: str, end: str, *args: Any, **kwargs: Any) -> Any:
        key = (str(ticker).upper(), str(start), str(end))
        if key in self._ohlcv:
            self.hits += 1
            return self._ohlcv[key]
        self.misses += 1
        df = await self._inner.fetch_ohlcv(ticker, start, end, *args, **kwargs)
        # A copy per caller would be safer against a backtest mutating the
        # frame in place, but run_equity only reads and reassigns .columns
        # on its own local; paying a deep copy per grid point to guard a
        # mutation that does not happen is the worse trade.
        self._ohlcv[key] = df
        return df


def expand_grid(grid: dict[str, Sequence[float]]) -> list[ExitGeometry]:
    """Cartesian product of the grid axes, as validated geometries.

    Order is deterministic (stop-major) so two runs of the same grid test
    candidates in the same order and ties break identically.
    """
    stops = list(grid.get("stop_atr_multiplier", ()))
    targets = list(grid.get("target_atr_multiplier", ()))
    if not stops or not targets:
        raise ValueError(
            "grid needs non-empty stop_atr_multiplier and "
            "target_atr_multiplier axes"
        )
    return [
        ExitGeometry(stop_atr_multiplier=s, target_atr_multiplier=t)
        for s, t in product(stops, targets)
    ]


def resolved_trades(results: Sequence["BacktestResult"]) -> list[Any]:
    """Every trade across the pooled results, minus window-end artifacts."""
    out: list[Any] = []
    for r in results:
        for t in getattr(r, "trades", ()) or ():
            if getattr(t, "exit_reason", None) != UNRESOLVED_EXIT_REASON:
                out.append(t)
    return out


def unresolved_count(results: Sequence["BacktestResult"]) -> int:
    return sum(
        1
        for r in results
        for t in (getattr(r, "trades", ()) or ())
        if getattr(t, "exit_reason", None) == UNRESOLVED_EXIT_REASON
    )


def score_pooled(
    geometry: ExitGeometry,
    results: Sequence["BacktestResult"],
    *,
    starting_capital: float,
) -> GridPoint:
    """Pool every ticker's trades into one GridPoint.

    Pooled rather than averaged per ticker: a mean of per-ticker Sharpes
    weights a 2-trade ticker the same as a 40-trade one. Pooling also
    matches how the portfolio actually experiences the geometry — one
    account, all names.
    """
    trades = resolved_trades(results)
    unresolved = unresolved_count(results)
    pnls = [float(getattr(t, "pnl", 0.0) or 0.0) for t in trades]
    holds = [int(getattr(t, "hold_days", 0) or 0) for t in trades]
    reasons = [getattr(t, "exit_reason", None) for t in trades]
    n = len(trades)

    if n < MIN_TRADES_TO_SCORE:
        return GridPoint(
            geometry=geometry, sharpe=UNSCORABLE, n_trades=n,
            n_unresolved=unresolved, win_rate=0.0, expectancy=0.0,
            profit_factor=0.0, total_pnl=sum(pnls), avg_win=0.0, avg_loss=0.0,
            target_hit_rate=0.0, stop_hit_rate=0.0, avg_hold_days=0.0,
        )

    # Equity commissions are recorded per trade by run_equity, so sum them
    # rather than reusing strategy_optimizer's 4-leg options estimate,
    # which would overstate an equity fill's cost by roughly an order of
    # magnitude.
    commissions = sum(
        float(getattr(t, "commission_paid", 0.0) or 0.0) for t in trades
    )
    metrics = calculate_all_metrics(
        pnl_series=pnls,
        hold_days=holds,
        starting_capital=starting_capital,
        total_commissions=commissions,
    )
    return GridPoint(
        geometry=geometry,
        sharpe=float(metrics.sharpe_ratio),
        n_trades=n,
        n_unresolved=unresolved,
        win_rate=float(metrics.win_rate),
        expectancy=float(metrics.expectancy),
        profit_factor=float(metrics.profit_factor),
        total_pnl=sum(pnls),
        avg_win=float(metrics.avg_win),
        avg_loss=float(metrics.avg_loss),
        target_hit_rate=reasons.count(TARGET_EXIT_REASON) / n,
        stop_hit_rate=reasons.count(STOP_EXIT_REASON) / n,
        avg_hold_days=float(metrics.avg_hold_days),
    )


class EquityExitOptimizer:
    """Walk-forward sweep over equity stop/target geometry.

    Scores every grid point against real Backtester.run_equity() calls on a
    training window, then validates the winner out-of-sample. Submits no
    orders, writes no config, touches no database.
    """

    # Full grid — 42 points, each one backtest per ticker. Opt in via
    # sweep(full_grid=True); wall-clock has not been measured, and with
    # one yfinance fetch per distinct (ticker, window) it is the backtests,
    # not the network, that dominate.
    PARAM_GRID: dict[str, Sequence[float]] = {
        "stop_atr_multiplier":   [0.75, 1.0, 1.5, 2.0, 2.5, 3.0],
        "target_atr_multiplier": [0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0],
    }
    # Default grid — 12 points, spanning "target far nearer than the stop"
    # through the shipped geometry. Both shipped values (stop 2.0, target
    # 4.0) are present, so the baseline is always itself a grid point and
    # the comparison is like-for-like rather than against a re-run.
    REDUCED_PARAM_GRID: dict[str, Sequence[float]] = {
        "stop_atr_multiplier":   [1.0, 1.5, 2.0],
        "target_atr_multiplier": [1.0, 1.5, 2.0, 4.0],
    }

    MIN_VALIDATION_TRADES = 8
    # An absolute Sharpe gain, not the percentage gate strategy_optimizer
    # uses. Deliberate divergence: percentage improvement over a baseline
    # whose Sharpe is near zero or negative is unstable to the point of
    # meaninglessness — and the production baseline this was built for is
    # losing money, so that is the expected case, not an edge case. A
    # fixed gain in Sharpe units stays interpretable through zero.
    MIN_VALIDATION_SHARPE_GAIN = 0.20
    # How much of the training-window edge may evaporate out-of-sample
    # before the candidate reads as fitted to the training window.
    MAX_TRAIN_TO_VALIDATE_DECAY = 0.75

    def __init__(
        self,
        backtester: Any,
        *,
        base_params: Optional[EquitySignalParams] = None,
        starting_capital: float = 25000.0,
    ) -> None:
        self.backtester = backtester
        self.base_params = base_params or EquitySignalParams()
        self.starting_capital = starting_capital

    @property
    def baseline_geometry(self) -> ExitGeometry:
        """The geometry currently in force, read off base_params.

        Read from the params rather than hardcoded, so a sweep run against
        non-default settings compares to what is actually shipped there.
        """
        return ExitGeometry(
            stop_atr_multiplier=self.base_params.stop_atr_multiplier,
            target_atr_multiplier=self.base_params.target_atr_multiplier,
        )

    @staticmethod
    def windows(
        end_date: Optional[str] = None,
        train_months: int = 6,
        validate_months: int = 1,
    ) -> tuple[str, str, str, str]:
        """(train_start, train_end, validate_start, validate_end).

        Validation sits immediately after training and ends at end_date, so
        the out-of-sample window is strictly later than everything the
        winner was chosen on. 30-day months, matching
        strategy_optimizer.optimize()'s own calendar arithmetic.
        """
        if train_months <= 0 or validate_months <= 0:
            raise ValueError("train_months and validate_months must be > 0")
        end = (
            datetime.now(timezone.utc).date()
            if end_date is None
            else datetime.strptime(end_date, "%Y-%m-%d").date()
        )
        validate_start = end - timedelta(days=30 * validate_months)
        train_start = validate_start - timedelta(days=30 * train_months)
        return (
            train_start.isoformat(),
            validate_start.isoformat(),
            validate_start.isoformat(),
            end.isoformat(),
        )

    async def _score_geometry(
        self,
        geometry: ExitGeometry,
        tickers: Sequence[str],
        start: str,
        end: str,
    ) -> GridPoint:
        params = geometry.apply(self.base_params)
        results: list[Any] = []
        for ticker in tickers:
            try:
                results.append(
                    await self.backtester.run_equity(
                        ticker, start, end,
                        starting_capital=self.starting_capital,
                        equity_params=params,
                    )
                )
            except Exception as exc:
                # One ticker with no data must not void the whole grid
                # point — but it must not silently read as "this geometry
                # produced no trades on it" either, which would make a
                # broken fetch look like a conservative geometry.
                logger.warning(
                    "exit-sweep: %s %s %s..%s failed, excluded from this "
                    "grid point: %s", geometry.label, ticker, start, end, exc,
                )
        return score_pooled(
            geometry, results, starting_capital=self.starting_capital,
        )

    async def sweep(
        self,
        tickers: Sequence[str],
        *,
        end_date: Optional[str] = None,
        train_months: int = 6,
        validate_months: int = 1,
        full_grid: bool = False,
        grid: Optional[dict[str, Sequence[float]]] = None,
    ) -> ExitSweepResult:
        """Grid-search on train, validate the winner out-of-sample.

        1. Split [train | validate], validate strictly after train.
        2. Score every geometry on train, pooled across tickers.
        3. Take the best training Sharpe.
        4. Re-score that winner AND the baseline on validate.
        5. Accept only if the out-of-sample gain clears
           MIN_VALIDATION_SHARPE_GAIN and did not mostly decay from train.
        """
        tickers = [str(t).upper() for t in tickers]
        if not tickers:
            raise ValueError("sweep needs at least one ticker")

        chosen_grid = grid or (
            self.PARAM_GRID if full_grid else self.REDUCED_PARAM_GRID
        )
        geometries = expand_grid(chosen_grid)
        train_start, train_end, validate_start, validate_end = self.windows(
            end_date, train_months, validate_months,
        )
        baseline = self.baseline_geometry

        logger.info(
            "exit-sweep: %d geometries x %d tickers, train %s..%s, "
            "validate %s..%s", len(geometries), len(tickers),
            train_start, train_end, validate_start, validate_end,
        )

        inner_fetcher = getattr(self.backtester, "fetcher", None)
        memo = _MemoFetcher(inner_fetcher) if inner_fetcher is not None else None
        if memo is not None:
            self.backtester.fetcher = memo
        try:
            train_results = [
                await self._score_geometry(g, tickers, train_start, train_end)
                for g in geometries
            ]
            scorable = [p for p in train_results if p.is_scorable]
            best_on_train = (
                max(scorable, key=lambda p: p.sharpe) if scorable else None
            )

            baseline_on_validate: Optional[GridPoint] = None
            best_on_validate: Optional[GridPoint] = None
            if best_on_train is not None:
                baseline_on_validate = await self._score_geometry(
                    baseline, tickers, validate_start, validate_end,
                )
                best_on_validate = (
                    baseline_on_validate
                    if best_on_train.geometry == baseline
                    else await self._score_geometry(
                        best_on_train.geometry, tickers,
                        validate_start, validate_end,
                    )
                )
        finally:
            # Restored even when a backtest raises, so a failed sweep does
            # not leave a memo (and its retained frames) attached to a
            # backtester the caller goes on using.
            if memo is not None:
                self.backtester.fetcher = inner_fetcher
                logger.info(
                    "exit-sweep: OHLCV fetches %d, memo hits %d",
                    memo.misses, memo.hits,
                )

        accepted, reason = self._decide(
            baseline, best_on_train, train_results,
            baseline_on_validate, best_on_validate,
        )
        return ExitSweepResult(
            tickers=tickers,
            train_start=train_start, train_end=train_end,
            validate_start=validate_start, validate_end=validate_end,
            grid_points_tested=len(geometries),
            baseline=baseline,
            best_on_train=best_on_train,
            train_results=train_results,
            baseline_on_validate=baseline_on_validate,
            best_on_validate=best_on_validate,
            accepted=accepted,
            rejection_reason=reason,
        )

    def _decide(
        self,
        baseline: ExitGeometry,
        best_on_train: Optional[GridPoint],
        train_results: Sequence[GridPoint],
        baseline_on_validate: Optional[GridPoint],
        best_on_validate: Optional[GridPoint],
    ) -> tuple[bool, Optional[str]]:
        """Accept or reject, with the reason spelled out on a reject."""
        if best_on_train is None:
            return False, (
                f"no geometry produced {MIN_TRADES_TO_SCORE}+ resolved "
                f"trades on the training window — nothing to score"
            )
        if best_on_train.geometry == baseline:
            return False, (
                "the shipped geometry already won the training window — "
                "no change to recommend"
            )
        if best_on_validate is None or baseline_on_validate is None:
            return False, "validation window did not run"
        if best_on_validate.n_trades < self.MIN_VALIDATION_TRADES:
            return False, (
                f"only {best_on_validate.n_trades} out-of-sample trades "
                f"(need {self.MIN_VALIDATION_TRADES}) — cannot confirm"
            )
        gain = best_on_validate.sharpe - baseline_on_validate.sharpe
        if gain < self.MIN_VALIDATION_SHARPE_GAIN:
            return False, (
                f"out-of-sample Sharpe gain {gain:+.2f} below the "
                f"{self.MIN_VALIDATION_SHARPE_GAIN:.2f} minimum — "
                f"keeping the shipped geometry"
            )
        # The baseline is always itself a grid point (see
        # REDUCED_PARAM_GRID), so its training score is in train_results
        # rather than needing a re-run. When it is absent or unscorable
        # the decay check has no denominator and is skipped — silently
        # treating an unscorable baseline as Sharpe -999 would make every
        # training gain look enormous and reject every candidate.
        baseline_train = next(
            (p for p in train_results
             if p.geometry == baseline and p.is_scorable),
            None,
        )
        train_gain = (
            best_on_train.sharpe - baseline_train.sharpe
            if baseline_train is not None else None
        )
        # No `train_gain > 0` guard: best_on_train is the max Sharpe over the
        # scorable points and baseline_train is non-None only when the
        # baseline is one of them, so train_gain cannot be negative. At
        # exactly zero the comparison below is `gain < 0`, which a candidate
        # that already cleared MIN_VALIDATION_SHARPE_GAIN never satisfies —
        # so the guard was unreachable in one direction and a no-op in the
        # other. It was removed because no test could distinguish it.
        if (train_gain is not None
                and gain < train_gain * (1.0 - self.MAX_TRAIN_TO_VALIDATE_DECAY)):
            return False, (
                f"out-of-sample gain {gain:+.2f} retained less than "
                f"{(1.0 - self.MAX_TRAIN_TO_VALIDATE_DECAY):.0%} of the "
                f"training gain {train_gain:+.2f} — reads as fitted to the "
                f"training window"
            )
        return True, None
