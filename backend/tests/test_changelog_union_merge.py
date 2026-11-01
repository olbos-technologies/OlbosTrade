"""
Guard: CHANGELOG.md keeps its union merge driver, and nothing else gets one.

Every branch adds its entry at the TOP of CHANGELOG.md, so any two concurrent
branches conflict there by construction. On 2026-10-31 that happened five
times in one evening across three agents working in parallel. Twice it left
unresolved markers committed to main; the rest of the time it re-blocked pull
requests whose own CI was green, including one that had to be re-merged twice
within ten minutes because another PR landed in between.

`merge=union` resolves it by taking both sides — which is precisely what a
human does here every time, because the entries are independent prose and
keeping both is always correct.

The second test is the important one. Union merge applied to CODE is
dangerous: it would silently splice both sides of a conflicting function into
something that compiles and misbehaves, with no marker to warn anyone. This
file exists as much to stop the pattern spreading as to keep it in place.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
GITATTRIBUTES = ROOT / ".gitattributes"

#: Extensions where a silent both-sides merge would produce broken or
#: misbehaving output rather than a longer list.
CODE_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".sql",
    ".yml", ".yaml", ".json", ".toml", ".ini", ".sh", ".conf",
}


def _rules() -> list[tuple[str, str]]:
    assert GITATTRIBUTES.exists(), (
        ".gitattributes is gone — CHANGELOG.md conflicts come straight back"
    )
    out = []
    for line in GITATTRIBUTES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        for attr in parts[1:]:
            out.append((parts[0], attr))
    assert out, ".gitattributes parsed to no rules at all — this guard would pass vacuously"
    return out


def test_the_changelog_keeps_its_union_merge():
    assert ("CHANGELOG.md", "merge=union") in _rules(), (
        "CHANGELOG.md lost `merge=union`. Every branch appends to the top of "
        "that file, so without it any two concurrent branches conflict there "
        "and someone resolves it by hand — which is how unresolved markers "
        "reached main twice on 2026-10-31."
    )


def test_union_merge_is_never_applied_to_code():
    """
    A both-sides merge of prose yields a longer list. A both-sides merge of a
    function yields code that compiles and does the wrong thing, with no
    conflict marker to catch it in review.
    """
    offenders = [
        pattern for pattern, attr in _rules()
        if attr == "merge=union" and pathlib.PurePath(pattern).suffix in CODE_SUFFIXES
    ]
    assert not offenders, (
        f"union merge is set on code: {offenders}. It resolves conflicts by "
        "concatenating both sides — safe for an append-only changelog, silently "
        "destructive for anything executable."
    )


def test_a_wildcard_never_carries_union_merge():
    """`* merge=union` or `*.md merge=union` would apply it far beyond intent."""
    wild = [p for p, a in _rules() if a == "merge=union" and ("*" in p or "?" in p)]
    assert not wild, (
        f"union merge is set via a pattern: {wild}. Name files explicitly — a "
        "wildcard will eventually catch a file where both-sides merging is wrong."
    )
