# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import json
import unittest
from pathlib import Path

from sbk_dashboard.version import VERSION

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BASE = (
    "python:3.12.13-slim-trixie@"
    "sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36"
)


class ContainerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cls.compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        cls.development_compose = (ROOT / "compose.dev.yaml").read_text(encoding="utf-8")
        cls.resources_compose = (ROOT / "compose.resources.yaml").read_text(encoding="utf-8")
        cls.workflow = (ROOT / ".github/workflows/container.yml").read_text(encoding="utf-8")
        cls.ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        cls.native_manifest = json.loads(
            (ROOT / "src/sbk_dashboard/resources/native-artifacts.json").read_text(encoding="utf-8")
        )
        cls.container_build_requirements = (
            ROOT / "requirements/container-build.txt"
        ).read_text(encoding="utf-8")
        cls.container_runtime_requirements = (
            ROOT / "requirements/container-runtime.txt"
        ).read_text(encoding="utf-8")
        cls.trivy_ignore = (ROOT / ".trivyignore.yaml").read_text(encoding="utf-8")

    def test_image_runs_as_non_root_with_persistent_data_and_two_public_ports(self):
        self.assertIn(f"ARG PYTHON_BASE={PYTHON_BASE}", self.dockerfile)
        self.assertEqual(3, self.dockerfile.count("FROM ${PYTHON_BASE}"))
        self.assertNotIn("slim-bookworm", self.dockerfile)
        self.assertIn(f"ARG APPLICATION_VERSION={VERSION}", self.dockerfile)
        self.assertIn(
            f"image: ${{SBK_DASHBOARD_IMAGE:-kmgowda/sbk-dashboard:{VERSION}}}",
            self.compose,
        )
        self.assertIn("USER 10001:10001", self.dockerfile)
        self.assertIn('VOLUME ["/var/lib/sbk-dashboard"]', self.dockerfile)
        self.assertIn("EXPOSE 9721 3000", self.dockerfile)
        self.assertNotIn("EXPOSE 9090", self.dockerfile)
        self.assertIn('ENTRYPOINT ["/usr/bin/tini", "--", "sbk-dashboard"]', self.dockerfile)
        self.assertIn("STOPSIGNAL SIGTERM", self.dockerfile)
        self.assertIn("HEALTHCHECK", self.dockerfile)
        self.assertIn("http://127.0.0.1:9721/api/health", self.dockerfile)
        self.assertIn("SBK_DASHBOARD_DEFAULT_TARGET_HOST=host.docker.internal", self.dockerfile)

    def test_compose_publishes_dashboard_ports_and_persists_state(self):
        self.assertIn('"${SBK_DASHBOARD_PUBLISH_HOST:-127.0.0.1}:9721:9721"', self.compose)
        self.assertIn('"${SBK_DASHBOARD_PUBLISH_HOST:-127.0.0.1}:3000:3000"', self.compose)
        self.assertNotRegex(self.compose, r'["\s]9090:9090')
        self.assertIn("sbk-dashboard-data:/var/lib/sbk-dashboard", self.compose)
        self.assertIn("host.docker.internal:host-gateway", self.compose)
        self.assertIn("no-new-privileges:true", self.compose)
        self.assertIn("cap_drop:", self.compose)
        self.assertIn("read_only: true", self.compose)
        self.assertIn("/tmp:size=64m,mode=1777", self.compose)
        self.assertIn("enable_ipv6: true", self.compose)
        self.assertIn("pull_policy: missing", self.compose)
        self.assertIn("stop_grace_period: 30s", self.compose)
        self.assertNotIn("build:", self.compose)
        self.assertIn(f"image: sbk-dashboard:{VERSION}", self.development_compose)
        self.assertIn("pull_policy: never", self.development_compose)
        self.assertIn("build:", self.development_compose)
        self.assertIn("VCS_REF: ${SBK_DASHBOARD_VCS_REF:-local}", self.development_compose)
        self.assertIn("SBK_DASHBOARD_MEMORY_LIMIT:-4g", self.resources_compose)
        self.assertIn("SBK_DASHBOARD_CPU_LIMIT:-2.0", self.resources_compose)
        self.assertIn("SBK_DASHBOARD_PIDS_LIMIT:-512", self.resources_compose)

    def test_container_python_dependencies_and_build_tools_are_hash_pinned(self):
        self.assertIn("setuptools==80.9.0", self.container_build_requirements)
        self.assertIn("packaging==26.3", self.container_build_requirements)
        self.assertIn("wheel==0.48.0", self.container_build_requirements)
        self.assertIn("psutil==7.2.2", self.container_runtime_requirements)
        self.assertEqual(2, self.container_runtime_requirements.count("--hash=sha256:"))
        self.assertIn("--require-hashes", self.dockerfile)
        self.assertIn("apt-get upgrade --yes --no-install-recommends", self.dockerfile)
        self.assertIn("--no-build-isolation --no-deps", self.dockerfile)
        self.assertIn('test "${installed_version}" = "${APPLICATION_VERSION}"', self.dockerfile)
        self.assertNotIn("--chown=10001:10001 /opt/prometheus", self.dockerfile)
        self.assertNotIn("--chown=10001:10001 /opt/grafana", self.dockerfile)

    def test_image_native_versions_and_checksums_match_packaged_bootstrap(self):
        artifacts = self.native_manifest["artifacts"]
        self.assertEqual({"prometheus", "grafana"}, set(artifacts))
        for tool in artifacts.values():
            self.assertEqual(
                {"linux-x86_64", "linux-arm64", "macos-x86_64", "macos-arm64",
                 "windows-x86_64", "windows-arm64"},
                set(tool),
            )
        self.assertNotIn("PROMETHEUS_VERSION", self.dockerfile)
        self.assertNotIn("GRAFANA_VERSION", self.dockerfile)
        self.assertNotIn("_SHA256", self.dockerfile)
        self.assertIn("COPY src/sbk_dashboard/resources/native-artifacts.json", self.dockerfile)
        self.assertIn("COPY scripts/resolve_native_artifact.py", self.dockerfile)
        self.assertEqual(7, self.dockerfile.count("/usr/local/bin/resolve-native-artifact"))
        self.assertEqual(2, self.dockerfile.count('--max-filesize "${max_download_bytes}"'))
        self.assertIn("FROM native-download-base AS prometheus-tools", self.dockerfile)
        self.assertIn("FROM native-download-base AS grafana-tools", self.dockerfile)
        self.assertIn("id=sbk-dashboard-prometheus-downloads", self.dockerfile)
        self.assertIn("id=sbk-dashboard-grafana-downloads", self.dockerfile)
        self.assertIn("COPY --from=prometheus-tools", self.dockerfile)
        self.assertIn("COPY --from=grafana-tools", self.dockerfile)
        self.assertEqual(2, self.dockerfile.count("python /usr/local/bin/docker-safe-extract"))

    def test_build_context_excludes_generated_and_runtime_data(self):
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        for required in (".git", ".venv", ".coverage*", "build", "dist", "downloads", "runtime"):
            self.assertIn(required, ignored)

    def test_container_smoke_uses_ephemeral_host_ports_and_bounded_exporters(self):
        smoke = (ROOT / "tests/container_smoke.py").read_text(encoding="utf-8")
        self.assertIn('listener.bind(("127.0.0.1", 0))', smoke)
        self.assertIn('arguments.add_argument("--dashboard-port", type=int)', smoke)
        self.assertIn('arguments.add_argument("--grafana-port", type=int)', smoke)
        self.assertIn("BaseHTTPRequestHandler, HTTPServer", smoke)
        self.assertNotIn("ThreadingHTTPServer", smoke)
        self.assertIn('"docker", "kill", "--signal", "KILL"', smoke)
        self.assertIn('"--read-only"', smoke)
        self.assertIn("Native installations are not immutable root-owned content", smoke)

    def test_ci_runs_smoke_and_builds_both_linux_architectures(self):
        self.assertIn('- "compose.dev.yaml"', self.workflow)
        self.assertIn("Validate production and development Compose definitions", self.workflow)
        self.assertIn(
            "docker compose -f compose.yaml -f compose.dev.yaml config --quiet",
            self.workflow,
        )
        self.assertIn(
            "docker compose -f compose.yaml -f compose.resources.yaml config --quiet",
            self.workflow,
        )
        self.assertIn("python tests/compose_contract.py", self.workflow)
        self.assertIn("python tests/container_smoke.py --image sbk-dashboard:ci", self.workflow)
        self.assertIn("platforms: linux/amd64", self.workflow)
        self.assertIn("platforms: linux/arm64", self.workflow)
        self.assertIn("platforms: linux/amd64,linux/arm64", self.workflow)
        self.assertIn("images: kmgowda/sbk-dashboard", self.workflow)
        self.assertIn("Log in to Docker Hub", self.workflow)
        self.assertIn("secrets.DOCKERHUB_USERNAME", self.workflow)
        self.assertIn("secrets.DOCKERHUB_TOKEN", self.workflow)
        self.assertIn("aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25", self.workflow)
        self.assertIn("severity: CRITICAL,HIGH", self.workflow)
        self.assertIn("trivyignores: .trivyignore.yaml", self.workflow)
        self.assertEqual(18, self.trivy_ignore.count("  - id:"))
        self.assertEqual(18, self.trivy_ignore.count("expired_at: 2026-09-30"))
        self.assertEqual(18, self.trivy_ignore.count("statement:"))
        self.assertIn("sigstore/cosign-installer@6f9f17788090df1f26f669e9d70d6ae9567deba6", self.workflow)
        self.assertIn("id-token: write", self.workflow)
        self.assertIn("cosign sign --yes", self.workflow)
        self.assertNotIn("ghcr.io", self.workflow)
        self.assertIn("Verify release tag matches the package version", self.workflow)
        self.assertEqual(2, self.workflow.count("runs-on: ubuntu-24.04"))
        self.assertNotIn("ubuntu-latest", self.workflow)
        self.assertIn("runs-on: ubuntu-24.04", self.ci_workflow)
        self.assertNotIn("runs-on: ubuntu-latest", self.ci_workflow)
        self.assertEqual(2, self.workflow.count("cache-to: type=gha,mode=max,scope="))
        self.assertEqual(2, self.workflow.count("ignore-error=true"))
        self.assertEqual(
            3,
            self.workflow.count("APPLICATION_VERSION=${{ steps.version.outputs.value }}"),
        )
        self.assertNotRegex(self.workflow + self.ci_workflow, r"uses: [^\s]+@v\d+(?:\s|$)")
        self.assertIn("windows-2022", self.ci_workflow)
        self.assertNotIn("windows-latest", self.ci_workflow)

if __name__ == "__main__":
    unittest.main()
