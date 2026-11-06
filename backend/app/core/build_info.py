"""Which commit this process was built from.

The deploy bakes the short commit SHA into both images (GIT_SHA for the backend,
VITE_GIT_SHA for the frontend), so "is the server running what was merged" can be
answered from the running thing instead of from the pull request. Nothing about
the stamp is trusted for any decision; it is a label for a human.

Unset or malformed reads as "unknown", never as a plausible-looking version: an
image built outside update.sh (a manual `docker compose build`, a CI image) has no
stamp, and that has to be visible rather than defaulted.
"""
from __future__ import annotations

import logging
import os
import re

UNKNOWN = "unknown"

# A git short SHA: lowercase hex, 7 to 40 characters. Anything else (a stray
# newline-bearing value, shell text, a branch name) is rejected rather than echoed
# into logs and responses.
_SHA = re.compile(r"[0-9a-f]{7,40}")


def git_sha() -> str:
    raw = (os.environ.get("GIT_SHA") or "").strip()
    return raw if _SHA.fullmatch(raw) else UNKNOWN


def build_info() -> dict:
    return {"git_sha": git_sha()}


def log_build_stamp(logger: logging.Logger) -> None:
    sha = git_sha()
    if sha == UNKNOWN:
        logger.warning("Build stamp: UNKNOWN — this image was not built by deploy/hetzner/update.sh")
    else:
        logger.info("Build stamp: %s", sha)
