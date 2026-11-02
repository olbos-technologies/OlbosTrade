"""Run deploy/hetzner/ibkr_watchdog.sh and check what it actually does.

Background. The watchdog probed GET /api/health/detail and turned any exception
into "DEAD". After AUTH_ENABLED that route returned 401 from inside the
container, so every probe "failed" and the watchdog restarted a healthy gateway
about 800 times — in bursts of four, repeatedly dropping the broker connection
during market hours. An error from the probe is not evidence about the thing
being probed.

What is real here: the script, unmodified, executed by bash. A real HTTP server
on an ephemeral port plays the backend's /health route and is scripted to
return exactly the responses that matter (broker up, broker down, 401, 500,
garbage, a field an older backend never sent, nothing listening at all).

What is substituted: `docker`. A stub on PATH answers `inspect`, runs the probe
snippet locally with python (what `docker exec ... python3 -` does on the
server) and records every restart/start. So this proves the script's decisions,
not that Docker behaves as expected on the server.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import subprocess
import threading
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "hetzner" / "ibkr_watchdog.sh"

_DOCKER_STUB = r"""#!/usr/bin/env bash
echo "docker $*" >> "$WD_TEST_LOG"
case "$1" in
  inspect)
    # `docker inspect -f '{{.State.Running}}' NAME`
    name="${@: -1}"
    case "$name" in
      ibkr-gateway) echo "${WD_GATEWAY_RUNNING:-true}" ;;
      olbostrade-backend) echo "${WD_BACKEND_RUNNING:-true}" ;;
      *) echo false ;;
    esac
    ;;
  exec)
    # `docker exec -i -e HEALTH_URL=... olbostrade-backend python3 -` : run the
    # probe snippet from stdin locally, with the env the real call would set.
    shift
    while [ $# -gt 0 ]; do
      case "$1" in
        -i) shift ;;
        -e) export "$2"; shift 2 ;;
        *) break ;;
      esac
    done
    shift   # container name
    exec "$WD_TEST_PYTHON" -
    ;;
  restart|start) : ;;
  *) : ;;
esac
"""


class _Backend:
    """A stand-in for the backend's /health, scripted per test."""

    def __init__(self):
        outer = self
        self.status, self.body, self.delay = 200, {"status": "ok"}, 0.0

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if outer.delay:
                    time.sleep(outer.delay)
                payload = outer.body if isinstance(outer.body, (bytes, str)) else json.dumps(outer.body)
                payload = payload.encode() if isinstance(payload, str) else payload
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/health"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def env(tmp_path):
    import sys
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "docker"
    stub.write_text(_DOCKER_STUB)
    stub.chmod(0o755)
    backend = _Backend()
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "docker.log"
    log.write_text("")

    class E:
        pass

    e = E()
    e.backend, e.state, e.log = backend, state, log
    e.base = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "WD_TEST_LOG": str(log),
        "WD_TEST_PYTHON": sys.executable,
        "WATCHDOG_STATE_DIR": str(state),
        "WATCHDOG_HEALTH_URL": backend.url,
    }

    def run(**extra):
        r = subprocess.run(["bash", str(SCRIPT)], env={**e.base, **extra},
                           capture_output=True, text=True, timeout=60)
        return r

    e.run = run
    e.restarts = lambda: [l for l in log.read_text().splitlines() if l.startswith("docker restart")]
    e.starts = lambda: [l for l in log.read_text().splitlines() if l.startswith("docker start")]
    yield e
    backend.close()


def _history(env, n, age=60):
    now = int(time.time())
    (env.state / "ibkr-watchdog-restart-history").write_text(
        "\n".join(str(now - age * (i + 1)) for i in range(n)) + "\n")


def test_the_script_is_actually_the_one_under_test():
    assert SCRIPT.exists() and "broker_connected" in SCRIPT.read_text()


# ── the decision table ──────────────────────────────────────────────────────

def test_a_connected_broker_is_left_alone_and_history_is_cleared(env):
    env.backend.body = {"status": "ok", "broker_connected": True}
    _history(env, 2)
    r = env.run()
    assert r.returncode == 0 and env.restarts() == []
    assert (env.state / "ibkr-watchdog-restart-history").read_text().strip() == ""


def test_a_disconnected_broker_restarts_the_gateway_once_then_cools_down(env):
    env.backend.body = {"status": "ok", "broker_connected": False}
    r = env.run()
    assert len(env.restarts()) == 1 and "restarting ibkr-gateway" in r.stdout
    assert (env.state / "ibkr-watchdog-last-restart").exists()
    env.run()
    assert len(env.restarts()) == 1             # second run is inside the cooldown


@pytest.mark.parametrize("label,status,body", [
    ("401 from an authenticated route", 401, {"detail": "Not authenticated"}),
    ("500 from the backend", 500, {"detail": "boom"}),
    ("an older backend with no broker_connected field", 200, {"status": "ok", "broker": "ibkr"}),
    ("broker_connected is null (backend could not tell)", 200, {"status": "ok", "broker_connected": None}),
    ("not JSON at all", 200, "<html>proxy error</html>"),
    ("a string where a bool belongs", 200, {"broker_connected": "false"}),
])
def test_an_inconclusive_probe_never_restarts_the_gateway(env, label, status, body):
    """The regression: each of these used to be read as 'the gateway is dead'."""
    env.backend.status, env.backend.body = status, body
    _history(env, 1)
    before = (env.state / "ibkr-watchdog-restart-history").read_text()
    r = env.run()
    assert env.restarts() == [], label
    assert "inconclusive" in r.stdout, label
    # and an inconclusive probe learned nothing, so it must not clear the history
    assert (env.state / "ibkr-watchdog-restart-history").read_text() == before, label


def test_a_timeout_never_restarts_the_gateway(env):
    env.backend.delay = 12                      # the probe waits 8 seconds
    env.backend.body = {"broker_connected": False}
    r = env.run()
    assert env.restarts() == [] and "inconclusive" in r.stdout


def test_nothing_listening_never_restarts_the_gateway(env):
    r = env.run(WATCHDOG_HEALTH_URL="http://127.0.0.1:9/health")   # the discard port
    assert env.restarts() == [] and "inconclusive" in r.stdout


# ── the rest of the script's behaviour must be intact ───────────────────────

def test_the_circuit_breaker_still_stops_repeated_ineffective_restarts(env):
    env.backend.body = {"broker_connected": False}
    _history(env, 4)
    r = env.run()
    assert env.restarts() == [] and "CIRCUIT BREAKER OPEN" in r.stdout


def test_a_stopped_gateway_container_is_started(env):
    r = env.run(WD_GATEWAY_RUNNING="false")
    assert len(env.starts()) == 1 and "not running" in r.stdout


def test_a_stopped_backend_is_skipped_without_probing_or_restarting(env):
    r = env.run(WD_BACKEND_RUNNING="false")
    assert env.restarts() == [] and "skipping probe" in r.stdout


def test_default_state_paths_are_unchanged():
    text = SCRIPT.read_text()
    assert '${WATCHDOG_STATE_DIR:-/tmp}' in text          # production still uses /tmp
    assert 'http://127.0.0.1:8000/health' in text


def test_no_deploy_doc_or_script_publishes_the_gateway_api_on_every_interface():
    """`-p 4002:4004` binds 0.0.0.0, and Docker's published ports bypass ufw.

    With READ_ONLY_API=no, anyone who can reach that port can place and cancel
    orders, and the app's kill switch does not stand between them and the broker.
    The README documented exactly that form and the production gateway was
    started from it. The only acceptable form binds loopback.
    """
    scanned = 0
    for path in list((REPO / "deploy").rglob("*")) + [REPO / "README.md"] + list((REPO / "docs").rglob("*.md")):
        if not path.is_file() or path.suffix not in {".md", ".sh", ".yml", ".yaml", ""}:
            continue
        scanned += 1
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if "4002:400" in line and "127.0.0.1:4002:400" not in line and not line.lstrip().startswith("#"):
                raise AssertionError(f"{path.relative_to(REPO)}:{n} publishes the IB Gateway API on all interfaces: {line.strip()}")
    assert scanned > 5
    assert "127.0.0.1:4002:4004" in (REPO / "deploy/hetzner/README.md").read_text()   # and it is documented
