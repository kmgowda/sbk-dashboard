import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTest(unittest.TestCase):
    def test_readme_documents_environment_exit_and_deactivation(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("deactivate", readme)
        self.assertIn("conda deactivate", readme)
        self.assertIn("docs/USAGE.md", readme)
        self.assertIn("docs/INTERNALS.md", readme)
        self.assertIn("SBK_DASHBOARD_DEFAULT_TARGET_HOST", readme)
        self.assertIn("host.docker.internal", readme)

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
        self.assertIn("DOCKER_HUB.md", docker)
        self.assertIn("docker buildx imagetools inspect", docker_hub)
        self.assertIn("DOCKERHUB_USERNAME", docker_hub)
        self.assertIn("DOCKERHUB_TOKEN", docker_hub)
        self.assertIn("docker compose -f compose.yaml -f compose.dev.yaml build", docker_hub)

    def test_cross_platform_ci_uses_an_explicit_macos_runner(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        testing = (ROOT / "docs" / "TESTING.md").read_text(encoding="utf-8")
        self.assertIn("macos-15-intel", workflow)
        self.assertNotIn("macos-latest", workflow)
        self.assertIn("macos-15-intel", testing)


if __name__ == "__main__":
    unittest.main()
