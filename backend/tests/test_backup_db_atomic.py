"""The production backup job must not prune good copies after a failed backup."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "deploy/hetzner/backup_db.sh"


@pytest.fixture
def backup_job(tmp_path: Path):
    root = tmp_path / "checkout"
    script = root / "deploy/hetzner/backup_db.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)
    backups = tmp_path / "backups"
    backups.mkdir()
    old = backups / "olbostrade-20200101-000000.sql.gz"
    old.write_bytes(b"previous-good-copy")
    stale = time.time() - 30 * 86400
    os.utime(old, (stale, stale))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '-- PostgreSQL database dump' 'CREATE TABLE pilot (id int);'\n"
        "if [ \"${FAIL_DUMP:-0}\" = 1 ]; then exit 17; fi\n"
    )
    docker.chmod(0o755)
    rclone = bin_dir / "rclone"
    rclone.write_text("#!/bin/sh\nexit \"${FAIL_OFFSITE:-0}\"\n")
    rclone.chmod(0o755)
    scp = bin_dir / "scp"
    scp.write_text("#!/bin/sh\nexit \"${FAIL_OFFSITE:-0}\"\n")
    scp.chmod(0o755)

    def run(**overrides: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "BACKUP_DIR": str(backups),
            "BACKUP_RETENTION_DAYS": "14",
            "BACKUP_RCLONE_REMOTE": "",
            "BACKUP_SCP_TARGET": "",
            "FAIL_DUMP": "0",
            "FAIL_OFFSITE": "0",
            **overrides,
        }
        return subprocess.run(
            ["bash", str(script)], cwd=root, env=env,
            capture_output=True, text=True, check=False,
        )

    return run, backups, old


def test_failed_dump_preserves_old_copy_and_publishes_nothing(backup_job):
    run, backups, old = backup_job
    result = run(FAIL_DUMP="1")
    assert result.returncode != 0
    assert old.exists()
    assert list(backups.iterdir()) == [old]


def test_verified_dump_is_private_and_only_then_prunes_old_copy(backup_job):
    run, backups, old = backup_job
    result = run()
    assert result.returncode == 0, result.stderr
    assert not old.exists()
    new = list(backups.glob("olbostrade-*.sql.gz"))
    assert len(new) == 1
    assert new[0].stat().st_mode & 0o777 == 0o600
    assert subprocess.run(["gzip", "-t", str(new[0])], check=False).returncode == 0


@pytest.mark.parametrize("target", ["BACKUP_RCLONE_REMOTE", "BACKUP_SCP_TARGET"])
def test_failed_offsite_copy_keeps_previous_copy(backup_job, target):
    run, backups, old = backup_job
    result = run(**{target: "test:backups", "FAIL_OFFSITE": "1"})
    assert result.returncode != 0
    assert old.exists()
    assert len(list(backups.glob("olbostrade-*.sql.gz"))) == 2
