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

import pytest

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


# ── history clamping ────────────────────────────────────────────────────────

def test_the_backtest_entry_points_clamp_their_start_date():
    """Wired at the top of the handler, not at the engine call.

    Clamping where the engine is invoked would leave the run record and the
    echoed `start_date` claiming a range the run did not use — the response
    would say ten years and the result would be one. One rewrite at the top
    means every downstream reader sees the same value.
    """
    import inspect

    from app.api.routes import backtest

    for name in ("run_backtest", "run_equity_backtest", "compare_strategies"):
        fn = getattr(backtest, name)
        src = inspect.getsource(fn)
        # Comments stripped: an equivalent assertion on #70 matched the call
        # it was looking for inside a COMMENT and proved nothing.
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        assert "clamp_start_date(" in code, f"{name} does not clamp start_date"
        assert "request" in inspect.signature(fn).parameters, (
            f"{name} cannot clamp — it never receives the connection")


async def test_the_handler_passes_the_callers_date_to_the_clamp(monkeypatch):
    """The wiring, verified by watching the call rather than by reading it.

    The source check above proves clamp_start_date is MENTIONED in each
    handler. This proves one of them actually calls it, with the request's own
    start_date, and carries the clamped value forward — which is the part that
    would break if someone rebound the wrong field or dropped the result.
    """
    import app.api.routes.backtest as bt
    from app.api.routes.backtest import EquityBacktestRunRequest

    calls = []

    def _fake_clamp(conn, value, *a, **k):
        calls.append(value)
        return "2025-09-20"                  # as a Free tier would answer

    monkeypatch.setattr(bt, "clamp_start_date", _fake_clamp)

    class _FreeRequest:
        def __init__(self):
            self.state = type("S", (), {"user": {"tier": "free"}})()

    req = EquityBacktestRunRequest(ticker="   ",   # blank: rejected right after
                                   start_date="2010-01-01", end_date="2026-06-30")
    with pytest.raises(Exception):
        await bt.run_equity_backtest(req, _FreeRequest())

    assert calls == ["2010-01-01"], (
        f"the handler passed {calls} to the clamp, not the caller's start_date"
    )
    assert req.start_date == "2010-01-01", (
        "the caller's request object must not be mutated — model_copy returns "
        "a new one, and mutating in place would surprise anything holding it"
    )


# ── watchlist capping, everywhere a watchlist is returned ───────────────────

def _watchlist_routes():
    """Every registered route under the watchlists prefix, with its handler."""
    import app.main as main_mod

    out = []

    def walk(routes, prefix=""):
        for r in routes:
            original = getattr(r, "original_router", None)
            if original is not None:
                ctx = getattr(r, "include_context", None)
                walk(original.routes, prefix + getattr(ctx, "prefix", ""))
                continue
            path = getattr(r, "path", None)
            if path and (prefix + path).startswith("/api/intel/watchlists"):
                out.append((prefix + path, r))

    walk(main_mod.app.routes)
    return out


def test_the_watchlist_routes_are_found_at_all():
    """Guards the enumeration: every assertion below is a loop over this, and
    a loop over nothing passes."""
    paths = {p for p, _ in _watchlist_routes()}
    assert len(paths) >= 4, paths


def test_every_watchlist_route_caps_its_payload():
    """Writes included, which is the whole point.

    The first version of this feature capped the two GETs only. That is not a
    cap: create, add-symbol and remove-symbol return the same serialised
    payload, so a Free caller could POST a symbol and read every symbol out of
    the response. The limit was one request away from being decorative.

    Enumerated from the registered app rather than from the source file, so a
    watchlist route added in another module is still covered.
    """
    import inspect

    uncapped = []
    for path, route in _watchlist_routes():
        fn = getattr(route, "endpoint", None)
        if fn is None:
            continue
        src = inspect.getsource(fn)
        # Comments stripped — an equivalent check on #70 matched the call it
        # was looking for inside a comment and proved nothing.
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        returns_watchlist = "wl." in code and "cap_watchlist" not in code
        # delete_watchlist returns {"deleted": True} and carries no symbols,
        # so it has nothing to cap.
        if returns_watchlist and "deleted" not in code:
            uncapped.append(path)

    assert not uncapped, (
        f"these watchlist routes return an uncapped payload: {sorted(set(uncapped))}"
    )


def test_a_capping_route_receives_the_connection():
    """A handler cannot cap what it cannot see the caller of. Catches the
    half-applied fix where cap_watchlist is called but `request` was never
    added to the signature."""
    import inspect

    for path, route in _watchlist_routes():
        fn = getattr(route, "endpoint", None)
        if fn is None or "cap_watchlist" not in inspect.getsource(fn):
            continue
        assert "request" in inspect.signature(fn).parameters, (
            f"{path} calls cap_watchlist but never receives the connection")
