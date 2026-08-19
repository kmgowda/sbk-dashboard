# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import build_release_manifest, release
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

    def test_matching_workflow_is_exact_commit_and_branch(self):
        runs = [
            {"head_sha": "b" * 40, "head_branch": "main"},
            {"head_sha": COMMIT, "head_branch": "other"},
            {"head_sha": COMMIT, "head_branch": "main", "status": "completed"},
        ]
        self.assertEqual(runs[2], release.matching_workflow_run(runs, COMMIT, "main"))
        self.assertIsNone(release.matching_workflow_run(runs, COMMIT, "missing"))

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

    def test_root_commands_dispatch_release_without_starting_dashboard(self):
        shell = (ROOT / "sbk-dashboard").read_text(encoding="utf-8")
        powershell = (ROOT / "sbk-dashboard.ps1").read_text(encoding="utf-8")
        self.assertIn("release-sbk-dashboard.sh", shell)
        self.assertIn("Release-SbkDashboard.ps1", powershell)

    def test_portable_workflow_publishes_only_after_all_builds(self):
        workflow = (ROOT / ".github/workflows/portable.yml").read_text(encoding="utf-8")
        self.assertIn("needs: [build, package]", workflow)
        self.assertIn("scripts/build_release_manifest.py", workflow)
        self.assertIn('if: github.event_name == \'release\'', workflow)
        self.assertNotIn("gh release upload \"${{ github.event.release.tag_name }}\" dist/portable/*", workflow)


if __name__ == "__main__":
    unittest.main()
