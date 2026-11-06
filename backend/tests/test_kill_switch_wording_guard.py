"""The kill switch has one vocabulary on screen: ENGAGED or CLEAR.

2026-11-06: the same state read "NOT ARMED" in the status strip, "CLEAR" on the Command Center
card, "Clear" in the Trade Desk header and "OFF" (hardcoded, whatever the state) in the Risk
rules list. "NOT ARMED" was the worst: for a safety control it can be read as "the protection
is not ready". The words now come from frontend/src/utils/killSwitchWords.ts.

This lives in the backend suite because it reads frontend source files, which the frontend's own
test environment cannot do. It asserts it read something: a scan that silently matches nothing
is worse than no test.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _sources():
    return [p for p in SRC.rglob("*.ts*")
            if "__tests__" not in p.parts and ".test." not in p.name and p.suffix in (".ts", ".tsx")]


def _code_lines(path: Path):
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*")):
            continue
        yield n, line


def test_the_scan_actually_reads_the_frontend_source():
    files = _sources()
    assert len(files) > 100, f"only {len(files)} frontend source files found; the path is wrong"
    assert (SRC / "utils" / "killSwitchWords.ts").exists()


def test_no_surface_still_says_not_armed():
    hits = [f"{p.relative_to(SRC)}:{n}" for p in _sources() for n, line in _code_lines(p)
            if re.search(r"NOT ARMED", line)]
    assert hits == [], f"'NOT ARMED' is back: {hits}"


def test_the_status_strip_and_the_rules_row_use_the_shared_words():
    strip = (SRC / "components" / "GlobalRiskStatus.tsx").read_text(encoding="utf-8")
    assert "killSwitchWords" in strip and "KILL_ENGAGED" in strip and "KILL_CLEAR" in strip
    rules = (SRC / "pages" / "Guardrails.tsx").read_text(encoding="utf-8")
    assert "killSwitchWord(" in rules
    assert not re.search(r'label:\s*"Kill Switch"[^}]*val:\s*"OFF"', rules), \
        "the rules row is hardcoded OFF again"


def test_the_shared_words_are_engaged_and_clear():
    words = (SRC / "utils" / "killSwitchWords.ts").read_text(encoding="utf-8")
    assert 'KILL_ENGAGED = "ENGAGED"' in words and 'KILL_CLEAR = "CLEAR"' in words
