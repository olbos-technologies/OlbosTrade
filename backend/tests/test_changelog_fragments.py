"""
changelog.d keeps the shape the convention depends on.

Entries live one-per-file so that two concurrent branches never touch the same
lines. That property is easy to lose quietly: someone appends a second entry to
an existing fragment, or names a file so generically that two changes pick the
same name, and the collision the directory exists to prevent returns — at the
merge button, where `merge=union` does not reach.

These tests pin the shape, not the content: a name that is unlikely to
collide, a heading in the form the old file used, and a body. Every scan asserts
it read something, because a check that matches no files passes vacuously.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIR = ROOT / "changelog.d"
NAME = re.compile(r"^\d{8}-[a-z0-9][a-z0-9-]*\.md$")


def _fragments() -> list[pathlib.Path]:
    assert DIR.is_dir(), "changelog.d/ is missing — the convention has no home"
    found = sorted(p for p in DIR.glob("*.md") if p.name != "README.md")
    assert found, (
        "changelog.d holds no fragments at all, so every check below would pass "
        "vacuously. If every entry was folded into a release, delete this test "
        "together with the directory."
    )
    return found


def test_the_readme_explains_the_convention():
    readme = DIR / "README.md"
    assert readme.is_file() and readme.read_text(encoding="utf-8").strip()
    text = readme.read_text(encoding="utf-8")
    assert "Do not edit `CHANGELOG.md`" in text, (
        "the one instruction an agent must not miss is gone from the README"
    )


def test_fragment_names_are_dated_and_slugged():
    bad = [p.name for p in _fragments() if not NAME.match(p.name)]
    assert not bad, (
        f"fragment names must look like 20261101-short-slug.md, got {bad}. A "
        "date plus a slug is what makes two concurrent changes unlikely to pick "
        "the same file."
    )


def test_every_fragment_opens_with_the_changelog_heading():
    bad = [
        p.name for p in _fragments()
        if not p.read_text(encoding="utf-8").lstrip().startswith("## Unreleased — ")
    ]
    assert not bad, (
        f"{bad} do not start with '## Unreleased — '. Keep the heading the old "
        "file used so entries read the same in both places."
    )


def test_every_fragment_has_a_body():
    bad = [
        p.name for p in _fragments()
        if not any(
            line.lstrip().startswith("- ")
            for line in p.read_text(encoding="utf-8").splitlines()
        )
    ]
    assert not bad, f"{bad} have a heading and nothing under it"


def test_a_fragment_holds_one_change():
    """
    Two headings in one file means someone appended to an existing fragment —
    exactly the shared-lines edit this directory exists to prevent.
    """
    bad = [
        p.name for p in _fragments()
        if sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")) != 1
    ]
    assert not bad, (
        f"{bad} contain more or fewer than one '## ' heading. One change per "
        "file: add a new fragment instead of extending an existing one."
    )
