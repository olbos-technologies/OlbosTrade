r"""
update.sh cleans up after the build it just did.

The coupling this guards: the deploy builds with `--no-cache`, which makes
BuildKit write every layer it produces and then guarantees the next build will
not read any of them. Nothing collected that. By 2026-09-17 the cache had
reached 40.83GB over 185 entries — 51GB of /var/lib/containerd on a 75GB disk
at 82% full, produced entirely by routine deploys. It had to be cleared by hand
from a rescue system, which is a poor way to find out.

`--no-cache` without a prune is the bug, so that is what this file asserts:
not that a particular command is present, but that the two stay coupled and in
the right order. A prune that ran BEFORE the build would collect the previous
deploy's garbage and leave its own — technically present, quietly useless.

It also pins `image prune` to the dangling-only form. `-a` evicts every image
without a running container, and this host runs other compose projects
(ibkr-gateway). A deploy that happened to coincide with one of
those being stopped would delete its image. That is a real footgun in an
unattended script, and the dangling-only sweep already collects what a rebuild
orphans.

PARSING: comments are stripped, and the danger is real — the step-5 comment
block in update.sh explains itself using `--no-cache`, `builder prune -af` and
`image prune -f` as prose, which is exactly how an earlier guard in this repo
(test_login_rate_limit_deployment.py) came to match its own explanation and
pass against a broken deployment.

But the stripping is NOT what holds here, and saying otherwise would invite
someone to lean on it. Mutation-testing: delete the real prune commands, keep
the comment, disable the stripping — the tests still fail, correctly. What
saves them is that every pattern below requires the literal `docker ` prefix,
and the comment's shorthand omits it.

That makes the stripping defence in depth against a FUTURE comment written in
full command form. It also means the prefix is load-bearing: shortening any
pattern here to `builder prune` or `image prune` would make the comment block
match, and then only the stripping would stand between this file and a guard
that tests its own prose.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UPDATE_SH = REPO / "deploy" / "hetzner" / "update.sh"


def executable_lines() -> list[str]:
    """update.sh as bash sees it: comments and blanks dropped, backslash-continued
    lines joined.

    Joining matters. A prune written across a continuation puts the command on
    one physical line and its `|| { ... }` guard on the next, so a per-line
    check for `||` would report the guard missing on a script that has it —
    and a per-line check for a flag would miss one moved past the break. Both
    are false readings of a correct file, which is the same class of error as
    a guard matching its own prose.
    """
    out: list[str] = []
    pending = ""
    for line in UPDATE_SH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1].rstrip() + " "
            continue
        out.append((pending + stripped).strip())
        pending = ""
    if pending:
        out.append(pending.strip())
    return out


def index_of(pattern: str) -> int | None:
    for i, line in enumerate(executable_lines()):
        if re.search(pattern, line):
            return i
    return None


def test_the_script_was_actually_parsed():
    """Guards the guard: an empty list satisfies every 'not present' assertion."""
    lines = executable_lines()
    assert lines, "no executable lines parsed out of update.sh"
    assert any("docker compose" in l for l in lines), (
        f"update.sh no longer runs docker compose at all; parsed {len(lines)} lines"
    )


def test_a_no_cache_build_is_followed_by_a_cache_prune():
    # --no-cache is detected SEPARATELY from the build command. Keying the skip
    # off an unparseable build line means a formatting change — wrapping the
    # build across a continuation, reordering its flags — silently disables
    # this guard instead of failing it. Fail-open in a test that exists to
    # catch a 40GB disk leak is worse than no test, because it reads as
    # covered. Caught in review on PR #64.
    uses_no_cache = any("--no-cache" in l for l in executable_lines())
    if not uses_no_cache:
        # Dropping --no-cache is a legitimate fix for the same problem: the
        # cache would then be USED rather than orphaned. Nothing to guard.
        import pytest
        pytest.skip("update.sh no longer builds with --no-cache")

    build = index_of(r"docker compose .*build")
    assert build is not None, (
        "update.sh passes --no-cache but no `docker compose ... build` line "
        "could be located. The guard below orders the prune against the build, "
        "so an unparseable build line must fail here rather than skip."
    )

    prune = index_of(r"docker\s+builder\s+prune")
    assert prune is not None, (
        "update.sh builds with --no-cache but never prunes the build cache. "
        "BuildKit writes every layer and the next build is told to ignore all "
        "of them, so the store grows without bound — it reached 40.83GB on the "
        "Hetzner box and filled the disk to 82%."
    )
    assert prune > build, (
        f"the build cache prune (line {prune}) runs BEFORE the build (line "
        f"{build}). It would collect the previous deploy's garbage and leave "
        f"its own, which looks like a fix and is not one."
    )


def test_image_prune_stays_dangling_only():
    lines = executable_lines()
    # Any option position, not just the first. `docker image prune -f -a` is
    # the same destructive command and the old pattern — which anchored `-a`
    # immediately after `prune` — let it through. Caught in review on PR #64.
    greedy = [
        l for l in lines
        if re.search(r"docker\s+image\s+prune\b", l)
        and re.search(r"(?:^|\s)(?:--all\b|-[a-zA-Z]*a[a-zA-Z]*\b)", l)
    ]
    assert not greedy, (
        f"update.sh runs an image prune with -a: {greedy}. That evicts every "
        f"image without a RUNNING container, and this host carries other "
        f"compose project (ibkr-gateway) whose images would be "
        f"deleted if they were stopped when the deploy ran. Rebuilding a tag "
        f"already orphans the image it replaces, so dangling-only collects this "
        f"deploy's garbage without reaching into anyone else's."
    )


def test_cleanup_cannot_fail_the_deploy():
    """`set -e` is on, and step 5 runs after the app is already serving."""
    lines = executable_lines()
    prunes = [l for l in lines if re.search(r"docker\s+(builder|image)\s+prune", l)]
    assert prunes, "no prune commands found at all"
    unguarded = [l for l in prunes if "||" not in l]
    assert not unguarded, (
        f"these prunes would abort the script under `set -e`: {unguarded}. "
        f"Cleanup runs after migrations, so the deployment has already "
        f"succeeded by then — failing the run here reports a working deploy as "
        f"a broken one, and the operator rolls back something that was fine."
    )


def test_the_prunes_keep_their_cross_project_age_filter():
    """The filter IS the safety fix; without it the command is the bug again.

    `docker builder prune` and `docker image prune` both act on the whole
    daemon, not this compose project. The first version of this step used a
    bare `-af`, justified by "our build passes --no-cache so the cache is
    worthless" — true of OlbosTrade's layers and silent about the sibling
    projects on this host (ibkr-gateway). `--filter until=` is
    what scopes it; a guard that only checks a prune EXISTS would let the
    unscoped form back in unnoticed. Caught in review on PR #64.
    """
    lines = executable_lines()
    prunes = [l for l in lines if re.search(r"docker\s+(builder|image)\s+prune", l)]
    assert prunes, "no prune commands found at all"

    unfiltered = [l for l in prunes if not re.search(r"--filter\s+until=", l)]
    assert not unfiltered, (
        f"these prunes have no age filter: {unfiltered}. Both commands are "
        f"daemon-wide, so an unfiltered prune discards cache and dangling "
        f"images belonging to other compose projects on this host — forcing "
        f"expensive rebuilds elsewhere and destroying images a neighbour may "
        f"be holding for rollback."
    )
