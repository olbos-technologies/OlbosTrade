"""
Guard: the equity scanner's rationale must actually reach the UI.

This test exists because it did not. The scanner (app/main.py, _scan_one)
stores the signal's rationale under the key ``reasons`` — a dict of factor
name -> signed point contribution, built by
``equity_signal_engine.score_equity_signal``. EquitySignals.tsx read
``sig.reason``: a different key, holding a different type. Neither the name
nor the shape matched, so every signal card in production rendered
"No plain-language rationale supplied by this producer" and every detail
drawer showed an empty evidence list — for months, silently, because a
missing field and a producer that supplies no rationale look identical on
screen.

It lives in the backend suite because it reads frontend files, which the
frontend's own jsdom test environment cannot do (see the other guard tests
in this directory for the same reason).

Two things are asserted:
  1. The scanner still emits ``reasons``, and the frontend still reads it.
  2. Every factor key the engine can emit has a plain-language phrase, so a
     factor added to the engine cannot quietly reach a user as a raw
     snake_case identifier.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENGINE = ROOT / "backend" / "app" / "services" / "equity_signal_engine.py"
MAIN = ROOT / "backend" / "app" / "main.py"
RATIONALE = ROOT / "frontend" / "src" / "utils" / "signalRationale.ts"
PAGE = ROOT / "frontend" / "src" / "pages" / "EquitySignals.tsx"


def _read(path: pathlib.Path) -> str:
    assert path.exists(), f"{path} is missing — this guard reads nothing without it"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"{path} is empty — this guard would pass vacuously"
    return text


def engine_factor_keys() -> set[str]:
    keys = set(re.findall(r'reasons\[\s*"([a-z0-9_]+)"\s*\]\s*=', _read(ENGINE)))
    # A scan that matches nothing is worse than no test: pin the floor.
    assert len(keys) >= 20, f"only found {len(keys)} factor keys — the scan pattern has drifted"
    return keys


def mapped_phrase_keys() -> set[str]:
    text = _read(RATIONALE)
    body = text.split("const PHRASES", 1)
    assert len(body) == 2, "PHRASES map not found in signalRationale.ts"
    block = body[1].split("};", 1)[0]
    return set(re.findall(r"^\s*([a-z0-9_]+):", block, re.MULTILINE))


def test_the_scanner_still_emits_reasons():
    assert '"reasons":' in _read(MAIN), (
        "app/main.py no longer stores a 'reasons' key on the equity signal — "
        "the UI reads that key, so renaming it blanks every signal card."
    )


def test_the_card_reads_the_key_the_scanner_writes():
    page = _read(PAGE)
    assert "describeSignalReasons(sig.reasons" in page, (
        "EquitySignals.tsx must derive its rationale from sig.reasons (the key "
        "the scanner writes). Reading sig.reason alone is the original defect."
    )
    assert "reasonList(sig.reasons" in page, (
        "the detail drawer must be fed from sig.reasons, or its evidence list "
        "is empty for every scanner-produced signal."
    )


def test_every_engine_factor_has_a_plain_language_phrase():
    missing = sorted(engine_factor_keys() - mapped_phrase_keys())
    assert not missing, (
        "these scoring factors would reach a user as raw identifiers: "
        f"{missing}. Add a phrase for each in frontend/src/utils/signalRationale.ts."
    )


def test_the_phrase_map_has_no_entries_the_engine_cannot_emit():
    stale = sorted(mapped_phrase_keys() - engine_factor_keys())
    assert not stale, (
        f"signalRationale.ts maps factors the engine no longer emits: {stale}. "
        "Remove them so the map stays an accurate inventory of the scoring rules."
    )
