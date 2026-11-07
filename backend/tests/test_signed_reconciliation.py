"""Reconciliation compares equity holdings WITH direction.

The legacy check summed abs(quantity) on both sides, so long 10 and short 10
were the same number. The failures worth catching are exactly the ones that
preserve size and flip side: an exit that fires on top of a manual close, a
stop that triggers after the position was already flat, an entry sent the wrong
way. Every size-only check called those clean.

What these tests pin, beyond the arithmetic:

* a flipped position is reported but NEVER fed to the quantity auto-correct,
  which would resize the row to the broker's number and make the flip look
  settled;
* option and mixed exposure stays on the legacy size check, because no
  verified leg ledger exists for it and a signed comparison there would be
  invented evidence;
* a row whose direction is not recognised is "unverified", never guessed long.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.broker.broker_interface import Position
from app.services.position_reconciler import (
    PositionReconciler, ReconciliationError, signed_equity_findings,
)


# ── builders ───────────────────────────────────────────────────────────────

def eq_pos(sym: str, qty: int) -> Position:
    return Position(
        symbol=sym, underlying=sym, strike=Decimal("0"), expiration=date(2026, 11, 6),
        option_type="call", quantity=qty, avg_cost=Decimal("100"), asset_type="equity",
    )


def opt_pos(sym: str, qty: int) -> Position:
    return Position(
        symbol=f"{sym}261120P00100000", underlying=sym, strike=Decimal("100"),
        expiration=date(2026, 11, 20), option_type="put", quantity=qty,
        avg_cost=Decimal("1.25"), asset_type="option",
    )


def row(sym: str, qty: int, spread_type="equity_long", strategy="equity"):
    return SimpleNamespace(
        underlying=sym, quantity=qty, spread_type=spread_type, strategy=strategy,
    )


def kinds(findings):
    return [(f.symbol, f.kind) for f in findings]


# ── the pure comparison ────────────────────────────────────────────────────

def test_the_legacy_comparison_could_not_tell_these_apart():
    """The defect, stated as arithmetic: same size, opposite side."""
    assert abs(10) == abs(-10)


@pytest.mark.parametrize("broker_qty, spread_type", [
    (10, "equity_long"),
    (-10, "equity_short"),
])
def test_matching_size_and_side_is_clean_and_handled(broker_qty, spread_type):
    findings, handled = signed_equity_findings(
        [eq_pos("AAPL", broker_qty)], [row("AAPL", 10, spread_type)],
    )
    assert findings == [] and handled == {"AAPL"}


@pytest.mark.parametrize("broker_qty, spread_type", [
    (-10, "equity_long"),     # broker short, row says long
    (10, "equity_short"),     # broker long, row says short
    (-7, "equity_long"),      # opposite side AND a different size
    (3, "equity_short"),
])
def test_opposite_side_is_a_direction_mismatch_whatever_the_size(broker_qty, spread_type):
    findings, handled = signed_equity_findings(
        [eq_pos("AAPL", broker_qty)], [row("AAPL", 10, spread_type)],
    )
    assert kinds(findings) == [("AAPL", "direction")]
    assert handled == {"AAPL"}


@pytest.mark.parametrize("broker_qty, spread_type, db_qty", [
    (10, "equity_long", 7),
    (5, "equity_long", 10),
    (-10, "equity_short", 4),
])
def test_same_side_different_size_is_a_quantity_mismatch_not_a_direction_one(
    broker_qty, spread_type, db_qty,
):
    findings, _ = signed_equity_findings(
        [eq_pos("AAPL", broker_qty)], [row("AAPL", db_qty, spread_type)],
    )
    assert kinds(findings) == [("AAPL", "quantity")]


def test_several_rows_on_one_underlying_are_summed_with_their_signs():
    findings, handled = signed_equity_findings(
        [eq_pos("AAPL", 10)], [row("AAPL", 6), row("AAPL", 4)],
    )
    assert findings == [] and handled == {"AAPL"}


def test_a_long_and_a_short_row_that_net_to_zero_do_not_explain_a_position():
    """Net zero in the database against a real position is a size problem, not
    a flip; it must not be called a direction mismatch."""
    findings, _ = signed_equity_findings(
        [eq_pos("AAPL", 5)], [row("AAPL", 5), row("AAPL", 5, "equity_short")],
    )
    assert kinds(findings) == [("AAPL", "quantity")]


def test_underlyings_are_matched_case_insensitively():
    findings, handled = signed_equity_findings([eq_pos("aapl", -10)], [row("AAPL", 10)])
    assert kinds(findings) == [("AAPL", "direction")] and handled == {"AAPL"}


def test_a_symbol_present_on_one_side_only_is_left_to_untracked_and_phantom():
    findings, handled = signed_equity_findings([eq_pos("AAPL", 10)], [row("MSFT", 10)])
    assert findings == [] and handled == set()


def test_zero_quantity_broker_rows_are_ignored():
    findings, handled = signed_equity_findings([eq_pos("AAPL", 0)], [row("AAPL", 10)])
    assert findings == [] and handled == set()


# ── what it must NOT judge ─────────────────────────────────────────────────

def test_option_exposure_on_the_same_underlying_stays_on_the_legacy_check():
    findings, handled = signed_equity_findings(
        [eq_pos("AAPL", -10), opt_pos("AAPL", -1)], [row("AAPL", 10)],
    )
    assert findings == [] and handled == set()


def test_an_options_row_on_the_same_underlying_stays_on_the_legacy_check():
    options_row = row("AAPL", 1, spread_type="put", strategy="bull_put_spread")
    findings, handled = signed_equity_findings(
        [eq_pos("AAPL", -10)], [row("AAPL", 10), options_row],
    )
    assert findings == [] and handled == set()


def test_a_pure_options_book_is_untouched():
    findings, handled = signed_equity_findings(
        [opt_pos("SPY", -2)], [row("SPY", 1, spread_type="put", strategy="bull_put_spread")],
    )
    assert findings == [] and handled == set()


@pytest.mark.parametrize("spread_type", ["equity_buy", "equity", "", None, MagicMock()])
def test_an_unrecognised_direction_is_unverified_and_never_guessed_long(spread_type):
    """The broker is SHORT. If an unknown row were assumed long this would be
    reported as a direction mismatch; if assumed short, it would pass. It must
    be neither — only 'could not verify'."""
    r = row("AAPL", 10, spread_type=spread_type, strategy="equity")
    findings, handled = signed_equity_findings([eq_pos("AAPL", -10)], [r])
    assert kinds(findings) == [("AAPL", "unverified")]
    assert handled == set()          # the legacy size check still runs


def test_a_test_double_row_reads_as_unknown_not_as_equity():
    """MagicMock answers every attribute with a truthy MagicMock. That must not
    be mistaken for an 'equity' row with a direction."""
    findings, handled = signed_equity_findings([eq_pos("AAPL", -10)], [MagicMock(underlying="AAPL", quantity=10)])
    assert findings == [] and handled == set()


def test_only_a_real_string_counts_as_a_direction():
    """An object that merely PRINTS as 'equity_long' is not the string. A
    direction is read from the stored text and nothing that happens to
    stringify to it."""
    class LooksLong:
        def __str__(self):
            return "equity_long"

    r = row("AAPL", 10, spread_type=LooksLong(), strategy="equity")
    findings, handled = signed_equity_findings([eq_pos("AAPL", -10)], [r])
    assert kinds(findings) == [("AAPL", "unverified")] and handled == set()


def test_a_missing_quantity_counts_as_one_like_the_legacy_check():
    r = row("AAPL", 0)
    r.quantity = None
    findings, _ = signed_equity_findings([eq_pos("AAPL", 1)], [r])
    assert findings == []


# ── wording ────────────────────────────────────────────────────────────────

def test_the_findings_say_which_side_each_is_on():
    (f,), _ = signed_equity_findings([eq_pos("AAPL", -10)], [row("AAPL", 10)])
    text = f.describe()
    assert "DIRECTION MISMATCH AAPL" in text and "broker=short 10" in text and "db=long 10" in text


def test_a_quantity_finding_keeps_the_legacy_wording():
    (f,), _ = signed_equity_findings([eq_pos("AAPL", 10)], [row("AAPL", 7)])
    assert f.describe() == "QUANTITY MISMATCH AAPL: broker=10 db=7"


def test_an_unverified_finding_says_what_could_not_be_checked():
    (f,), _ = signed_equity_findings([eq_pos("AAPL", 10)], [row("AAPL", 10, "equity_buy")])
    assert "DIRECTION UNVERIFIED AAPL" in f.describe()


# ── check() ────────────────────────────────────────────────────────────────

def _wire(trades):
    """Patch the reconciler's session so its open-trade query yields `trades`."""
    patcher = patch("app.services.position_reconciler.AsyncSessionLocal")
    mock_db = patcher.start()
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=trades)))
    ))
    mock_db.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return patcher


async def _check(broker_positions, trades):
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=broker_positions)
    patcher = _wire(trades)
    try:
        return await PositionReconciler(broker, organization_id=None).check()
    finally:
        patcher.stop()


async def _reconcile(broker_positions, trades):
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=broker_positions)
    patcher = _wire(trades)
    try:
        return await PositionReconciler(broker, organization_id=None).reconcile()
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_check_reports_a_flipped_position_and_keeps_it_out_of_the_autocorrect_list():
    res = await _check([eq_pos("AAPL", -10)], [row("AAPL", 10)])
    assert res.clean is False
    assert res.direction_mismatch_tickers == ["AAPL"]
    # The one that matters: quantity_mismatch_tickers is what the auto-correct
    # reads. A flip in there would be "fixed" by resizing it.
    assert res.quantity_mismatch_tickers == []
    assert any("DIRECTION MISMATCH AAPL" in w for w in res.warnings)


@pytest.mark.asyncio
async def test_check_is_clean_when_side_and_size_both_agree():
    res = await _check([eq_pos("AAPL", -10), eq_pos("MSFT", 5)],
                       [row("AAPL", 10, "equity_short"), row("MSFT", 5)])
    assert res.clean is True and res.warnings == []
    assert res.direction_mismatch_tickers == [] and res.quantity_mismatch_tickers == []


@pytest.mark.asyncio
async def test_check_still_feeds_same_side_size_drift_to_the_autocorrect():
    res = await _check([eq_pos("AAPL", 10)], [row("AAPL", 7)])
    assert res.clean is False
    assert res.quantity_mismatch_tickers == ["AAPL"]
    assert res.direction_mismatch_tickers == []


@pytest.mark.asyncio
async def test_check_does_not_double_report_a_handled_underlying():
    res = await _check([eq_pos("AAPL", -10)], [row("AAPL", 10)])
    assert len(res.warnings) == 1


@pytest.mark.asyncio
async def test_check_with_an_unverified_row_is_unclean_but_falls_back_to_size():
    res = await _check([eq_pos("AAPL", -10)], [row("AAPL", 10, "equity_buy")])
    assert res.clean is False
    assert any("DIRECTION UNVERIFIED AAPL" in w for w in res.warnings)
    # sizes are equal under abs(), so the legacy path adds no quantity ticker
    assert res.quantity_mismatch_tickers == [] and res.direction_mismatch_tickers == []


@pytest.mark.asyncio
async def test_check_leaves_an_options_book_on_the_legacy_size_check():
    res = await _check([opt_pos("SPY", -2)],
                       [row("SPY", 1, spread_type="put", strategy="bull_put_spread")])
    assert res.quantity_mismatch_tickers == ["SPY"]       # legacy: 2 legs vs 1 spread
    assert res.direction_mismatch_tickers == []


@pytest.mark.asyncio
async def test_check_still_reports_untracked_and_phantom_alongside_a_flip():
    res = await _check([eq_pos("AAPL", -10), eq_pos("NEW", 3)],
                       [row("AAPL", 10), row("GONE", 2)])
    assert res.untracked_at_broker == ["NEW"] and res.phantom_in_db == ["GONE"]
    assert res.direction_mismatch_tickers == ["AAPL"]


# ── reconcile() ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reconcile_halts_on_a_flipped_position_and_says_why():
    with pytest.raises(ReconciliationError, match="direction mismatch") as exc:
        await _reconcile([eq_pos("AAPL", -10)], [row("AAPL", 10)])
    assert exc.value.result.direction_mismatch_tickers == ["AAPL"]
    assert exc.value.result.clean is False
    assert any("opposite SIDE" in w for w in exc.value.result.warnings)


@pytest.mark.asyncio
async def test_reconcile_reports_direction_before_quantity_when_both_exist():
    with pytest.raises(ReconciliationError, match="direction mismatch") as exc:
        await _reconcile([eq_pos("AAPL", -10), eq_pos("MSFT", 10)],
                         [row("AAPL", 10), row("MSFT", 7)])
    assert exc.value.result.direction_mismatch_tickers == ["AAPL"]
    assert len(exc.value.result.quantity_mismatches) == 1       # MSFT, recorded too


@pytest.mark.asyncio
async def test_reconcile_still_halts_on_same_side_size_drift():
    with pytest.raises(ReconciliationError, match="quantity mismatch") as exc:
        await _reconcile([eq_pos("AAPL", 10)], [row("AAPL", 7)])
    assert exc.value.result.direction_mismatch_tickers == []
    assert "Possible double-fill" in exc.value.result.quantity_mismatches[0]


@pytest.mark.asyncio
async def test_reconcile_is_clean_when_side_and_size_agree():
    res = await _reconcile([eq_pos("AAPL", -10)], [row("AAPL", 10, "equity_short")])
    assert res.clean is True and res.direction_mismatch_tickers == []


@pytest.mark.asyncio
async def test_reconcile_still_treats_untracked_as_the_first_failure():
    with pytest.raises(ReconciliationError, match="UNTRACKED"):
        await _reconcile([eq_pos("AAPL", -10), eq_pos("NEW", 3)], [row("AAPL", 10)])


# ── the periodic sweep: report, never repair ───────────────────────────────

async def _sweep(broker_positions, trades):
    """Run _reconcile_positions' per-owner body once, with the repairs spied on."""
    import app.main as m
    from conftest import single_operator_work

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=broker_positions)
    scoped = single_operator_work(broker)

    async def run_once(session_factory, fn, job):
        await fn(scoped)
        return {"skipped": 0, "failed": 0, "scopes": 1, "ok": 1, "errors": []}

    patcher = _wire(trades)
    try:
        with patch("app.services.worker_scope.for_each_scope", new=run_once), \
             patch.object(m, "_correct_quantity_mismatches", new=AsyncMock()) as correct, \
             patch.object(m, "_adopt_untracked_positions", new=AsyncMock()) as adopt, \
             patch("app.services.observability.observability.incr") as incr, \
             patch("app.services.observability.observability.event") as event:
            await m._reconcile_positions()
    finally:
        patcher.stop()
    return correct, adopt, incr, event


@pytest.mark.asyncio
async def test_a_flipped_position_is_reported_and_never_resized_or_adopted():
    correct, adopt, incr, event = await _sweep([eq_pos("AAPL", -10)], [row("AAPL", 10)])
    correct.assert_not_awaited()      # resizing a flip would make it look settled
    adopt.assert_not_awaited()
    incr.assert_any_call("reconciliation.direction_mismatch")
    names = [c.args[0] for c in event.call_args_list]
    assert "reconciliation_direction_mismatch" in names
    flip = next(c for c in event.call_args_list if c.args[0] == "reconciliation_direction_mismatch")
    assert flip.kwargs["tickers"] == ["AAPL"]


@pytest.mark.asyncio
async def test_ordinary_size_drift_is_still_auto_corrected():
    """The positive control: the repair path still works for what it is for."""
    correct, adopt, incr, _ = await _sweep([eq_pos("AAPL", 10)], [row("AAPL", 7)])
    correct.assert_awaited_once()
    assert correct.await_args.args[0] == ["AAPL"]
    assert all(c.args[0] != "reconciliation.direction_mismatch" for c in incr.call_args_list)


@pytest.mark.asyncio
async def test_a_flip_and_ordinary_drift_together_correct_only_the_drift():
    correct, _, _, _ = await _sweep(
        [eq_pos("AAPL", -10), eq_pos("MSFT", 10)], [row("AAPL", 10), row("MSFT", 7)],
    )
    correct.assert_awaited_once()
    assert correct.await_args.args[0] == ["MSFT"]


# ── the status routes carry the finding, including when reconcile halts ────

def _scoped_patches(reconciler):
    from app.services.worker_scope import ScopedWork
    return (
        patch("app.services.worker_scope.broker_for_scope",
              new=AsyncMock(return_value=ScopedWork(
                  organization_id=None, broker=MagicMock(), broker_source="process"))),
        patch("app.api.routes.risk.PositionReconciler", return_value=reconciler),
        patch("app.services.position_reconciler.PositionReconciler", return_value=reconciler),
    )


@pytest.mark.asyncio
async def test_the_status_route_reports_which_tickers_are_on_the_wrong_side():
    from conftest import single_operator_conn
    from app.api.routes.risk import get_reconciliation

    res = await _check([eq_pos("AAPL", -10)], [row("AAPL", 10)])
    recon = MagicMock(); recon.check = AsyncMock(return_value=res)
    a, b, c = _scoped_patches(recon)
    with a, b, c:
        out = await get_reconciliation(single_operator_conn())
    assert out["clean"] is False
    assert out["direction_mismatch_tickers"] == ["AAPL"]


@pytest.mark.asyncio
async def test_the_operator_reconcile_route_keeps_the_finding_when_it_halts():
    """A direction mismatch makes reconcile() RAISE, so the route answers from
    its error branch. The finding has to survive that branch too."""
    from conftest import single_operator_conn
    from app.api.routes.risk import run_reconciliation

    with pytest.raises(ReconciliationError) as exc:
        await _reconcile([eq_pos("AAPL", -10)], [row("AAPL", 10)])
    recon = MagicMock(); recon.reconcile_and_record = AsyncMock(side_effect=exc.value)
    a, b, c = _scoped_patches(recon)
    with a, b, c, patch("app.core.database.AsyncSessionLocal") as sess, \
         patch("app.api.routes.risk.scope_or_401", new=AsyncMock(return_value=None)):
        sess.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        sess.return_value.__aexit__ = AsyncMock(return_value=False)
        out = await run_reconciliation(single_operator_conn())
    assert out["status"] == "error"
    assert out["result"]["direction_mismatch_tickers"] == ["AAPL"]
    assert out["result"]["clean"] is False


@pytest.mark.asyncio
async def test_the_operator_reconcile_route_reports_the_finding_when_it_returns():
    from conftest import single_operator_conn
    from app.api.routes.risk import run_reconciliation

    res = await _reconcile([eq_pos("AAPL", -10)], [row("AAPL", 10, "equity_short")])
    recon = MagicMock(); recon.reconcile_and_record = AsyncMock(return_value=res)
    a, b, c = _scoped_patches(recon)
    with a, b, c, patch("app.core.database.AsyncSessionLocal") as sess, \
         patch("app.api.routes.risk.scope_or_401", new=AsyncMock(return_value=None)):
        sess.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        sess.return_value.__aexit__ = AsyncMock(return_value=False)
        out = await run_reconciliation(single_operator_conn())
    assert out["result"]["direction_mismatch_tickers"] == []
    assert out["result"]["clean"] is True
