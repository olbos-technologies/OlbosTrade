"""The deployed commit must be readable from the running system.

2026-11: after three PRs merged, nothing could say whether the server was running
them. The footer was written to show the commit ("so a deploy can be verified
in-browser") but nothing ever passed the commit to the build, so production showed
the hardcoded fallback "Olbos v5.0" — a version string that looked real and said
nothing. This pins the whole chain: update.sh -> compose build args -> Dockerfile
ARG/ENV -> the backend's detail route, startup log and the frontend footer.

The stamp is a label for a human. It is deliberately not on the public /health,
which carries one bit for the watchdog and the container healthcheck.
"""
from __future__ import annotations

import logging
import pathlib
import re

import pytest
import yaml

from app.core import build_info as bi

REPO = pathlib.Path(__file__).resolve().parents[2]


# ── the helper ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("3a2cbe3", "3a2cbe3"),
    ("  3a2cbe3\n", "3a2cbe3"),
    ("3a2cbe37e192281dd492410a63ce9a9be822b859", "3a2cbe37e192281dd492410a63ce9a9be822b859"),
])
def test_a_real_sha_is_reported(monkeypatch, raw, expected):
    monkeypatch.setenv("GIT_SHA", raw)
    assert bi.git_sha() == expected and bi.build_info() == {"git_sha": expected}


@pytest.mark.parametrize("raw", [
    "", "   ", "main", "3A2CBE3", "3a2cbe", "xyz1234", "3a2cbe3; rm -rf /",
    "3a2cbe3\nINFO fake log line", "$(whoami)", "a" * 41,
])
def test_anything_else_reads_as_unknown_never_echoed(monkeypatch, raw):
    monkeypatch.setenv("GIT_SHA", raw)
    assert bi.git_sha() == "unknown"


def test_unset_reads_as_unknown(monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert bi.git_sha() == "unknown"


def test_the_startup_log_names_the_stamp_or_warns_it_is_missing(monkeypatch, caplog):
    lg = logging.getLogger("build-stamp-test")
    monkeypatch.setenv("GIT_SHA", "3a2cbe3")
    with caplog.at_level(logging.INFO, logger="build-stamp-test"):
        bi.log_build_stamp(lg)
    assert any("3a2cbe3" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)
    caplog.clear()
    monkeypatch.delenv("GIT_SHA")
    with caplog.at_level(logging.INFO, logger="build-stamp-test"):
        bi.log_build_stamp(lg)
    assert any("UNKNOWN" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


# ── the routes ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_detail_route_reports_the_build(monkeypatch):
    from test_health_detail_accounts import _broker, _call

    monkeypatch.setenv("GIT_SHA", "3a2cbe3")
    out = await _call(_broker(["DU1234567"]))
    assert out["build"] == {"git_sha": "3a2cbe3"}


@pytest.mark.asyncio
async def test_the_detail_route_says_unknown_for_an_unstamped_image(monkeypatch):
    from test_health_detail_accounts import _broker, _call

    monkeypatch.delenv("GIT_SHA", raising=False)
    assert (await _call(_broker(["DU1234567"])))["build"] == {"git_sha": "unknown"}


def test_the_public_health_route_does_not_carry_the_build(monkeypatch):
    """/health is public and read by the watchdog; it must stay one bit and no version."""
    import asyncio
    from unittest.mock import MagicMock, patch
    from app.main import health_check

    monkeypatch.setenv("GIT_SHA", "3a2cbe3")
    b = MagicMock()
    b._connected = True
    b.ib.isConnected.return_value = True
    with patch("app.broker.broker_factory.get_broker", return_value=b):
        out = asyncio.run(health_check())
    assert set(out) == {"status", "broker", "broker_connected"}
    assert "3a2cbe3" not in str(out)


# ── the wiring that actually delivers the stamp ─────────────────────────────

def _compose():
    path = REPO / "docker-compose.hetzner.yml"
    data = yaml.safe_load(path.read_text())
    assert data["services"], "read nothing from the compose file"
    return data["services"]


def test_compose_passes_the_stamp_to_both_builds_and_tolerates_it_being_unset():
    svc = _compose()
    assert svc["backend"]["build"]["args"]["GIT_SHA"] == "${GIT_SHA:-}"
    assert svc["frontend"]["build"]["args"]["VITE_GIT_SHA"] == "${GIT_SHA:-}"
    # existing frontend args are untouched
    assert svc["frontend"]["build"]["args"]["VITE_API_URL"] == ""


def test_the_stamp_is_a_build_arg_not_a_runtime_variable():
    """A runtime env var would change the container's config on every deploy and
    force a recreate of services whose code did not change."""
    for name in ("backend", "frontend"):
        env = _compose()[name].get("environment") or []
        text = "\n".join(env) if isinstance(env, list) else "\n".join(f"{k}={v}" for k, v in env.items())
        assert "GIT_SHA" not in text


def test_the_backend_image_bakes_the_stamp_into_its_runtime_stage():
    text = (REPO / "backend" / "Dockerfile").read_text()
    runtime = text.split("AS runtime", 1)
    assert len(runtime) == 2, "read no runtime stage"
    assert re.search(r'^ARG GIT_SHA=""$', runtime[1], re.M)
    assert re.search(r"^ENV GIT_SHA=\$GIT_SHA$", runtime[1], re.M)


def test_the_frontend_image_bakes_the_stamp_in_before_the_vite_build():
    text = (REPO / "frontend" / "Dockerfile").read_text()
    build = text.index("RUN npm run build")
    arg = text.index('ARG VITE_GIT_SHA=""')
    env = text.index("ENV VITE_GIT_SHA=$VITE_GIT_SHA")
    assert arg < env < build, "VITE_GIT_SHA must be set before the bundle is built"


def test_the_footer_no_longer_falls_back_to_a_made_up_version():
    src = (REPO / "frontend" / "src" / "components" / "TerminalLayout.tsx").read_text()
    lines = [l for l in src.splitlines() if "VITE_GIT_SHA" in l]
    assert lines, "read nothing: the footer line moved"
    assert all('v5.0' not in l for l in lines)
    assert any("build unknown" in l for l in lines)


def test_update_script_exports_the_stamp_before_building():
    text = (REPO / "deploy" / "hetzner" / "update.sh").read_text()
    export = text.index('export GIT_SHA="${LOCAL_SHA:0:7}"')
    assert text.index('LOCAL_SHA="$(git rev-parse HEAD)"') < export
    assert export < text.index("docker compose -f docker-compose.hetzner.yml build")
