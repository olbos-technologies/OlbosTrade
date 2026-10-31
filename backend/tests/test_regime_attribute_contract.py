"""
Guard + regression: reading a regime field that does not exist must not be silent.

`app/main.py` read `_current_regime.regime_type` in two places. RegimeState has
no such field — its name is `regime` — and both reads used getattr with a
default, so neither raised:

  * the equity-skip log line always printed "regime unknown does not allow
    equities", naming no regime at all;
  * every options scan rejection was persisted with regime=None, so
    scripts/options_rejection_report.py reported "BY REGIME (unknown) 100%"
    and could not answer the question it exists to answer — which strategies
    the regime permitted when the scan found nothing.

This is the repository's documented "failed reads must not look like safe
values" trap (CLAUDE.md), in the plainest form: `getattr(x, "typo", default)`
turns a renamed field into a plausible value forever.

The parametrised guard at the bottom fails on any getattr against
`_current_regime` naming something RegimeState does not define, so the next
rename or typo is caught at CI rather than discovered in a report months
later.
"""
import ast
import dataclasses
import pathlib
import re

import pytest

from app.services.regime_classifier import RegimeState, RegimeType

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN = ROOT / "backend" / "app" / "main.py"


def _regime_state_names() -> set[str]:
    names = {f.name for f in dataclasses.fields(RegimeState)}
    names |= {n for n in dir(RegimeState) if not n.startswith("_")}
    assert "regime" in names and "equity_allowed" in names, (
        "RegimeState introspection returned nothing usable — this guard would "
        "pass vacuously"
    )
    return names


def _getattr_names_on_current_regime() -> list[tuple[str, int]]:
    """Every getattr(_current_regime, "<name>", ...) in main.py, with its line."""
    source = MAIN.read_text(encoding="utf-8")
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr"):
            continue
        if not node.args or getattr(node.args[0], "id", None) != "_current_regime":
            continue
        key = node.args[1]
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            found.append((key.value, node.lineno))
    return found


def test_no_site_reads_the_field_that_never_existed():
    source = MAIN.read_text(encoding="utf-8")
    hits = [
        m.start() for m in re.finditer(r'_current_regime,\s*["\']regime_type["\']', source)
    ]
    assert not hits, (
        "app/main.py reads _current_regime.regime_type again — RegimeState's "
        "field is `regime`. Use _regime_name()."
    )


@pytest.mark.parametrize("name,lineno", _getattr_names_on_current_regime())
def test_every_getattr_on_the_regime_names_a_real_field(name, lineno):
    assert name in _regime_state_names(), (
        f"app/main.py:{lineno} reads _current_regime.{name!r}, which RegimeState "
        "does not define. With a getattr default this never raises — it just "
        "returns the default forever."
    )


def test_regime_name_reports_the_active_regime_not_unknown():
    """The regression itself: a classified regime must stamp its own name."""
    import app.main as main

    state = RegimeState(
        regime=RegimeType.LOW_VOL_TRENDING,
        description="test",
        confidence=0.9,
        strategies_allowed=["bull_call_debit_spread"],
        equity_strategies_allowed=["momentum_long"],
        size_multiplier=1.0,
        equity_size_multiplier=1.0,
        options_size_multiplier=1.0,
        signal_threshold=None,
        iron_condor_allowed=False,
        credit_spread_allowed=False,
    )
    previous = main._current_regime
    try:
        main._current_regime = state
        assert main._regime_name() == RegimeType.LOW_VOL_TRENDING.value
        assert main._regime_name() != "unknown"
    finally:
        main._current_regime = previous


def test_regime_name_still_reports_absence_honestly():
    """No classification yet is a real absence, and must stay distinguishable."""
    import app.main as main

    previous = main._current_regime
    try:
        main._current_regime = None
        assert main._regime_name() == "unknown"
        assert main._regime_name(default="") == ""
    finally:
        main._current_regime = previous


def test_a_renamed_field_raises_instead_of_returning_a_default():
    """_regime_name must not re-introduce a silent getattr on the field itself."""
    import app.main as main

    class Renamed:
        regime_type = RegimeType.CRISIS   # the old, wrong name only

    previous = main._current_regime
    try:
        main._current_regime = Renamed()
        with pytest.raises(AttributeError):
            main._regime_name()
    finally:
        main._current_regime = previous
