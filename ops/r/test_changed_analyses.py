"""Exercise push-base selection against real, disposable git histories."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("changed_analyses.py").resolve()


class ChangedAnalysesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        for topic in ("first", "second"):
            folder = self.root / "analyses/fixture" / topic
            folder.mkdir(parents=True)
            (folder / "analysis.R").write_text("x <- 1\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "Initial fixture")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True)

    def selected(self, base):
        env = {**os.environ, "MDP_R_BASE": base}
        return subprocess.check_output(
            ["python3", str(SCRIPT)], cwd=self.root, env=env, text=True,
        ).splitlines()

    def test_unavailable_base_checks_all_analyses(self):
        for base in ("", "0" * 40, "f" * 40):
            with self.subTest(base=base):
                self.assertEqual(self.selected(base), [
                    "analyses/fixture/first", "analyses/fixture/second",
                ])

    def test_valid_base_includes_changed_and_untracked_analyses(self):
        (self.root / "analyses/fixture/first/analysis.R").write_text("x <- 2\n")
        folder = self.root / "analyses/fixture/new topic"
        folder.mkdir()
        (folder / "analysis.R").write_text("x <- 3\n")
        self.assertEqual(self.selected(self.base), [
            "analyses/fixture/first", "analyses/fixture/new topic",
        ])

    def test_deleted_analysis_is_not_selected(self):
        self.git("rm", "-r", "analyses/fixture/first")
        self.assertEqual(self.selected(self.base), [])


if __name__ == "__main__":
    unittest.main()
