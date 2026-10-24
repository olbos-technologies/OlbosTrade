"""R2-Q architectural guard — must fail until direct ENTRY submit sites migrate.

AST inspect *all* backend API route modules. A rename/move cannot silently
turn this into an empty scan: require expected trade_desk execution source and
a real centralized submission implementation.
"""
import ast
from pathlib import Path


def test_no_api_route_calls_broker_entry_placement():
    api = Path(__file__).resolve().parents[1] / "app/api"
    files = sorted(api.rglob("*.py"))
    assert len(files) >= 5, "API scan unexpectedly empty"
    entry_fn_found = False
    prohibited = []
    permitted_closes = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            if fn.name == "_execute_signal":
                entry_fn_found = True
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"place_order", "place_equity_order"}:
                    continue
                if not isinstance(node.func.value, ast.Name) or node.func.value.id != "broker":
                    continue
                if fn.name in {"close_position", "close_untracked_position"}:
                    permitted_closes.append(f"{path.relative_to(api)}:{fn.name}:{node.lineno}")
                else:
                    prohibited.append(f"{path.relative_to(api)}:{fn.name}:{node.lineno}")
    assert entry_fn_found, "_execute_signal entry pipeline disappeared; audit routing"
    assert permitted_closes, "Expected independently scoped close placement disappeared"
    assert not prohibited, "Direct broker submission in API entry paths: " + ", ".join(prohibited)


def test_centralized_submission_requires_capability_argument():
    import inspect
    from app.services.entry_authority import EntryAuthorization
    from app.services import order_submission
    for method in (order_submission.submit_equity_entry, order_submission.submit_options_entry):
        assert "authorization" in inspect.signature(method).parameters
        assert "EntryAuthorization" in str(inspect.signature(method).parameters["authorization"].annotation)
