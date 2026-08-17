# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPYRIGHT_NOTICE = "Copyright (c) KMG. All Rights Reserved."
COMMENTABLE_SUFFIXES = {
    ".cmd",
    ".css",
    ".html",
    ".js",
    ".md",
    ".mdc",
    ".properties",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
}
COMMENTABLE_NAMES = {".dockerignore", ".gitignore", "Dockerfile", "MANIFEST.in", "sbk-dashboard"}
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "sbk_dashboard.egg-info",
}
REPOSITORY_SOURCE_DIRECTORIES = {
    ".cursor",
    ".github",
    ".windsurf",
    "docs",
    "requirements",
    "scripts",
    "src",
    "tests",
}


class DocumentationContractTest(unittest.TestCase):
    def test_commentable_repository_files_have_license_header(self):
        candidates = []
        roots = [ROOT, *(ROOT / name for name in REPOSITORY_SOURCE_DIRECTORIES)]
        for source_root in roots:
            paths = source_root.iterdir() if source_root == ROOT else source_root.rglob("*")
            for path in paths:
                if not path.is_file() or any(part in IGNORED_DIRECTORY_NAMES for part in path.parts):
                    continue
                if (
                    path.suffix in COMMENTABLE_SUFFIXES
                    or path.name in COMMENTABLE_NAMES
                    or path.parent == ROOT / "requirements" and path.suffix == ".txt"
                ):
                    candidates.append(path)

        self.assertTrue(candidates)
        for path in sorted(set(candidates)):
            with self.subTest(path=path.relative_to(ROOT)):
                prefix = path.read_text(encoding="utf-8")[:1024]
                self.assertIn(COPYRIGHT_NOTICE, prefix)

    def test_readme_documents_environment_exit_and_deactivation(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("deactivate", readme)
        self.assertIn("conda deactivate", readme)
        self.assertIn("docs/USAGE.md", readme)
        self.assertIn("docs/INTERNALS.md", readme)
        self.assertIn("SBK_DASHBOARD_DEFAULT_TARGET_HOST", readme)
        self.assertIn("host.docker.internal", readme)
        self.assertIn("start-sbk-dashboard.sh", readme)
        self.assertIn("Start-SbkDashboard.ps1", readme)

    def test_detailed_documents_exist_and_cross_link(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        usage = (ROOT / "docs/USAGE.md").read_text(encoding="utf-8")
        internals = (ROOT / "docs/INTERNALS.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
        docker = (ROOT / "docs/DOCKER.md").read_text(encoding="utf-8")
        docker_hub = (ROOT / "docs/DOCKER_HUB.md").read_text(encoding="utf-8")
        self.assertIn("conda deactivate", usage)
        self.assertIn("ARCHITECTURE.md", usage)
        self.assertIn("INTERNALS.md", usage)
        self.assertIn("ManagedMonitoringStack", internals)
        self.assertIn("USAGE.md", internals)
        self.assertIn("INTERNALS.md", architecture)
        self.assertIn("docs/USAGE.md", agents)
        self.assertIn("docs/INTERNALS.md", agents)
        self.assertIn("host.docker.internal", docker)
        self.assertIn("docker compose pull", docker)
        self.assertIn("compose.dev.yaml", docker)
        self.assertIn("SBK_DASHBOARD_LAUNCHER_DIR", usage)
        self.assertIn("SBK_DASHBOARD_STOP_TIMEOUT", usage)
        self.assertIn("require Python 3.10 or newer", usage)
        self.assertIn("DOCKER_HUB.md", docker)
        self.assertIn("docker buildx imagetools inspect", docker_hub)
        self.assertIn("DOCKERHUB_USERNAME", docker_hub)
        self.assertIn("DOCKERHUB_TOKEN", docker_hub)
        self.assertIn("docker compose -f compose.yaml -f compose.dev.yaml build", docker_hub)

    def test_cross_platform_ci_uses_an_explicit_macos_runner(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        testing = (ROOT / "docs" / "TESTING.md").read_text(encoding="utf-8")
        self.assertIn("os: [macos-15, windows-2022]", workflow)
        self.assertNotIn("macos-15-intel", workflow)
        self.assertNotIn("macos-latest", workflow)
        self.assertIn("machine == 'arm64'", workflow)
        self.assertIn("python -m sbk_dashboard -v", workflow)
        self.assertIn("Apple Silicon `macos-15`", testing)


if __name__ == "__main__":
    unittest.main()
