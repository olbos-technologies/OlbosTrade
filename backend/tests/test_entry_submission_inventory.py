"""R2-Q migration guard: detect drift in existing direct API submission sites.

This is a baseline inventory, NOT a completed ban: the two entry placements
must migrate into centralized submission before this can become deny-all.
"""
import ast
from pathlib import Path


def test_trade_desk_submission_sites_cannot_silently_expand():
    path = Path(__file__).resolve().parents[1] / "app/api/routes/trade_desk.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    placement_methods = {"place_equity_order", "place_order"}
    observed = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # inspect each method's own subtree, including nested lambdas
        if node.name not in {"_execute_signal", "close_position"}:
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                if call.func.attr in placement_methods and isinstance(call.func.value, ast.Name) and call.func.value.id == "broker":
                    observed.append((node.name, call.func.attr))
    # Assert a nonempty inventory. A refactor must consciously migrate and
    # update this guard; a silent method rename cannot pass vacuously.
    assert observed, "Submission scan found zero broker placement sites"
    entry = [method for function, method in observed if function == "_execute_signal"]
    assert sorted(entry) == ["place_equity_order", "place_order"], (
        "Direct entry placement inventory changed; route all new sites through centralized authority"
    )
