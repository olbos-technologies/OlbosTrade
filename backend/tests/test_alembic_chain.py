"""
The migration chain must be a single unbroken line.

CLAUDE.md lists duplicate revision ids under "things that have broken before",
and the consequence is worse than a failed migration: deploy/hetzner/update.sh
runs `alembic upgrade head` after rebuilding, so a chain Alembic cannot resolve
takes the DEPLOY down, not just the schema change. Two branches both numbered
0036 pass code review easily — each file reads correctly on its own, and only
the pair is wrong.

Parsed from the files rather than by importing them, so this needs no database
and no Alembic context.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# The annotation is optional because both styles are in this directory:
# `revision = "0034"` and `revision: str = "0001"`. A pattern that matched only
# one silently halves the chain it is checking.
REVISION = re.compile(r'^revision(?:\s*:[^=\n]+)?\s*=\s*["\']([^"\']+)["\']', re.M)
DOWN = re.compile(
    r'^down_revision(?:\s*:[^=\n]+)?\s*=\s*(?:["\']([^"\']+)["\']|None)', re.M)


def _chain() -> dict[str, tuple[str | None, str]]:
    """{revision: (down_revision, filename)}"""
    out: dict[str, tuple[str | None, str]] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text()
        rev = REVISION.search(text)
        down = DOWN.search(text)
        assert rev, f"{path.name} declares no revision id"
        assert down, f"{path.name} declares no down_revision"
        assert rev.group(1) not in out, (
            f"duplicate revision id {rev.group(1)!r} in {path.name} and "
            f"{out[rev.group(1)][1]} — `alembic upgrade head` cannot resolve "
            "this, and update.sh runs it during deploy"
        )
        out[rev.group(1)] = (down.group(1), path.name)
    return out


def test_the_scan_reads_the_migrations():
    """Guards the guard: a regex that matched nothing would pass everything."""
    chain = _chain()
    assert len(chain) > 20, f"only found {len(chain)} migrations; scan is broken"


def test_revision_ids_are_unique():
    _chain()  # the duplicate assertion lives in the parse


def test_every_down_revision_exists():
    chain = _chain()
    missing = [(rev, down, name) for rev, (down, name) in chain.items()
               if down is not None and down not in chain]
    assert not missing, (
        "a migration points at a parent that is not in this directory: "
        + ", ".join(f"{name} -> {down!r}" for _, down, name in missing)
    )


def test_there_is_exactly_one_base_and_one_head():
    """Two heads is the shape a duplicate id usually takes after a bad merge:
    both files are valid, both are reachable, and Alembic refuses to pick."""
    chain = _chain()
    bases = [rev for rev, (down, _) in chain.items() if down is None]
    parents = {down for down, _ in chain.values() if down is not None}
    heads = [rev for rev in chain if rev not in parents]

    assert len(bases) == 1, f"expected one base migration, found {sorted(bases)}"
    assert len(heads) == 1, (
        f"expected one head, found {sorted(heads)} — `alembic upgrade head` is "
        "ambiguous with more than one, and the deploy runs exactly that"
    )


def test_no_two_migrations_share_a_parent():
    """A fork is the same deploy failure as a duplicate id, one step earlier."""
    seen: dict[str, str] = {}
    forks = []
    for rev, (down, name) in sorted(_chain().items()):
        if down is None:
            continue
        if down in seen:
            forks.append(f"{seen[down]} and {name} both follow {down!r}")
        seen[down] = name
    assert not forks, "the chain forks: " + "; ".join(forks)
