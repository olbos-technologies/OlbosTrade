"""
Nothing outside the tracker destructures _resolve_one's return value.

THE BUG THIS CATCHES, which shipped in PR #65 and was found in review:
_resolve_one grew from six return values to nine when MFE stopped being
truncated at the target. `scripts/backfill_signal_outcomes.py` kept its own
copy of the payload mapping and kept unpacking six:

    status, exit_price, resolved_at, days, mfe, mae = res

which is `ValueError: too many values to unpack` on the first resolved row.
The backfill could not have drained anything. CI stayed green the whole time
because `backend/scripts/` is not under test — nothing imports it, so nothing
noticed it had been broken.

Patching that one line would leave the mechanism intact. The real defect is a
SECOND copy of the mapping living away from the function it has to track, so
both callers now go through `_resolved_payload()` and this test keeps it that
way: a tuple target on `_resolve_one` is the shape that silently breaks when
the return grows, and it is the shape a reviewer cannot see from the diff of
the function being changed.

Parsed with `ast`, not grepped. A regex over source would match the pattern
inside this docstring — which is the trap a guard in this repo has fallen into
before (see test_login_rate_limit_deployment.py, which once matched its own
explanatory prose and passed against a broken deployment).
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

#: Where the function lives. It may destructure its own result.
OWNER = BACKEND / "app" / "services" / "signal_outcome_tracker.py"


def _python_files() -> list[Path]:
    return [
        p for p in BACKEND.rglob("*.py")
        if "tests" not in p.parts
        and "alembic" not in p.parts
        and "__pycache__" not in p.parts
    ]


def _callee(node: ast.Call) -> str | None:
    fn = node.func
    return fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)


def _destructuring_calls(path: Path) -> list[int]:
    """Line numbers where _resolve_one's result is unpacked into a tuple.

    BOTH shapes, and the second is the one that actually shipped:

        a, b, c = _resolve_one(...)     # direct
        res = _resolve_one(...)         # two-step — what the backfill did
        ...
        a, b, c = res

    The first version of this guard only looked for the direct form, so it
    missed the exact bug it was written for and passed the mutation that
    restored it. Caught by mutation testing, which is the only reason this
    comment exists rather than a false sense of coverage.
    """
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return []

    # Names bound to a _resolve_one result anywhere in the module.
    bound: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and _callee(node.value) == "_resolve_one"):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    bound.add(t.id)

    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, (ast.Tuple, ast.List)) for t in node.targets):
            continue
        v = node.value
        direct = isinstance(v, ast.Call) and _callee(v) == "_resolve_one"
        two_step = isinstance(v, ast.Name) and v.id in bound
        if direct or two_step:
            hits.append(node.lineno)
    return hits


def test_the_scan_actually_finds_the_call():
    """Guards the guard: if nothing can see the calls, every assertion below
    passes vacuously."""
    seen = 0
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if name == "_resolve_one":
                    seen += 1
    assert seen >= 2, (
        f"only {seen} call(s) to _resolve_one found across backend/. Expected "
        f"at least the tracker and the backfill script — if this drops, the "
        f"test below is checking nothing."
    )


def test_no_caller_outside_the_tracker_unpacks_the_tuple():
    offenders = {
        str(p.relative_to(BACKEND)): lines
        for p in _python_files()
        if p != OWNER
        for lines in [_destructuring_calls(p)]
        if lines
    }
    assert not offenders, (
        f"these destructure _resolve_one's return directly: {offenders}. "
        f"Its arity has changed once already (six values to nine, when MFE "
        f"stopped being censored at the target) and the caller that did this "
        f"broke silently — backend/scripts/ is not under test, so CI stayed "
        f"green while the backfill could not process a single resolved row. "
        f"Use _resolved_payload(row_id, res), which tracks the function it "
        f"maps and writes the new columns without each caller being updated."
    )


def test_the_backfill_script_uses_the_shared_mapping():
    """The specific caller that broke. Named so a regression is unambiguous."""
    script = BACKEND / "scripts" / "backfill_signal_outcomes.py"
    assert script.exists(), "backfill_signal_outcomes.py has moved or gone"

    tree = ast.parse(script.read_text())
    calls = {
        (n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", None))
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    }
    assert "_resolved_payload" in calls, (
        "the backfill builds its payload by hand again. That parallel copy is "
        "what drifted out of sync with _resolve_one in the first place."
    )
    assert not _destructuring_calls(script)
