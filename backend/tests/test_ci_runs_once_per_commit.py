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
4. the job pulls NO container image at all, and the PostgreSQL it uses
   instead has its major version asserted.

Property 4 was twice weaker than this. It began as "not a bare Docker Hub
image", then "pinned to a verified ECR Public digest" — and ECR Public then
exhausted its own data limit and failed `backend-tests` on PR #117 the same
way, in "Initialize containers", before checkout, every later step reading
`skipped`. Both fixes were right about their cause and wrong about the
category: any anonymous registry has a quota behind it, so a third registry
resets the clock rather than stopping it. The job now starts the PostgreSQL
that ships on the runner image, which needs no registry, no network and no
secrets. What that gives up is digest pinning, so the major version is
asserted in the workflow and here.

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


def test_backend_tests_pulls_no_container_image(workflow):
    """No service containers, so no registry quota can fail this job.

    This is the property both earlier fixes were reaching for. A `services:`
    block is pulled during "Initialize containers", before checkout and
    before any step runs, so a quota refusal there cannot be caught,
    retried past, or distinguished from a real failure by anyone reading
    the PR. Removing the pull removes the failure mode rather than
    relocating it.
    """
    job = workflow["jobs"]["backend-tests"]
    assert "services" not in job, (
        "backend-tests declared a service container again. That is a "
        "registry pull before any step runs, and an anonymous one has "
        "already failed this job twice (Docker Hub on #102, ECR Public on "
        "#117). Start the database from the runner image instead, or if a "
        "container is genuinely needed, authenticate it via `credentials:` "
        "reading `${{ secrets.* }}`."
    )
    raw_job = yaml.dump(job)
    assert "public.ecr.aws" not in raw_job and "docker.io" not in raw_job, (
        "a registry reference reappeared in backend-tests"
    )


def test_postgres_is_started_from_the_runner_image(workflow):
    """The database still exists, and is a real PostgreSQL.

    Dropping the service container must not quietly become "no database":
    the concurrency tests skip without one, and a skipped test is a green
    tick that proves nothing. `PostgreSQL-backed tests must RUN, not skip`
    is the step that catches that at runtime; this catches it in review.
    """
    steps = workflow["jobs"]["backend-tests"]["steps"]
    start = next(
        (s for s in steps if "postgresql" in (s.get("name") or "").lower()),
        None,
    )
    assert start is not None, "no step starts PostgreSQL"
    run = start["run"]
    assert "systemctl start postgresql" in run
    assert "createdb olbos_test" in run, (
        "the test database must be created; the old service container got "
        "it from POSTGRES_DB"
    )
    assert "pg_isready" in run, (
        "wait for readiness — the unit returning is not the server "
        "accepting connections"
    )


def test_the_backend_runner_image_is_pinned(workflow):
    """This job depends on what the runner image contains, so it names one.

    `ubuntu-latest` moves to the next LTS on GitHub's schedule. Since the
    database now comes from the image rather than a registry, that move
    would swap the database under the suite at a time nobody chose. The
    major-version assertion would catch it, but as surprise red CI instead
    of a decision someone made.
    """
    runs_on = workflow["jobs"]["backend-tests"]["runs-on"]
    assert runs_on != "ubuntu-latest", (
        "backend-tests must pin its runner image, because it starts the "
        "PostgreSQL that image ships"
    )
    assert re.fullmatch(r"ubuntu-\d\d\.\d\d", runs_on), (
        f"expected a pinned ubuntu image, got {runs_on!r}"
    )


def test_the_postgres_major_version_is_asserted_not_assumed(workflow):
    """What replaces the digest pin.

    The runner image is GitHub's to bump. Without this assertion a bump
    would run the whole suite against a different PostgreSQL and report
    green, which is exactly the drift the digest pin existed to prevent.
    """
    steps = workflow["jobs"]["backend-tests"]["steps"]
    run = next(
        s["run"] for s in steps
        if "postgresql" in (s.get("name") or "").lower()
    )
    assert re.search(r'major[^\n]*!=\s*"16"', run), (
        "the workflow no longer asserts the PostgreSQL major version. A "
        "runner-image bump would then change the database under the suite "
        "silently. Update the expected major deliberately, in the same "
        "change that validates against it."
    )
    assert "::error::" in run, (
        "a version mismatch must surface as a GitHub error annotation, not "
        "a bare non-zero exit buried in the log"
    )


def test_no_credentials_are_written_into_the_workflow(raw):
    """Authenticated pulls are fine; credential literals in the repo are not.

    If a container is ever reintroduced and needs an authenticated registry,
    its `credentials:` block must read `${{ secrets.* }}` — a reference,
    which this test permits — and never a username and password typed into
    the file.

    The allowlist this test used to carry (`POSTGRES_PASSWORD: postgres`)
    is gone with the service container, which makes the scan stricter: that
    line reappearing now fails here as well as in
    test_backend_tests_pulls_no_container_image. The ephemeral test
    database's password is set by an `ALTER USER` statement in a `run:`
    step instead — deliberately not a `key: value` pair, which is the shape
    a leaked credential takes in YAML and the shape this pattern matches.
    """
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "secrets." in stripped:
            continue          # a secrets reference is the correct form
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
