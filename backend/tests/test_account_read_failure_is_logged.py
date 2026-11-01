"""
A failed account-value read must say why.

`/api/guardrails/status` reads net liquidation from the broker, and on ANY
exception substituted a constant and carried on — silently. When production
showed "MAX DRAWDOWN LIMIT HIT: 81.82%" the only recoverable fact was that the
read had failed; whether the gateway was down, the call had timed out, or the
IBKR queue was backed up was gone, because the handler logged nothing. Those
three have different fixes, and until it was logged no amount of reading
`guardrail_events` could separate them.

AST-based rather than a call into the route: the route needs a database, a
broker and the full app, and the property worth pinning is structural — the
handler that sets `account_value_known = False` must also log.
"""
import ast
import pathlib

MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"


def _handler_that_marks_value_unknown() -> ast.ExceptHandler:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for stmt in ast.walk(node):
            if (
                isinstance(stmt, ast.Assign)
                and any(getattr(t, "id", None) == "account_value_known" for t in stmt.targets)
                and isinstance(stmt.value, ast.Constant)
                and stmt.value.value is False
            ):
                return node
    raise AssertionError(
        "no except handler sets account_value_known = False — the substitution "
        "this guard watches has moved or been removed; update the guard"
    )


def test_the_failure_handler_binds_the_exception():
    handler = _handler_that_marks_value_unknown()
    assert handler.name, (
        "the handler discards the exception (`except Exception:`) so nothing "
        "about the failure can be reported"
    )


def test_the_failure_handler_logs_a_warning():
    handler = _handler_that_marks_value_unknown()
    calls = [
        n for n in ast.walk(handler)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "warning"
        and getattr(n.func.value, "id", None) == "logger"
    ]
    assert calls, (
        "the handler that substitutes STARTING_CAPITAL does not log. A fabricated "
        "drawdown then has no recoverable cause."
    )


def test_it_logs_the_exception_type_not_only_its_text():
    """asyncio.TimeoutError has an empty message; str(exc) alone logs nothing."""
    handler = _handler_that_marks_value_unknown()
    src = ast.unparse(handler)
    assert "type(exc).__name__" in src, (
        "log the exception TYPE — a timeout's str() is empty, so the likeliest "
        "cause would otherwise appear as a blank message"
    )


def test_it_never_logs_a_traceback():
    """
    This route is polled many times a minute. A traceback per call would flood
    the log during exactly the outage being diagnosed.
    """
    handler = _handler_that_marks_value_unknown()
    for call in ast.walk(handler):
        if isinstance(call, ast.Call):
            for kw in call.keywords:
                assert kw.arg != "exc_info", "exc_info would log a traceback per poll"


def test_the_logged_text_is_bounded():
    handler = _handler_that_marks_value_unknown()
    assert "[:200]" in ast.unparse(handler), (
        "an exception string is not guaranteed free of identifiers; truncate it"
    )
