"""
Which routes Elite gates, stated as a list rather than left to grep.

The gate itself is tested in test_tier_limits.py. What this file pins is
WHERE it is applied, because that is the part that rots: a new order-placing
route added next to the existing ones will carry require_api_key and
rate_limit out of habit and can easily miss the tier check, and nothing about
the resulting endpoint looks wrong.

So the check runs against the registered app, the same way
test_auth_route_coverage.py does — asking FastAPI what it actually resolved,
not asking the source what it appears to say.
"""

from __future__ import annotations

from app.api.tier_deps import require_broker_access


def _routes_with(dependency):
    """Every path whose resolved dependency tree contains `dependency`.

    Walks _IncludedRouter wrappers as well as flattened routes — see the
    note in test_auth_route_coverage.py, where a FastAPI upgrade made an
    identical enumeration silently see ten routes out of ~160.
    """
    import app.main as main_mod

    found = set()

    def walk(routes, prefix=""):
        for r in routes:
            original = getattr(r, "original_router", None)
            if original is not None:
                ctx = getattr(r, "include_context", None)
                walk(original.routes, prefix + getattr(ctx, "prefix", ""))
                continue
            path = getattr(r, "path", None)
            dep = getattr(r, "dependant", None)
            if not path or dep is None:
                continue
            stack = [dep]
            while stack:
                d = stack.pop()
                if getattr(d, "call", None) is dependency:
                    found.add(prefix + path)
                    break
                stack.extend(getattr(d, "dependencies", []))

    walk(main_mod.app.routes)
    return found


#: Every route that can reach a broker. Elite alone.
EXPECTED_ELITE_ONLY = {
    "/api/trade-desk/execution-mode",
    "/api/trade-desk/approve/{signal_id}",
    "/api/trade-desk/reject/{signal_id}",
    "/api/trade-desk/rotation-review/{review_id}/approve",
    "/api/trade-desk/rotation-review/{review_id}/reject",
    "/api/trade-desk/manual-trade",
    "/api/trade-desk/close-position",
    "/api/trade-desk/close-untracked-position",
    "/api/trade-desk/signal",
}


def test_exactly_the_broker_touching_routes_are_elite_only():
    gated = _routes_with(require_broker_access)

    assert gated == EXPECTED_ELITE_ONLY, (
        f"unexpectedly gated: {sorted(gated - EXPECTED_ELITE_ONLY)}; "
        f"missing the gate: {sorted(EXPECTED_ELITE_ONLY - gated)}"
    )


def test_the_enumeration_is_not_silently_empty():
    """The assertion above is an equality, so it cannot pass vacuously — but
    this says so out loud, because the equivalent check in
    test_auth_route_coverage.py DID go blind on a FastAPI upgrade and every
    assertion downstream of it passed anyway."""
    assert len(_routes_with(require_broker_access)) >= 9


def test_the_kill_switch_is_never_tier_gated():
    """Deliberate, and the one exception worth stating.

    The kill switch STOPS trading. Refusing someone the ability to stop, on
    the grounds that their plan does not include it, is the wrong direction
    on a safety control — and it would be a genuinely dangerous thing to do
    to a user whose tier was misread. Reading a position is not execution
    either, so the analysis and log routes stay open too.
    """
    gated = _routes_with(require_broker_access)
    for path in ("/api/trade-desk/kill-switch",
                 "/api/trade-desk/pending",
                 "/api/trade-desk/evaluate-equity",
                 "/api/trade-desk/evaluate-options",
                 "/api/trade-desk/execution-log",
                 "/api/trade-desk/rotation-reviews"):
        assert path not in gated, f"{path} must not require a tier"


def test_live_market_data_is_not_elite_gated():
    """Pro pays for "full live equity + options signal feed", so the live data
    socket is not an execution route and must not be behind broker access.

    It is also a WebSocket: an HTTPException raised during a handshake is not
    translated into a close frame by Starlette and surfaces as a server error,
    so gating it with this dependency would break the connection rather than
    refuse it. Both reasons point the same way.
    """
    assert "/api/ibkr/live" not in _routes_with(require_broker_access)
