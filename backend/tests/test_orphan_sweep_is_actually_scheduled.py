"""
The sweep has to RUN. That is the entire point of the change.

The repair logic already existed before this PR — main.py's reconciliation
canceller — and the detection already existed too, in
rotation_preflight._no_orphans(). Both were correct. Neither cleared the 18
orphans found on 2026-09-19, because one only fires for symbols closed during
its own pass and the other only runs when someone calls the rotation route.

So the defect this PR fixes is not "no code does this". It is "the code that
does this is never reached in the state that needs it". A new service with the
same property would fix nothing, and a unit test of that service would pass
exactly as happily.

This asserts the wiring by parsing main.py's AST rather than by eye. It is the
same shape as the guards in test_docs_use_the_published_port.py: the authority
is the code, the claim lives elsewhere, and nothing ties them together unless
something like this does.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "backend" / "app" / "main.py"

SWEEP = "_sweep_orphaned_orders"
#: The job it is deliberately paired with. Reconciliation creates both
#: conditions — a position without an order, and an order without a position —
#: so both cleanups belong behind it on the same tick.
SIBLING = "_backfill_equity_stops"


def main_tree() -> ast.Module:
    return ast.parse(MAIN.read_text())


def called_names(tree: ast.Module) -> list[str]:
    """Every function called anywhere in main.py, in source order."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.append(node.func.id)
    return names


def scheduler_body() -> ast.AsyncFunctionDef:
    for node in ast.walk(main_tree()):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_background_scheduler":
            return node
    raise AssertionError(
        "_background_scheduler not found in main.py — this test can no longer "
        "see the loop it is guarding. Fix the lookup, do not delete the test."
    )


def test_the_sweep_function_exists():
    """Guards the guard: a renamed job would make every check below vacuous."""
    names = {n.name for n in ast.walk(main_tree())
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert SWEEP in names, (
        f"main.py defines no {SWEEP}(). If it was renamed, rename it here too — "
        f"the checks below would otherwise pass against nothing."
    )
    assert SIBLING in names, f"main.py defines no {SIBLING}()"


def test_the_sweep_is_called_from_the_background_scheduler():
    """A service nothing calls is precisely the bug this PR fixes."""
    called = called_names(scheduler_body())
    assert SWEEP in called, (
        f"{SWEEP}() is never called from _background_scheduler. The orphan "
        f"sweep only helps if it runs on a schedule: repair logic that fires "
        f"solely on an event is what left 18 orphaned orders resting for three "
        f"weeks after the event-triggered canceller shipped."
    )


def test_the_sweep_runs_behind_reconciliation_like_its_sibling():
    """Both cleanups are created by reconciliation, so both sit behind it.

    Asserted as adjacency to the stop backfill rather than as a literal line
    number, so reordering unrelated jobs does not fail this, while moving the
    sweep onto a timer of its own does — the drift the pairing exists to avoid.
    """
    called = called_names(scheduler_body())
    assert SIBLING in called, f"{SIBLING}() is no longer called from the scheduler"
    gap = abs(called.index(SWEEP) - called.index(SIBLING))
    assert gap <= 2, (
        f"{SWEEP}() and {SIBLING}() are {gap} calls apart in the scheduler. "
        f"They are paired deliberately: reconciliation is what both adopts a "
        f"position without a stop and closes a position leaving its orders "
        f"behind, so both cleanups run on its tick rather than drifting in and "
        f"out of phase on timers of their own."
    )


def test_the_sweep_call_is_guarded():
    """Everything in this loop runs under _guarded(), which timeouts and
    swallows. An unguarded call that hangs stops the scheduler — and with it
    fills, reconciliation, and every scan."""
    for node in ast.walk(scheduler_body()):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_guarded"):
            inner = node.args[0] if node.args else None
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == SWEEP):
                return
    raise AssertionError(
        f"{SWEEP}() is called from the scheduler but not inside _guarded(). "
        f"Every other job in that loop is wrapped: an unguarded coroutine that "
        f"hangs takes fills and reconciliation down with it."
    )
