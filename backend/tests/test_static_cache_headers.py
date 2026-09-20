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

ROOT = pathlib.Path(__file__).parent.parent.parent
ENTRYPOINT = ROOT / "frontend/docker-entrypoint.sh"

#: The OTHER production nginx config. docker-compose.prod.yml mounts
#: deploy/nginx/olbostrade.conf, and it carried the same unhashed-asset
#: pinning — `expires 30d; Cache-Control "public, immutable"` over the same
#: extension list. Fixing the entrypoint alone would have left that path
#: serving a year-stale hero image, which is the fourth time in this session
#: that a fix landed on one instance while an identical sibling survived.
PROXY_CONF = ROOT / "deploy/nginx/olbostrade.conf"


def _nginx_config() -> str:
    """The server block the entrypoint writes, lifted out of its heredoc."""
    text = ENTRYPOINT.read_text()
    start = text.index("cat > /etc/nginx/conf.d/default.conf")
    body = text[start:]
    return body[: body.index("\nEOF")]


def _parse_locations(text: str) -> dict:
    """Map each location matcher to the directives DIRECTLY inside it.

    Brace-matched rather than regex-bounded, and nested blocks are stripped
    from their parent's body. Both matter: deploy/nginx/olbostrade.conf puts
    its static rules inside `location /`, and a flat `[^}]*` body stops at the
    first closing brace — which swallowed the whole `^~ /assets/` block into
    the parent and reported `location /` as the one carrying `immutable`.

    My first version of this claimed in its own docstring that one level of
    nesting was fine for a flat regex. It is not, and the proxy tests below
    failed against a correct config until this was fixed.
    """
    blocks: dict = {}
    for match in re.finditer(r"location\s+([^\{\n]+?)\s*\{", text):
        depth, i = 1, match.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[match.end():i - 1]
        # Drop nested location blocks so a parent is not credited with a
        # child's directives.
        inner = re.sub(r"location\s+[^\{\n]+?\s*\{[^{}]*\}", "", body, flags=re.S)
        # COMMENTS TOO. The comments explaining these rules necessarily use the
        # words the assertions look for — "so `immutable` pins whatever was
        # cached first" sits right next to the block it describes — and a
        # parent block keeps its children's comments after the strip above.
        # That reported `location /` as carrying an immutable directive it
        # does not have. Same shape as the #70 lock test that matched
        # `.with_for_update()` inside a comment and proved nothing.
        inner = "\n".join(l for l in inner.splitlines()
                          if not l.strip().startswith("#"))
        # Two server blocks means two `location /`; keep the richer one rather
        # than letting the last silently win.
        matcher = match.group(1).strip()
        if len(inner) > len(blocks.get(matcher, "")):
            blocks[matcher] = inner
    return blocks


def _location_blocks() -> dict:
    return _parse_locations(_nginx_config())


def test_the_config_was_actually_extracted():
    """Vacuity guard: every assertion below scans this, and an empty string
    satisfies all of them."""
    config = _nginx_config()
    assert "location /" in config
    assert "Cache-Control" in config
    assert len(_location_blocks()) >= 4, sorted(_location_blocks())


def test_no_public_cache_header_is_forced_onto_error_responses():
    """`always` is what let a 401 be cached for a year.

    Scoped to any PUBLIC directive, not just `immutable`. The first version
    of this guard only inspected locations containing `immutable`, so adding
    `always` back to the new one-hour header would have re-cached a 401 for
    /olbos-hero.png — shorter, but the same bug — and this test would have
    passed. Raised in review on #76.

    `location /` keeps its `always` deliberately: no-store SHOULD apply to
    errors too, which is why the check is on `public` rather than on
    Cache-Control generally.
    """
    offenders = [
        matcher for matcher, body in _location_blocks().items()
        if re.search(r'add_header\s+Cache-Control\s+"[^"]*public[^"]*"[^;]*always', body)
    ]
    assert not offenders, (
        f"these locations force a public cache header onto error responses, "
        f"so a 401 gets cached: {offenders}")


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
        # ^~ is load-bearing, not decoration. nginx gives regex locations
        # precedence over plain prefixes, so a bare `location /assets/` loses
        # to the image regex below — and .js/.css, which that regex no longer
        # matches, would fall through to `location /` and become no-store.
        # The hashed bundle would stop being cached at all. Checking only for
        # the "/assets/" substring missed this; raised in review on #76.
        assert matcher.startswith("^~"), (
            f"location {matcher!r} must use ^~ or the regex below overrides "
            "it and the hashed bundle loses its cache policy")


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


# ── the second production config ────────────────────────────────────────────
#
# docker-compose.prod.yml mounts deploy/nginx/olbostrade.conf. It carried the
# same unhashed-asset pinning as the entrypoint did, and fixing one without
# the other would leave that path serving a stale hero image for 30 days.

def _proxy_location_blocks() -> dict:
    """Location blocks in the standalone proxy config.

    Its static rules are nested inside `location /`, which is precisely the
    shape that broke the first parser — see _parse_locations.
    """
    return _parse_locations(PROXY_CONF.read_text())


def test_the_proxy_config_was_actually_extracted():
    """Vacuity guard, same reason as the entrypoint's."""
    assert PROXY_CONF.exists(), f"{PROXY_CONF} is gone — retire these tests too"
    blocks = _proxy_location_blocks()
    assert blocks, "no location blocks parsed from the proxy config"
    assert any("Cache-Control" in b for b in blocks.values())


def test_the_proxy_does_not_pin_unhashed_assets_either():
    """The defect this PR fixes, in the other production path."""
    for matcher, body in _proxy_location_blocks().items():
        if "immutable" not in body:
            continue
        assert "/assets/" in matcher, (
            f"proxy location {matcher!r} is immutable but not scoped to Vite's "
            "hashed output — unhashed files would be pinned")
        assert matcher.startswith("^~"), (
            f"proxy location {matcher!r} must use ^~ or the regex overrides it")


def test_the_proxy_does_not_force_public_caching_onto_errors():
    """It has no `always` today. Pinned so adding one is a test failure rather
    than a year of cached 401s discovered from a screenshot."""
    offenders = [
        matcher for matcher, body in _proxy_location_blocks().items()
        if re.search(r'add_header\s+Cache-Control\s+"[^"]*public[^"]*"[^;]*always', body)
    ]
    assert not offenders, offenders


@pytest.mark.parametrize("ext", ["png", "ico", "svg"])
def test_proxy_unhashed_images_are_revalidated(ext):
    matched = [(m, b) for m, b in _proxy_location_blocks().items()
               if m.startswith("~") and ext in m]
    assert matched, f".{ext} is not matched by any proxy location block"
    for matcher, body in matched:
        assert "immutable" not in body, (
            f".{ext} is immutable under proxy location {matcher!r}")
