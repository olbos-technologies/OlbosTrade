"""
A winner's excursion is measured past the target, not truncated at it.

`_resolve_one` used to RETURN at the target touch, so `max_favorable_pct` for a
target_hit row was capped at its own target. Every winner read as exactly the
target and never further, because the measurement stopped at the moment it
succeeded.

That made one question unanswerable: would a trailing stop or a partial profit
target have captured more? It needs to know how far winners actually ran, and
the column that should say so had stopped looking.

The hit rate cannot substitute for it. For barriers at +a/-b the breakeven hit
rate and the random-walk hit rate are BOTH b/(a+b) — identical for every ratio
— so hit rate alone carries no information about edge. The excursion
distribution is where that information is.

What these tests pin:
  * resolution is UNCHANGED — status, exit price and days_to_resolve are what
    they always were, because they define the trade's result under current
    rules and redefining them would reinterpret every historical row;
  * `max_favorable_pct` keeps its censored meaning, so old and new rows stay
    comparable on it;
  * `mfe_full_pct` sees past the target;
  * the window is FIXED at max_hold_days, so rows are comparable;
  * `full_window_days` reports what was actually observed, so a consumer can
    refuse to aggregate a row whose window is short.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from app.services.signal_outcome_tracker import (
    DEFAULT_MAX_HOLD_DAYS, _censoring_ceiling_r, _is_uncensored, _mfe_r,
    _resolve_one, _resolved_payload,
)


ENTRY_DAY = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _row(action="BUY", entry=100.0, stop=98.0, target=104.0):
    """Mirrors the live geometry: stop 2.0*ATR, target 4.0*ATR with ATR=1."""
    return SimpleNamespace(
        id="s1", action=action, generated_at=ENTRY_DAY,
        entry_price=Decimal(str(entry)), stop_price=Decimal(str(stop)),
        target_price=Decimal(str(target)),
    )


def _bars(rows):
    """rows = [(high, low, close), ...] starting the day AFTER entry."""
    idx = [ENTRY_DAY + timedelta(days=i + 1) for i in range(len(rows))]
    return pd.DataFrame(
        {"High": [r[0] for r in rows], "Low": [r[1] for r in rows],
         "Close": [r[2] for r in rows]},
        index=pd.DatetimeIndex(idx),
    )


def test_a_winner_that_kept_running_is_measured_past_its_target():
    """The bug, stated as a test.

    Target 104 is touched on day 1; the move continues to 118 by day 3.
    The censored column must still say ~4% (it defines the trade's result);
    the uncensored one must say ~18%.
    """
    hist = _bars([
        (104.0, 99.0, 103.0),   # day 1 — target touched
        (112.0, 103.0, 111.0),  # day 2 — kept going
        (118.0, 110.0, 117.0),  # day 3 — further still
        (116.0, 112.0, 113.0),
        (115.0, 111.0, 112.0),
    ])

    (status, exit_price, _resolved_at, days, mfe, mae,
     mfe_full, mae_full, window) = _resolve_one(_row(), hist, max_hold_days=5)

    # Resolution unchanged: it hit the target on day 1, at the target.
    assert status == "target_hit"
    assert exit_price == 104.0
    assert days == 1

    # Censored: capped at the target it hit.
    assert mfe == pytest.approx(0.04, abs=1e-6)

    # Uncensored: the move it actually made.
    assert mfe_full == pytest.approx(0.18, abs=1e-6)
    assert mfe_full > mfe, "this is the entire point of the change"
    assert window == 5


def test_the_censored_value_is_not_quietly_replaced():
    """Old rows carry the censored number; new rows must too, or the two
    populations stop being comparable on that column."""
    hist = _bars([(104.0, 99.0, 103.0), (130.0, 103.0, 129.0)])

    _s, _e, _r, _d, mfe, _mae, mfe_full, _maef, _w = _resolve_one(
        _row(), hist, max_hold_days=5)

    assert mfe == pytest.approx(0.04, abs=1e-6)   # NOT 0.30
    assert mfe_full == pytest.approx(0.30, abs=1e-6)


def test_a_loser_that_would_have_recovered_is_visible_too():
    """Stop at 98 on day 1, then the move goes to 110. The trade still lost —
    but the excursion says the stop was what ended it, not the thesis."""
    hist = _bars([
        (101.0, 98.0, 99.0),    # day 1 — stopped out
        (106.0, 99.0, 105.0),
        (110.0, 104.0, 109.0),
    ])

    status, exit_price, _r, days, mfe, _mae, mfe_full, _maef, _w = _resolve_one(
        _row(), hist, max_hold_days=3)

    assert status == "stop_hit"
    assert exit_price == 98.0
    assert days == 1
    assert mfe == pytest.approx(0.01, abs=1e-6)
    assert mfe_full == pytest.approx(0.10, abs=1e-6)


def test_the_window_is_fixed_not_open_ended():
    """max_hold_days bounds the look-ahead for EVERY row.

    'To the end of available history' would give an old signal a longer window
    than a recent one and make the two incomparable.
    """
    hist = _bars([
        (104.0, 99.0, 103.0),
        (106.0, 103.0, 105.0),
        (200.0, 105.0, 199.0),  # day 3 — outside a 2-day window
    ])

    *_rest, mfe_full, _maef, window = _resolve_one(_row(), hist, max_hold_days=2)

    assert window == 2
    assert mfe_full == pytest.approx(0.06, abs=1e-6), (
        "a bar beyond max_hold_days leaked into the measurement"
    )


def test_a_short_window_is_reported_not_hidden():
    """A signal resolved yesterday has almost no history; its 'uncensored'
    number is censored by data availability. full_window_days says so."""
    hist = _bars([(104.0, 99.0, 103.0)])

    *_rest, window = _resolve_one(_row(), hist, max_hold_days=10)

    assert window == 1, (
        "the window must report bars ACTUALLY observed, not max_hold_days — "
        "otherwise a one-day-old row looks like a complete measurement"
    )


def test_the_stop_wins_a_same_bar_tie_exactly_as_before():
    """The conservative tie-break is load-bearing and must not have moved."""
    hist = _bars([(104.0, 98.0, 101.0), (120.0, 100.0, 119.0)])

    status, exit_price, _r, days, *_rest = _resolve_one(_row(), hist, max_hold_days=5)

    assert status == "stop_hit"
    assert exit_price == 98.0
    assert days == 1


def test_it_works_for_shorts():
    """SELL: target BELOW entry, favourable excursion is downward."""
    row = _row(action="SELL", entry=100.0, stop=102.0, target=96.0)
    hist = _bars([
        (101.0, 96.0, 97.0),   # day 1 — target touched
        (95.0, 88.0, 89.0),    # kept falling
    ])

    status, _e, _r, _d, mfe, _mae, mfe_full, _maef, _w = _resolve_one(
        row, hist, max_hold_days=5)

    assert status == "target_hit"
    assert mfe == pytest.approx(0.04, abs=1e-6)
    assert mfe_full == pytest.approx(0.12, abs=1e-6)


def test_still_pending_returns_none():
    """Neither barrier touched and the window is not exhausted."""
    hist = _bars([(101.0, 99.0, 100.0)])
    assert _resolve_one(_row(), hist, max_hold_days=10) is None


class TestTheTwoExcursionsReachTheRightColumns:
    """The mapping, not just the measurement.

    Both excursions are floats of the same shape, so swapping them would
    launder a truncated number into the column whose whole purpose is to be
    untruncated — and every test above would still pass, because they only
    exercise _resolve_one. Mutation-testing found exactly that hole: replacing
    max_favorable_pct with the uncensored value broke nothing.
    """

    @staticmethod
    def _payload():
        hist = _bars([(104.0, 99.0, 103.0), (118.0, 103.0, 117.0)])
        res = _resolve_one(_row(), hist, max_hold_days=2)
        return _resolved_payload("row-1", res)

    def test_the_censored_value_goes_to_the_censored_column(self):
        p = self._payload()
        assert p["max_favorable_pct"] == Decimal("0.0400"), (
            "max_favorable_pct must keep its censored meaning — rows written "
            "before this change carry the truncated number, and overwriting it "
            "here makes the two populations silently incomparable"
        )

    def test_the_uncensored_value_goes_to_the_new_column(self):
        p = self._payload()
        assert p["mfe_full_pct"] == Decimal("0.1800")

    def test_the_two_are_not_the_same_column(self):
        p = self._payload()
        assert p["max_favorable_pct"] != p["mfe_full_pct"], (
            "if these ever match on a winner that ran past its target, one of "
            "them is being written from the wrong source"
        )

    def test_the_window_length_is_carried_through(self):
        assert self._payload()["full_window_days"] == 2

    def test_every_column_the_model_declares_is_present(self):
        p = self._payload()
        for k in ("id", "status", "exit_price", "resolved_at", "days_to_resolve",
                  "max_favorable_pct", "max_adverse_pct",
                  "mfe_full_pct", "mae_full_pct", "full_window_days"):
            assert k in p, f"{k} missing from the resolved payload"


def _outcome(*, status="target_hit", entry=100.0, stop=98.0, target=104.0,
             mfe=0.04, mfe_full=None, window=None, action="BUY"):
    """An outcome row as the R-metric helpers consume it."""
    o = {
        "status": status, "action": action,
        "entry_price": Decimal(str(entry)), "stop_price": Decimal(str(stop)),
        "target_price": Decimal(str(target)),
        "max_favorable_pct": Decimal(str(mfe)),
        "mfe_full_pct": None if mfe_full is None else Decimal(str(mfe_full)),
        "full_window_days": window,
    }
    return o


class TestWhichRowsCountAsUncensored:
    """The gate everything downstream depends on."""

    def test_a_pre_fix_row_is_censored(self):
        # mfe_full_pct NULL: resolved before this shipped, unrecoverable.
        assert _is_uncensored(_outcome(mfe_full=None, window=None)) is False

    def test_a_short_window_is_censored_by_data_availability(self):
        # Measured, but over 3 bars of a 20-bar window — the same
        # understatement as target-censoring, in different clothes.
        assert _is_uncensored(_outcome(mfe_full=0.09, window=3)) is False

    def test_a_complete_window_is_uncensored(self):
        assert _is_uncensored(
            _outcome(mfe_full=0.09, window=DEFAULT_MAX_HOLD_DAYS)) is True

    def test_a_value_with_no_window_recorded_is_censored(self):
        """Cannot confirm completeness, so do not assume it."""
        assert _is_uncensored(_outcome(mfe_full=0.09, window=None)) is False

    def test_a_complete_window_with_no_value_is_still_censored(self):
        """The one configuration where the NULL check earns its place.

        The tracker writes mfe_full_pct and full_window_days together, so this
        pairing cannot arise from a normal pass — it takes a partial backfill
        or a hand-edited row. Without the NULL check, such a row reports as
        uncensored, _mfe_r then reads None out of it and drops the row from
        every aggregate, and _censoring_ceiling_r stops treating it as a limit
        on the strength of a measurement that does not exist. Mutation testing
        showed the check was otherwise unreachable, which is a reason to cover
        it, not to delete it.
        """
        assert _is_uncensored(
            _outcome(mfe_full=None, window=DEFAULT_MAX_HOLD_DAYS)) is False


class TestTheRMetricUsesTheUncensoredValue:
    """Without this, the new column is recorded and never read."""

    def test_it_prefers_the_uncensored_excursion(self):
        # risk = 2.0/share. Censored 4% of 100 = 4.0 -> 2.0R.
        # Uncensored 18% of 100 = 18.0 -> 9.0R.
        o = _outcome(mfe=0.04, mfe_full=0.18, window=DEFAULT_MAX_HOLD_DAYS)
        assert _mfe_r(o) == pytest.approx(9.0), (
            "a target_hit row with a complete measurement must report how far "
            "it actually ran, not where its target was"
        )

    def test_it_falls_back_for_a_pre_fix_row(self):
        assert _mfe_r(_outcome(mfe=0.04, mfe_full=None)) == pytest.approx(2.0)

    def test_it_falls_back_when_the_window_is_short(self):
        o = _outcome(mfe=0.04, mfe_full=0.18, window=2)
        assert _mfe_r(o) == pytest.approx(2.0), (
            "an incomplete window must not be treated as a full measurement"
        )


class TestTheCensoringCeilingLiftsAsRowsBecomeMeasurable:
    """_censoring_ceiling_r exists ONLY because MFE was censored.

    A target_hit row with a complete uncensored excursion does record whether
    the move continued past its target, so it must stop constraining the
    counterfactual — otherwise the fix changes nothing where it matters.
    """

    def test_a_censored_target_hit_still_caps_the_ceiling(self):
        rows = [_outcome(status="target_hit", target=104.0, mfe_full=None)]
        assert _censoring_ceiling_r(rows) == pytest.approx(2.0)

    def test_an_uncensored_target_hit_does_not_cap_it(self):
        rows = [_outcome(status="target_hit", target=104.0,
                         mfe_full=0.18, window=DEFAULT_MAX_HOLD_DAYS)]
        assert _censoring_ceiling_r(rows) == float("inf"), (
            "this row CAN answer what happened past its target, so it must "
            "not limit which targets the counterfactual will evaluate"
        )

    def test_the_lowest_still_censored_target_sets_the_limit(self):
        rows = [
            _outcome(status="target_hit", target=104.0, mfe_full=None),        # 2.0R, censored
            _outcome(status="target_hit", target=110.0,                        # 5.0R, uncensored
                     mfe_full=0.30, window=DEFAULT_MAX_HOLD_DAYS),
            _outcome(status="stop_hit", target=103.0, mfe_full=None),          # never censoring
        ]
        assert _censoring_ceiling_r(rows) == pytest.approx(2.0)

    def test_once_every_target_hit_is_measurable_the_limit_is_gone(self):
        rows = [
            _outcome(status="target_hit", target=104.0,
                     mfe_full=0.18, window=DEFAULT_MAX_HOLD_DAYS),
            _outcome(status="stop_hit", target=104.0, mfe_full=None),
            _outcome(status="expired", target=104.0, mfe_full=None),
        ]
        assert _censoring_ceiling_r(rows) == float("inf")
