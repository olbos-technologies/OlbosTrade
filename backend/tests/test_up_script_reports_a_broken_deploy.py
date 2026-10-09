r"""
`up.sh` must not report success when the deployment is not working.

Two findings from Sourcery on #68, with one root cause: every check in the
script printed a warning and fell through to the same green banner and exit 0.

  1. If docker_default was missing it was created and the script continued.
     An EMPTY network satisfies compose's `external: true`, so everything
     starts — but IBKR_HOST=ibkr-gateway is a Docker DNS name that resolves
     only while that container is attached. The backend then comes up, serves,
     and reports healthy with no route to the broker. Nothing looks wrong
     until an order does not go anywhere.

  2. If Caddy had exited — a port conflict is the likely cause, and during the
     olbos-caddy -> olbostrade-caddy cutover the old container holds 80/443 —
     `docker exec` failed, the script printed a warning, and then said
     "✅ OlbosTrade is running" and exited 0.

These are tested by RUNNING the script against a stubbed `docker` rather than
by grepping it for the string "exit 1". A text assertion would pass against a
script that contains an unreachable exit, and the property that matters here
is the exit status an operator or a CI job actually observes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UP_SH = REPO / "deploy" / "hetzner" / "up.sh"

#: Stub standing in for the real docker CLI. Every branch up.sh takes is driven
#: by an environment variable so a test can put the host into one state.
DOCKER_STUB = r"""#!/usr/bin/env bash
case "$1 $2" in
  "network ls")      echo "${STUB_NETWORKS:-docker_default}" ;;
  # `${VAR-default}`, NOT `${VAR:-default}`: an explicitly EMPTY value means
  # "no containers attached", and the colon form would silently substitute the
  # default instead — which made this stub report ibkr-gateway as attached in
  # exactly the test that sets it to empty.
  "network inspect") echo "${STUB_ATTACHED-ibkr-gateway olbostrade-backend}" ;;
  "network create")  exit 0 ;;
  "compose -f")      exit 0 ;;
  "inspect -f")      echo "${STUB_CADDY_STATE:-running}" ;;
  "exec olbostrade-caddy")
      exit "${STUB_CADDY_VALIDATE:-0}" ;;
  "exec olbostrade-backend")
      exit 0 ;;
  *) exit 0 ;;
esac
"""


def run_up(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run up.sh in a throwaway tree with `docker` stubbed out."""
    (tmp_path / "deploy" / "hetzner").mkdir(parents=True)
    shutil.copy(UP_SH, tmp_path / "deploy" / "hetzner" / "up.sh")

    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env.prod").write_text(
        "POSTGRES_PASSWORD=x\nTRUSTED_PROXY_SECRET=y\nIBKR_HOST=ibkr-gateway\n"
    )
    (tmp_path / "docker-compose.hetzner.yml").write_text("services: {}\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(DOCKER_STUB)
    stub.chmod(0o755)

    environ = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", **env}
    return subprocess.run(
        ["bash", str(tmp_path / "deploy" / "hetzner" / "up.sh")],
        capture_output=True, text=True, timeout=120, env=environ,
    )


def test_a_healthy_deploy_still_succeeds(tmp_path: Path):
    """Guards the guard. If the script exited nonzero unconditionally the two
    failure tests below would pass while the script was broken for everyone."""
    r = run_up(tmp_path)
    assert r.returncode == 0, (
        f"up.sh failed on a fully healthy stub deployment.\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "OlbosTrade is running" in r.stdout


def test_a_stopped_caddy_fails_the_deploy(tmp_path: Path):
    r = run_up(tmp_path, STUB_CADDY_STATE="exited")
    assert r.returncode != 0, (
        "up.sh exited 0 with Caddy stopped. HTTPS is the only way into this "
        "deployment — :8080 is loopback-bound — so a green banner here tells "
        f"the operator a broken deploy is done.\nstdout:\n{r.stdout}"
    )
    assert "STARTED WITH PROBLEMS" in r.stdout
    assert "HTTPS is down" in r.stdout


def test_an_invalid_caddy_config_fails_the_deploy(tmp_path: Path):
    r = run_up(tmp_path, STUB_CADDY_VALIDATE="1")
    assert r.returncode != 0, (
        f"up.sh exited 0 with a Caddy config that does not validate.\n"
        f"stdout:\n{r.stdout}"
    )
    assert "did not validate" in r.stdout


def test_a_missing_ibkr_gateway_fails_the_deploy(tmp_path: Path):
    """The quiet one. Everything comes up and looks healthy."""
    r = run_up(tmp_path, STUB_ATTACHED="olbostrade-backend olbostrade-frontend")
    assert r.returncode != 0, (
        "up.sh exited 0 with ibkr-gateway absent from docker_default. The "
        "backend serves and reports healthy in that state, with no route to "
        f"the broker — nothing looks wrong until an order goes nowhere.\n"
        f"stdout:\n{r.stdout}"
    )
    assert "no broker connection" in r.stdout


def test_a_missing_network_is_created_rather_than_fatal(tmp_path: Path):
    """Creating it is deliberate, and not the same as ignoring the problem.

    The network EXISTING is not the property that matters — ibkr-gateway being
    ATTACHED is — and hard-failing on a missing network would block a
    legitimate first deploy on a fresh host, where nothing has created it
    either. So the network is created, and the attachment check still fails the
    run, which is what this asserts: a created-but-empty network is NOT a pass.
    """
    r = run_up(tmp_path, STUB_NETWORKS="bridge", STUB_ATTACHED="")
    assert "creating it" in r.stdout
    assert r.returncode != 0, (
        "up.sh created docker_default and then reported success. An empty "
        "network satisfies compose's `external: true` while resolving nothing, "
        f"which is the failure this pair of checks exists to separate.\n"
        f"stdout:\n{r.stdout}"
    )
