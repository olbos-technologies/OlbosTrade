"""
In-process rate limiting for auth-gated mutate routes (order placement,
approvals, kill-switch trigger). No Redis in this stack — single-process
Hetzner deployment — so this matches the existing in-memory dict+TTL
pattern already used by options_chain_cache.py / orderflow_engine.py, just
keyed per-client with a list of recent request timestamps instead of a
single cached value.

Applied alongside require_api_key/require_api_key_configured (app/api/
deps.py), not instead of — this only limits request rate, it doesn't
replace the identity check.
"""
from __future__ import annotations

import hmac
import logging
import time

from fastapi import Header, HTTPException, Request

from app.core.config import settings

logger = logging.getLogger(__name__)

PROXY_SECRET_HEADER = "X-Olbos-Proxy-Secret"
_warned_no_secret = False


def client_ip(request: Request) -> str:
    """The caller's address, believed only when the proxy vouches for it.

    Every per-IP control in this file is worth exactly what this function is
    worth, so the trust rule lives in one place rather than three.

    WHY NOT --proxy-headers. uvicorn's ProxyHeadersMiddleware decides whether
    to believe X-Forwarded-For by PEER ADDRESS, and on the Hetzner stack that
    cannot separate the frontend from anything else: the backend shares the
    external docker_default network with Caddy and the IBKR gateway, because
    that is where IBKR_HOST resolves. So the middleware's only workable
    setting there was --forwarded-allow-ips=*, which trusts every container on
    that network to name its own IP and duck its own rate limit. uvicorn 0.29
    also matches trusted hosts by exact string, so a CIDR silently matches
    nothing — narrowing it was not available either. Issue #60.

    A shared secret decides on PROVENANCE instead. The frontend nginx is the
    only thing that sets X-Forwarded-For from $remote_addr — overwriting, not
    appending — so a request carrying the secret has a header nginx wrote, not
    one the caller supplied. Nothing about the network layout can change that,
    which is the property peer-address trust never had.

    With no secret configured this returns the direct peer, which behind a
    proxy is the proxy: every caller lands in one bucket. Safe, but degraded,
    so docker-compose.hetzner.yml requires the variable rather than defaulting
    it, and this warns once.
    """
    peer = request.client.host if request.client else "unknown"
    secret = settings.trusted_proxy_secret

    if not secret:
        global _warned_no_secret
        if not _warned_no_secret:
            _warned_no_secret = True
            logger.warning(
                "TRUSTED_PROXY_SECRET is not set — X-Forwarded-For is ignored and "
                "every caller behind the proxy shares one rate-limit bucket. "
                "Set it on both the backend and frontend services."
            )
        return peer

    # compare_digest, not ==: the header is attacker-supplied, and a
    # short-circuiting comparison leaks the secret a byte at a time.
    #
    # BYTES, not str. compare_digest refuses non-ASCII str outright —
    # "comparing strings with non-ASCII characters is not supported", a
    # TypeError — and HTTP allows obs-text bytes in a header value, which
    # Starlette hands over as a latin-1 decoded str. So a forged header of
    # b"\xff\xfe..." turned an invalid credential into an unhandled 500
    # instead of the peer fallback below, on the login path. Raised in review
    # on #63 and reproduced.
    #
    # latin-1 on the way back is the exact inverse of Starlette's decode, so
    # this round-trips any header to the bytes that arrived on the wire; utf-8
    # for the secret is how the env var reached us. For an ASCII secret — what
    # `openssl rand -hex 32` produces — the two agree, and a non-ASCII secret
    # still matches because nginx sends its utf-8 bytes and latin-1 decode +
    # encode returns them unchanged.
    presented = request.headers.get(PROXY_SECRET_HEADER, "")
    if not presented:
        return peer
    if not hmac.compare_digest(presented.encode("latin-1"), secret.encode("utf-8")):
        return peer

    # nginx OVERWRITES the header with a single value, so the first entry is
    # the address it observed. Split anyway: if some future hop appends, the
    # leftmost is still the one nginx wrote and the rest are not ours to trust.
    forwarded = request.headers.get("X-Forwarded-For", "")
    first = forwarded.split(",")[0].strip()
    return first or peer

WINDOW_S = 60.0
MAX_REQUESTS = 20

_request_log: dict[str, list[float]] = {}


def _client_key(request: Request, x_api_key: str) -> str:
    """Prefer the API key as identity — it's already the primary identity
    on every route this guards. Falls back to client IP only when no key
    is configured (e.g. local dev, where require_api_key itself no-ops)."""
    return x_api_key or client_ip(request)


LOGIN_WINDOW_S = 300.0
LOGIN_MAX_ATTEMPTS = 10

_login_log: dict[str, list[float]] = {}


def login_rate_limit(request: Request) -> None:
    """
    Throttle login attempts by source IP, deliberately ignoring X-Api-Key.

    rate_limit() below keys on the API key because on the routes it guards the
    key IS the caller's identity — a shared secret they had to already possess.
    On /api/auth/login there is no such key yet, so that header is nothing but
    attacker-controlled text: sending a fresh random value per request would put
    every attempt in its own bucket and make the limit vacuous. Password
    guessing has to be limited by something the client cannot choose.

    Tighter than the operator limit (10 per 5 minutes, not 20 per minute)
    because a human logging in types a password once or twice, not twenty times.

    WHO the caller is comes from client_ip() above, not from
    request.client.host — read that function before changing anything here,
    because every per-IP limit in this file is worth exactly what it is worth.

    The short history, because this control has been wrong twice and the shape
    of both mistakes is worth keeping. First the deployment ran uvicorn without
    --proxy-headers at all, so every request carried the frontend proxy's
    address and all callers shared ONE bucket: ten failed logins from anyone
    locked out everybody for five minutes, a denial of service against login
    rather than a brake on guessing. Then the fix for that trusted
    X-Forwarded-For by PEER ADDRESS (--forwarded-allow-ips=*), which cannot
    distinguish the frontend from anything else sharing docker_default — so a
    container there could name its own IP and duck its own limit.

    Neither flag is used now. client_ip() decides on provenance instead: the
    frontend presents a shared secret alongside the X-Forwarded-For it
    overwrites, and the header is believed only when that secret matches.

    One thing still to know. Behind Caddy the frontend's own $remote_addr is
    Caddy, so Caddy-routed callers share a bucket until TRUSTED_PROXY_CIDR
    names Caddy's subnet (frontend/docker-entrypoint.sh,
    deploy/hetzner/.env.example). That is a separate knob from the secret, and
    both need setting for per-caller limiting on the domain path.
    """
    key = client_ip(request)
    now = time.monotonic()
    recent = [t for t in _login_log.get(key, []) if now - t < LOGIN_WINDOW_S]
    if len(recent) >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts — try again shortly",
        )
    recent.append(now)
    _login_log[key] = recent


SIGNUP_WINDOW_S = 3600.0
SIGNUP_MAX_REQUESTS = 5

_signup_log: dict[str, list[float]] = {}


def signup_rate_limit(request: Request) -> None:
    """
    Throttle access-request submissions by source IP.

    Keyed on client_ip() and NOT on any header the caller controls, for the
    same reason login_rate_limit is — read that function's history before
    changing anything here. A public form is the one endpoint on this app an
    unauthenticated stranger is invited to POST to, so the only identity
    available is one they cannot choose.

    Five an hour, which is tighter than login's ten per five minutes and
    deliberately so: a human asks for an account once. The cost of a false
    positive is that someone waits and retries; the cost of no limit is a
    table filling with whatever a script feels like sending, and an operator
    review queue that is useless precisely when it matters.

    Not a substitute for the enumeration protection in the route itself. This
    limits VOLUME; the route's identical response for "already has an account"
    and "does not" is what stops the form answering questions.
    """
    key = client_ip(request)
    now = time.monotonic()
    recent = [t for t in _signup_log.get(key, []) if now - t < SIGNUP_WINDOW_S]
    if len(recent) >= SIGNUP_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Too many requests — try again later",
        )
    recent.append(now)
    _signup_log[key] = recent


def rate_limit(request: Request, x_api_key: str = Header(default="", alias="X-Api-Key")) -> None:
    """20 requests / 60s per client on order-placement/kill-switch-adjacent
    routes — generous for a human operator clicking approve/manual-trade/
    kill-switch, well below anything a script hammering a leaked or
    guessed key could usefully exploit."""
    key = _client_key(request, x_api_key)
    now = time.monotonic()
    recent = [t for t in _request_log.get(key, []) if now - t < WINDOW_S]
    if len(recent) >= MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — max {MAX_REQUESTS} requests per {WINDOW_S:.0f}s",
        )
    recent.append(now)
    _request_log[key] = recent
