"""The deploy must migrate from the NEW image before any new container serves.

Two separate mistakes are pinned here, both of which this repository has
actually shipped:

1. Starting containers and migrating afterwards. Between those two steps the
   new code is live against the old schema. For an additive migration whose
   table the new code reads, that is the code running without something it
   needs — for migration 0040 it meant entries refusing for the length of a
   deploy.

2. "Pre-applying" the migration with `docker exec olbostrade-backend alembic
   upgrade head`. That runs inside the container that is STILL RUNNING THE OLD
   IMAGE, which does not contain the new revision, so it reports success
   having applied nothing. A command that appears to close the window and does
   not is worse than no command: it is believed.

There is no way to assert this by running the deploy here — there is no Docker
daemon, no registry and no production database in the test environment — so
this reads the script. That is weaker than executing it and is the reason the
ordering is also stated in docs/runbook.md: a reviewer changing the order has
to get past both.
"""
from __future__ import annotations

import pathlib
import re

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "hetzner" / "update.sh"


@pytest.fixture(scope="module")
def script() -> str:
    text = SCRIPT.read_text()
    assert text.strip(), "update.sh is empty — this guard would pass on nothing"
    return text


def _line_of(script: str, pattern: str) -> int:
    for i, line in enumerate(script.splitlines()):
        if line.lstrip().startswith("#"):
            continue          # the comments discuss the wrong order on purpose
        if re.search(pattern, line):
            return i
    raise AssertionError(f"update.sh no longer contains anything matching {pattern!r}")


def test_the_migration_runs_from_the_newly_built_image(script):
    """Not via `docker exec` into the container still running the old one."""
    migrate = _line_of(script, r"alembic upgrade head")
    line = script.splitlines()[migrate]
    assert "compose" in line or "run --rm" in script.splitlines()[migrate - 1], (
        "the migration must run in a container made from the image just built"
    )
    assert not re.search(r"docker exec \S+ python3 -m alembic upgrade head", script), (
        "`docker exec` migrates inside the OLD image, which cannot contain a "
        "revision that arrived with this deploy"
    )


def test_the_migration_runs_before_containers_are_started(script):
    build = _line_of(script, r"compose .*build")
    migrate = _line_of(script, r"alembic upgrade head")
    up = _line_of(script, r"compose .*up -d")

    assert build < migrate, "cannot migrate from an image that has not been built"
    assert migrate < up, (
        "containers were started before the migration — that reopens the "
        "window where new code serves against the old schema"
    )


def test_a_failed_migration_stops_the_deploy(script):
    """The migration must not be guarded into a warning.

    `set -e` is what makes a failed migration stop the deploy before any new
    container starts, leaving the old image serving a schema it matches. A
    `|| true` or a `|| echo` on that line would turn the one failure that must
    halt the deploy into a line of log output.
    """
    assert re.search(r"^set -euo pipefail", script, re.M)
    migrate_line = script.splitlines()[_line_of(script, r"alembic upgrade head")]
    assert "||" not in migrate_line, (
        "the migration's failure is guarded; it must be fatal"
    )
