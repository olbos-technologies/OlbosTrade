"""
Reading the caller's tier, and refusing what it does not grant.

Sits on top of auth_deps: require_session has already resolved the caller and
put them on conn.state.user by the time anything here runs, so this never hits
the database. That matters — a tier lookup per request on routes that already
do real work would be a query nobody notices until it is thousands a minute.

THE FAIL-SAFE DIRECTION IS DIFFERENT FOR THE TWO CASES, and it is worth being
explicit because they look alike:

  auth disabled   → UNLIMITED. There are no accounts; the operator is the only
                    caller and already had everything. Restricting here would
                    take features away from an install that never asked for
                    tiers at all.
  auth enabled,
  no tier resolved → FREE. Somebody is signed in but their tier did not come
                    through. That is a broken read, and the safe answer to a
                    broken read is the least access, not the most.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from starlette.requests import HTTPConnection

from app.api.auth_deps import current_user
from app.core.config import settings
from app.services.tier_limits import TierLimits, limits_for


def caller_tier(conn: HTTPConnection) -> str | None:
    """The signed-in caller's tier, or None when auth is off."""
    if not settings.auth_enabled:
        return None
    user = current_user(conn)
    # "" and a missing key both mean "signed in, tier unknown" — limits_for
    # maps any unrecognised label to FREE. Returning None here instead would
    # hand them UNLIMITED, which is the bug this branch exists to avoid.
    return user.get("tier") or ""


def caller_limits(conn: HTTPConnection) -> TierLimits:
    """FastAPI dependency: the limits in force for this request."""
    return limits_for(caller_tier(conn))


def require_broker_access(conn: HTTPConnection) -> None:
    """Refuse a caller whose tier does not include broker-connected routes.

    403, not 404. Hiding the endpoint would be the enumeration-style answer,
    and it is the wrong trade here: these paths are on the pricing page, the
    caller is authenticated, and "your plan does not include this" is the
    thing they need to read. A 404 would send someone to support over a
    working system.
    """
    if caller_limits(conn).broker_access:
        return
    raise HTTPException(
        status_code=403,
        detail="Connecting a broker requires the Elite plan.",
    )


def clamp_history_years(conn: HTTPConnection, requested: int | None) -> int:
    """The smaller of what was asked for and what the tier allows.

    Clamps rather than refusing: someone on Free asking for five years should
    get their one year, not an error. The response says what they got — see
    the `history_years` field the callers attach.
    """
    allowed = caller_limits(conn).history_years
    if allowed is None:
        # No cap. An unspecified request still needs a number, and the only
        # honest one is what was asked for.
        return requested if requested and requested > 0 else 0
    if requested is None or requested <= 0:
        return allowed
    return min(requested, allowed)


def cap_symbols(conn: HTTPConnection, symbols: list) -> list:
    """Trim a symbol list to the tier's watchlist allowance.

    Order is the caller's, not sorted here: whatever ranking the route applied
    is the thing to truncate, so a Free user sees the FIRST symbol by that
    ranking rather than an alphabetical accident.
    """
    cap = caller_limits(conn).watchlist_symbols
    if cap is None:
        return symbols
    return symbols[:cap]


def cap_watchlist(conn: HTTPConnection, payload: dict) -> dict:
    """Trim one serialised watchlist's symbols to the tier's allowance.

    A COPY, never a mutation. These dicts come straight from
    watchlist_service._serialize and in some call paths are the same objects a
    cache or a later request could hand out again; editing in place would let
    one Free caller's cap leak into what everyone else sees.

    `total_symbols` and `capped` are added rather than left implicit, because
    a truncated list that does not say it was truncated is indistinguishable
    from a short watchlist — and the UI has to be able to say "upgrade to see
    the other 41" rather than silently showing one name.
    """
    symbols = payload.get("symbols") or []
    kept = cap_symbols(conn, symbols)
    if len(kept) == len(symbols):
        return payload
    return {**payload, "symbols": kept,
            "total_symbols": len(symbols), "capped": True}


def cap_watchlists(conn: HTTPConnection, payloads: list) -> list:
    """cap_watchlist across a collection.

    The cap is PER WATCHLIST, not shared across them: a Free caller sees one
    symbol from each list rather than one symbol in total. Capping the total
    would make the second and subsequent lists render as empty, which reads as
    a broken response rather than as a plan limit.
    """
    return [cap_watchlist(conn, p) for p in payloads]


#: Days per year used to turn a tier's history allowance into a date.
#: 365.25 rather than 365, so a five-year window does not drift a day earlier
#: each leap year and quietly widen what Pro can reach.
_DAYS_PER_YEAR = 365.25


def earliest_start(conn: HTTPConnection, now: date | None = None) -> date:
    """The oldest date this caller's tier may reach back to."""
    today = now or datetime.now(timezone.utc).date()
    years = caller_limits(conn).history_years
    if years is None:
        return date.min                  # no floor at all
    return today - timedelta(days=round(years * _DAYS_PER_YEAR))


def clamp_start_date(conn: HTTPConnection, requested: str | None,
                     now: date | None = None) -> str | None:
    """Move a requested ISO start date forward to the tier's earliest.

    Clamps rather than refusing, for the same reason clamp_history_years does:
    a Free caller who asks for ten years should get their one year back, not
    an error that makes every default date picker look broken.

    An unparseable date — including None and "" — is returned UNTOUCHED, which
    is why the annotation admits None. This is a tier control, not a
    validator — the route's own parsing already rejects malformed input with a
    message about the date, and swallowing it here would turn "2024-13-01" into
    a silent, confusing clamp instead.
    """
    if not requested:
        return requested
    try:
        asked = datetime.fromisoformat(requested).date()
    except (ValueError, TypeError):
        return requested
    floor = earliest_start(conn, now)
    return floor.isoformat() if asked < floor else requested
