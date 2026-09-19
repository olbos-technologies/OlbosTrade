r"""
Caddy must not serve this app over plain HTTP.

On 2026-09-19 the frontend's published :8080 was bound to loopback to remove a
plain-HTTP route into the terminal. Closing it was necessary and not
sufficient: the Caddyfile still carried

    (olbos_app) {
        handle /health* { reverse_proxy olbostrade-backend:8000 }
        handle          { reverse_proxy olbostrade-frontend:3000 }
    }

    :80   { import olbos_app }
    :8081 { import olbos_app }

An ADDRESS-ONLY site block — one whose address names a port but no host —
matches any hostname over plain HTTP. Both served the entire terminal (kill
switch, position closing, execution mode) in clear text to anyone who knew the
server's IP, on the DEFAULT http port, while the afternoon's work was spent
closing a different door for the same reason.

Basic Auth made it worse rather than better. HTTP Basic sends `user:password`
base64-encoded, which is reversible by anyone reading the traffic, so the
credentials the prompt existed to enforce travelled in the clear.

A second effect is easy to miss: an explicit `:80` block SUPPRESSES Caddy's
automatic HTTP-to-HTTPS redirect. Port 80 served the app instead of bouncing to
TLS precisely because that block existed. Removing it restores both the 308 and
ACME handling, which is why the fix is deletion rather than adding a redirect.

So this asserts a property of the deployment, not a spelling: every site block
must name a host and must not force the plaintext scheme.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CADDYFILE = REPO / "deploy" / "hetzner" / "Caddyfile"

#: A line opening a block: everything before the trailing `{`.
BLOCK_OPEN = re.compile(r"^\s*(?P<addr>[^{#]+?)\s*\{\s*$")

#: `(name) { ... }` defines a reusable snippet rather than a site, and never
#: listens on anything by itself.
SNIPPET_DEF = re.compile(r"^\(.+\)$")


def site_addresses() -> list[tuple[int, str]]:
    """(line_no, address) for every site block in the Caddyfile.

    Comments are stripped because this file's own explanation of the removed
    `:80` block quotes it, and a guard that matched its own documentation would
    fail on the very comment recording why it exists.
    """
    found: list[tuple[int, str]] = []
    depth = 0
    for n, raw in enumerate(CADDYFILE.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0]
        if not line.strip():
            continue
        m = BLOCK_OPEN.match(line)
        if m and depth == 0:
            addr = m.group("addr").strip()
            if not SNIPPET_DEF.match(addr):
                found.append((n, addr))
        depth += line.count("{") - line.count("}")
    return found


def test_the_caddyfile_was_actually_parsed():
    """Guards the guard: no addresses found makes every check below vacuous."""
    assert CADDYFILE.exists(), f"{CADDYFILE} is missing"
    addrs = site_addresses()
    assert addrs, (
        "no site blocks parsed out of deploy/hetzner/Caddyfile. Either Caddy "
        "serves nothing (the app is down), or the file's shape changed and this "
        "test has stopped checking anything. Fix the parser — do not delete it."
    )


def test_no_site_block_listens_without_a_hostname():
    """`:80 { ... }` matches ANY host, over plain HTTP. That is the defect."""
    bare = [
        (n, a) for n, a in site_addresses()
        if any(part.strip().startswith(":") for part in a.split(","))
    ]
    assert not bare, (
        "these Caddy site blocks name a port but no host:\n"
        + "\n".join(f"  line {n} → {a!r}" for n, a in bare)
        + "\n\nAn address-only block matches every hostname over PLAIN HTTP, so "
          "it serves the whole terminal in clear text to anyone who knows the "
          "server's IP — and an explicit `:80` block additionally suppresses "
          "Caddy's automatic HTTP-to-HTTPS redirect. If you need port 80 to do "
          "something, you almost certainly do not: with no `:80` block Caddy "
          "already answers there with ACME challenges and a 308 to HTTPS."
    )


def test_no_site_block_forces_the_plaintext_scheme():
    """`http://host { ... }` opts out of TLS for a named host."""
    plaintext = [
        (n, a) for n, a in site_addresses()
        if any(part.strip().lower().startswith("http://") for part in a.split(","))
    ]
    assert not plaintext, (
        "these Caddy site blocks use the http:// scheme, which disables "
        "automatic HTTPS for that host:\n"
        + "\n".join(f"  line {n} → {a!r}" for n, a in plaintext)
        + "\n\nName the host without a scheme and Caddy obtains and renews a "
          "certificate for it."
    )


def test_the_app_is_served_over_https_somewhere():
    """The inverse of the two above: refusing plaintext must not pass by
    serving nothing at all. Deleting every site block would satisfy both
    assertions above while taking the deployment offline."""
    served = [a for _, a in site_addresses()]
    assert any(
        not a.startswith(":") and "://" not in a and "." in a
        for a in served
    ), (
        f"no site block names a bare hostname, so nothing is served over "
        f"automatic HTTPS. Parsed addresses: {served}"
    )


# ── The consequence of removing the `:80` app block ──────────────────────────
#
# With no address-only site block, port 80 answers with an ACME challenge or a
# 308 to HTTPS — never the app. Any runbook command of the form
# `curl http://localhost/api/...` on the server therefore stopped working at
# the same moment, silently: it returns a redirect body instead of JSON, and a
# `grep -o` over that finds nothing, which reads as "the check failed" during a
# credential rotation.
#
# Three such commands were in docs/credential_rotation_runbook.md and I fixed
# two of them before a grep found the third. Hence a guard rather than a
# careful re-read.

DOC_ROOTS = [REPO / "docs", REPO / "deploy"]

#: `http://localhost/...` or `http://127.0.0.1/...` with NO port, which means
#: port 80. A port that is spelled out (`:8080`, `:8000`) is something else —
#: an SSH tunnel or a container-local address — and is fine.
#:
#: `http`, NOT `https?`. The first version matched both schemes, so
#: `https://localhost/...` — which means port 443, not 80 — would have failed a
#: guard whose entire subject is port 80. Caught by Copilot on #68. A guard
#: that fires outside its own stated scope is one someone eventually silences,
#: and it would have taken the real check with it.
HOST_PORT80_URL = re.compile(r"http://(?:localhost|127\.0\.0\.1)(?![:\w])")


def commands_curling_port_80() -> list[tuple[str, int, str]]:
    """(file, line_no, line) for port-80 host URLs inside fenced code blocks.

    Fenced blocks only, and the complement of the placeholder guard in
    test_docs_use_the_published_port.py, deliberately: a command someone runs
    lives in a fence, while an instruction to open a URL lives in prose. Each
    guard scans where its own defect lives. Prose is excluded here so that this
    very explanation — and the runbook's note about why the URLs changed — does
    not trip it.
    """
    found: list[tuple[str, int, str]] = []
    for root in DOC_ROOTS:
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".md", ".sh"} or not path.is_file():
                continue
            in_fence = path.suffix == ".sh"
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if path.suffix == ".md" and line.lstrip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if not in_fence or line.lstrip().startswith("#"):
                    continue
                if HOST_PORT80_URL.search(line):
                    found.append((str(path.relative_to(REPO)), n, line.strip()))
    return found


def test_no_runbook_curls_the_app_on_port_80():
    offenders = commands_curling_port_80()
    assert not offenders, (
        "these commands reach the host on port 80, which Caddy uses only for "
        "ACME challenges and a 308 to HTTPS — never the app:\n"
        + "\n".join(f"  {f}:{n}\n      {l}" for f, n, l in offenders)
        + "\n\nThey return a redirect body rather than JSON, so a grep over the "
          "output silently finds nothing and reads as a failed check. Use "
          "`docker exec olbostrade-backend curl -s http://127.0.0.1:8000/...`, "
          "which does not traverse Caddy at all, or the HTTPS domain with "
          "credentials. Restoring a plaintext `:80` site block to make these "
          "work again is the defect this module exists to prevent."
    )


def test_the_port_80_pattern_matches_port_80_and_nothing_else():
    """Asserted directly, because the repo scan only proves today's files pass.

    Nothing in `docs/` or `deploy/` currently contains an `https://localhost`
    URL, so the over-match Copilot found was invisible through
    commands_curling_port_80() — the guard was wrong and the suite was green.
    The same blind spot would hide the opposite error: narrow the pattern until
    it matches nothing and every scan still passes.
    """
    must_match = [
        "curl -s http://localhost/api/health/detail",
        "curl http://127.0.0.1/api/rotation/preflight",
        "ssh root@host 'curl -s http://localhost/api/x'",
    ]
    must_not_match = [
        # 443, not 80 — the false positive.
        "curl -s https://localhost/api/health/detail",
        "curl -s https://127.0.0.1/api/health",
        # An explicit port is a tunnel or a container-local address.
        "curl -s http://localhost:8080/health",
        "curl -s http://127.0.0.1:8000/api/health/detail",
        # A different host entirely.
        "curl -sI https://trade.olbos.us | head -2",
        # Substring of a longer hostname must not count.
        "curl http://localhosting.example.com/",
    ]
    for line in must_match:
        assert HOST_PORT80_URL.search(line), (
            f"pattern failed to match a port-80 host URL: {line!r}. Narrowing "
            f"it until it matches nothing would leave every repo scan passing."
        )
    for line in must_not_match:
        assert not HOST_PORT80_URL.search(line), (
            f"pattern matched something that is not a port-80 host URL: "
            f"{line!r}. A guard that fires outside its stated scope is one "
            f"someone silences, taking the real check with it."
        )
