"""
Default-deny coverage: every route is either authenticated or deliberately public.

This is the actual deliverable of auth Phase 2. The wiring is easy; what is
hard is that it stays true. There are ~160 registered routes, and the failure
mode of per-route auth is that someone adds route 161 without the dependency
and nobody notices until it matters.

So rather than trusting review, this test enumerates what FastAPI actually
registered and asserts each path is covered by the app-level dependency or
named in the allowlist. A new public path has to be added to PUBLIC_EXACT /
PUBLIC_PREFIXES on purpose, in a diff, where it can be argued about.
"""

from __future__ import annotations

import pytest

from app.api.auth_deps import PUBLIC_EXACT, PUBLIC_PREFIXES, is_public_path


def _walk(routes, prefix: str = ""):
    """Yield every route path, descending into included routers.

    TWO SHAPES, because FastAPI changed one. Through 0.111 — the version
    requirements.txt pins — include_router() FLATTENED the child's routes into
    app.routes, so every path was a direct member. From ~0.135 it appends a
    lazy `_IncludedRouter` wrapper instead, holding the child router and the
    prefix it was mounted at, and resolves it at request time.

    That difference is not cosmetic here. Under the newer shape app.routes
    holds ten real routes and thirty-one wrappers, so the coverage assertion
    below found nothing to object to and PASSED — a security test reporting
    green because it could no longer see the thing it audits. The one signal
    was test_websocket_routes_are_enumerated failing, which is exactly why
    that canary exists. Handle both shapes rather than pinning a version,
    since the pin is what would silently rot next time.
    """
    for r in routes:
        original = getattr(r, "original_router", None)
        if original is not None:
            ctx = getattr(r, "include_context", None)
            yield from _walk(original.routes, prefix + getattr(ctx, "prefix", ""))
            continue
        path = getattr(r, "path", None)
        if path:
            yield prefix + path


def _registered_paths() -> list[str]:
    """
    Every route, HTTP and WebSocket alike.

    This originally filtered on hasattr(r, "methods"), which quietly dropped
    WebSocket routes — and that blind spot hid a real break: the app-level
    dependency was annotated Request, which FastAPI cannot supply in a
    WebSocket scope, so /api/ibkr/live raised TypeError on every connection
    even with auth disabled. The test could not see the route, so CI stayed
    green. Do not narrow this filter again.
    """
    import app.main as main_mod
    return sorted({
        p for p in _walk(main_mod.app.routes)
        if p.startswith(("/api", "/health", "/ws"))
    })


def test_enough_routes_are_visible_to_audit():
    """A floor, so "sees nothing" can never again read as "nothing wrong".

    Every other assertion in this file is of the form "no path is X". All of
    them hold vacuously against an empty list, which is precisely what a
    FastAPI upgrade produced. This is the one assertion that fails when the
    enumeration breaks rather than when the app does.
    """
    paths = _registered_paths()
    assert len(paths) > 100, (
        f"only {len(paths)} routes visible, but this app registers ~160. The "
        "enumeration is broken, not the app — every coverage assertion below "
        "is passing vacuously. See _walk()."
    )


def test_websocket_routes_are_enumerated():
    """Guards the filter above against being narrowed back to HTTP-only."""
    assert "/api/ibkr/live" in _registered_paths(), (
        "WebSocket routes must be covered — they are as capable of leaking "
        "data as any GET, and they were invisible to this test once already"
    )


def test_the_app_carries_a_global_auth_dependency():
    """
    Protection is applied once, on the app, not per route. If this ever becomes
    per-route the coverage guarantee below silently stops meaning anything.
    """
    import app.main as main_mod
    from app.api.auth_deps import require_session

    # FastAPI(dependencies=[...]) lands on app.router.dependencies, not
    # app.dependencies — checked against the running app rather than assumed.
    deps = getattr(main_mod.app.router, "dependencies", None) or []
    assert any(getattr(d, "dependency", None) is require_session for d in deps), (
        "require_session must be an app-level dependency — per-route auth is "
        "how route 161 ships unprotected"
    )


def test_every_registered_route_is_protected_or_explicitly_public():
    unprotected = [p for p in _registered_paths() if is_public_path(p)]
    # Everything public must be there ON PURPOSE. If this list grows, the diff
    # is the review.
    expected_public = {
        "/api/auth/login",
        "/api/auth/logout",
        # Boot-time "is auth even on, and do I hold a session". Public because
        # with auth off a 401 from /me is ambiguous between "logged out" and
        # "nothing to log into". Reports only the caller's own session.
        "/api/auth/status",
        # Both health paths: the container healthcheck and the nginx probe
        # cannot hold a session, and a healthcheck that 401s marks a working
        # container unhealthy and restarts it in a loop.
        "/api/health",
        "/health",
        # Asking for an account and redeeming an approved one. The caller has
        # no account yet, which is the entire point of both routes.
        #
        # The operator's review queue is NOT here, and not on this prefix. It
        # is a GET on /api/admin/access-requests, because this allowlist
        # matches on PATH and not on METHOD: had the queue been a GET on
        # /api/access-requests, the entry below would have published every
        # pending email address to anyone who asked.
        "/api/access-requests",
        "/api/access-requests/claim",
    }
    surprising = set(unprotected) - expected_public
    assert not surprising, (
        f"These routes are reachable without a session: {sorted(surprising)}. "
        "Either protect them, or add them to expected_public here with a reason."
    )


def test_health_stays_public():
    """Container health checks cannot log in."""
    assert is_public_path("/api/health")
    assert is_public_path("/health")


def test_login_is_public_but_me_is_not():
    """Obvious, and exactly the pair worth pinning."""
    assert is_public_path("/api/auth/login")
    assert not is_public_path("/api/auth/me")


@pytest.mark.parametrize("path", [
    "/api/trade-desk/execute",
    "/api/risk/kill-switch/trigger",
    "/api/portfolio/positions",
    "/api/equity/signals",
    "/api/options/signals",
    "/api/backtest/run",
])
def test_sensitive_paths_are_never_public(path):
    assert not is_public_path(path), f"{path} must require a session"


def test_allowlist_prefixes_cannot_swallow_the_api():
    """
    A prefix like "/" or "/api" in PUBLIC_PREFIXES would silently make
    everything public while every other test still passed.
    """
    for prefix in PUBLIC_PREFIXES:
        assert prefix not in ("", "/", "/api"), f"dangerously broad public prefix: {prefix!r}"
        assert not "/api".startswith(prefix.rstrip("/")) or prefix.startswith("/api/"), (
            f"prefix {prefix!r} would expose API routes"
        )


def test_exact_allowlist_contains_no_api_wildcards():
    for path in PUBLIC_EXACT:
        assert not path.endswith("*"), f"wildcards are not matched literally: {path!r}"


@pytest.mark.parametrize("path", [
    "/docs-internal",
    "/assets-private",
    "/openapi.json.bak",
    "/staticfiles/secrets",
    "/healthz-admin",
])
def test_a_prefix_does_not_extend_to_sibling_paths(path):
    """
    is_public_path used a bare startswith, so every allowlist entry was an
    open-ended wildcard: "/static" made "/staticfiles/secrets" public, because
    it merely begins with the same characters. Raised in review.
    """
    assert not is_public_path(path), (
        f"{path} is not beneath any allowlisted prefix and must require a session"
    )


@pytest.mark.parametrize("path", [
    "/docs",
    "/docs/oauth2-redirect",
    "/static/app.css",
    "/assets/logo.svg",
])
def test_real_children_of_a_prefix_stay_public(path):
    """The other half — tightening the match must not break what it allows."""
    assert is_public_path(path)
