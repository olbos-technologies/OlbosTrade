"""
Tier limits, and the promise they make to the landing page.

The single most important test here is the last one: it reads Landing.tsx and
checks that what the pricing cards advertise is what the API enforces. Those
numbers lived in the TSX alone, labelled "(planned)", with nothing behind
them. Now that something IS behind them, the failure mode changes shape — a
limit edited on one side and not the other is no longer a cosmetic mismatch,
it is the product charging for something it does not deliver, or withholding
something it sold.

Everything above it is the fail-safe direction, which is the part that is easy
to get subtly backwards: two different "we don't know" cases that must resolve
in OPPOSITE directions.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.api.tier_deps import (
    cap_symbols, caller_limits, caller_tier, clamp_history_years,
    clamp_start_date, earliest_start, require_broker_access,
)
from app.core.config import settings
from app.models.user import TIER_ELITE, TIER_FREE, TIER_PRO
from app.services.tier_limits import (
    BY_TIER, DELAY_EOD, DELAY_LIVE, ELITE, FREE, PRO, UNLIMITED, limits_for,
)

LANDING = (pathlib.Path(__file__).parent.parent.parent
           / "frontend/src/pages/Landing.tsx")


class _Conn:
    """Stands in for an HTTPConnection carrying a resolved user."""

    def __init__(self, user=None):
        self.state = type("S", (), {"user": user})()


# ── the fail-safe directions ────────────────────────────────────────────────

def test_an_install_with_auth_off_is_not_limited(monkeypatch):
    """The whole point of shipping this without breaking anything.

    A single-operator install has no accounts, so there is no tier. Falling
    back to FREE there would silently take the full watchlist, live signals
    and broker access away from the one person who already had them — an
    upgrade that removes features, with no account to upgrade.
    """
    monkeypatch.setattr(settings, "auth_enabled", False)

    assert caller_tier(_Conn()) is None
    assert caller_limits(_Conn()) is UNLIMITED
    assert caller_limits(_Conn()).broker_access is True


def test_a_signed_in_caller_with_no_tier_gets_the_least_access(monkeypatch):
    """The opposite direction, and the one that is easy to get backwards.

    Auth is ON and somebody is signed in, but their tier did not come through
    — a broken read, a half-written row, a user created by a path that forgot
    the column. Resolving that to "no tier, therefore unlimited" would turn
    every such row into free Elite access.
    """
    monkeypatch.setattr(settings, "auth_enabled", True)

    for user in ({}, {"id": "x"}, {"id": "x", "tier": ""}, {"id": "x", "tier": None}):
        assert caller_limits(_Conn(user)) is FREE, user


def test_an_unrecognised_tier_is_not_trusted(monkeypatch):
    """A label this build does not know is a typo or a newer version's row.
    Either way it is not evidence of having paid for anything."""
    monkeypatch.setattr(settings, "auth_enabled", True)

    assert caller_limits(_Conn({"tier": "enterprise"})) is FREE
    assert caller_limits(_Conn({"tier": "ELITE"})) is FREE, (
        "tier matching is case-sensitive and the column stores lower case; "
        "accepting 'ELITE' here would mean the check can be dodged by case"
    )


def test_every_known_tier_has_a_row():
    assert set(BY_TIER) == {TIER_FREE, TIER_PRO, TIER_ELITE}
    for tier, row in BY_TIER.items():
        assert limits_for(tier) is row


# ── the limits themselves ───────────────────────────────────────────────────

def test_free_is_the_most_restrictive_row_on_every_axis():
    """Pinned as an ordering rather than as values, so a future limit change
    cannot accidentally make Free more generous than Pro on one axis while
    every value-based test still passes."""
    assert FREE.watchlist_symbols is not None
    assert PRO.watchlist_symbols is None and ELITE.watchlist_symbols is None
    assert FREE.history_years <= PRO.history_years <= ELITE.history_years
    assert not FREE.sees_live_signals
    assert PRO.sees_live_signals and ELITE.sees_live_signals
    assert not FREE.broker_access and not PRO.broker_access
    assert ELITE.broker_access


def test_broker_access_is_elite_alone():
    """The one capability that separates Elite from Pro. If this ever becomes
    true for Pro, Elite has nothing left to sell."""
    assert [t for t, r in BY_TIER.items() if r.broker_access] == [TIER_ELITE]


# ── enforcement helpers ─────────────────────────────────────────────────────

@pytest.mark.parametrize("tier,allowed", [
    (TIER_FREE, False), (TIER_PRO, False), (TIER_ELITE, True),
])
def test_require_broker_access_follows_the_table(monkeypatch, tier, allowed):
    monkeypatch.setattr(settings, "auth_enabled", True)
    conn = _Conn({"tier": tier})

    if allowed:
        require_broker_access(conn)              # does not raise
    else:
        with pytest.raises(Exception) as exc:
            require_broker_access(conn)
        assert exc.value.status_code == 403


def test_a_refused_broker_call_says_why(monkeypatch):
    """403 with a reason, not 404. These paths are on the pricing page and the
    caller is authenticated — "your plan does not include this" is the thing
    they need to read. A 404 sends someone to support over a working system.
    """
    monkeypatch.setattr(settings, "auth_enabled", True)

    with pytest.raises(Exception) as exc:
        require_broker_access(_Conn({"tier": TIER_FREE}))

    assert exc.value.status_code == 403
    assert "Elite" in exc.value.detail


def test_an_operator_install_keeps_broker_access(monkeypatch):
    """Auth off must not lock the operator out of their own execution routes.
    This is the regression that would break every existing deployment."""
    monkeypatch.setattr(settings, "auth_enabled", False)
    require_broker_access(_Conn())               # does not raise


@pytest.mark.parametrize("tier,asked,expected", [
    (TIER_FREE, 5, 1),          # clamped down
    (TIER_FREE, None, 1),       # unspecified means "as much as allowed"
    (TIER_FREE, 0, 1),          # nonsense treated as unspecified
    (TIER_PRO, 5, 5),
    (TIER_PRO, 10, 5),          # clamped to the tier, not the request
    (TIER_PRO, 2, 2),           # asking for less than allowed is honoured
    (TIER_ELITE, 99, 5),
])
def test_history_is_clamped_not_refused(monkeypatch, tier, asked, expected):
    """Someone on Free asking for five years should get one year, not an
    error. A hard refusal makes every default-range UI control break for
    them, which reads as a bug rather than as a plan limit."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    assert clamp_history_years(_Conn({"tier": tier}), asked) == expected


def test_history_is_unclamped_with_auth_off(monkeypatch):
    """An install with no accounts keeps every year it already had.

    UNLIMITED.history_years used to be ELITE.history_years, which reads as
    "the most any tier gets" and is not the same thing as "no limit": it
    clamped an auth-disabled install to five years of history it had always
    been able to reach. Found by checking the clamp's actual output rather
    than by reading it.
    """
    monkeypatch.setattr(settings, "auth_enabled", False)

    assert UNLIMITED.history_years is None
    assert clamp_history_years(_Conn(), 20) == 20
    assert clamp_start_date(_Conn(), "1990-01-01") == "1990-01-01"


def test_symbols_are_capped_in_the_callers_order(monkeypatch):
    """Truncation, not selection. Whatever ranking the route applied is the
    thing to cut, so a Free user sees the top-ranked symbol rather than an
    alphabetical accident."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    ranked = ["NVDA", "AAPL", "MSFT"]

    assert cap_symbols(_Conn({"tier": TIER_FREE}), ranked) == ["NVDA"]
    assert cap_symbols(_Conn({"tier": TIER_PRO}), ranked) == ranked
    assert cap_symbols(_Conn({"tier": TIER_ELITE}), ranked) == ranked


def test_symbols_are_uncapped_with_auth_off(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    assert cap_symbols(_Conn(), ["A", "B", "C"]) == ["A", "B", "C"]


# ── the contract with the landing page ──────────────────────────────────────

def _landing_plans() -> dict:
    """Parse the PLANS table out of Landing.tsx.

    Reading the TSX rather than duplicating its numbers here, because a copy
    is exactly the thing that drifts — and drift is now a product defect, not
    a cosmetic mismatch.
    """
    src = LANDING.read_text()
    plans = {}
    for block in re.finditer(
        r'name:\s*"(Free|Pro|Elite)".*?limits:\s*\[(.*?)\]', src, re.S
    ):
        name, body = block.group(1), block.group(2)
        plans[name.lower()] = {
            f: lim for f, lim in
            re.findall(r'feature:\s*"([^"]+)",\s*limit:\s*"([^"]+)"', body)
        }
    return plans


def test_the_landing_page_still_publishes_three_plans():
    """Guards the parser above: if it silently matched nothing, every
    assertion below would pass over an empty dict."""
    plans = _landing_plans()
    assert set(plans) == {"free", "pro", "elite"}
    for name, limits in plans.items():
        assert limits, f"{name} parsed with no limits — the parser is broken"


@pytest.mark.parametrize("plan,tier", [
    ("free", TIER_FREE), ("pro", TIER_PRO), ("elite", TIER_ELITE),
])
def test_tier_limits_match_the_landing_page(plan, tier):
    """What the pricing card sells is what the API enforces.

    Deliberately matched loosely on the copy ("1 ticker", "End of day") rather
    than on an exact string, so the marketing wording can be edited without
    breaking the build — but the NUMBER and the KIND cannot change on one side
    alone.
    """
    published = _landing_plans()[plan]
    enforced = BY_TIER[tier]

    watchlist = published["Watchlist coverage"].lower()
    if enforced.watchlist_symbols is None:
        assert "full" in watchlist, (
            f"{plan} is enforced as the full watchlist but advertises "
            f"{watchlist!r}")
    else:
        assert str(enforced.watchlist_symbols) in watchlist, (
            f"{plan} is enforced at {enforced.watchlist_symbols} symbols but "
            f"advertises {watchlist!r}")

    delay = published["Signal delay"].lower()
    expected_word = "live" if enforced.signal_delay == DELAY_LIVE else "end of day"
    assert expected_word in delay, (
        f"{plan} is enforced as {enforced.signal_delay!r} but advertises "
        f"{delay!r}")

    history = published["Historical data"].lower()
    assert str(enforced.history_years) in history, (
        f"{plan} is enforced at {enforced.history_years} years but advertises "
        f"{history!r}")

    broker = published["Broker connections"].lower()
    if enforced.broker_access:
        assert "none" not in broker, (
            f"{plan} grants broker access but advertises {broker!r}")
    else:
        assert "none" in broker, (
            f"{plan} has no broker access but advertises {broker!r}")


# ── watchlist capping ───────────────────────────────────────────────────────

from app.api.tier_deps import cap_watchlist, cap_watchlists  # noqa: E402

_WL = {"slug": "mega", "name": "Mega Cap", "description": "", "is_system": True,
       "symbols": [{"symbol": s, "asset_class": "equity"}
                   for s in ("NVDA", "AAPL", "MSFT")]}


def test_a_capped_watchlist_says_it_was_capped(monkeypatch):
    """A truncated list that does not admit it is indistinguishable from a
    short one. The UI has to be able to say "upgrade to see the other 2"
    rather than silently showing a one-name watchlist as if that were all
    there is."""
    monkeypatch.setattr(settings, "auth_enabled", True)

    out = cap_watchlist(_Conn({"tier": TIER_FREE}), _WL)

    assert [s["symbol"] for s in out["symbols"]] == ["NVDA"]
    assert out["capped"] is True
    assert out["total_symbols"] == 3


def test_an_uncapped_watchlist_is_not_labelled_capped(monkeypatch):
    """Pro and Elite must not get a `capped` flag they would have to explain."""
    monkeypatch.setattr(settings, "auth_enabled", True)

    out = cap_watchlist(_Conn({"tier": TIER_PRO}), _WL)

    assert out is _WL
    assert "capped" not in out


def test_capping_never_mutates_the_payload(monkeypatch):
    """These dicts come from watchlist_service._serialize and on some paths
    are shared. Editing in place would let one Free caller's cap leak into
    what every other caller sees — a data bug that looks like a tier bug."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    before = [s["symbol"] for s in _WL["symbols"]]

    cap_watchlist(_Conn({"tier": TIER_FREE}), _WL)

    assert [s["symbol"] for s in _WL["symbols"]] == before
    assert "capped" not in _WL


def test_the_cap_is_per_watchlist_not_shared_across_them(monkeypatch):
    """One symbol from EACH list, not one symbol in total. A shared budget
    would render the second and later lists empty, which reads as a broken
    response rather than as a plan limit."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    second = {**_WL, "slug": "semis"}

    out = cap_watchlists(_Conn({"tier": TIER_FREE}), [_WL, second])

    assert [len(w["symbols"]) for w in out] == [1, 1]


def test_watchlists_are_whole_with_auth_off(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    assert cap_watchlist(_Conn(), _WL) is _WL


# ── history windows as dates ────────────────────────────────────────────────

_NOW = __import__("datetime").date(2026, 9, 20)


@pytest.mark.parametrize("tier,expected", [
    (TIER_FREE, "2025-09-20"),
    (TIER_PRO, "2021-09-20"),
    (TIER_ELITE, "2021-09-20"),
])
def test_a_too_early_start_date_is_moved_forward(monkeypatch, tier, expected):
    monkeypatch.setattr(settings, "auth_enabled", True)
    conn = _Conn({"tier": tier})

    assert clamp_start_date(conn, "2010-01-01", _NOW) == expected
    assert earliest_start(conn, _NOW).isoformat() == expected


def test_a_start_date_inside_the_window_is_left_alone(monkeypatch):
    """Clamping must not move a date the caller is entitled to."""
    monkeypatch.setattr(settings, "auth_enabled", True)

    assert clamp_start_date(_Conn({"tier": TIER_FREE}), "2026-08-01", _NOW) \
        == "2026-08-01"


def test_a_malformed_date_is_not_silently_clamped(monkeypatch):
    """This is a tier control, not a validator. The route's own parsing
    rejects a bad date with a message about the date; turning "2024-13-01"
    into a silent clamp here would replace that with a confusing success."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    conn = _Conn({"tier": TIER_FREE})

    for bad in ("2024-13-01", "not-a-date", "", None):
        assert clamp_start_date(conn, bad, _NOW) == bad


def test_the_year_length_does_not_drift_across_leap_years(monkeypatch):
    """365.25, not 365. A five-year window built from 365-day years reaches
    about a day further back each leap year, quietly widening what Pro can
    see — the kind of drift nobody notices until the numbers stop matching
    the pricing page."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    import datetime as _dt

    conn = _Conn({"tier": TIER_PRO})
    span = (_NOW - earliest_start(conn, _NOW)).days
    assert span == round(5 * 365.25)
    assert span > 5 * 365, "a plain 365-day year loses the leap days"
    assert isinstance(earliest_start(conn, _NOW), _dt.date)
