#!/usr/bin/env python3
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Check or publish one complete SBK Dashboard release through GitHub Actions."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, NoReturn

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from release_contract import release_tag, required_release_assets, validate_version  # noqa: E402
from sync_release_metadata import package_version, synchronize  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "main"
EXPECTED_GITHUB_USER = "kmgowda"
DEFAULT_REPOSITORY = f"{EXPECTED_GITHUB_USER}/sbk-dashboard"
DEFAULT_IMAGE = "kmgowda/sbk-dashboard"
CI_WORKFLOW = "ci.yml"
CONTAINER_WORKFLOW = "container.yml"
PORTABLE_WORKFLOW = "portable.yml"
GITHUB_API_URL = "https://api.github.com"
DOCKER_HUB_API_URL = "https://hub.docker.com/v2/repositories"
DEFAULT_POLL_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_POLL_SECONDS = 5 * 60
MAX_TIMEOUT_SECONDS = 6 * 60 * 60
HTTP_TIMEOUT_SECONDS = 30.0
MAX_API_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_WORKFLOW_RUNS = 50
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
GITHUB_REMOTE_PATTERN = re.compile(
    r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?$"
)


class ReleaseError(RuntimeError):
    """A safe release precondition or remote operation failed."""


@dataclass(frozen=True)
class ReleasePlan:
    """Immutable release identity resolved from the checked-out source tree."""

    version: str
    tag: str
    commit: str
    repository: str
    remote: str
    branch: str
    image: str


class GitRepository:
    """Small checked subprocess boundary for release-specific Git operations."""

    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def run(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments], cwd=self.root, capture_output=True, check=False, text=True
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
            raise ReleaseError(detail)
        return completed.stdout.strip()

    def clean(self) -> bool:
        return not self.run("status", "--porcelain=v1")

    def branch(self) -> str:
        return self.run("symbolic-ref", "--quiet", "--short", "HEAD")

    def commit(self) -> str:
        return self.run("rev-parse", "HEAD")

    def tracked_commit(self, remote: str, branch: str, *, online: bool) -> str:
        if not online:
            return self.run("rev-parse", f"refs/remotes/{remote}/{branch}")
        output = self.run("ls-remote", "--heads", remote, f"refs/heads/{branch}")
        if not output:
            raise ReleaseError(f"Remote branch {remote}/{branch} was not found")
        return output.split()[0]

    def remote_url(self, remote: str) -> str:
        return self.run("remote", "get-url", remote)

    def local_tag_commit(self, tag: str) -> str | None:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
            cwd=self.root,
            capture_output=True,
            check=False,
            text=True,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None

    def remote_tag_commit(self, remote: str, tag: str) -> str | None:
        output = self.run(
            "ls-remote", "--tags", remote, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"
        )
        if not output:
            return None
        entries = {reference: sha for sha, reference in (line.split() for line in output.splitlines())}
        return entries.get(f"refs/tags/{tag}^{{}}") or entries.get(f"refs/tags/{tag}")

    def create_tag(self, tag: str, version: str) -> None:
        self.run("tag", "--annotate", tag, "--message", f"SBK Dashboard {version}")

    def delete_local_tag(self, tag: str) -> None:
        self.run("tag", "--delete", tag)

    def push_tag(self, remote: str, tag: str) -> None:
        self.run("push", remote, f"refs/tags/{tag}:refs/tags/{tag}")


class JsonApi:
    """Bounded JSON HTTP client using only the Python standard library."""

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(
        self, method: str, path: str, payload: dict[str, object] | None = None
    ) -> tuple[int, dict[str, Any]]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "sbk-dashboard-release"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = response.read(MAX_API_RESPONSE_BYTES + 1)
                if len(body) > MAX_API_RESPONSE_BYTES:
                    raise ReleaseError("Remote API response exceeded the configured bound")
                decoded = json.loads(body.decode("utf-8")) if body else {}
                if not isinstance(decoded, dict):
                    raise ReleaseError("Remote API returned an unexpected JSON value")
                return response.status, decoded
        except urllib.error.HTTPError as error:
            body = error.read(MAX_API_RESPONSE_BYTES).decode("utf-8", errors="replace")
            try:
                message = json.loads(body).get("message", body)
            except json.JSONDecodeError:
                message = body
            if error.code == HTTPStatus.NOT_FOUND:
                return error.code, {}
            raise ReleaseError(f"Remote API returned HTTP {error.code}: {message}") from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ReleaseError(f"Unable to use remote API: {error}") from error


class GitHubClient:
    """Release and Actions operations scoped to one GitHub repository."""

    def __init__(self, repository: str, token: str | None) -> None:
        self.repository = repository
        self.api = JsonApi(GITHUB_API_URL, token)

    def _path(self, suffix: str) -> str:
        return f"/repos/{self.repository}{suffix}"

    def require_release_user(self) -> None:
        """Require the personal release token to belong to the project owner."""
        _, response = self.api.request("GET", "/user")
        login = response.get("login")
        if login != EXPECTED_GITHUB_USER:
            raise ReleaseError(
                f"GITHUB_TOKEN must authenticate as {EXPECTED_GITHUB_USER!r}, not {login!r}"
            )

    def release(self, tag: str) -> dict[str, Any] | None:
        status, response = self.api.request(
            "GET", self._path(f"/releases/tags/{urllib.parse.quote(tag, safe='')}")
        )
        return None if status == HTTPStatus.NOT_FOUND else response

    def create_release(self, plan: ReleasePlan) -> dict[str, Any]:
        status, response = self.api.request(
            "POST",
            self._path("/releases"),
            {
                "tag_name": plan.tag,
                "target_commitish": plan.commit,
                "name": f"SBK Dashboard {plan.version}",
                "generate_release_notes": True,
                "draft": False,
                "prerelease": False,
                "make_latest": "true",
            },
        )
        if status != HTTPStatus.CREATED:
            raise ReleaseError(f"GitHub did not create release {plan.tag}; HTTP {status}")
        return response

    def workflow_runs(self, workflow: str, event: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"event": event, "per_page": MAX_WORKFLOW_RUNS})
        _, response = self.api.request(
            "GET", self._path(f"/actions/workflows/{urllib.parse.quote(workflow, safe='')}/runs?{query}")
        )
        runs = response.get("workflow_runs", [])
        if not isinstance(runs, list):
            raise ReleaseError("GitHub Actions returned an invalid workflow run list")
        return [run for run in runs if isinstance(run, dict)]


class DockerHubClient:
    """Read public Docker Hub tag metadata after the signed workflow completes."""

    def __init__(self, image: str) -> None:
        self.image = image
        self.api = JsonApi(DOCKER_HUB_API_URL)

    def digest(self, tag: str) -> str | None:
        status, response = self.api.request(
            "GET", f"/{self.image}/tags/{urllib.parse.quote(tag, safe='')}"
        )
        if status == HTTPStatus.NOT_FOUND:
            return None
        digest = response.get("digest")
        return digest if isinstance(digest, str) and digest.startswith("sha256:") else None


def repository_from_remote(url: str) -> str:
    """Extract an owner/name pair from an HTTPS or SSH GitHub remote."""
    match = GITHUB_REMOTE_PATTERN.fullmatch(url)
    if match is None:
        raise ReleaseError(f"Remote is not a supported GitHub repository URL: {url!r}")
    repository = match.group(1)
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ReleaseError(f"Remote produced an invalid GitHub repository name: {repository!r}")
    return repository


def resolve_plan(
    git: GitRepository,
    *,
    remote: str,
    branch: str,
    repository: str | None,
    image: str,
    allow_branch: bool,
    online: bool,
) -> ReleasePlan:
    """Validate local metadata and resolve the exact release identity without publishing."""
    if not git.clean():
        raise ReleaseError("The working tree must be clean before release validation")
    current_branch = git.branch()
    if not allow_branch and current_branch != branch:
        raise ReleaseError(f"Release must run from branch {branch!r}, not {current_branch!r}")
    version = validate_version(package_version())
    mismatches = synchronize(write=False)
    if mismatches:
        raise ReleaseError("Release metadata is out of sync: " + ", ".join(mismatches))
    commit = git.commit()
    tracked = git.tracked_commit(remote, branch, online=online)
    if commit != tracked and not allow_branch:
        raise ReleaseError(f"HEAD {commit} does not match {remote}/{branch} {tracked}")
    selected_repository = repository or repository_from_remote(git.remote_url(remote))
    if REPOSITORY_PATTERN.fullmatch(selected_repository) is None:
        raise ReleaseError(f"Invalid GitHub repository: {selected_repository!r}")
    if selected_repository != DEFAULT_REPOSITORY:
        raise ReleaseError(
            f"GitHub repository must be {DEFAULT_REPOSITORY!r}, not {selected_repository!r}"
        )
    if "/" not in image:
        raise ReleaseError(f"Docker image must include its namespace: {image!r}")
    return ReleasePlan(
        version=version,
        tag=release_tag(version),
        commit=commit,
        repository=selected_repository,
        remote=remote,
        branch=branch,
        image=image,
    )


def matching_workflow_run(
    runs: list[dict[str, Any]], commit: str, branch: str | None
) -> dict[str, Any] | None:
    """Return the newest workflow run for an exact release commit and optional branch/tag."""
    for run in runs:
        if run.get("head_sha") == commit and (branch is None or run.get("head_branch") == branch):
            return run
    return None


def wait_for_workflow(
    github: GitHubClient,
    workflow: str,
    event: str,
    plan: ReleasePlan,
    *,
    head_branch: str | None,
    timeout_seconds: float,
    poll_seconds: float,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Wait a bounded period for one exact-commit workflow and require success."""
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        run = matching_workflow_run(github.workflow_runs(workflow, event), plan.commit, head_branch)
        if run is None:
            print(f"Waiting for {workflow} to start for {plan.commit[:12]}...")
        elif run.get("status") == "completed":
            if run.get("conclusion") != "success":
                raise ReleaseError(
                    f"{workflow} completed with {run.get('conclusion')!r}: {run.get('html_url', '')}"
                )
            print(f"{workflow} passed: {run.get('html_url', '')}")
            return run
        else:
            print(f"Waiting for {workflow}: {run.get('status', 'unknown')}...")
        sleeper(poll_seconds)
    raise ReleaseError(f"Timed out after {timeout_seconds:g} seconds waiting for {workflow}")


def wait_for_assets(
    github: GitHubClient,
    plan: ReleasePlan,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    """Wait until the published GitHub Release exposes the complete contracted asset set."""
    expected = set(required_release_assets(plan.version))
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        release = github.release(plan.tag)
        if release is not None:
            assets = release.get("assets", [])
            names = {
                asset.get("name") for asset in assets if isinstance(asset, dict) and isinstance(asset.get("name"), str)
            }
            missing = expected - names
            if not missing:
                if not str(release.get("body", "")).strip():
                    raise ReleaseError("GitHub Release notes are empty")
                return release
            print("Waiting for release assets: " + ", ".join(sorted(missing)))
        time.sleep(poll_seconds)
    raise ReleaseError("Timed out waiting for the complete GitHub Release asset set")


def wait_for_docker_tags(
    docker_hub: DockerHubClient,
    plan: ReleasePlan,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> str:
    """Wait for version and latest Docker tags to resolve to one immutable digest."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        version_digest = docker_hub.digest(plan.version)
        latest_digest = docker_hub.digest("latest")
        if version_digest and version_digest == latest_digest:
            return version_digest
        print("Waiting for Docker Hub version/latest tags to converge...")
        time.sleep(poll_seconds)
    raise ReleaseError("Timed out waiting for Docker Hub version and latest tags")


def print_plan(plan: ReleasePlan) -> None:
    """Print the immutable inputs and outputs before any release mutation."""
    print(f"Repository: {plan.repository}")
    print(f"Branch: {plan.branch}")
    print(f"Commit: {plan.commit}")
    print(f"Version: {plan.version}")
    print(f"Git tag: {plan.tag}")
    print(f"GitHub Release: https://github.com/{plan.repository}/releases/tag/{plan.tag}")
    print(f"Docker tags: {plan.image}:{plan.version}, {plan.image}:latest")
    print("GitHub assets:")
    for asset in required_release_assets(plan.version):
        print(f"  {asset}")


def require_ci(
    github: GitHubClient, plan: ReleasePlan, timeout_seconds: float, poll_seconds: float
) -> None:
    """Require the exact main commit to pass the cross-platform CI workflow before tagging."""
    wait_for_workflow(
        github,
        CI_WORKFLOW,
        "push",
        plan,
        head_branch=plan.branch,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def publish(
    git: GitRepository,
    github: GitHubClient,
    docker_hub: DockerHubClient,
    plan: ReleasePlan,
    *,
    resume: bool,
    timeout_seconds: float,
    poll_seconds: float,
) -> None:
    """Publish the tag, gated image, generated-notes release, and complete asset set."""
    local_tag = git.local_tag_commit(plan.tag)
    remote_tag = git.remote_tag_commit(plan.remote, plan.tag)
    existing_release = github.release(plan.tag)
    for location, commit in (("local", local_tag), ("remote", remote_tag)):
        if commit is not None and commit != plan.commit:
            raise ReleaseError(f"Existing {location} tag {plan.tag} points to {commit}, not {plan.commit}")
    if (local_tag or remote_tag or existing_release) and not resume:
        raise ReleaseError(f"{plan.tag} already exists; inspect it and use --resume only for this exact commit")
    require_ci(github, plan, timeout_seconds, poll_seconds)
    if remote_tag is None:
        created_local = False
        if local_tag is None:
            git.create_tag(plan.tag, plan.version)
            created_local = True
        try:
            git.push_tag(plan.remote, plan.tag)
        except ReleaseError:
            if created_local:
                git.delete_local_tag(plan.tag)
            raise
        print(f"Pushed annotated tag {plan.tag}")
    wait_for_workflow(
        github,
        CONTAINER_WORKFLOW,
        "push",
        plan,
        head_branch=plan.tag,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    if existing_release is None:
        existing_release = github.create_release(plan)
        print(f"Published GitHub Release: {existing_release.get('html_url', '')}")
    wait_for_workflow(
        github,
        PORTABLE_WORKFLOW,
        "release",
        plan,
        head_branch=None,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    release = wait_for_assets(
        github, plan, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds
    )
    digest = wait_for_docker_tags(
        docker_hub, plan, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds
    )
    print(f"Release complete: {release.get('html_url', '')}")
    print(f"Docker digest: {plan.image}@{digest}")


def bounded_number(value: str, maximum: float, label: str) -> float:
    """Parse one finite, positive release wait bounded by policy."""
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > maximum:
        raise argparse.ArgumentTypeError(f"{label} must be greater than zero and at most {maximum:g}")
    return number


def timeout_seconds(value: str) -> float:
    """Parse a bounded overall workflow timeout."""
    return bounded_number(value, MAX_TIMEOUT_SECONDS, "timeout seconds")


def poll_seconds(value: str) -> float:
    """Parse a bounded API polling interval."""
    return bounded_number(value, MAX_POLL_SECONDS, "poll seconds")


def parser() -> argparse.ArgumentParser:
    """Build the cross-platform release command parser."""
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--remote", default=DEFAULT_REMOTE)
    command.add_argument("--branch", default=DEFAULT_BRANCH)
    command.add_argument(
        "--repository", help=f"GitHub owner/name; must be {DEFAULT_REPOSITORY}"
    )
    command.add_argument("--image", default=DEFAULT_IMAGE)
    subcommands = command.add_subparsers(dest="action", required=True)
    check = subcommands.add_parser("check", help="validate and print the release plan without publishing")
    check.add_argument("--allow-branch", action="store_true", help="permit PR-branch validation")
    check.add_argument("--offline", action="store_true", help="use local remote-tracking state and skip APIs")
    publish_command = subcommands.add_parser("publish", help="publish after explicit tag confirmation")
    publish_command.add_argument("--confirm", required=True, help="must exactly equal the planned v<version> tag")
    publish_command.add_argument("--resume", action="store_true", help="continue an exact-commit partial release")
    for selected in (check, publish_command):
        selected.add_argument("--timeout-seconds", type=timeout_seconds, default=DEFAULT_TIMEOUT_SECONDS)
        selected.add_argument("--poll-seconds", type=poll_seconds, default=DEFAULT_POLL_SECONDS)
    return command


def fail(message: str) -> NoReturn:
    """Exit with a concise operator-facing release error."""
    raise SystemExit(f"error: {message}")


def main(arguments: list[str] | None = None) -> int:
    """Validate a release or explicitly publish it; check never mutates remote state."""
    selected = parser().parse_args(arguments)
    git = GitRepository()
    try:
        plan = resolve_plan(
            git,
            remote=selected.remote,
            branch=selected.branch,
            repository=selected.repository,
            image=selected.image,
            allow_branch=bool(getattr(selected, "allow_branch", False)),
            online=not bool(getattr(selected, "offline", False)),
        )
        print_plan(plan)
        if selected.action == "check":
            if not selected.offline:
                token = os.environ.get("GITHUB_TOKEN")
                github = GitHubClient(plan.repository, token)
                if token:
                    github.require_release_user()
                release = github.release(plan.tag)
                if release is not None:
                    raise ReleaseError(f"GitHub Release {plan.tag} already exists: {release.get('html_url', '')}")
                remote_tag = git.remote_tag_commit(plan.remote, plan.tag)
                if remote_tag is not None:
                    raise ReleaseError(f"Remote tag {plan.tag} already exists at {remote_tag}")
                require_ci(github, plan, selected.timeout_seconds, selected.poll_seconds)
            print("Release check passed. No tag, release, artifact, or image was created.")
            return 0
        if selected.confirm != plan.tag:
            raise ReleaseError(f"--confirm must exactly equal {plan.tag!r}")
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ReleaseError("GITHUB_TOKEN is required for release publication")
        github = GitHubClient(plan.repository, token)
        github.require_release_user()
        publish(
            git,
            github,
            DockerHubClient(plan.image),
            plan,
            resume=selected.resume,
            timeout_seconds=selected.timeout_seconds,
            poll_seconds=selected.poll_seconds,
        )
        return 0
    except ReleaseError as error:
        fail(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
