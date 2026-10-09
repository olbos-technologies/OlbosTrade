"""The workflow must run once per commit, and not pull images anonymously.

Guards against a real failure. `ci.yml` triggered on both
`push: branches: ["**"]` and `pull_request` with no `concurrency` block, so
every push to a branch with an open PR started TWO full runs of the same
workflow on the same commit. They doubled runner minutes, made every CI
report ambiguous about which run was being quoted, and pulled the postgres
service image twice per push. That doubling exhausted Docker Hub's
anonymous per-IP quota and failed `backend-tests` on PR #102 with

    Error response from daemon: toomanyrequests: You have reached your
    unauthenticated pull rate limit.

three retries deep, before a single test executed — a red PR caused by CI
configuration rather than by the code under review.

Four properties are pinned here, because each one silently undoes the fix:

1. push does not fire on every branch (restores the duplicate pair),
2. a concurrency group exists (otherwise pushes race instead of superseding),
3. cancel-in-progress is conditional, never a bare `true` (a bare one would
   cancel main's post-merge validation run),
4. the service image is not an anonymous Docker Hub pull, and is pinned by
   digest so "verified mirror" stays true rather than drifting with a tag.

Also asserts the job names, because required status checks are matched by
name: renaming a job silently orphans the branch-protection rule that was
gating merges on it, and nothing else in the repository would notice.
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

WORKFLOW = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

#: The postgres service image's expected index digest, verified byte-for-byte
#: against Docker Hub's official `library/postgres:16` manifest. Both
#: registries return this, and it matches Docker Hub's own
#: docker-content-digest header. Re-verify with the command in ci.yml.
VERIFIED_POSTGRES_DIGEST = (
    "sha256:ca0bd484cb98bf4b24eb1010e73fb3fcbd6714d240fbc1a10eea5b7dbecb641d"
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.exists(), f"{WORKFLOW} is missing — this guard reads nothing"
    loaded = yaml.safe_load(WORKFLOW.read_text())
    # PyYAML resolves the bare key `on` to the boolean True (YAML 1.1).
    triggers = loaded.get("on", loaded.get(True))
    assert triggers, "the workflow has no triggers; this guard is reading nothing"
    loaded["__triggers__"] = triggers
    return loaded


@pytest.fixture(scope="module")
def raw() -> str:
    return WORKFLOW.read_text()


def test_push_does_not_fire_on_every_branch(workflow):
    """`branches: ["**"]` next to `pull_request` is the duplicate-run pair."""
    push = workflow["__triggers__"].get("push")
    assert push is not None, "main-branch validation on push was removed"
    branches = push.get("branches")
    assert branches, "push with no branch filter runs on every branch again"
    assert "**" not in branches and "*" not in branches, (
        "push fires on every branch again; combined with pull_request that is "
        "two full runs of this workflow per commit"
    )


def test_main_is_still_validated_on_push(workflow):
    """The thing the branch filter must not throw away."""
    assert "main" in workflow["__triggers__"]["push"]["branches"], (
        "pushes to main no longer run CI — post-merge validation is gone"
    )


def test_pull_requests_are_still_checked(workflow):
    """Required checks hang off the pull_request event."""
    assert "pull_request" in workflow["__triggers__"], (
        "removing pull_request would leave every PR with no checks to require"
    )


def test_a_concurrency_group_supersedes_older_runs(workflow):
    group = workflow.get("concurrency", {}).get("group")
    assert group, "no concurrency group: pushes race instead of superseding"
    assert "github.workflow" in group, (
        "the group should be scoped per workflow, or unrelated workflows cancel "
        "each other"
    )


def test_cancel_in_progress_is_conditional_not_a_bare_true(workflow):
    """A bare `true` would cancel main's validation run mid-flight.

    Superseding a PR run is right: nobody is waiting on an older commit. Doing
    it to main is not — that run IS the post-merge validation, and cancelling
    it leaves main unverified while looking like a passing pipeline.
    """
    cancel = workflow.get("concurrency", {}).get("cancel-in-progress")
    assert cancel is not True, (
        "cancel-in-progress: true cancels main's post-merge validation run; "
        "make it conditional on the ref"
    )
    assert isinstance(cancel, str) and "github.ref" in cancel, (
        "cancel-in-progress should be an expression that exempts main"
    )
    assert "refs/heads/main" in cancel


def test_the_postgres_service_is_not_an_anonymous_docker_hub_pull(workflow):
    image = workflow["jobs"]["backend-tests"]["services"]["postgres"]["image"]
    assert not re.fullmatch(r"postgres(:.*)?", image), (
        "a bare `postgres` image is an anonymous Docker Hub pull, which is "
        "rate-limited per IP on shared runners — the failure this guards"
    )
    assert "docker.io/" not in image and not image.startswith("library/")


def test_the_postgres_service_is_pinned_to_the_verified_digest(workflow):
    """Content-addressed, so the mirror cannot quietly serve something else.

    A tag on a mirror requires trusting the mirror continuously. A digest
    requires trusting it once, verifiably: this is the same manifest Docker
    Hub serves for the official image.
    """
    image = workflow["jobs"]["backend-tests"]["services"]["postgres"]["image"]
    assert "@sha256:" in image, (
        "pin the image by digest; a tag can drift and 'verified mirror' then "
        "means nothing"
    )
    assert image.endswith(VERIFIED_POSTGRES_DIGEST), (
        "the postgres digest changed. That is allowed, but re-verify it "
        "against Docker Hub's official manifest and update "
        "VERIFIED_POSTGRES_DIGEST in the same change — see the command in "
        "ci.yml"
    )


#: The ephemeral test database's password. Not a credential: the service
#: container is reachable only from inside its own job, is destroyed with it,
#: and holds nothing but tables the tests just created. It has to be written
#: somewhere for the service to start.
EPHEMERAL_SERVICE_LITERALS = {"POSTGRES_PASSWORD: postgres"}


def test_no_credentials_are_written_into_the_workflow(raw):
    """Authenticated pulls are fine; credential literals in the repo are not.

    If the postgres service is ever switched to an authenticated registry, the
    `credentials:` block must read `${{ secrets.* }}` — a reference, which this
    test permits — and never a username and password typed into the file.
    """
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "secrets." in stripped:
            continue          # a secrets reference is the correct form
        if stripped in EPHEMERAL_SERVICE_LITERALS:
            continue
        assert not re.search(
            r"(password|token|secret)\s*:\s*['\"]?[A-Za-z0-9_\-]{8,}", stripped, re.I
        ), f"a credential looks hard-coded into ci.yml: {stripped!r}"


def test_the_required_check_names_are_unchanged(workflow):
    """Branch protection matches required checks by job name.

    Renaming a job does not fail anything — it just means the rule that was
    gating merges no longer matches any check, and merges stop being gated.
    Nothing else in this repository would notice.
    """
    assert set(workflow["jobs"]) == {
        "backend-tests",
        "frontend-tests",
        "e2e-tests",
    }, (
        "a CI job was renamed, added or removed. If deliberate, update the "
        "repository's required status checks in branch protection in the same "
        "change, then update this test."
    )
