"""
Every container healthcheck must probe a route that exists AND is public.

This file exists because both halves failed at once in production and took the
site down on deploy.

The backend healthcheck probed /api/guardrails/status, which is not in
auth_deps.PUBLIC_EXACT. With AUTH_ENABLED=true every probe returned 401,
`curl -fsS` treated that as failure, and the container could never reach
healthy — so the frontend, whose depends_on requires a healthy backend,
refused to start. The backend itself was serving perfectly the entire time.

It hid for as long as it did because a container that is ALREADY RUNNING does
not re-evaluate depends_on. Turning on AUTH_ENABLED did not break anything
visible; recreating the stack weeks later did.

And the obvious fix was a trap: /api/health was named in the allowlist but no
such route was registered, so pointing the healthcheck there would have
swapped a 401 for a 404 and failed identically. Both conditions have to hold
together, which is why this test checks them together.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent.parent
COMPOSE = ROOT / "docker-compose.hetzner.yml"

#: EVERY file that probes the backend, not just the compose healthcheck.
#:
#: The first version of this scanned docker-compose.hetzner.yml alone, and
#: review immediately found two more copies of the same bug it was written to
#: catch: up.sh ran its own readiness probe against /api/guardrails/status, so
#: a fresh install would time out after 90 seconds against a healthy
#: container, and the README told operators to curl it as a post-deploy check.
#:
#: Fixing the instance in front of you and leaving its siblings is the shape
#: of defect this whole file exists to stop, so the scan covers the
#: deployment surface rather than one file of it.
PROBE_SOURCES = (
    COMPOSE,
    ROOT / "deploy/hetzner/up.sh",
    ROOT / "deploy/hetzner/update.sh",
    ROOT / "deploy/hetzner/README.md",
)


def _registered_paths() -> set:
    import app.main as main_mod

    def walk(routes, prefix=""):
        for r in routes:
            original = getattr(r, "original_router", None)
            if original is not None:
                ctx = getattr(r, "include_context", None)
                yield from walk(original.routes, prefix + getattr(ctx, "prefix", ""))
                continue
            path = getattr(r, "path", None)
            if path:
                yield prefix + path

    return set(walk(main_mod.app.routes))


def _backend_probe_paths() -> list:
    """Every backend path any deployment file curls, across PROBE_SOURCES.

    Text-scanned rather than YAML-parsed: PyYAML is not a declared dependency
    (see test_auth_flag_is_explicit.py), and these are all one-line shell
    commands anyway. Matches both the in-container form (127.0.0.1:8000) and
    the public form the README documents, since a 401 is a 401 either way.

    Lines whose first non-space character is # or comment markers are skipped
    — the fixes left explanatory comments naming the old path, and matching
    those would fail the test against correct code.
    """
    found: list = []
    for path in PROBE_SOURCES:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            found += re.findall(
                r"curl[^\"']*?(?:http://127\.0\.0\.1:8000|https://[^/\s]+)"
                r"(/[^\s\"'|>]*)",
                line,
            )
    return sorted(set(found))


def test_backend_probes_are_actually_found():
    """Vacuity guard. Every assertion below loops over this list, and a loop
    over nothing passes — which is a comfortable way to stop checking the
    thing that took the site down.

    Two or more: the compose healthcheck and up.sh's readiness probe. If this
    drops to one, a probe stopped being visible to the scan rather than
    stopping existing.
    """
    paths = _backend_probe_paths()
    assert len(paths) >= 2, (
        f"only found {paths} — the scan no longer matches the shape of the "
        "deployment probes in PROBE_SOURCES")


@pytest.mark.parametrize("path", _backend_probe_paths() or ["<none found>"])
def test_the_healthcheck_path_is_registered(path):
    """A 404 fails `curl -fsS` exactly like a 401 does.

    /api/health was in the allowlist for months with no route behind it, so
    "it's public" was not enough to make it a safe probe target.
    """
    assert path in _registered_paths(), (
        f"the healthcheck probes {path}, which is not a registered route — "
        f"curl -fsS would get a 404 and mark the container unhealthy")


@pytest.mark.parametrize("path", _backend_probe_paths() or ["<none found>"])
def test_the_healthcheck_path_is_public(path):
    """With AUTH_ENABLED=true a non-allowlisted path returns 401, and a
    healthcheck that 401s marks a working container unhealthy — then blocks
    everything with depends_on against it."""
    from app.api.auth_deps import is_public_path

    assert is_public_path(path), (
        f"the healthcheck probes {path}, which requires a session. With "
        "AUTH_ENABLED=true this container can never become healthy.")


def test_both_health_paths_exist_and_are_public():
    """/health for in-container probes, /api/health for anything coming
    through the proxy — which forwards /api and nothing else, so an external
    monitor cannot reach /health at all."""
    from app.api.auth_deps import is_public_path

    registered = _registered_paths()
    for path in ("/health", "/api/health"):
        assert path in registered, f"{path} is not registered"
        assert is_public_path(path), f"{path} is not public"


def test_the_allowlist_does_not_promise_routes_that_do_not_exist():
    """An allowlist entry for a nonexistent path is worse than no entry: it
    reads as a working public endpoint and 404s. That is precisely what sent
    the obvious fix for the outage into a second identical failure.

    Scoped to /api and /health — the allowlist also covers static asset
    prefixes served by the frontend, which this app never registers.
    """
    from app.api.auth_deps import PUBLIC_EXACT

    registered = _registered_paths()
    phantom = sorted(
        p for p in PUBLIC_EXACT
        if p.startswith(("/api", "/health")) and p not in registered
    )
    assert not phantom, (
        f"PUBLIC_EXACT allowlists paths with no route behind them: {phantom}")
