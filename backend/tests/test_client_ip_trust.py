"""
Who the rate limiter thinks you are, and why it believes it.

Issue #60. The previous design let uvicorn decide by PEER ADDRESS
(--proxy-headers --forwarded-allow-ips=*), which on the Hetzner stack could
not separate the frontend from anything else on the shared docker_default
network — the backend is on it because that is where the IBKR gateway
resolves. So any container there could name its own IP and duck its own rate
limit.

client_ip() decides on PROVENANCE instead: the frontend nginx overwrites
X-Forwarded-For with the address it observed AND presents a shared secret, so
a request carrying the secret has a header nginx wrote rather than one the
caller supplied. Network layout stops mattering.

These tests are the forgery attempts, written from the attacker's side.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from app.api import rate_limit
from app.api.rate_limit import PROXY_SECRET_HEADER, client_ip

SECRET = "s3cr3t-value-from-env"
PROXY_PEER = "172.18.0.7"      # the frontend container, as the backend sees it
REAL_CLIENT = "203.0.113.9"    # what nginx observed and wrote into XFF
ATTACKER = "198.51.100.4"      # what a forger would like to be believed


def make_request(headers: dict[str, str], peer: str = PROXY_PEER) -> Request:
    """A Starlette Request over a hand-built ASGI scope.

    Headers must be a list of lowercased byte pairs — Starlette does not
    normalise a dict for you, and getting that wrong yields a request whose
    headers are all silently absent, which would make every test below pass
    for the wrong reason.

    latin-1, NOT utf-8, for the values. ASGI carries raw bytes and Starlette
    decodes them as latin-1, so encoding a test value as utf-8 puts different
    bytes on the wire than a real server would — which silently broke the
    non-ASCII secret test until the helper was fixed rather than the code.
    """
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1"))
                    for k, v in headers.items()],
        "client": (peer, 54321),
        "query_string": b"",
    })


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "trusted_proxy_secret", SECRET)
    monkeypatch.setattr(rate_limit, "_warned_no_secret", False)


class TestTheHeaderIsBelievedOnlyWhenVouchedFor:
    def test_the_real_client_comes_through_when_the_secret_matches(self):
        ip = client_ip(make_request({
            PROXY_SECRET_HEADER: SECRET,
            "X-Forwarded-For": REAL_CLIENT,
        }))
        assert ip == REAL_CLIENT

    def test_a_forged_header_without_the_secret_is_ignored(self):
        """The attack the old design allowed.

        Any container on docker_default can open a socket to uvicorn and claim
        whatever address it likes. Without the secret that claim is discarded
        and the caller is rate-limited as the peer it actually is.
        """
        ip = client_ip(make_request({"X-Forwarded-For": ATTACKER}, peer=ATTACKER))
        assert ip == ATTACKER, "should fall back to the real peer, not the claim"

        # And the claim must not be honoured even when it names someone else —
        # that is how a forger would dodge a bucket rather than fill one.
        ip = client_ip(make_request({"X-Forwarded-For": REAL_CLIENT}, peer=ATTACKER))
        assert ip == ATTACKER

    def test_a_wrong_secret_is_ignored(self):
        ip = client_ip(make_request({
            PROXY_SECRET_HEADER: "not-the-secret",
            "X-Forwarded-For": ATTACKER,
        }, peer=PROXY_PEER))
        assert ip == PROXY_PEER

    def test_an_empty_presented_secret_never_matches(self):
        """Guards against an empty-vs-empty comparison succeeding."""
        ip = client_ip(make_request({
            PROXY_SECRET_HEADER: "",
            "X-Forwarded-For": ATTACKER,
        }))
        assert ip == PROXY_PEER

    def test_only_the_leftmost_forwarded_entry_is_taken(self):
        """nginx overwrites, so there is normally one value.

        If a hop ever appends, the leftmost is still the one nginx wrote and
        the rest are not ours to trust — taking the last would hand the
        decision to whoever appended.
        """
        ip = client_ip(make_request({
            PROXY_SECRET_HEADER: SECRET,
            "X-Forwarded-For": f"{REAL_CLIENT}, {ATTACKER}",
        }))
        assert ip == REAL_CLIENT

    def test_a_vouched_request_with_no_forwarded_header_uses_the_peer(self):
        ip = client_ip(make_request({PROXY_SECRET_HEADER: SECRET}))
        assert ip == PROXY_PEER


class TestWithNoSecretConfigured:
    def test_the_header_is_ignored_entirely(self, monkeypatch):
        """Unset means degraded-but-safe, never forgeable.

        Every caller lands in the proxy's bucket — the denial-of-service this
        whole thread started from — which is why the compose file requires the
        variable instead of defaulting it. But it must not be the other
        failure: believing an unvouched header.
        """
        monkeypatch.setattr(rate_limit.settings, "trusted_proxy_secret", "")
        ip = client_ip(make_request({
            PROXY_SECRET_HEADER: "anything",
            "X-Forwarded-For": ATTACKER,
        }))
        assert ip == PROXY_PEER

    def test_an_absent_header_does_not_match_an_unset_secret(self, monkeypatch):
        """The empty-versus-empty trap, and it is not hypothetical.

        hmac.compare_digest("", "") is True. So if the secret is unset AND the
        caller simply sends no secret header, a bare compare would MATCH and
        the forged X-Forwarded-For would be believed — the exact forgery this
        change exists to stop, handed out for free to anyone who noticed the
        variable was missing.

        Two guards prevent it: the early return on an unset secret, and the
        `not presented` check. Mutation testing showed each one alone is
        enough, which is why removing either in isolation changes nothing —
        and why this test asserts the BEHAVIOUR rather than one of them. With
        both removed it fails, and the suite had no other test that did.
        """
        monkeypatch.setattr(rate_limit.settings, "trusted_proxy_secret", "")
        ip = client_ip(make_request({"X-Forwarded-For": ATTACKER}))
        assert ip == PROXY_PEER, (
            "an unset secret plus an absent header was treated as a match, so "
            "a forged X-Forwarded-For was believed"
        )

    def test_it_warns_once_rather_than_per_request(self, monkeypatch, caplog):
        monkeypatch.setattr(rate_limit.settings, "trusted_proxy_secret", "")
        monkeypatch.setattr(rate_limit, "_warned_no_secret", False)
        with caplog.at_level("WARNING"):
            for _ in range(3):
                client_ip(make_request({}))
        hits = [r for r in caplog.records if "TRUSTED_PROXY_SECRET" in r.message]
        assert len(hits) == 1, "a per-request warning would drown the log it belongs in"


class TestTheLimiterUsesIt:
    def test_two_vouched_clients_get_separate_buckets(self, monkeypatch):
        """The point of the whole exercise.

        Ten failed logins from one address must not lock out another. Before
        the deployment fix both were the proxy; if client_ip stopped being
        used here they would silently be again.
        """
        monkeypatch.setattr(rate_limit, "_login_log", {})
        me = make_request({PROXY_SECRET_HEADER: SECRET, "X-Forwarded-For": REAL_CLIENT})
        them = make_request({PROXY_SECRET_HEADER: SECRET, "X-Forwarded-For": ATTACKER})

        for _ in range(rate_limit.LOGIN_MAX_ATTEMPTS):
            rate_limit.login_rate_limit(them)

        with pytest.raises(Exception):          # they are locked out
            rate_limit.login_rate_limit(them)
        rate_limit.login_rate_limit(me)         # I am not

    def test_unvouched_callers_share_the_proxy_bucket(self, monkeypatch):
        """The cost of an unset secret, asserted rather than assumed."""
        monkeypatch.setattr(rate_limit, "_login_log", {})
        monkeypatch.setattr(rate_limit.settings, "trusted_proxy_secret", "")
        a = make_request({"X-Forwarded-For": REAL_CLIENT})
        b = make_request({"X-Forwarded-For": ATTACKER})

        for _ in range(rate_limit.LOGIN_MAX_ATTEMPTS):
            rate_limit.login_rate_limit(a)
        with pytest.raises(Exception):
            rate_limit.login_rate_limit(b)      # b pays for a's attempts


class TestAForgedHeaderCannotCrashTheLimiter:
    def test_a_non_ascii_secret_header_falls_back_instead_of_raising(self):
        """Raised in review on #63, and reproduced before fixing.

        HTTP permits obs-text bytes in a header value, and Starlette hands
        those over as a latin-1 decoded str. hmac.compare_digest REFUSES
        non-ASCII str — "comparing strings with non-ASCII characters is not
        supported", a TypeError — so a forged header of b"\xff\xfe..." turned
        an invalid credential into an unhandled 500 on the login path, rather
        than the peer fallback it was supposed to get.

        Comparing bytes removes the whole class: any header that arrived on
        the wire round-trips back to its own bytes and simply fails to match.
        """
        forged = b"\xff\xfe-not-the-secret".decode("latin-1")
        ip = client_ip(make_request({
            PROXY_SECRET_HEADER: forged,
            "X-Forwarded-For": ATTACKER,
        }))
        assert ip == PROXY_PEER

    def test_a_non_ascii_secret_still_matches_itself(self):
        """The fix must not break a secret that is simply not ASCII.

        nginx sends its utf-8 bytes; latin-1 decode then encode returns those
        bytes unchanged, so the comparison still succeeds.
        """
        secret = "sécret-ünicode"
        as_starlette_sees_it = secret.encode("utf-8").decode("latin-1")
        with patch.object(rate_limit.settings, "trusted_proxy_secret", secret):
            ip = client_ip(make_request({
                PROXY_SECRET_HEADER: as_starlette_sees_it,
                "X-Forwarded-For": REAL_CLIENT,
            }))
        assert ip == REAL_CLIENT


class TestTheApiLimiterUsesTheSameRule:
    """rate_limit()'s no-key fallback, which nothing exercised.

    The existing no-key test sends no forwarded header, so it would pass
    unchanged if _client_key regressed to request.client.host — and that
    regression would let a direct caller forge X-Forwarded-For to evade the
    API limiter. Raised in review on #63.
    """

    def test_two_vouched_keyless_callers_get_separate_buckets(self, monkeypatch):
        monkeypatch.setattr(rate_limit, "_request_log", {})
        me = make_request({PROXY_SECRET_HEADER: SECRET, "X-Forwarded-For": REAL_CLIENT})
        them = make_request({PROXY_SECRET_HEADER: SECRET, "X-Forwarded-For": ATTACKER})

        for _ in range(rate_limit.MAX_REQUESTS):
            rate_limit.rate_limit(them, x_api_key="")

        with pytest.raises(Exception):
            rate_limit.rate_limit(them, x_api_key="")
        rate_limit.rate_limit(me, x_api_key="")      # unaffected

    def test_an_unvouched_forged_header_cannot_escape_its_bucket(self, monkeypatch):
        """The evasion the fallback has to refuse.

        Without the secret, a direct caller rotating X-Forwarded-For would get
        a fresh bucket per request and the limit would be vacuous.
        """
        monkeypatch.setattr(rate_limit, "_request_log", {})
        for i in range(rate_limit.MAX_REQUESTS):
            rate_limit.rate_limit(
                make_request({"X-Forwarded-For": f"10.0.0.{i}"}, peer=ATTACKER),
                x_api_key="",
            )
        with pytest.raises(Exception):
            rate_limit.rate_limit(
                make_request({"X-Forwarded-For": "10.0.0.250"}, peer=ATTACKER),
                x_api_key="",
            )


class TestTheSessionAuditRecordsTheRealCaller:
    """The login audit row, driven through the real handler.

    Raised in review on #63: the trust tests above call client_ip() directly
    and the auth tests never assert UserSession.ip, so a regression in
    auth.py to request.client.host would leave every limiter test green while
    recording every proxied login as coming from the proxy. An audit trail
    that says that is worse than none — it looks authoritative and is uniform
    noise.

    So this calls login() itself with a fake session, and reads the row.
    """

    @staticmethod
    def _run_login(request):
        """Drive the real handler; return the UserSession it would persist."""
        import asyncio
        from types import SimpleNamespace as NS

        from app.api.routes import auth as auth_routes

        added = []
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.add = lambda row: added.append(row)
        session.commit = AsyncMock()

        user = NS(id="u1", email="a@b.c", tier="pro", is_active=True,
                  password_hash="hash", last_login_at=None)
        # scalar_one_or_none(), which is what auth.py actually calls —
        # mocking scalars().first() instead yields a MagicMock user and the
        # handler fails far downstream on response validation.
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=result)

        body = auth_routes.LoginRequest(email="a@b.c", password="pw")
        response = MagicMock()

        with patch("app.core.database.AsyncSessionLocal", return_value=session), \
             patch.object(auth_routes.settings, "auth_enabled", True), \
             patch.object(auth_routes, "verify_password", return_value=True), \
             patch.object(auth_routes, "new_session_token",
                          return_value=("tok", "tokhash")):
            asyncio.run(auth_routes.login(body=body, request=request, response=response))

        sessions = [r for r in added if hasattr(r, "ip")]
        assert sessions, "login persisted no UserSession row"
        return sessions[0]

    def test_a_vouched_login_records_the_forwarded_address(self):
        row = self._run_login(make_request({
            PROXY_SECRET_HEADER: SECRET,
            "X-Forwarded-For": REAL_CLIENT,
        }))
        assert row.ip == REAL_CLIENT, (
            "the audit row recorded the proxy instead of the caller"
        )

    def test_an_unvouched_login_records_the_peer_not_the_claim(self):
        row = self._run_login(make_request({"X-Forwarded-For": ATTACKER},
                                           peer=PROXY_PEER))
        assert row.ip == PROXY_PEER, (
            "an unvouched X-Forwarded-For was written into the audit trail, so "
            "anyone reaching the backend directly can forge their own history"
        )
