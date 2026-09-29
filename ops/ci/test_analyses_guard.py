"""Exercise the guard against actual tracked, untracked and ignored files."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from analyses_guard import offenders


class AnalysesGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git("init", "-q")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def write(self, name, content=""):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def bad(self):
        return [name for name, _ in offenders(self.root)]

    def test_all_data_and_secret_formats(self):
        names = [
            "x" + ext
            for ext in (".duckdb", ".parquet", ".feather", ".rds", ".RData", ".rda")
        ]
        names += [".Renviron", ".pgpass", "pgpass.conf"]
        for name in names:
            self.write("analyses/demo/topic/" + name)
        self.assertEqual(len(self.bad()), len(names))

    def test_tracked_ignored_is_still_checked(self):
        self.write("analyses/x.rds")
        self.git("add", "analyses/x.rds")
        self.write(".gitignore", "*.rds\n")
        self.write("analyses/ignored.rds")
        self.assertEqual(self.bad(), ["analyses/x.rds"])

    def test_source_and_other_folders_are_allowed(self):
        self.write("analyses/demo/topic/R/features.R", "x <- 1\n")
        self.write("elsewhere/data.rds")
        self.assertEqual(self.bad(), [])

    def test_template_is_guarded(self):
        self.write("r/mdpr/inst/templates/analysis/.Renviron")
        self.assertEqual(len(self.bad()), 1)

    def test_size_boundaries(self):
        self.write("analyses/ok.csv", "x" * 1_000_000)
        self.write("analyses/large.csv", "x" * 1_000_001)
        self.write("analyses/ok.txt", "x" * 5_000_000)
        self.write("analyses/large.txt", "x" * 5_000_001)
        self.assertEqual(self.bad(), ["analyses/large.csv", "analyses/large.txt"])

    def test_notebook_outputs_and_counts(self):
        for name, cell in {
            "clean": {"outputs": [], "execution_count": None},
            "output": {"outputs": [{"text": "data"}]},
            "count": {"outputs": [], "execution_count": 0},
        }.items():
            self.write(f"analyses/{name}.ipynb", json.dumps({"cells": [cell]}))
        self.assertEqual(self.bad(), ["analyses/count.ipynb", "analyses/output.ipynb"])

    def test_invalid_notebook_fails_closed(self):
        self.write("analyses/bad.ipynb", "{")
        self.assertEqual(self.bad(), ["analyses/bad.ipynb"])

    def test_deleted_tracked_file_is_ignored(self):
        path = self.write("analyses/old.rds")
        self.git("add", "analyses/old.rds")
        path.unlink()
        self.assertEqual(self.bad(), [])

    def test_symlink_refused(self):
        target = self.write("elsewhere/data.txt", "private")
        self.write("analyses/.gitkeep")
        (self.root / "analyses/link").symlink_to(target)
        self.assertEqual(self.bad(), ["analyses/link"])


if __name__ == "__main__":
    unittest.main()
