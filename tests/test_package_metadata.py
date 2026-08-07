"""Release metadata and plugin-manifest consistency tests."""

import json
import re
import sys
import unittest
from importlib import metadata
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "safe-shell"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

import safe_shell_mcp  # noqa: E402


class PackageMetadataTests(unittest.TestCase):
    def test_pyproject_uses_package_version_as_single_python_source(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        project = text.split("[project]", 1)[1].split(
            "[project.optional-dependencies]", 1
        )[0]
        self.assertNotRegex(project, re.compile(r"^version\s*=", re.MULTILINE))
        self.assertIn('dynamic = ["version"]', project)
        self.assertIn(
            'version = { attr = "safe_shell_mcp.__version__" }', text
        )

    def test_plugin_manifest_matches_package_and_resolves_resources(self):
        manifest_path = REPO_ROOT / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], safe_shell_mcp.__version__)
        self.assertEqual(manifest["name"], "safe-shell-skill")

        skills = (REPO_ROOT / manifest["skills"]).resolve()
        mcp_servers = (REPO_ROOT / manifest["mcpServers"]).resolve()
        self.assertTrue(skills.is_dir())
        self.assertTrue(mcp_servers.is_file())

    def test_marketplace_entry_targets_this_plugin(self):
        marketplace = json.loads(
            (
                REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
            ).read_text(encoding="utf-8")
        )
        plugins = marketplace["plugins"]
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["name"], "safe-shell-skill")
        source = plugins[0]["source"]
        self.assertEqual(source["source"], "url")
        self.assertTrue(source["url"].endswith("/safe-shell-skill.git"))
        self.assertEqual(source["ref"], "master")

    def test_installed_distribution_metadata_and_entry_points(self):
        try:
            distribution = metadata.distribution("safe-shell-mcp")
        except metadata.PackageNotFoundError:
            self.skipTest("distribution is not installed")
        self.assertEqual(distribution.version, safe_shell_mcp.__version__)
        scripts = {
            entry.name: entry.value
            for entry in distribution.entry_points
            if entry.group == "console_scripts"
        }
        self.assertEqual(scripts["safe-shell"], "safe_shell_mcp.cli:main")
        self.assertEqual(
            scripts["safe-shell-mcp"], "safe_shell_mcp.server:main"
        )


if __name__ == "__main__":
    unittest.main()
