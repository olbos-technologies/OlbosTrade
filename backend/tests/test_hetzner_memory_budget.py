r"""
The Hetzner stack's memory limits fit the machine the file is written for.

Two failures are possible here and they are not symmetric.

Set a limit too LOW and one container is killed while the host sits idle. That
is what happened: the backend was capped at 1500M and the kernel killed uvicorn
five times between 2026-09-14 and 2026-09-17 with `constraint=CONSTRAINT_MEMCG`
and `anon-rss:1530596kB`, on a box with gigabytes free. `restart:
unless-stopped` brought it back each time, so nobody noticed for three days.

Set them too HIGH and the containers can collectively overcommit the host. Then
the kernel OOM-killer picks a victim globally — possibly Postgres, possibly
sshd — and that is a worse day than losing one container to its own cgroup.

So this file asserts a budget, not a number: the limits total must fit the CX33
this compose file targets, and the backend's limit must stay above the value we
have actually watched it die at. Both are properties of the deployment, which
is why they live in a test rather than a comment that nothing enforces.

On parsing: the comment above `memory: 3000M` quotes the kernel's own
"Memory cgroup out of memory:" line, so the file now contains prose that a loose
regex would read as a setting. This repo has made that exact mistake before —
test_login_rate_limit_deployment.py documents a guard that matched its own
explanatory comment, so deleting the real value left every assertion green.

What prevents it here is the ANCHORED regex (`^\s+memory:\s*(\d+)M\s*$`),
not the comment stripping. Mutation-testing showed the stripping carries no
weight on its own: disabling it leaves all four tests passing, because the
kernel's line has a `#` before `Memory` and no digits-plus-M after a colon.
The stripping stays as defence in depth for a future comment that is less
convenient, but anyone loosening that regex should know it is the only thing
actually holding.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.hetzner.yml"

#: Total RAM on the CX33 this file targets, as the rescue system reports it.
HOST_TOTAL_MB = 7771

#: Ceiling for the limits declared in THIS file. The remainder is for the
#: containers that share the host from other compose projects (ibkr-gateway,
#: measured at ~617M and uncapped) plus the OS and the Docker daemon. Limits
#: are ceilings rather than usage, so this is the worst-case sum, not the
#: expected one.
#:
#: Caddy used to be counted in that remainder at 256M. It moved into this
#: compose file on 2026-09-19, so its limit is now inside the sum below rather
#: than outside it — the same 256M, just on the other side of the line. The
#: budget did not change; what changed is which total it constrains.
STACK_BUDGET_MB = 5000

#: The backend's measured death point: anon-rss at the moment the kernel killed
#: it, from /var/log/syslog. A limit at or below this is known-broken, not
#: merely tight — we have watched it fail there.
OBSERVED_OOM_MB = 1530


def compose_without_comments() -> str:
    """The compose file with comment lines removed (see the module docstring)."""
    return "\n".join(
        line for line in COMPOSE.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def declared_memory_limits() -> dict[str, int]:
    """{service: limit_in_MB} for every service declaring one."""
    text = compose_without_comments()
    limits: dict[str, int] = {}
    service = None
    for line in text.splitlines():
        svc = re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", line)
        if svc:
            service = svc.group(1)
            continue
        mem = re.match(r"^\s+memory:\s*(\d+)M\s*$", line)
        if mem and service:
            limits[service] = int(mem.group(1))
    return limits


def test_the_limits_were_actually_found():
    """Guards the guard — an empty dict satisfies every sum below."""
    limits = declared_memory_limits()
    assert limits, (
        "no `memory:` limits were parsed out of docker-compose.hetzner.yml. "
        "Either they were removed (which un-caps every container) or the file's "
        "shape changed and this test has stopped checking anything."
    )
    assert "backend" in limits, (
        f"the backend service declares no memory limit; parsed: {limits}"
    )


def test_the_stack_fits_the_machine():
    limits = declared_memory_limits()
    total = sum(limits.values())
    assert total <= STACK_BUDGET_MB, (
        f"the limits in docker-compose.hetzner.yml total {total}M, over the "
        f"{STACK_BUDGET_MB}M budgeted for this stack on a {HOST_TOTAL_MB}M host. "
        f"ibkr-gateway shares this machine from another compose project and "
        f"is not counted here. Overcommitting means the kernel "
        f"picks the victim globally — which can be Postgres or sshd, not the "
        f"container that grew. Per service: {limits}"
    )


def test_the_backend_limit_stays_above_where_we_watched_it_die():
    limits = declared_memory_limits()
    backend = limits["backend"]
    assert backend > OBSERVED_OOM_MB, (
        f"the backend limit is {backend}M, at or below the {OBSERVED_OOM_MB}M "
        f"anon-rss the kernel recorded when it killed uvicorn on 2026-09-14 "
        f"through 2026-09-17. This is not a tight limit, it is a measured "
        f"failure — the container will be killed again."
    )


def test_no_single_service_can_take_the_host_alone():
    """A limit above the host's RAM is not a limit."""
    limits = declared_memory_limits()
    too_big = {s: m for s, m in limits.items() if m >= HOST_TOTAL_MB}
    assert not too_big, (
        f"{too_big} declare limits at or above the host's {HOST_TOTAL_MB}M. "
        f"A cgroup ceiling above physical RAM cannot contain anything — the "
        f"host OOM-killer fires first, and it does not have to choose the "
        f"container that misbehaved."
    )
