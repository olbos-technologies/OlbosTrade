"""Tests for API key dependencies and kill-switch reset code config."""

import pytest
from fastapi import HTTPException

from app.api.deps import require_api_key, require_api_key_configured


def test_require_api_key_skips_when_secret_unset(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.secret_key", "")
    require_api_key("")  # must not raise


def test_require_api_key_rejects_mismatch(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.secret_key", "prod-secret")
    with pytest.raises(HTTPException) as exc:
        require_api_key("wrong")
    assert exc.value.status_code == 403


def test_require_api_key_accepts_match(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.secret_key", "prod-secret")
    require_api_key("prod-secret")


def test_require_api_key_configured_needs_secret(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.secret_key", "")
    with pytest.raises(HTTPException) as exc:
        require_api_key_configured("")
    assert exc.value.status_code == 503


def test_require_api_key_compares_in_constant_time():
    """hmac.compare_digest, not ==, so response timing does not leak how much
    of a guessed key matched. Read from source: a timing test would be flaky."""
    import inspect
    import app.api.deps as deps

    src = inspect.getsource(deps)
    assert "compare_digest" in src
    assert "x_api_key != settings.secret_key" not in src
    assert "x_api_key == settings.secret_key" not in src


def test_require_api_key_rejects_a_prefix_of_the_key(monkeypatch):
    from fastapi import HTTPException
    import pytest
    from app.core.config import settings
    from app.api.deps import require_api_key

    monkeypatch.setattr(settings, "secret_key", "correct-horse")
    with pytest.raises(HTTPException) as exc:
        require_api_key("correct-hor")
    assert exc.value.status_code == 403
