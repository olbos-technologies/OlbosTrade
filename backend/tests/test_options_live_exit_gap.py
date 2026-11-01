"""
What protects a live options position — and what the charter says does.

TRADING_POLICY.md said profit targets, stop multiplier and DTE time-stop were
"live". The evidence says otherwise, and this file records it so it cannot drift:

  * `check_exit` — the only code that evaluates a profit target, a stop-loss
    multiple or a DTE time-stop — has no caller outside the backtester.
  * A combo (SpreadOrder) carries no stop or target field, so there is no
    broker-side exit either; bracket orders exist for single-leg orders only.
  * An open options spread is therefore closed only manually, by position
    rotation, or by expiry/assignment.

That makes backtested expectancy — which assumes the exits — a description of a
system that does not run, and it is why adding a strategy to the live scan is a
larger step than it looks.

The strict xfail below is deliberate. It FAILS the day someone wires an exit into
the live path, which forces them to remove the marker, re-read the debit-spread
retry limitation noted in the PR that added this, and update the charter in the
same change. A skip would have let any of that happen silently.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
POLICY = ROOT / "TRADING_POLICY.md"

#: Where evaluating an exit is NOT live behaviour: research tooling.
RESEARCH = {"backtester.py", "strategy_optimizer.py"}


def _live_check_exit_callers() -> list[str]:
    callers = []
    for path in sorted(APP.rglob("*.py")):
        if path.name in RESEARCH:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "check_exit"
            ):
                callers.append(f"{path.relative_to(APP)}:{node.lineno}")
    return callers


def test_the_scan_reads_something_so_the_search_below_is_not_vacuous():
    """The caller search must be able to find a caller; prove it can."""
    found = []
    for path in APP.rglob("backtester.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found += [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "check_exit"
        ]
    assert found, (
        "the backtester no longer calls check_exit, so this file's search is "
        "looking for something that has moved"
    )


@pytest.mark.xfail(
    strict=True,
    reason="No live component evaluates an options exit rule (audited 2026-11-01). "
           "Remove this marker when one does — and update TRADING_POLICY.md with it.",
)
def test_a_live_component_evaluates_options_exit_rules():
    assert _live_check_exit_callers(), "no live caller of check_exit"


def test_the_charter_does_not_claim_exits_the_system_does_not_run():
    """The document and the code must agree, whichever way it changes."""
    text = POLICY.read_text(encoding="utf-8")
    assert "## Exit conditions" in text, "the section this guard checks has moved"
    section = text.split("## Exit conditions", 1)[1].split("\n## ", 1)[0]
    # Markdown wraps lines and bolds phrases; compare the words, not the layout.
    section = " ".join(section.replace("**", "").split())
    if _live_check_exit_callers():
        return      # exits are live now; the charter may say so
    assert "not evaluated against live options positions" in section, (
        "TRADING_POLICY.md describes options exits as live, but check_exit has no "
        "caller outside the backtester. Either wire an exit or correct the charter."
    )
    assert "DTE time-stop live" not in section


def test_the_leg_mapping_knows_the_strategy_by_the_name_the_system_uses():
    from app.services.spread_leg_mapping import _KNOWN_TWO_LEG_KINDS

    assert _KNOWN_TWO_LEG_KINDS["bear_put_spread"] == "put"
    assert _KNOWN_TWO_LEG_KINDS["bear_put_debit_spread"] == "put", (
        "the old key must stay in case a stored row carries it"
    )
