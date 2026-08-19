# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import build_release_manifest, release, select_release_assets
from scripts.release_contract import (
    CHECKSUMS_FILENAME,
    RELEASE_MANIFEST_FILENAME,
    release_tag,
    required_build_artifacts,
    required_release_assets,
)

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.3.4"
COMMIT = "a" * 40


def plan() -> release.ReleasePlan:
    return release.ReleasePlan(
        version=VERSION,
        tag=release_tag(VERSION),
        commit=COMMIT,
        repository="kmgowda/sbk-dashboard",
        remote="origin",
        branch="main",
        checked_branch="main",
        image="kmgowda/sbk-dashboard",
    )


class ReleaseContractTest(unittest.TestCase):
    @staticmethod
    def write_artifacts(directory: Path) -> None:
        for number, name in enumerate(required_build_artifacts(VERSION), start=1):
            if not name.endswith(".sha256"):
                (directory / name).write_bytes(f"artifact-{number}".encode())
        for name in required_build_artifacts(VERSION):
            if name.endswith(".sha256"):
                archive_name = name.removesuffix(".sha256")
                digest = hashlib.sha256((directory / archive_name).read_bytes()).hexdigest()
                (directory / name).write_text(f"{digest}  {archive_name}\n", encoding="ascii")

    def test_release_asset_contract_is_complete_and_platform_specific(self):
        assets = required_release_assets(VERSION)
        self.assertEqual(10, len(assets))
        self.assertIn("sbk_dashboard-1.2.3.4-py3-none-any.whl", assets)
        self.assertIn("sbk_dashboard-1.2.3.4.tar.gz", assets)
        self.assertIn("sbk-dashboard-1.2.3.4-linux-amd64.tar.gz", assets)
        self.assertIn("sbk-dashboard-1.2.3.4-macos-arm64.tar.gz", assets)
        self.assertIn("sbk-dashboard-1.2.3.4-windows-amd64.zip", assets)
        self.assertIn(CHECKSUMS_FILENAME, assets)
        self.assertIn(RELEASE_MANIFEST_FILENAME, assets)

    def test_release_manifest_requires_exact_artifacts_and_writes_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_artifacts(directory)
            manifest = build_release_manifest.build_manifest(
                directory, VERSION, release_tag(VERSION), COMMIT
            )
            self.assertEqual(VERSION, manifest["applicationVersion"])
            self.assertEqual(COMMIT, manifest["commit"])
            self.assertEqual(8, len(manifest["artifacts"]))
            checksums = (directory / CHECKSUMS_FILENAME).read_text(encoding="ascii")
            self.assertEqual(8, len(checksums.splitlines()))
            self.assertTrue((directory / RELEASE_MANIFEST_FILENAME).is_file())

    def test_release_manifest_rejects_invalid_platform_checksum(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_artifacts(directory)
            checksum = next(directory.glob("*.sha256"))
            checksum.write_text(f"{'0' * 64}  {checksum.name.removesuffix('.sha256')}\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "checksum does not match"):
                build_release_manifest.build_manifest(
                    directory, VERSION, release_tag(VERSION), COMMIT
                )

    def test_release_manifest_rejects_missing_or_unexpected_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "unexpected.zip").write_bytes(b"unexpected")
            with self.assertRaisesRegex(ValueError, "artifact mismatch"):
                build_release_manifest.build_manifest(
                    directory, VERSION, release_tag(VERSION), COMMIT
                )

    def test_repository_name_is_derived_from_https_and_ssh_remotes(self):
        self.assertEqual(
            "kmgowda/sbk-dashboard",
            release.repository_from_remote("https://github.com/kmgowda/sbk-dashboard.git"),
        )
        self.assertEqual(
            "kmgowda/sbk-dashboard",
            release.repository_from_remote("git@github.com:kmgowda/sbk-dashboard.git"),
        )
        with self.assertRaisesRegex(release.ReleaseError, "supported GitHub"):
            release.repository_from_remote("https://example.test/project.git")

    def test_release_repository_is_always_owned_by_kmgowda(self):
        git = Mock()
        git.clean.return_value = True
        git.branch.return_value = "main"
        git.commit.return_value = COMMIT
        git.tracked_commit.return_value = COMMIT
        git.remote_url.return_value = "https://github.com/another/sbk-dashboard.git"
        with (
            patch.object(release, "package_version", return_value=VERSION),
            patch.object(release, "synchronize", return_value=[]),
            self.assertRaisesRegex(release.ReleaseError, "kmgowda/sbk-dashboard"),
        ):
            release.resolve_plan(
                git,
                remote="origin",
                branch="main",
                repository=None,
                image="kmgowda/sbk-dashboard",
                allow_branch=False,
                online=False,
            )

    def test_release_token_must_authenticate_kmgowda(self):
        github = release.GitHubClient("kmgowda/sbk-dashboard", "token")
        github.api = Mock()
        github.api.request.return_value = (200, {"login": "another"})
        with self.assertRaisesRegex(release.ReleaseError, "kmgowda"):
            github.require_release_user()
        github.api.request.return_value = (200, {"login": "kmgowda"})
        github.require_release_user()

    def test_matching_workflow_is_exact_commit_and_branch(self):
        runs = [
            {"head_sha": "b" * 40, "head_branch": "main"},
            {"head_sha": COMMIT, "head_branch": "other"},
            {"head_sha": COMMIT, "head_branch": "main", "status": "completed"},
        ]
        self.assertEqual(runs[2], release.matching_workflow_run(runs, COMMIT, "main"))
        self.assertIsNone(release.matching_workflow_run(runs, COMMIT, "missing"))

    def test_matching_workflow_accepts_fully_qualified_refs(self):
        head = {"head_sha": COMMIT, "head_branch": "refs/heads/main"}
        tag = {"head_sha": COMMIT, "head_branch": "refs/tags/v1.2.3.4"}
        self.assertEqual(head, release.matching_workflow_run([head], COMMIT, "main"))
        self.assertEqual(tag, release.matching_workflow_run([tag], COMMIT, "v1.2.3.4"))

    def test_pr_branch_check_may_differ_from_remote_main(self):
        git = Mock()
        git.clean.return_value = True
        git.branch.return_value = "release-feature"
        git.commit.return_value = COMMIT
        git.tracked_commit.return_value = "b" * 40
        git.remote_url.return_value = "https://github.com/kmgowda/sbk-dashboard.git"
        with (
            patch.object(release, "package_version", return_value=VERSION),
            patch.object(release, "synchronize", return_value=[]),
        ):
            selected = release.resolve_plan(
                git,
                remote="origin",
                branch="main",
                repository=None,
                image="kmgowda/sbk-dashboard",
                allow_branch=True,
                online=False,
            )
        self.assertEqual(COMMIT, selected.commit)
        self.assertEqual("release-feature", selected.checked_branch)
        self.assertEqual("main", selected.branch)

    def test_api_headers_are_specific_to_github_and_docker_hub(self):
        github = release.GitHubClient("kmgowda/sbk-dashboard", "token")
        docker_hub = release.DockerHubClient("kmgowda/sbk-dashboard")
        self.assertEqual(release.GITHUB_ACCEPT, github.api.accept)
        self.assertEqual(release.GITHUB_API_VERSION, github.api.api_version)
        self.assertEqual(release.JSON_ACCEPT, docker_hub.api.accept)
        self.assertIsNone(docker_hub.api.api_version)

    def test_wait_for_workflow_rejects_failed_exact_commit(self):
        github = Mock()
        github.workflow_runs.return_value = [
            {
                "head_sha": COMMIT,
                "head_branch": "main",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://example.test/run",
            }
        ]
        with self.assertRaisesRegex(release.ReleaseError, "failure"):
            release.wait_for_workflow(
                github,
                release.CI_WORKFLOW,
                "push",
                plan(),
                head_branch="main",
                timeout_seconds=1,
                poll_seconds=0.01,
            )

    def test_wait_for_assets_retries_while_generated_notes_are_empty(self):
        selected = plan()
        assets = [{"name": name} for name in required_release_assets(VERSION)]
        github = Mock()
        github.release.side_effect = [
            {"assets": assets, "body": ""},
            {"assets": assets, "body": "Generated notes"},
        ]
        sleeper = Mock()
        monotonic = Mock(side_effect=[0, 0, 0, 0])
        result = release.wait_for_assets(
            github,
            selected,
            timeout_seconds=10,
            poll_seconds=1,
            sleeper=sleeper,
            monotonic=monotonic,
        )
        self.assertEqual("Generated notes", result["body"])
        sleeper.assert_called_once_with(1)

    def test_docker_verification_honors_rate_limit_and_backs_off(self):
        docker_hub = Mock()
        docker_hub.digest.side_effect = [
            release.RateLimitError("limited", 120),
            "sha256:123",
            "sha256:123",
        ]
        sleeper = Mock()
        monotonic = Mock(side_effect=[0, 0, 0, 0])
        digest = release.wait_for_docker_tags(
            docker_hub,
            plan(),
            timeout_seconds=300,
            poll_seconds=15,
            sleeper=sleeper,
            monotonic=monotonic,
        )
        self.assertEqual("sha256:123", digest)
        sleeper.assert_called_once_with(120)

    def test_retry_after_requires_a_positive_finite_delta(self):
        self.assertEqual(30, release.retry_after_seconds("30"))
        for value in (None, "date", "0", "-1", "inf"):
            self.assertIsNone(release.retry_after_seconds(value))

    def test_json_api_exposes_http_429_retry_after(self):
        error = release.urllib.error.HTTPError(
            "https://example.test/api",
            429,
            "rate limited",
            {"Retry-After": "45"},
            io.BytesIO(b'{"message":"slow down"}'),
        )
        with (
            patch.object(release.urllib.request, "urlopen", side_effect=error),
            self.assertRaises(release.RateLimitError) as raised,
        ):
            release.JsonApi("https://example.test").request("GET", "/api")
        self.assertEqual(45, raised.exception.retry_after)

    def test_asset_rerun_selects_only_missing_and_rejects_conflicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_artifacts(directory)
            build_release_manifest.build_manifest(directory, VERSION, release_tag(VERSION), COMMIT)
            first_name = required_release_assets(VERSION)[0]
            first_path = directory / first_name
            existing = {
                "name": first_name,
                "size": first_path.stat().st_size,
                "digest": f"sha256:{build_release_manifest.sha256(first_path)}",
            }
            missing = select_release_assets.missing_assets(
                directory, VERSION, {"assets": [existing]}
            )
            self.assertEqual(len(required_release_assets(VERSION)) - 1, len(missing))
            self.assertNotIn(first_path, missing)
            existing["digest"] = "sha256:" + "0" * 64
            with self.assertRaisesRegex(select_release_assets.AssetSelectionError, "differs"):
                select_release_assets.missing_assets(
                    directory, VERSION, {"assets": [existing]}
                )

    def test_check_mode_never_calls_publish_or_remote_mutations(self):
        selected = plan()
        with (
            patch.object(release, "resolve_plan", return_value=selected),
            patch.object(release, "publish") as publish,
            patch.object(release.GitRepository, "create_tag") as create_tag,
            patch.object(release.GitRepository, "push_tag") as push_tag,
        ):
            self.assertEqual(0, release.main(["check", "--allow-branch", "--offline"]))
        publish.assert_not_called()
        create_tag.assert_not_called()
        push_tag.assert_not_called()

    def test_publish_requires_exact_visible_tag_confirmation(self):
        selected = plan()
        with (
            patch.object(release, "resolve_plan", return_value=selected),
            patch.object(release, "publish") as publish,
            self.assertRaisesRegex(SystemExit, "--confirm"),
        ):
            release.main(["publish", "--confirm", "v9.9.9.9"])
        publish.assert_not_called()

    def test_noncanonical_token_variable_is_not_accepted(self):
        selected = plan()
        noncanonical_name = "".join(("GH", "_TOKEN"))
        with (
            patch.object(release, "resolve_plan", return_value=selected),
            patch.dict(os.environ, {noncanonical_name: "legacy-token"}, clear=True),
            patch.object(release, "publish") as publish,
            self.assertRaisesRegex(SystemExit, "GITHUB_TOKEN is required"),
        ):
            release.main(["publish", "--confirm", selected.tag])
        publish.assert_not_called()

    def test_wait_arguments_are_finite_and_bounded(self):
        self.assertEqual(60, release.timeout_seconds("60"))
        for value in ("0", "inf", "nan", str(release.MAX_TIMEOUT_SECONDS + 1)):
            with self.assertRaises(release.argparse.ArgumentTypeError):
                release.timeout_seconds(value)

    def test_publish_orders_tag_container_release_and_assets(self):
        selected = plan()
        git = Mock()
        git.local_tag_commit.return_value = None
        git.remote_tag_commit.return_value = None
        github = Mock()
        github.release.return_value = None
        github.create_release.return_value = {"html_url": "https://example.test/release"}
        docker_hub = Mock()
        order: list[str] = []
        git.create_tag.side_effect = lambda *_args: order.append("tag")
        git.push_tag.side_effect = lambda *_args: order.append("push")
        github.create_release.side_effect = lambda *_args: order.append("release") or {
            "html_url": "https://example.test/release"
        }
        with (
            patch.object(release, "require_ci", side_effect=lambda *_args: order.append("ci")),
            patch.object(
                release,
                "wait_for_workflow",
                side_effect=lambda _client, workflow, *_args, **_kwargs: order.append(workflow) or {},
            ),
            patch.object(
                release,
                "wait_for_assets",
                side_effect=lambda *_args, **_kwargs: order.append("assets")
                or {"html_url": "https://example.test/release"},
            ),
            patch.object(
                release,
                "wait_for_docker_tags",
                side_effect=lambda *_args, **_kwargs: order.append("docker") or "sha256:123",
            ),
        ):
            release.publish(
                git,
                github,
                docker_hub,
                selected,
                resume=False,
                timeout_seconds=1,
                poll_seconds=0.01,
            )
        self.assertEqual(
            [
                "ci",
                "tag",
                "push",
                release.CONTAINER_WORKFLOW,
                "release",
                release.PORTABLE_WORKFLOW,
                "assets",
                "docker",
            ],
            order,
        )

    def test_release_has_dedicated_root_commands(self):
        shell = (ROOT / "sbk-dashboard").read_text(encoding="utf-8")
        powershell = (ROOT / "sbk-dashboard.ps1").read_text(encoding="utf-8")
        self.assertNotIn("release-sbk-dashboard", shell.lower())
        self.assertNotIn("release-sbk-dashboard", powershell.lower())
        dedicated_shell = (ROOT / "release-sbk-dashboard.sh").read_text(encoding="utf-8")
        dedicated_powershell = (ROOT / "Release-SbkDashboard.ps1").read_text(encoding="utf-8")
        dedicated_command = (ROOT / "release-sbk-dashboard.cmd").read_text(encoding="utf-8")
        self.assertIn("scripts/release-sbk-dashboard.sh", dedicated_shell)
        self.assertIn("scripts\\Release-SbkDashboard.ps1", dedicated_powershell)
        self.assertIn("Release-SbkDashboard.ps1", dedicated_command)
        implementation = (ROOT / "scripts/Release-SbkDashboard.ps1").read_text(encoding="utf-8")
        self.assertIn("@('py', 'py.exe')", implementation)

    def test_portable_workflow_publishes_only_after_all_builds(self):
        workflow = (ROOT / ".github/workflows/portable.yml").read_text(encoding="utf-8")
        self.assertIn("needs: [build, package]", workflow)
        self.assertIn("scripts/build_release_manifest.py", workflow)
        self.assertIn("scripts/select_release_assets.py", workflow)
        self.assertIn("missing_output=$(python scripts/select_release_assets.py", workflow)
        self.assertNotIn("mapfile -t missing_assets < <(", workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertIn('if: github.event_name == \'release\'', workflow)
        self.assertNotIn("gh release upload \"${{ github.event.release.tag_name }}\" dist/portable/*", workflow)


if __name__ == "__main__":
    unittest.main()
