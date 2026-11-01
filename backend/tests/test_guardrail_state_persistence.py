"""
Guardrail counters may survive a restart; measurements may not.

Both halves of "FIX #12" — save_portfolio_snapshot and the loader beside it —
were written, documented as "what survives restarts", and never called from
anywhere in the repository. `portfolio_snapshots` has held zero rows in
production for the life of the feature.

That had a silver lining on 2026-10-31. A disconnected IBKR gateway made the
account-summary route substitute STARTING_CAPITAL (50,000) for a reading it
could not take; against the persisted all-time peak (275,091.64) the engine
computed a drawdown of 81.82%, breached the 30% limit, and recorded
"Trading suspended for 30 days." The ONLY reason that did not stick is that
nothing was writing the snapshot table.

So these tests pin the two properties that make the feature safe to wire up:

  1. The loader returns COUNTERS ONLY. It used to return `current_value` read
     off a stored snapshot — a stale number standing in for a live
     measurement, which is the same defect with a different value in it.
  2. The writer refuses to persist a cycle whose account value was not known,
     so a fabricated breach cannot outlive the outage that produced it.
"""
import inspect

from app.services.position_reconciler import PositionReconciler


MEASUREMENTS = ("current_value", "starting_capital", "daily_pnl", "weekly_pnl", "monthly_pnl")
COUNTERS = ("consecutive_losses", "trades_today", "cooling_off_until")


def test_the_loader_returns_counters_and_not_measurements():
    """
    Read the source rather than call it: this asserts the CONTRACT, and the
    function needs a database to run. A measurement key reappearing here is the
    regression, whatever value it happens to hold.
    """
    src = inspect.getsource(PositionReconciler.load_guardrail_counters_from_db)
    body = src.split('"""', 2)[-1]          # skip the docstring, which names them
    for key in MEASUREMENTS:
        assert f'"{key}"' not in body, (
            f"load_guardrail_counters_from_db returns {key!r} again. A stored "
            "measurement standing in for a live one is what suspended the desk "
            "for 30 days on a fabricated drawdown."
        )
    for key in COUNTERS:
        assert f'"{key}"' in body, f"{key} is no longer restored"


def test_the_writer_takes_an_account_value_known_flag():
    sig = inspect.signature(PositionReconciler.save_portfolio_snapshot)
    assert "account_value_known" in sig.parameters
    assert sig.parameters["account_value_known"].default is True, (
        "must default True so an existing caller is unchanged — the flag is "
        "opt-in refusal, not opt-in safety"
    )


def test_the_writer_refuses_when_the_account_value_was_not_known():
    src = inspect.getsource(PositionReconciler.save_portfolio_snapshot)
    guard = src.index("if not account_value_known:")
    build = src.index("PortfolioSnapshot(")
    assert guard < build, (
        "the refusal must come before the row is built, or a fabricated "
        "suspension is persisted anyway"
    )
    assert "return" in src[guard:build], "the guard must actually return"


def test_the_old_loader_name_is_gone():
    """
    Renamed deliberately: load_guardrail_state_from_db promised STATE and
    delivered measurements. Anything still calling the old name should fail
    loudly rather than silently get a narrower dict.
    """
    assert not hasattr(PositionReconciler, "load_guardrail_state_from_db")
    assert hasattr(PositionReconciler, "load_guardrail_counters_from_db")
