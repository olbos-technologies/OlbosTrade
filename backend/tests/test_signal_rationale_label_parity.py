"""
Guard: the UI's factor labels must track the scoring engine that produces them.

PR #192 fixed the live-signal rationale — the scanner stores contributing
factors as a dict under `reasons`, the card read a string `reason` nothing
ever set, so every card showed the same empty-state copy. The fix maps each
factor key to a plain-language label in
`frontend/src/utils/equitySignalRationale.ts`.

That map is 22 keys hardcoded in TypeScript against 22 keys hardcoded in
Python, with nothing holding them together. `score_equity_signal()` can add,
rename or remove a factor and no test fails: `humanizeKey()` quietly
title-cases whatever it does not recognise, so a renamed factor degrades from
"MACD bullish cross" to "Macd Bull Xover" on screen and the label it used to
have becomes dead code nobody notices. Graceful degradation is the right
runtime behaviour and the wrong CI behaviour.

This lives in the backend suite because it reads a frontend file, which the
frontend's own jsdom environment cannot do — the same reason
test_static_cache_headers.py and test_display_font_scope.py live here.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENGINE = ROOT / "backend" / "app" / "services" / "equity_signal_engine.py"
LABELS = ROOT / "frontend" / "src" / "utils" / "equitySignalRationale.ts"


def _read(path: pathlib.Path) -> str:
    assert path.exists(), f"{path} is missing — this guard reads nothing without it"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"{path} is empty — this guard would pass vacuously"
    return text


def engine_factor_keys() -> set[str]:
    keys = set(re.findall(r'reasons\[\s*"([a-z0-9_]+)"\s*\]\s*=', _read(ENGINE)))
    assert len(keys) >= 20, (
        f"only {len(keys)} factor keys found in equity_signal_engine.py — the "
        "scan pattern has drifted and this guard is no longer reading the engine"
    )
    return keys


def label_map_keys() -> set[str]:
    text = _read(LABELS)
    _, sep, rest = text.partition("const REASON_LABELS")
    assert sep, "REASON_LABELS not found in equitySignalRationale.ts"
    block = rest.split("};", 1)[0]
    keys = set(re.findall(r"^\s*([a-z0-9_]+):", block, re.MULTILINE))
    assert len(keys) >= 20, f"only {len(keys)} labels parsed — the block shape has changed"
    return keys


def test_every_scoring_factor_has_a_written_label():
    missing = sorted(engine_factor_keys() - label_map_keys())
    assert not missing, (
        f"these factors reach a user as auto-title-cased identifiers: {missing}. "
        "humanizeKey() keeps the card working, which is why nothing else catches "
        "this. Add a label for each in frontend/src/utils/equitySignalRationale.ts."
    )


def test_no_label_describes_a_factor_the_engine_cannot_emit():
    stale = sorted(label_map_keys() - engine_factor_keys())
    assert not stale, (
        f"equitySignalRationale.ts labels factors the engine no longer emits: "
        f"{stale}. Remove them so the map stays an accurate inventory of the "
        "scoring rules rather than a graveyard of renamed ones."
    )


def test_the_two_volume_labels_still_mention_their_vwap_condition():
    """
    `volume_confirmed_bull` fires on `vol_ratio > threshold AND above_vwap`
    (equity_signal_engine.py), not on volume alone — its label says so, and a
    volume-only rewording would overstate what the factor checked.
    """
    text = _read(LABELS)
    for key in ("volume_confirmed_bull", "volume_confirmed_bear"):
        line = next((ln for ln in text.splitlines() if ln.strip().startswith(key + ":")), "")
        assert "VWAP" in line, (
            f"{key}'s label dropped its VWAP condition: {line.strip()!r}. The engine "
            "gates this factor on price's side of VWAP as well as volume."
        )
