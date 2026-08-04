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

    def test_detailed_documents_exist_and_cross_link(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        usage = (ROOT / "docs/USAGE.md").read_text(encoding="utf-8")
        internals = (ROOT / "docs/INTERNALS.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("conda deactivate", usage)
        self.assertIn("ARCHITECTURE.md", usage)
        self.assertIn("INTERNALS.md", usage)
        self.assertIn("ManagedMonitoringStack", internals)
        self.assertIn("USAGE.md", internals)
        self.assertIn("INTERNALS.md", architecture)
        self.assertIn("docs/USAGE.md", agents)
        self.assertIn("docs/INTERNALS.md", agents)


if __name__ == "__main__":
    unittest.main()
