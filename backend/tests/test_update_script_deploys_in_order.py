"""Execute deploy/hetzner/update.sh and check what it actually does.

`test_update_script_migrates_before_serving.py` reads the script. This runs
it, which is a different kind of evidence: ordering asserted by parsing text
survives a rewrite that keeps the words and loses the behaviour.

What is real here and what is substituted:

* **Real**: the script itself, unmodified, executed by bash with `set -euo
  pipefail` in force. A real `alembic upgrade head` over every migration in
  the repository, against a real PostgreSQL database created and dropped for
  the test. The schema is then queried to prove the migration landed.

* **Substituted**: Docker. There is no Docker daemon in this environment, so
  `docker` on PATH is a recording stub that logs each invocation and forwards
  the migration to the real alembic. `git` is stubbed too, because the script
  starts with `git pull origin main` and a test must not move the checkout.

So this proves the script's ORDER, its failure semantics, and that the
migration it runs works — not that Docker behaves as expected on the server.
That gap is named in the PR's limitations and is why the parsing guards stay.

The failure case is the one that matters most: a migration that fails must
stop the deploy BEFORE new containers start, leaving the old image serving a
schema it matches. Here that is observable — `up -d` is absent from the log.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
)

REPO = pathlib.Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
SCRIPT = REPO / "deploy" / "hetzner" / "update.sh"

#: Records every stubbed command, one per line, so ordering is observable.
LOG_NAME = "invocations.log"

_DOCKER_STUB = r"""#!/usr/bin/env bash
# Recording stub. Logs the invocation, then either forwards the migration to a
# real alembic or just succeeds.
echo "docker $*" >> "$DEPLOY_TEST_LOG"

is_migration=0
for arg in "$@"; do
  if [ "$arg" = "alembic" ]; then is_migration=1; fi
done

if [ "$is_migration" = "1" ]; then
  case "$*" in
    *"upgrade head"*)
      if [ "${DEPLOY_TEST_FAIL_MIGRATION:-0}" = "1" ]; then
        echo "FATAL: alembic upgrade failed (simulated)" >&2
        exit 1
      fi
      echo "real-alembic upgrade head" >> "$DEPLOY_TEST_LOG"
      cd "$DEPLOY_TEST_BACKEND" || exit 1
      DATABASE_URL="$DEPLOY_TEST_DB_URL" \
        "$DEPLOY_TEST_PYTHON" -m alembic upgrade head >>"$DEPLOY_TEST_LOG" 2>&1
      exit $?
      ;;
    *current*)
      echo "real-alembic current" >> "$DEPLOY_TEST_LOG"
      cd "$DEPLOY_TEST_BACKEND" || exit 1
      DATABASE_URL="$DEPLOY_TEST_DB_URL" \
        "$DEPLOY_TEST_PYTHON" -m alembic current >>"$DEPLOY_TEST_LOG" 2>&1
      exit $?
      ;;
  esac
fi
exit 0
"""

_GIT_STUB = """#!/usr/bin/env bash
# The script begins with `git pull origin main`. Running the real one would
# move the checkout this test is reading from.
echo "git $*" >> "$DEPLOY_TEST_LOG"
exit 0
"""

_SLEEP_STUB = """#!/usr/bin/env bash
echo "sleep $*" >> "$DEPLOY_TEST_LOG"
exit 0
"""


@pytest_asyncio.fixture
async def disposable_db():
    """A real, empty database, dropped afterwards."""
    name = f"olbos_deploy_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(
        TEST_DB_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()

    # Same server, different database.
    url = TEST_DB_URL
    base, _, query = url.partition("?")
    head, _, _old = base.rpartition("/")
    target = f"{head}/{name}" + (f"?{query}" if query else "")

    try:
        yield target
    finally:
        admin = create_async_engine(
            TEST_DB_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT",
        )
        async with admin.connect() as conn:
            await conn.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ), {"n": name})
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        await admin.dispose()


def _deploy_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway copy of the deploy layout the script expects."""
    root = tmp_path / "opt" / "olbostrade"
    (root / "deploy" / "hetzner").mkdir(parents=True)
    (root / "backend").mkdir(parents=True)

    shutil.copy(SCRIPT, root / "deploy" / "hetzner" / "update.sh")
    # The script sources this. No real secrets: the stubs need nothing, and a
    # test must never read backend/.env.prod.
    (root / "backend" / ".env.prod").write_text(
        "DEPLOY_TEST_FAKE_ENV=1\n"
    )
    compose = REPO / "docker-compose.hetzner.yml"
    if compose.exists():
        shutil.copy(compose, root / "docker-compose.hetzner.yml")
    return root


def _stub_bin(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    for name, body in (
        ("docker", _DOCKER_STUB),
        ("git", _GIT_STUB),
        ("sleep", _SLEEP_STUB),
    ):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return bin_dir


def _run(tmp_path: pathlib.Path, db_url: str, *, fail_migration: bool):
    root = _deploy_root(tmp_path)
    bin_dir = _stub_bin(tmp_path)
    log = tmp_path / LOG_NAME
    log.write_text("")

    env = dict(os.environ)
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "DEPLOY_TEST_LOG": str(log),
        "DEPLOY_TEST_BACKEND": str(BACKEND),
        "DEPLOY_TEST_PYTHON": str(BACKEND / ".venv" / "bin" / "python"),
        # alembic wants the asyncpg driver; the deploy's own URL form.
        "DEPLOY_TEST_DB_URL": db_url,
        "DEPLOY_TEST_FAIL_MIGRATION": "1" if fail_migration else "0",
    })

    proc = subprocess.run(
        ["bash", str(root / "deploy" / "hetzner" / "update.sh")],
        env=env, capture_output=True, text=True, timeout=600,
    )
    return proc, [
        line for line in log.read_text().splitlines() if line.strip()
    ]


def _index_of(lines: list[str], needle: str) -> int:
    for i, line in enumerate(lines):
        if needle in line:
            return i
    raise AssertionError(f"no invocation matching {needle!r} in:\n" + "\n".join(lines))


def _has(lines: list[str], needle: str) -> bool:
    return any(needle in line for line in lines)


@pytest.mark.asyncio
async def test_a_successful_deploy_migrates_before_starting_containers(
    tmp_path, disposable_db
):
    """Build, then migrate from that image, then start serving, then verify."""
    if not (BACKEND / ".venv" / "bin" / "python").exists():
        pytest.skip("no backend venv to run alembic with")

    proc, lines = _run(tmp_path, disposable_db, fail_migration=False)
    assert proc.returncode == 0, (
        f"deploy failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    build = _index_of(lines, "build --no-cache")
    migrate = _index_of(lines, "real-alembic upgrade head")
    up = _index_of(lines, "up -d")

    assert build < migrate, "migrated from an image that had not been built"
    assert migrate < up, (
        "containers started before the migration — that is the window where "
        "new code serves against an old schema"
    )

    # The migration was real: the table the new code needs is there.
    engine = create_async_engine(disposable_db, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            present = (await conn.execute(
                text("SELECT to_regclass('position_claims') IS NOT NULL")
            )).scalar()
    finally:
        await engine.dispose()
    assert present is True, "the deploy reported success without creating the table"


@pytest.mark.asyncio
async def test_the_migration_runs_in_a_throwaway_container_not_docker_exec(
    tmp_path, disposable_db
):
    """`docker exec` would run inside the OLD image, which lacks the revision."""
    if not (BACKEND / ".venv" / "bin" / "python").exists():
        pytest.skip("no backend venv to run alembic with")

    _, lines = _run(tmp_path, disposable_db, fail_migration=False)
    migrate_line = lines[_index_of(lines, "real-alembic upgrade head") - 1]
    assert "run --rm" in migrate_line, (
        f"the migration was not run in a throwaway container: {migrate_line}"
    )
    assert "--no-deps" in migrate_line, (
        "without --no-deps the database may be recreated under the running backend"
    )
    assert not _has(
        [line for line in lines if "upgrade head" in line], "docker exec"
    ), "the migration ran via docker exec, i.e. inside the old image"


@pytest.mark.asyncio
async def test_a_failed_migration_leaves_the_running_service_alone(
    tmp_path, disposable_db
):
    """The important failure: stop before swapping anything.

    If the migration fails and the deploy carries on, new code serves a schema
    that does not match it. If it fails and the deploy stops, the old
    containers keep running against the schema they were built for — degraded
    only in that the deploy did not happen.
    """
    proc, lines = _run(tmp_path, disposable_db, fail_migration=True)

    assert proc.returncode != 0, (
        "a failed migration did not fail the deploy:\n" + proc.stdout
    )
    assert not _has(lines, "up -d"), (
        "containers were started after the migration failed — the running "
        "service was replaced with code whose schema is missing:\n"
        + "\n".join(lines)
    )
    assert not _has(lines, "builder prune"), (
        "cleanup ran after a failed migration; the deploy did not stop"
    )

    # And nothing was migrated, so the old schema is untouched.
    engine = create_async_engine(disposable_db, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            present = (await conn.execute(
                text("SELECT to_regclass('position_claims') IS NOT NULL")
            )).scalar()
    finally:
        await engine.dispose()
    assert present is False


@pytest.mark.asyncio
async def test_the_deploy_test_harness_can_observe_a_failure(
    tmp_path, disposable_db
):
    """Guard on the guard: the success and failure runs must differ.

    If the stub silently succeeded in both modes, the failure test above would
    pass against a script that never checks anything. This asserts the harness
    distinguishes them.
    """
    if not (BACKEND / ".venv" / "bin" / "python").exists():
        pytest.skip("no backend venv to run alembic with")

    ok, ok_lines = _run(tmp_path / "ok", disposable_db, fail_migration=False)
    bad, bad_lines = _run(tmp_path / "bad", disposable_db, fail_migration=True)

    assert ok.returncode == 0 and bad.returncode != 0
    assert _has(ok_lines, "up -d") and not _has(bad_lines, "up -d")
