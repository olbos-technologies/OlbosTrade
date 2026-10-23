"""The verification traps stay in AGENTS.md, where every agent reads them.

Guards against a real failure mode this repository has already produced.

Every rule that prevents a confidently wrong conclusion — a scan that matches
nothing, a safety control with no caller, jsdom proving nothing about layout,
vitest stubbing CSS, `-qq` swallowing the pytest summary — used to live only
in `CLAUDE.md`, which `AGENTS.md` introduces as "Claude-specific repository
details". Codex and Cursor are therefore told that file is not theirs, and
they worked from 23 lines of policy that contained none of the traps.

The cost was measurable: R2-A..R2-P (PRs #131-#146) added 13 modules
implementing a fail-closed entry-safety subsystem, and zero of those module
names appear anywhere in `app/api/routes/trade_desk.py`, the module that
actually places broker orders. R2-P's own `check_entry_before_submission()`
has no caller. Each PR was focused, tested, CI-green and compliant with every
written rule in AGENTS.md — and enforced nothing, while reading in the
changelog as though it did. `docs/audits/R2Q_ORDER_SUBMISSION_INTEGRATION.md`
records the same finding for R2-P independently.

So this test pins two properties:

  1. AGENTS.md still states each trap. Deleting one fails here rather than
     silently in a future PR written without it.
  2. CLAUDE.md does not grow a second long-form copy. Two copies drift, and
     the divergent one is the one somebody follows.

Per the first trap it is itself encoding, this asserts it read something: a
lower bound on file size and an explicit failure when a file is missing,
so a rename or a move cannot make the whole guard vacuously green.
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"

#: Phrases, not paraphrases: each is the stated rule in AGENTS.md. Reworded
#: deliberately? Update this list in the same change, having read the trap.
REQUIRED_TRAPS = [
    "A scan must assert that it read something",
    "A safety control must be reachable from the path it protects",
    "No linter or formatter is configured",
    "jsdom does no layout",
    "vitest stubs CSS",
    "pytest is already quiet",
    "A skipped test is a green tick that proves nothing",
    "Confirm a failure against a clean tree",
    "Alembic revision ids must be unique",
    "Failed reads must not look like safe values",
]

#: Long-form explanation markers. These belong to AGENTS.md's copy only; if
#: they reappear in CLAUDE.md the two files have started to diverge again.
LONG_FORM_MARKERS = ["import.meta.glob", "junitxml", "git stash"]


@pytest.fixture(scope="module")
def agents_text() -> str:
    assert AGENTS.exists(), f"{AGENTS} is missing — this guard reads nothing"
    text = AGENTS.read_text()
    # A truncated or stubbed AGENTS.md would pass a naive `in` check for
    # nothing at all, so require it to still be a real document.
    assert len(text) > 2000, (
        f"AGENTS.md is only {len(text)} bytes — too short to still contain "
        f"the shared rules; this guard would be reading almost nothing"
    )
    return text


@pytest.mark.parametrize("trap", REQUIRED_TRAPS)
def test_agents_md_states_each_verification_trap(agents_text, trap):
    assert trap in agents_text, (
        f"AGENTS.md no longer states: {trap!r}. Every coding agent reads "
        f"AGENTS.md and most do not read CLAUDE.md, so a trap removed here "
        f"is a trap no longer told to Codex or Cursor. Read this test's "
        f"docstring before changing the list."
    )


def test_the_trap_list_is_not_quietly_emptied():
    """A scan over an empty list passes while checking nothing."""
    assert len(REQUIRED_TRAPS) >= 10, (
        "REQUIRED_TRAPS was shortened. Traps are removed only when the "
        "underlying hazard is gone from the repository, not to make this "
        "test pass."
    )


def test_claude_md_points_at_the_shared_rules(agents_text):
    """A Claude session must be routed to the file holding the traps."""
    assert CLAUDE.exists(), f"{CLAUDE} is missing — this guard reads nothing"
    text = CLAUDE.read_text()
    assert len(text) > 2000, f"CLAUDE.md is only {len(text)} bytes"
    assert "AGENTS.md" in text, (
        "CLAUDE.md no longer references AGENTS.md, so a Claude session is "
        "never routed to the shared verification traps"
    )


@pytest.mark.parametrize("marker", LONG_FORM_MARKERS)
def test_claude_md_does_not_grow_a_second_copy(marker):
    """One home for the long form. Two copies drift; the stale one gets read."""
    text = CLAUDE.read_text()
    assert marker not in text, (
        f"{marker!r} is back in CLAUDE.md. The long-form traps live in "
        f"AGENTS.md so there is a single copy to keep true — point at them "
        f"instead of restating them."
    )
