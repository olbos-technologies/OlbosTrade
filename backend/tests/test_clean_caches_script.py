"""scripts/clean_caches.sh frees disk without deleting anything that cannot be regenerated.

The desk's machine has run out of disk before. A cache cleaner is only safe if it is an
allowlist: it must never reach trained models (not in git), .env files, node_modules or
virtualenvs, and a wrong path must never turn it into `rm -rf` somewhere else.

What is real: the script, unmodified, executed by bash over a throwaway project tree that
holds both things to remove and things that must survive. Not covered: macOS itself (the
script uses only POSIX find/du/rm, but these tests ran on Linux).
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "clean_caches.sh"
START = REPO / "start.sh"

REMOVABLE = [
    "backend/app/__pycache__/main.cpython-311.pyc",
    "backend/app/services/__pycache__/x.pyc",
    "backend/tests/.pytest_cache/v/cache/lastfailed",
    "backend/.mypy_cache/3.11/a.json",
    "backend/.ruff_cache/0.1/b",
    "frontend/node_modules/.vite/deps/react.js",
    "frontend/dist/index.html",
    "frontend/test-results/r.txt",
    "frontend/playwright-report/index.html",
]
PROTECTED = [
    "backend/app/main.py",
    "backend/ml/model_registry/signal_model.pkl",
    "backend/ml/model_registry/__pycache__/keep.pyc",     # model_registry is never entered
    "backend/.env",
    "backend/.env.prod",
    "backend/.venv/lib/python3.11/__pycache__/v.pyc",     # virtualenvs are never entered
    "backend/venv/lib/__pycache__/v.pyc",
    "frontend/package.json",
    "frontend/src/App.tsx",
    "frontend/node_modules/react/index.js",               # node_modules itself stays
    "frontend/node_modules/.bin/vite",
    "frontend/node_modules/some-pkg/__pycache__/n.pyc",   # not under backend/: left alone
    "logs/backend.log",
    "data/bars/SPY_1m.csv",
]


def _touch(root: pathlib.Path, rel: str, text: str = "x" * 2048) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "olbos trade"            # a space in the path, on purpose
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "clean_caches.sh")
    for rel in REMOVABLE + PROTECTED:
        _touch(root, rel)
    return root


def _run(root, *args):
    return subprocess.run(["bash", str(root / "scripts" / "clean_caches.sh"), *args],
                          capture_output=True, text=True, timeout=60)


def _exists(root, rel):
    return (root / rel).exists()


def test_the_script_under_test_exists():
    assert SCRIPT.exists() and os.access(SCRIPT, os.X_OK)


def test_a_dry_run_lists_but_removes_nothing(project):
    r = _run(project)
    assert r.returncode == 0, r.stderr
    assert "would remove" in r.stdout and "Dry run" in r.stdout
    assert all(_exists(project, rel) for rel in REMOVABLE + PROTECTED)


def test_apply_removes_every_cache_and_only_the_caches(project):
    r = _run(project, "--apply")
    assert r.returncode == 0, r.stderr
    for rel in REMOVABLE:
        assert not _exists(project, rel), f"{rel} should have been removed"
    for rel in PROTECTED:
        assert _exists(project, rel), f"{rel} must survive"
    assert "Freed" in r.stdout.splitlines()[-1]


def test_trained_models_and_credentials_survive_apply(project):
    """The two things that cannot be recovered. Called out on their own."""
    _run(project, "--apply")
    assert (project / "backend/ml/model_registry/signal_model.pkl").read_text().startswith("xx")
    assert (project / "backend/.env.prod").exists() and (project / "backend/.env").exists()


def test_a_second_run_is_a_clean_no_op(project):
    _run(project, "--apply")
    r = _run(project, "--apply")
    assert r.returncode == 0 and "0 cache folder" in r.stdout


def test_it_reports_what_it_freed(project):
    out = _run(project, "--apply").stdout
    assert "removed" in out and "backend/app/__pycache__" in out
    assert "Freed" in out


def test_a_symlinked_cache_is_left_alone_and_its_target_is_untouched(project, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep")
    shutil.rmtree(project / "frontend" / "dist")
    os.symlink(outside, project / "frontend" / "dist")
    r = _run(project, "--apply")
    assert r.returncode == 0
    assert (outside / "precious.txt").read_text() == "keep"
    assert (project / "frontend" / "dist").is_symlink()


@pytest.mark.parametrize("missing", ["backend/app/main.py", "frontend/package.json"])
def test_it_refuses_a_directory_that_is_not_the_project(project, missing):
    (project / missing).unlink()
    r = _run(project, "--apply")
    assert r.returncode == 2 and "refusing" in r.stderr
    assert all(_exists(project, rel) for rel in REMOVABLE), "removed something despite refusing"


def test_an_unknown_argument_removes_nothing(project):
    r = _run(project, "--everything")
    assert r.returncode == 64
    assert all(_exists(project, rel) for rel in REMOVABLE)


# ── start.sh wiring ─────────────────────────────────────────────────────────

def test_start_sh_runs_the_cleanup_after_stopping_old_processes_and_before_starting():
    text = START.read_text()
    assert text, "read nothing"
    kill = text.index('pkill -f "uvicorn app.main"')
    call = '"$PROJECT/scripts/clean_caches.sh" --apply'
    assert text.count(call) == 1, "the cleanup is invoked exactly once, with --apply"
    clean = text.index(call)
    backend = text.index("nohup python3 -m uvicorn")
    assert kill < clean < backend, "must run after the old processes stop and before the new start"


def test_a_cleanup_failure_cannot_stop_the_desk_from_starting():
    """start.sh runs under `set -e`; the cleanup must sit in an `if` so its failure is advisory."""
    text = START.read_text()
    assert "set -e" in text
    i = text.index('"$PROJECT/scripts/clean_caches.sh" --apply')
    line_start = text.rfind("\n", 0, i) + 1
    assert text[line_start:i].lstrip().startswith("if "), "cleanup must be guarded by `if`"
    assert "startup continues" in text


def test_start_sh_step_numbers_are_consistent():
    import re
    text = START.read_text()
    steps = re.findall(r'echo -n "\[(\d)/(\d)\]', text)
    assert [int(a) for a, _ in steps] == list(range(1, len(steps) + 1))
    assert {b for _, b in steps} == {str(len(steps))}
