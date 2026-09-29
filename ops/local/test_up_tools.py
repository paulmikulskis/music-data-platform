"""Check startup guidance without provisioning a practice stack."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ("docker", "uv", "pnpm", "node", "tmux", "psql", "curl")


class UpToolsTests(unittest.TestCase):
    def check_missing_tools(self, *, curl_present=False):
        bash = shutil.which("bash")
        self.assertIsNotNone(bash)
        before = set(Path("/tmp").glob("mdp-local-*"))
        with tempfile.TemporaryDirectory(prefix="up-tools-") as directory:
            path = Path(directory)
            for name in ("bash", "env", "dirname", "cksum", "cut"):
                executable = shutil.which(name)
                self.assertIsNotNone(executable, name)
                (path / name).symlink_to(executable)
            if curl_present:
                stub = path / "curl"
                stub.write_text("#!/usr/bin/env bash\nexit 0\n")
                stub.chmod(0o755)
            result = subprocess.run(
                [bash, "ops/local/up.sh"],
                cwd=ROOT,
                env={"HOME": directory, "PATH": directory},
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(set(Path("/tmp").glob("mdp-local-*")) - before, set())
        expected = [tool for tool in TOOLS if not (curl_present and tool == "curl")]
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), len(expected) + 1, result.stderr)
        for tool, line in zip(expected, lines):
            with self.subTest(tool=tool):
                self.assertTrue(line.startswith(f"Local stack: missing tool: {tool}."))
                self.assertIn("Install with", line)
                self.assertIn("macOS", line)
                self.assertIn("Linux", line)
                self.assertRegex(line, r"brew install|npm install")
                self.assertRegex(line, r"apt-get install|curl -LsSf|npm install|nvm install")
        self.assertEqual(
            lines[-1], "Install the tools above, then rerun bash ops/local/up.sh."
        )
        self.assertEqual(result.stdout, "")
        if curl_present:
            self.assertNotIn("missing tool: curl", result.stderr)

    def test_all_missing_tools(self):
        self.check_missing_tools()

    def test_installed_curl_is_not_listed(self):
        self.check_missing_tools(curl_present=True)


if __name__ == "__main__":
    unittest.main()
