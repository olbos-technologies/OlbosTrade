"""The recorder is wired in OFF, read-only, and at the scans' priority.

These read main.py and config as text because the scheduler loop cannot be run in a unit test;
each one asserts it read something, so a moved line fails loudly instead of passing vacuously.
"""
import pathlib
import re

from app.core.config import Settings

MAIN = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()


def _runner_source() -> str:
    m = re.search(r"async def _run_zero_dte_recorder\(\).*?(?=\nasync def |\ndef )", MAIN, re.S)
    assert m, "could not find _run_zero_dte_recorder in main.py"
    return m.group(0)


def test_it_is_off_by_default():
    assert Settings.model_fields["zero_dte_recorder_enabled"].default is False


def test_the_scheduler_only_runs_it_when_enabled():
    m = re.search(r"if settings\.zero_dte_recorder_enabled and now - last_zero_dte >= 60:\n\s+await _guarded\(_run_zero_dte_recorder\(\)", MAIN)
    assert m, "the scheduler call is missing or no longer gated by the setting"


def test_it_only_reads_market_data_at_the_scan_priority():
    src = _runner_source()
    assert "Priority.P2" in src and "get_options_chain" in src
    for banned in ("Priority.P0", "Priority.P1", "place_order", "PLACE_ORDER", "increases_risk", "handle_signal", "_execute_signal"):
        assert banned not in src, banned
