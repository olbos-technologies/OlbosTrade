"""
AUTH_ENABLED must be an explicit choice, and the app must say which it made.

The failure this guards is quiet and bad. settings.auth_enabled defaults to
False — deliberately, so an existing single-operator install keeps working on
nginx Basic Auth alone. But in a deployment that default means an absent or
misspelled line in .env.prod is INDISTINGUISHABLE from choosing to turn auth
off: the containers come up healthy, the site serves normally, and application
authentication is simply not there.

That matters because of the rollout this repo committed to (config.py, the
auth_enabled comment): ship app auth while Basic Auth is still in front,
verify it, and only then remove Basic Auth. A silent false is exactly the
state in which someone removes the outer wall believing the inner one is up.

Two independent guards, because they fail in different ways:
  * compose refuses to start without an explicit value — catches it before
    anything serves, but only for people who deploy through that file;
  * the app logs its resolved posture at startup — catches it everywhere else,
    and is still readable afterwards when a shell history is not.
"""

from __future__ import annotations

import logging
import pathlib

import pytest

ROOT = pathlib.Path(__file__).parent.parent.parent
COMPOSE = ROOT / "docker-compose.hetzner.yml"
ENV_EXAMPLE = ROOT / "deploy/hetzner/.env.example"


def _backend_environment() -> dict:
    """The backend service's `environment:` block, parsed without PyYAML.

    PyYAML is NOT declared in backend/requirements.txt and CI installs only
    that file. It happens to be importable today because uvicorn and
    pydantic-settings pull it in, which is precisely the kind of undeclared
    dependency that disappears on an unrelated version bump — and then these
    assertions raise ModuleNotFoundError instead of checking the deployment
    guard. Raised in review on #74.

    Hand-parsing YAML is its own trap, so this stays deliberately narrow: find
    the backend service block by indentation, then its environment block, then
    the `KEY: value` lines directly inside it. test_the_required_vars_use_one_
    pattern is the vacuity guard — if this returns an empty dict because the
    file's shape changed, that test fails rather than everything passing.
    """
    lines = COMPOSE.read_text().splitlines()

    # The `backend:` service, up to the next service at the same indent.
    start = next(i for i, l in enumerate(lines) if l.startswith("  backend:"))
    end = next(
        (i for i in range(start + 1, len(lines))
         if lines[i].startswith("  ") and not lines[i].startswith("   ")
         and lines[i].strip().endswith(":")),
        len(lines),
    )
    block = lines[start:end]

    env_at = next((i for i, l in enumerate(block)
                   if l.strip() == "environment:"), None)
    if env_at is None:
        return {}

    indent = len(block[env_at]) - len(block[env_at].lstrip())
    out: dict[str, str] = {}
    for line in block[env_at + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        depth = len(line) - len(line.lstrip())
        if depth <= indent:
            break                      # left the environment block
        if ":" not in line:
            continue
        key, _, value = line.strip().partition(":")
        out[key.strip()] = value.strip().strip('"')
    return out


def test_compose_requires_an_explicit_auth_enabled():
    """`${AUTH_ENABLED:?...}` — the same contract TRUSTED_PROXY_SECRET has.

    A plain `${AUTH_ENABLED}` or `${AUTH_ENABLED:-false}` would substitute
    empty or false and start anyway, which is the behaviour this exists to
    remove.
    """
    value = _backend_environment().get("AUTH_ENABLED")

    assert value is not None, (
        "AUTH_ENABLED is not passed to the backend service at all — an absent "
        "line in .env.prod would silently mean 'off'")
    assert ":?" in value, (
        f"AUTH_ENABLED is {value!r}, which substitutes a default instead of "
        "failing. Use ${AUTH_ENABLED:?message} so compose refuses to start.")
    assert ":-" not in value, f"AUTH_ENABLED supplies a fallback: {value!r}"


def test_the_refusal_message_says_what_to_do():
    """An error that names the variable and not the fix costs a support round."""
    value = _backend_environment()["AUTH_ENABLED"]
    message = value.split(":?", 1)[1]

    assert ".env.prod" in message, message
    assert "true" in message and "false" in message, (
        f"the message should name both acceptable values: {message}")


def test_the_required_vars_use_one_pattern():
    """If TRUSTED_PROXY_SECRET's contract is ever relaxed, this should be
    noticed rather than silently diverging."""
    env = _backend_environment()
    required = {k: v for k, v in env.items() if ":?" in str(v)}

    assert "AUTH_ENABLED" in required
    assert "TRUSTED_PROXY_SECRET" in required


def test_the_env_example_ships_an_explicit_value():
    """Copying .env.example must produce a working, unambiguous config — not a
    file that fails the check above the first time it is deployed."""
    text = ENV_EXAMPLE.read_text()
    lines = [l.strip() for l in text.splitlines()
             if l.strip().startswith("AUTH_ENABLED=")]

    assert len(lines) == 1, f"expected exactly one AUTH_ENABLED line, got {lines}"
    assert lines[0] in ("AUTH_ENABLED=false", "AUTH_ENABLED=true"), lines[0]


# ── the startup announcement ────────────────────────────────────────────────

@pytest.fixture
def posture(monkeypatch):
    from app.core.config import settings
    import app.main as main_mod

    def _run(enabled: bool, secret: str, caplog):
        monkeypatch.setattr(settings, "auth_enabled", enabled)
        monkeypatch.setattr(settings, "secret_key", secret)
        with caplog.at_level(logging.INFO):
            main_mod._log_auth_posture()
        return caplog.records

    return _run


def test_auth_off_is_logged_as_a_warning(posture, caplog):
    """WARNING, not INFO. "No application authentication" is not routine
    information on a service that can reach a broker, and INFO scrolls past."""
    records = posture(False, "", caplog)

    assert records, "startup said nothing about authentication"
    assert any(r.levelno >= logging.WARNING for r in records)
    text = " ".join(r.getMessage() for r in records)
    assert "OFF" in text
    assert "Basic Auth" in text, (
        "the warning should name what the instance is actually relying on")


def test_auth_on_is_logged(posture, caplog):
    records = posture(True, "a-secret", caplog)

    text = " ".join(r.getMessage() for r in records)
    assert "ON" in text
    assert not any(r.levelno >= logging.WARNING for r in records), (
        "a correctly configured instance should not warn")


def test_auth_on_without_a_secret_key_warns(posture, caplog):
    """The half-configured case: sessions are enforced but the operator-key
    routes fall open, because require_api_key no-ops on an empty SECRET_KEY.
    That reads as 'auth is on' while a whole class of route is not."""
    records = posture(True, "", caplog)

    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert warnings, "AUTH_ENABLED=true with no SECRET_KEY passed silently"
    assert "SECRET_KEY" in " ".join(r.getMessage() for r in warnings)


def test_the_posture_is_logged_before_anything_else_starts(caplog):
    """Ordering: it goes first in on_startup, so it is at the top of the log
    rather than buried under broker and scheduler output."""
    import inspect

    import app.main as main_mod

    src = inspect.getsource(main_mod.on_startup)
    # Comments dropped, and the `global` declaration with them: it names
    # _greeks_tracker on the first line without doing any startup work, so
    # comparing against it measured nothing. Found by this test failing
    # against correct code.
    code = "\n".join(
        l for l in src.splitlines()
        if not l.strip().startswith("#") and not l.strip().startswith("global ")
    )
    assert "_log_auth_posture()" in code

    position = code.index("_log_auth_posture()")
    later_work = [name for name in ("get_broker", "_greeks_tracker",
                                    "start_scheduler", "AsyncSessionLocal")
                  if name in code]
    assert later_work, "no later startup work found — this test proves nothing"
    for name in later_work:
        assert position < code.index(name), (
            f"the auth posture is logged after {name} — it should be first")
