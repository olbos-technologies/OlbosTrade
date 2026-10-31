"""
Guard: a frontend payload field must exist on the backend that produces it.

The bug this is built from: the equity scanner stored its rationale under
``reasons`` (a dict of scoring factor -> points); EquitySignals.tsx declared
``reason?: string`` and rendered ``sig.reason || "<placeholder>"``. Neither the
key nor the type matched, so every signal card in production printed "No
plain-language rationale supplied by this producer" and every detail drawer
showed an empty evidence list — for months. Nothing failed, because an absent
field and a producer with nothing to say render identically.

TypeScript cannot catch this: a hand-written interface is an assertion about
the server, not a fact checked against it. Several agents work this repository
in parallel, so the two sides drift independently and neither compiler sees the
other.

The mechanism, opt-in one interface at a time:

    /** @api-source backend/app/main.py#_scan_one */
    interface Signal {
      ticker: string;
      reasons?: Record<string, number>;
      isExpanded?: boolean;   // @api-local
    }

Every field declared in an annotated interface must appear as a quoted key
(``"ticker"``) inside the named producer — and the producer is a *function*,
not a file. That `#_scan_one` matters: `app/main.py` is 3,400 lines holding
three unrelated `"reason":` keys, so a file-wide search would have reported the
original bug as fine. Scoping to the function that builds the payload is what
makes the check mean anything. A field the UI adds for itself and never reads
off the wire is marked ``// @api-local`` on its own line and skipped.

This is deliberately a name check, not a type check — the full fix is
response_model on the routes plus generated types (169 routes, 1 of which has
a response_model today; the frontend consumes 39 of them). Name drift is what
actually bit, it is what this catches, and it costs one comment per interface.
"""
import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"

LOCAL_MARKER = "@api-local"


def _function_index(text: str) -> dict[str, list[ast.AST]]:
    """Every function in a module, keyed by both its bare and its dotted name."""
    index: dict[str, list[ast.AST]] = {}

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                dotted = f"{prefix}{child.name}"
                index.setdefault(child.name, []).append(child)
                if dotted != child.name:
                    index.setdefault(dotted, []).append(child)
                walk(child, dotted + ".")
            else:
                walk(child, prefix)

    walk(ast.parse(text))
    return index


def _producer_body(path: pathlib.Path, symbol: str | None) -> str:
    """
    The source of one function, or the whole file when no symbol is named.

    Uses the AST rather than indentation. A hand-rolled dedent walker gets this
    wrong: `def compute_equity_trade_plan(` has a multi-line signature whose
    closing `)` sits at column 0, which reads as the end of the body and
    silently returns an empty function — every field then looks missing.

    Nested functions are reachable (the equity scanner's `_scan_one` is defined
    inside `_run_equity_scan`), which importing and inspecting would not give.
    An ambiguous name is an error rather than a guess: `app/main.py` holds two
    functions called `_scan_one`, the equity one and the options one, and
    quietly checking the payload against the wrong one is exactly the kind of
    false green this guard exists to prevent. Disambiguate with a dotted path,
    e.g. `#_run_equity_scan._scan_one`.
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not symbol:
        return text
    matches = _function_index(text).get(symbol, [])
    assert matches, f"{path.name} has no function named {symbol} — fix the @api-source anchor"
    assert len(matches) == 1, (
        f"{path.name} has {len(matches)} functions named {symbol} "
        f"(lines {[m.lineno for m in matches]}). Use a dotted path so the guard "
        "checks the payload against the right one."
    )
    return ast.get_source_segment(text, matches[0]) or ""


def _annotated_interfaces() -> list[tuple[pathlib.Path, str, str, list[str]]]:
    """(tsx file, interface name, "path#symbol" producer ref, declared field names)."""
    found = []
    for path in sorted(FRONTEND.rglob("*.ts*")):
        if "__tests__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "@api-source" not in text:
            continue
        # Each annotation applies to the next interface/type block below it.
        for m in re.finditer(
            r"@api-source\s+(\S+)[^\n]*\n(?:[^\n]*\n)??\s*(?:export\s+)?(?:interface|type)\s+(\w+)\s*(?:=\s*)?\{([^}]*)\}",
            text, re.S,
        ):
            producer, name, body = m.group(1), m.group(2), m.group(3)
            fields = [
                fm.group(1)
                for line in body.splitlines()
                if LOCAL_MARKER not in line
                for fm in [re.match(r"\s*(\w+)\??\s*:", line)]
                if fm
            ]
            found.append((path, name, producer, fields))
    return found


def test_the_guard_covers_at_least_the_interface_the_bug_was_in():
    """A scan that matches nothing is worse than no test."""
    covered = {name for _, name, _, _ in _annotated_interfaces()}
    assert "Signal" in covered, (
        "EquitySignals.tsx's Signal interface lost its @api-source annotation — "
        "that is the interface the reason/reasons mismatch lived in"
    )


@pytest.mark.parametrize(
    "tsx,name,producer,fields",
    _annotated_interfaces(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_every_declared_field_exists_on_the_producer(tsx, name, producer, fields):
    rel, _, symbol = producer.partition("#")
    path = ROOT / rel
    assert path.exists(), (
        f"{tsx.relative_to(ROOT)}'s {name} names producer {rel} — which does "
        "not exist. Fix the @api-source path."
    )
    assert fields, f"{name} declares no fields — the annotation is matching the wrong block"
    source = _producer_body(path, symbol or None)
    produced = set(re.findall(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']\s*:', source))
    missing = [f for f in fields if f not in produced]
    assert not missing, (
        f"{tsx.relative_to(ROOT)} :: {name} declares {missing}, which "
        f"{producer} never emits as a key.\n"
        "Either the backend renamed it, the frontend guessed it, or it is a "
        "UI-only field — in which case mark that line `// @api-local`."
    )
