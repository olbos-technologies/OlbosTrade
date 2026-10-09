"""
The login rate limiter only works if the deployment lets it see the caller.

api/rate_limit.py keys login attempts on request.client.host. That is the real
caller ONLY when uvicorn runs with --proxy-headers behind a proxy that writes
X-Forwarded-For itself. Without the flag every request carries the proxy's
address, all callers land in one bucket, and the limit inverts into a denial of
service: ten failed logins from anyone lock out everybody for five minutes.

That is not hypothetical — it is what shipped. The image CMD had no
--proxy-headers, and nothing in the test suite noticed, because the limiter's
own behaviour was correct in isolation. The bug lived entirely in the gap
between the code and the way it is launched.

So these tests assert the deployment invariant rather than the function:

  * the stack that runs this app passes --proxy-headers
  * the stacks that publish the backend port do NOT

The second half matters as much as the first. uvicorn 0.29 with
--forwarded-allow-ips=* takes the LEFTMOST X-Forwarded-For entry, which a
caller can forge — harmless when the only route in is a proxy that overwrites
the header, and a free pass around the rate limit when the port is reachable
directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Stack files and whether their backend is reachable without passing a proxy.
COMPOSE_FILES = [
    "docker-compose.hetzner.yml",
    "docker-compose.yml",
    "docker-compose.prod.yml",
]


def service_block(compose_text: str, service: str) -> str:
    """
    The YAML block for one service, by indentation, WITH COMMENTS STRIPPED.

    Avoids a PyYAML import: it is not a declared dependency of this project,
    and a transitively-present package is a poor thing for a guard test to rest
    on.

    Stripping comments is not tidiness — without it this whole file was
    decorative. The `command:` in the Hetzner stack carries a long comment
    explaining why --proxy-headers is there and why it must not be copied
    elsewhere, so `"--proxy-headers" in block` matched the PROSE. Deleting the
    actual flag left every assertion green. Caught by mutation testing; the
    flag is only meaningful where Docker will read it.
    """
    lines = compose_text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if re.match(rf"^  {re.escape(service)}:\s*$", line):
            inside = True
            continue
        if inside:
            # A new top-level service (two-space indent, not deeper) ends it.
            if re.match(r"^  \S", line) and not line.startswith("    "):
                break
            if re.match(r"^\s*#", line):
                continue                      # comment line — never a directive
            out.append(re.sub(r"\s+#.*$", "", line))   # trailing comment
    if not inside:
        raise AssertionError(f"no `{service}` service found")
    return "\n".join(out)


def publishes_backend_port(block: str) -> bool:
    """A `ports:` mapping means something outside Docker can open a socket
    straight to uvicorn, bypassing any proxy."""
    return bool(re.search(r"^\s+-\s+\"?\d+:\d+", block, re.M))


def read(name: str) -> str:
    path = REPO / name
    assert path.exists(), f"{name} is missing — deployment invariant cannot be checked"
    return path.read_text()


class TestTheRunningStackCanSeeTheCaller:
    """The trust rule moved out of uvicorn and into the app (issue #60).

    The three tests that used to live here asserted --proxy-headers and
    --forwarded-allow-ips=*, and each of them was right for the deployment as
    it stood. That deployment was wrong: peer-address trust cannot separate
    the frontend from the rest of docker_default, so the only setting that
    worked at all was one that trusted every container on it.

    So these now assert the opposite, and the pairing is what matters — the
    wildcard must be GONE and the secret must be WIRED. Either half alone is a
    broken stack: no wildcard and no secret is one shared bucket, and the
    wildcard returning alongside the secret is the forgery hole reopened.
    """

    def test_the_hetzner_backend_no_longer_trusts_by_peer_address(self):
        block = service_block(read("docker-compose.hetzner.yml"), "backend")
        assert "--forwarded-allow-ips" not in block, (
            "--forwarded-allow-ips is back on the Hetzner backend. This service "
            "joins the shared docker_default network (it must — IBKR_HOST "
            "resolves there), so any value permissive enough to trust the "
            "frontend also trusts every other container on it, and a CIDR "
            "matches nothing because uvicorn 0.29 compares trusted hosts by "
            "exact string. Trust belongs with the proxy secret. See issue #60."
        )
        assert "--proxy-headers" not in block, (
            "--proxy-headers is back on the Hetzner backend. It would overwrite "
            "request.client.host from X-Forwarded-For before the app sees it, "
            "so client_ip() could no longer tell a vouched-for header from a "
            "forged one — the secret check becomes decoration."
        )

    def test_both_services_are_given_the_SAME_shared_secret(self):
        """The same interpolation, not merely the same variable name.

        A name-only check passes when the backend reads
        ${TRUSTED_PROXY_SECRET} and the frontend reads some other variable —
        the stack boots, the secrets differ, no request ever matches, and
        every caller silently falls back to the proxy bucket. That is the
        failure this guard exists to prevent, so it has to compare the VALUE
        each service is given. Raised in review on #63; this is the fourth
        time a guard in this file checked something adjacent to the invariant
        rather than the invariant.
        """
        compose = read("docker-compose.hetzner.yml")
        interpolations = {}
        for service in ("backend", "frontend"):
            block = service_block(compose, service)
            line = next((l for l in block.splitlines()
                         if "TRUSTED_PROXY_SECRET" in l), None)
            assert line, (
                f"the {service} service does not receive TRUSTED_PROXY_SECRET, "
                "so the frontend cannot vouch for X-Forwarded-For (or the "
                "backend cannot check that it did) and every caller shares one "
                "login rate-limit bucket"
            )
            m = re.search(r"\$\{(TRUSTED_PROXY_SECRET[^}]*)\}", line)
            assert m, (
                f"{service} does not interpolate TRUSTED_PROXY_SECRET from the "
                f"environment: {line.strip()!r}"
            )
            interpolations[service] = m.group(1)

        assert interpolations["backend"] == interpolations["frontend"], (
            "the two services interpolate DIFFERENT expressions "
            f"({interpolations!r}), so the secret the frontend sends is not the "
            "one the backend checks — the stack boots and the limiter is dead"
        )

    def test_the_secret_is_required_rather_than_defaulted(self):
        """`:-` would let the stack boot silently degraded.

        TRUSTED_PROXY_CIDR defaults to empty on purpose — unset there means
        trust nobody, which is safe. Unset HERE means the rate limit stops
        being per-caller, which is the bug this whole thread is about, so the
        stack should refuse to start instead.
        """
        compose = read("docker-compose.hetzner.yml")
        for service in ("backend", "frontend"):
            block = service_block(compose, service)
            line = next(l for l in block.splitlines() if "TRUSTED_PROXY_SECRET" in l)
            assert ":?" in line, (
                f"{service} defaults TRUSTED_PROXY_SECRET instead of requiring "
                f"it: {line.strip()!r}. An unset secret boots a stack whose "
                "login limiter buckets every caller together."
            )

    def test_the_proxy_actually_sends_the_secret_ON_THE_API_ROUTE(self):
        """Present in the file is not the same as emitted on the route.

        Finding the header name anywhere in the script passes even after
        ${SECRET_HEADER} is deleted from the /api location — nginx then sends
        no secret, the backend ignores X-Forwarded-For, and the guard stays
        green while the deployed trust chain is broken. Raised in review
        on #63.

        So this asserts the two halves that actually carry it: the /api proxy
        line emits the variable, and the variable is built from
        TRUSTED_PROXY_SECRET with the right header name.
        """
        entrypoint = read("frontend/docker-entrypoint.sh")

        api_line = next((l for l in entrypoint.splitlines()
                         if "location /api" in l), None)
        assert api_line, "the /api location block is gone from the entrypoint"
        assert "${SECRET_HEADER}" in api_line, (
            "the /api proxy line no longer emits ${SECRET_HEADER}, so nginx "
            f"sends no proxy secret and the backend ignores X-Forwarded-For: "
            f"{api_line.strip()!r}"
        )

        assign = next((l for l in entrypoint.splitlines()
                       if l.strip().startswith("SECRET_HEADER=")
                       and "proxy_set_header" in l), None)
        assert assign, "SECRET_HEADER is never built into a proxy_set_header"
        assert "X-Olbos-Proxy-Secret" in assign, (
            f"SECRET_HEADER does not set the header the backend checks: {assign.strip()!r}"
        )
        assert "TRUSTED_PROXY_SECRET" in assign, (
            f"SECRET_HEADER is not driven by TRUSTED_PROXY_SECRET: {assign.strip()!r}"
        )

    def test_hetzner_backend_is_not_directly_reachable(self):
        """The whole reason --forwarded-allow-ips=* is safe there."""
        block = service_block(read("docker-compose.hetzner.yml"), "backend")
        assert not publishes_backend_port(block), (
            "the Hetzner backend now publishes a host port. With "
            "--forwarded-allow-ips=* a caller can reach uvicorn directly and "
            "forge X-Forwarded-For, which makes the login rate limit vacuous. "
            "Remove the port, or scope the trusted proxies."
        )

    #: (flag, required value); None means a bare flag that takes no value.
    #:
    #: The VALUE is the invariant, not the flag. `"--workers" in source` is
    #: satisfied by `--workers 2` — the precise regression the docstring below
    #: calls out as the one that hurts quietly. This guard asserted the label
    #: and not the value until Copilot caught it on PR #63.
    UVICORN_FLAGS = (
        ("--host", "0.0.0.0"),
        ("--port", "8000"),
        ("--workers", "1"),
        ("--loop", "uvloop"),
        ("--access-log", None),
    )

    def test_the_uvicorn_flags_reach_uvicorn_however_they_get_there(self):
        """Guards the invariant, not the mechanism that currently satisfies it.

        `command:` replaces the image CMD WHOLESALE, so any override has to
        repeat every flag or it silently drops them. The Hetzner stack used to
        carry one (for --proxy-headers) and therefore had to repeat them;
        removing that override for issue #60 handed the job back to the
        Dockerfile. Asserting against the compose block alone would now fail
        for a stack that is correct, and asserting against the Dockerfile alone
        would miss a future override that forgets them.

        --workers 1 is the one that would hurt quietly: IBKR allows a single
        connection per client id, so a second worker is a broker fight, not a
        slow endpoint.
        """
        block = service_block(read("docker-compose.hetzner.yml"), "backend")
        overrides = "command:" in block
        source = block if overrides else read("backend/Dockerfile")
        where = "the compose command: override" if overrides else "backend/Dockerfile"

        # Both carriers tokenise alike once quotes, commas and line
        # continuations are gone: the Dockerfile's JSON exec form
        # ("--workers", "1") and a compose shell string (--workers 1).
        tokens = re.sub(r"""["',\\]""", " ", source).split()

        for flag, value in self.UVICORN_FLAGS:
            at = [i for i, t in enumerate(tokens) if t == flag]
            assert at, (
                f"{flag} is missing from {where}. `command:` replaces the image "
                f"CMD wholesale, so an override must repeat every flag the "
                f"Dockerfile sets."
            )
            if value is None:
                continue
            # Every occurrence, not just the first: a second one further down
            # is what actually reaches uvicorn.
            for i in at:
                actual = tokens[i + 1] if i + 1 < len(tokens) else None
                assert actual == value, (
                    f"{where} passes `{flag} {actual}`, not `{flag} {value}`. "
                    f"A presence check on {flag} alone passes for any value, "
                    f"which is how a wrong one ships unnoticed."
                )


class TestStacksThatExposeTheBackendDoNotTrustTheHeader:
    @pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.prod.yml"])
    def test_a_published_backend_port_never_pairs_with_proxy_headers(self, name):
        block = service_block(read(name), "backend")
        if not publishes_backend_port(block):
            pytest.skip(f"{name} no longer publishes the backend port")
        assert "--proxy-headers" not in block, (
            f"{name} publishes the backend port AND passes --proxy-headers. A "
            f"caller can then connect directly and set X-Forwarded-For to a "
            f"fresh value per request, giving every attempt its own bucket and "
            f"removing the login rate limit entirely."
        )


class TestTheProxyDoesNotLetCallersWriteTheHeader:
    """The other half of the chain: the header uvicorn trusts must be written
    by the proxy, never forwarded from the caller."""

    def test_frontend_overwrites_x_forwarded_for(self):
        entrypoint = (REPO / "frontend" / "docker-entrypoint.sh").read_text()
        api_line = next(
            (l for l in entrypoint.splitlines() if "location /api" in l), None
        )
        assert api_line, "the /api proxy block is gone"
        assert "X-Forwarded-For \\$remote_addr" in api_line, (
            "the /api proxy no longer sets X-Forwarded-For from $remote_addr. "
            "If it appends instead ($proxy_add_x_forwarded_for), the caller's "
            "own header survives into the chain and uvicorn — which reads the "
            "leftmost entry under --forwarded-allow-ips=* — would trust it."
        )

    def test_real_ip_trust_is_opt_in(self):
        """Trusting a proxy for real-IP recovery must default to off: a default
        that trusts a broad range would let a direct caller forge the header."""
        entrypoint = (REPO / "frontend" / "docker-entrypoint.sh").read_text()
        assert "set_real_ip_from" in entrypoint, "real-IP support was removed"
        assert re.search(r'if \[ -n "\$TRUSTED_PROXY_CIDR" \]', entrypoint), (
            "set_real_ip_from is no longer gated on TRUSTED_PROXY_CIDR being "
            "set — it must default to trusting nobody"
        )


class TestTheBlockExtractorIgnoresProse:
    """This file was briefly decorative because the extractor kept comments and
    the Hetzner `command:` is heavily commented. Guard the guard."""

    SAMPLE = """
  backend:
    # a comment mentioning --proxy-headers and "8000:8000" in prose
    image: example
    command: uvicorn app.main:app --workers 1   # trailing comment
  other:
    image: nope
"""

    def test_comment_text_is_not_mistaken_for_configuration(self):
        block = service_block(self.SAMPLE, "backend")
        assert "--proxy-headers" not in block, "a comment was read as a directive"
        assert not publishes_backend_port(block), "a port in prose was read as published"

    def test_real_directives_survive(self):
        block = service_block(self.SAMPLE, "backend")
        assert "--workers 1" in block
        assert "image: example" in block

    def test_the_block_stops_at_the_next_service(self):
        assert "nope" not in service_block(self.SAMPLE, "backend")
