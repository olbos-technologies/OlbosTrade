"""
Guard: no tracked file may contain an unresolved merge-conflict marker.

This repository is worked on by several agents at once (Claude, Cursor,
Codex) plus the operator, on separate branches that are merged by hand. That
is a standing source of this exact failure: `CHANGELOG.md` reached `main`
carrying `<<<<<<< HEAD`, `=======` and `>>>>>>> 96d0873` with four Unreleased
sections tangled between them, and nothing noticed — a conflicted Markdown
file still renders, still commits, and still passes every other test in this
suite.

The danger is not the Markdown. The same mistake in a Python module, a TS
module, an nginx config or an Alembic migration is a syntax error in the
deploy path, and `deploy/hetzner/update.sh` runs `alembic upgrade head`
against the image it just built. This guard is cheap insurance that the
mistake is caught by CI rather than by a failed production deploy.

Markers are built at runtime from their character and length rather than
written out, so this file does not match its own pattern.
"""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Built, not literal — see the module docstring.
START = "<" * 7 + " "
MIDDLE = "=" * 7
END = ">" * 7 + " "

# Binary and vendored trees: nothing a human merges, and `=======` appears
# inside legitimately (e.g. reStructuredText underlines in vendored docs).
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".ico", ".gif", ".svg", ".pdf",
    ".woff", ".woff2", ".ttf", ".otf", ".zip", ".gz", ".lock",
}
SELF = pathlib.Path(__file__).resolve()


def _tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    files = []
    for name in out.split("\0"):
        if not name:
            continue
        path = ROOT / name
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.resolve() == SELF:
            continue
        files.append(path)
    return files


def test_the_scan_actually_reads_the_repository():
    """A scan that silently matches nothing is worse than no test at all."""
    files = _tracked_files()
    assert len(files) > 500, (
        f"only {len(files)} tracked files found — git ls-files is not returning "
        "this repository, so the conflict-marker scan below would pass vacuously"
    )
    assert any(f.name == "CHANGELOG.md" for f in files), (
        "CHANGELOG.md — the file that carried conflict markers into main — is "
        "not in the scanned set"
    )


def test_no_tracked_file_contains_a_merge_conflict_marker():
    offenders: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: nothing a merge conflict survives in
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.startswith(START) or line.startswith(END) or line.rstrip() == MIDDLE:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line[:60]}")
    assert not offenders, (
        "unresolved merge-conflict markers are committed in:\n  "
        + "\n  ".join(offenders)
        + "\nResolve the conflict and remove the markers before merging."
    )
