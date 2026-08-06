import re
import unittest
from pathlib import Path

from sbk_dashboard.version import VERSION

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BASE = (
    "python:3.12.13-slim-trixie@"
    "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
)


class ContainerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cls.compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        cls.development_compose = (ROOT / "compose.dev.yaml").read_text(encoding="utf-8")
        cls.workflow = (ROOT / ".github/workflows/container.yml").read_text(encoding="utf-8")
        cls.ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        cls.properties = (
            ROOT / "src/sbk_dashboard/resources/monitoring-download.properties"
        ).read_text(encoding="utf-8")

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
        self.assertIn("HEALTHCHECK", self.dockerfile)
        self.assertIn("http://127.0.0.1:9721/api/health", self.dockerfile)
        self.assertIn("SBK_DASHBOARD_DEFAULT_TARGET_HOST=host.docker.internal", self.dockerfile)

    def test_compose_publishes_dashboard_ports_and_persists_state(self):
        self.assertIn('"9721:9721"', self.compose)
        self.assertIn('"3000:3000"', self.compose)
        self.assertNotRegex(self.compose, r'["\s]9090:9090')
        self.assertIn("sbk-dashboard-data:/var/lib/sbk-dashboard", self.compose)
        self.assertIn("host.docker.internal:host-gateway", self.compose)
        self.assertIn("no-new-privileges:true", self.compose)
        self.assertIn("cap_drop:", self.compose)
        self.assertIn("enable_ipv6: true", self.compose)
        self.assertIn("pull_policy: missing", self.compose)
        self.assertNotIn("build:", self.compose)
        self.assertIn(f"image: sbk-dashboard:{VERSION}", self.development_compose)
        self.assertIn("pull_policy: never", self.development_compose)
        self.assertIn("build:", self.development_compose)
        self.assertIn("VCS_REF: ${SBK_DASHBOARD_VCS_REF:-local}", self.development_compose)

    def test_image_native_versions_and_checksums_match_packaged_bootstrap(self):
        expected = {
            "PROMETHEUS_AMD64_SHA256": self._property(
                "prometheus.linux-x86_64.download.sha256"
            ),
            "PROMETHEUS_ARM64_SHA256": self._property(
                "prometheus.linux-arm64.download.sha256"
            ),
            "GRAFANA_AMD64_SHA256": self._property("grafana.linux-x86_64.download.sha256"),
            "GRAFANA_ARM64_SHA256": self._property("grafana.linux-arm64.download.sha256"),
        }
        for argument, checksum in expected.items():
            self.assertEqual(checksum, self._argument(argument), argument)
        self.assertIn(
            f"ARG PROMETHEUS_VERSION={self._url_version('prometheus.linux-x86_64.download.url', 'prometheus')}",
            self.dockerfile,
        )
        self.assertIn(
            f"ARG GRAFANA_VERSION={self._url_version('grafana.linux-x86_64.download.url', 'grafana')}",
            self.dockerfile,
        )
        grafana_builds = {
            self._grafana_build("grafana.linux-x86_64.download.url"),
            self._grafana_build("grafana.linux-arm64.download.url"),
        }
        self.assertEqual(1, len(grafana_builds))
        self.assertEqual(grafana_builds.pop(), self._argument("GRAFANA_BUILD"))
        self.assertEqual(
            self._property("download.max.bytes"), self._argument("NATIVE_DOWNLOAD_MAX_BYTES")
        )
        self.assertEqual(2, self.dockerfile.count('--max-filesize "${NATIVE_DOWNLOAD_MAX_BYTES}"'))
        self.assertIn("FROM native-download-base AS prometheus-tools", self.dockerfile)
        self.assertIn("FROM native-download-base AS grafana-tools", self.dockerfile)
        self.assertIn("id=sbk-dashboard-prometheus-downloads", self.dockerfile)
        self.assertIn("id=sbk-dashboard-grafana-downloads", self.dockerfile)
        self.assertIn("COPY --from=prometheus-tools", self.dockerfile)
        self.assertIn("COPY --from=grafana-tools", self.dockerfile)

    def test_build_context_excludes_generated_and_runtime_data(self):
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        for required in (".git", ".venv", ".coverage*", "build", "dist", "downloads", "runtime"):
            self.assertIn(required, ignored)

    def test_ci_runs_smoke_and_builds_both_linux_architectures(self):
        self.assertIn('- "compose.dev.yaml"', self.workflow)
        self.assertIn("Validate production and development Compose definitions", self.workflow)
        self.assertIn(
            "docker compose -f compose.yaml -f compose.dev.yaml config --quiet",
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

    def _argument(self, name):
        match = re.search(rf"^ARG {re.escape(name)}=([^\s]+)$", self.dockerfile, re.MULTILINE)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def _property(self, name):
        match = re.search(rf"^{re.escape(name)}=(.+)$", self.properties, re.MULTILINE)
        self.assertIsNotNone(match, name)
        return match.group(1).strip()

    def _url_version(self, name, tool):
        url = self._property(name)
        match = re.search(rf"/{re.escape(tool)}[-_/](?:release/)?v?(\d+\.\d+\.\d+)", url)
        if match is None:
            match = re.search(r"/v?(\d+\.\d+\.\d+)/", url)
        self.assertIsNotNone(match, url)
        return match.group(1)

    def _grafana_build(self, name):
        url = self._property(name)
        match = re.search(r"grafana_\d+\.\d+\.\d+_(\d+)_linux_", url)
        self.assertIsNotNone(match, url)
        return match.group(1)


if __name__ == "__main__":
    unittest.main()
