"""Review gate for broker submissions while execution paths are being unified.

This is an inventory, not proof that the current paths share one risk/OMS
pipeline. Entry broker call sites now belong only to order_submission.py;
its implementation must continue to refuse missing authorization. An added broker submission must receive explicit review rather than
quietly creating a new path around the existing controls.
"""

import ast
from collections import Counter
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "app"
ORDER_METHODS = {"place_order", "place_equity_order"}

# Entry, operator close, and emergency flatten currently have distinct paths.
# Do not add a site here without reviewing identity, environment, risk,
# idempotency, broker ambiguity, and reconciliation for that exact path.
APPROVED_INVENTORY = Counter({
    ("services/order_submission.py", "submit_options_entry", "place_order"): 1,
    ("services/order_submission.py", "submit_equity_entry", "place_equity_order"): 1,
    ("api/routes/trade_desk.py", "close_untracked_position", "place_equity_order"): 1,
    ("services/position_rotation.py", "close_equity_trade", "place_equity_order"): 1,
    ("services/position_rotation.py", "close_options_trade", "place_order"): 1,
    ("services/kill_switch.py", "_flatten_position", "place_order"): 1,
    ("services/kill_switch.py", "_flatten_equity", "place_equity_order"): 1,
})


def test_no_unreviewed_broker_submission_site():
    found = Counter()
    scanned = 0
    for path in APP.rglob("*.py"):
        scanned += 1
        tree = ast.parse(path.read_text())
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ORDER_METHODS):
                continue
            parent = parents[node]
            while parent is not tree and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent = parents[parent]
            owner = parent.name if parent is not tree else "<module>"
            found[(str(path.relative_to(APP)), owner, node.func.attr)] += 1
    assert scanned > 0
    assert found == APPROVED_INVENTORY
