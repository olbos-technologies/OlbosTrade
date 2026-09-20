"""
Cache directives in the frontend's generated nginx config.

Two defects lived in one line, and together they explain why the hero image
and favicon were broken on the live site for weeks:

    location ~* \\.(js|css|woff2?|png|jpg|jpeg|gif|svg|ico)$ {
        add_header Cache-Control "public, max-age=31536000, immutable" always;
    }

`always` attaches the header to ERROR responses too. Basic Auth sits at server
level, so an unauthenticated request for a .png returns 401 — carrying a
one-year immutable directive. Observed in production:

    HTTP/2 401
    cache-control: public, max-age=31536000, immutable
    www-authenticate: Basic realm="OlbosTrade"
    server: cloudflare

Anything that honours that stores the 401 and serves it to authenticated
browsers afterwards, because `immutable` means "do not revalidate".

And the regex matched more than its own comment claimed. "Hashed build assets
(filename changes every build) can cache forever" is true of Vite's /assets/
output and false of everything copied verbatim from public/ — the favicons and
olbos-hero.png keep the same name across builds, so a cached copy is pinned
for a year and replacing the image changes nothing for existing visitors.

Scanned rather than executed: there is no nginx in this test environment. The
entrypoint runs `nginx -t` before starting and exits on failure, so syntax is
covered there; what is checked here is intent, which `nginx -t` cannot see.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ENTRYPOINT = (pathlib.Path(__file__).parent.parent.parent
              / "frontend/docker-entrypoint.sh")


def _nginx_config() -> str:
    """The server block the entrypoint writes, lifted out of its heredoc."""
    text = ENTRYPOINT.read_text()
    start = text.index("cat > /etc/nginx/conf.d/default.conf")
    body = text[start:]
    return body[: body.index("\nEOF")]


def _location_blocks() -> dict:
    """Map each location's matcher to the directives inside it.

    Deliberately simple: these blocks are one level deep and never nested.
    """
    config = _nginx_config()
    blocks: dict = {}
    for match in re.finditer(r"location\s+([^\{]+?)\s*\{([^}]*)\}", config, re.S):
        blocks[match.group(1).strip()] = match.group(2)
    return blocks


def test_the_config_was_actually_extracted():
    """Vacuity guard: every assertion below scans this, and an empty string
    satisfies all of them."""
    config = _nginx_config()
    assert "location /" in config
    assert "Cache-Control" in config
    assert len(_location_blocks()) >= 4, sorted(_location_blocks())


def test_no_immutable_cache_header_is_forced_onto_error_responses():
    """`always` is what let a 401 be cached for a year.

    Without it, add_header applies only to 2xx/3xx — which is the whole point
    of a cache directive. `location /` keeps its `always` deliberately:
    no-store SHOULD apply to errors too.
    """
    offenders = [
        matcher for matcher, body in _location_blocks().items()
        if "immutable" in body and re.search(r"add_header[^;]*always", body)
    ]
    assert not offenders, (
        f"these locations force an immutable cache header onto error "
        f"responses, so a 401 gets cached: {offenders}")


def test_only_hashed_build_output_is_immutable():
    """Vite hashes /assets/ and copies public/ verbatim. Only the first can
    safely be immutable, because only the first gets a new filename when its
    contents change."""
    immutable = [m for m, b in _location_blocks().items() if "immutable" in b]

    assert immutable, "nothing is cached long-term — the hashed assets should be"
    for matcher in immutable:
        assert "/assets/" in matcher, (
            f"location {matcher!r} is immutable but is not scoped to Vite's "
            "hashed output; unhashed files keep their name across builds and "
            "would be pinned for a year")


@pytest.mark.parametrize("ext", ["png", "ico", "svg", "jpg"])
def test_unhashed_image_types_are_revalidated(ext):
    """These are the extensions public/ actually ships. They must be
    cacheable — but revalidated, so replacing the hero image takes effect."""
    matched = [
        (m, b) for m, b in _location_blocks().items()
        if m.startswith("~") and ext in m
    ]
    assert matched, f".{ext} is not matched by any location block"

    for matcher, body in matched:
        assert "immutable" not in body, (
            f".{ext} files are immutable under {matcher!r} — a replaced image "
            "would never be seen again")
        assert "must-revalidate" in body or "no-cache" in body, (
            f".{ext} under {matcher!r} is cached without revalidation")


def test_index_html_is_never_cached():
    """index.html names the current bundle hash. A cached copy silently keeps
    a browser on an old build after every deploy — which is exactly what a
    stale landing page looks like."""
    root = _location_blocks().get("/")
    assert root, "no catch-all location block"
    assert "no-store" in root, f"index.html is cacheable: {root.strip()}"


def test_the_healthcheck_stays_open():
    """Unrelated to caching, pinned here because it lives in the same
    generated config and a container probe has no credentials."""
    exact = _location_blocks().get("= /health")
    assert exact, "the frontend healthcheck location is gone"
    assert "auth_basic off" in exact


def test_the_entrypoint_still_parse_checks_before_starting():
    """This file checks intent; `nginx -t` checks syntax. Neither substitutes
    for the other, and there is no nginx in this test environment."""
    text = ENTRYPOINT.read_text()
    assert "nginx -t" in text
    assert re.search(r"if\s*!\s*nginx -t", text), (
        "nginx -t runs but its result is not acted on")
