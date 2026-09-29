"""Evidence caps over disposable Git trees; no services or credentials needed."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from evidence_size import (
    FILE_LIMIT,
    RANGE_LIMIT,
    allowances,
    inspect,
    tree,
    working_tree,
)


class EvidenceSizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root).decode().strip()

    def file(self, name, size):
        path = self.root / "ops/evidence" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * size)
        return path

    def snapshot(self):
        self.git("add", ".")
        return self.git("write-tree")

    def test_small_and_exact_limit_pass(self):
        before = tree(self.root, self.snapshot())
        self.file("small.png", 12)
        self.file("limit.png", FILE_LIMIT)
        failures, added, _ = inspect(before, tree(self.root, self.snapshot()), {})
        self.assertEqual(failures, [])
        self.assertEqual(added, FILE_LIMIT + 12)

    def test_two_mb_png_fails_and_names_file_and_next_step(self):
        self.file("large image.png", 2_000_000)
        failures, _, _ = inspect({}, tree(self.root, self.snapshot()), {})
        self.assertEqual(len(failures), 1)
        self.assertIn("ops/evidence/large image.png", failures[0])
        self.assertIn("scratch", failures[0])

    def test_changed_file_crossing_limit_fails(self):
        self.file("image.png", 20)
        before = tree(self.root, self.snapshot())
        self.file("image.png", FILE_LIMIT + 1)
        self.assertTrue(inspect(before, tree(self.root, self.snapshot()), {})[0])

    def test_same_size_replacements_count_full_versions(self):
        paths = [self.file(f"{i}.png", FILE_LIMIT) for i in range(6)]
        before = tree(self.root, self.snapshot())
        for path in paths:
            path.write_bytes(b"x" * FILE_LIMIT)
        failures, added, _ = inspect(before, tree(self.root, self.snapshot()), {})
        self.assertTrue(failures)
        self.assertEqual(added, 6_000_000)

    def test_range_keeps_deleted_versions_and_does_not_double_count_merges(self):
        empty = tree(self.root, self.snapshot())
        paths = [self.file(f"{i}.png", FILE_LIMIT) for i in range(6)]
        middle = tree(self.root, self.snapshot())
        seen = set()
        growth = {}
        inspect(empty, middle, {}, seen=seen, growth=growth)
        for path in paths:
            path.unlink()
        final = tree(self.root, self.snapshot())
        inspect(middle, final, {}, seen=seen, growth=growth)
        failures, added, _ = inspect(empty, middle, {}, seen=seen, growth=growth)
        self.assertTrue(failures)
        self.assertEqual(added, 6_000_000)

    def test_range_exact_limit_passes_and_one_more_byte_fails(self):
        for i in range(5):
            self.file(f"{i}.png", FILE_LIMIT)
        self.assertEqual(
            inspect({}, tree(self.root, self.snapshot()), {})[:2], ([], RANGE_LIMIT)
        )
        self.file("extra.txt", 1)
        failures, added, _ = inspect({}, tree(self.root, self.snapshot()), {})
        self.assertEqual(added, RANGE_LIMIT + 1)
        self.assertIn("range limit", failures[0])
        self.assertTrue(any("extra.txt" in failure for failure in failures))

    def test_deletions_do_not_cancel_growth(self):
        old = self.file("old.png", 8_000_000)
        before = tree(self.root, self.snapshot())
        old.unlink()
        for i in range(6):
            self.file(f"{i}.png", FILE_LIMIT)
        self.assertTrue(inspect(before, tree(self.root, self.snapshot()), {})[0])

    def test_existing_large_file_is_unchanged(self):
        self.file("old.png", 2_000_000)
        before = tree(self.root, self.snapshot())
        self.assertEqual(inspect(before, before, {})[0], [])

    def test_allowlist_accepts_same_size_and_shrinking_but_not_growth(self):
        path = self.file("old.png", 2_000_000)
        before = tree(self.root, self.snapshot())
        limits = {"ops/evidence/old.png": 2_000_000}
        for size, fails in [(2_000_000, False), (1_500_000, False), (2_000_001, True)]:
            path.write_bytes(b"x" * size)
            with self.subTest(size=size):
                failures, _, kept = inspect(
                    before, tree(self.root, self.snapshot()), limits
                )
                self.assertEqual(bool(failures), fails)
                self.assertEqual(bool(kept), not fails)

    def test_allowlist_does_not_restore_a_shrunk_file(self):
        self.file("old.png", 1_500_000)
        before = tree(self.root, self.snapshot())
        self.file("old.png", 1_600_000)
        self.assertTrue(
            inspect(
                before,
                tree(self.root, self.snapshot()),
                {"ops/evidence/old.png": 2_000_000},
            )[0]
        )

    def test_allowlist_does_not_exempt_new_or_renamed_file(self):
        self.file("new.png", 2_000_000)
        self.assertTrue(
            inspect(
                {},
                tree(self.root, self.snapshot()),
                {"ops/evidence/new.png": 2_000_000},
            )[0]
        )

    def test_allowlist_sizes_are_checked_against_pinned_tree(self):
        self.file("old.png", 2_000_000)
        ref = self.snapshot()
        path = self.root / "allowlist.json"
        for size, valid in [(2_000_000, True), (3_000_000, False), (True, False)]:
            path.write_text(
                json.dumps({"baseline": ref, "files": {"ops/evidence/old.png": size}})
            )
            if valid:
                self.assertEqual(
                    allowances(self.root, path)["ops/evidence/old.png"], size
                )
            else:
                with self.assertRaises(ValueError):
                    allowances(self.root, path)

    def test_local_check_includes_untracked_and_unstaged_files(self):
        self.file("tracked.png", 100)
        before = tree(self.root, self.snapshot())
        self.file("tracked.png", FILE_LIMIT + 1)
        self.file("untracked.png", FILE_LIMIT + 1)
        self.assertEqual(len(inspect(before, working_tree(self.root), {})[0]), 2)

    def test_local_check_ignores_ignored_files_but_checks_tracked_logs(self):
        self.file("tracked.log", 12)
        before = tree(self.root, self.snapshot())
        (self.root / ".gitignore").write_text("*.log\n")
        self.file("tracked.log", FILE_LIMIT + 1)
        self.file("ignored.log", FILE_LIMIT + 1)
        failures = inspect(before, working_tree(self.root), {})[0]
        self.assertEqual(len(failures), 1)
        self.assertIn("tracked.log", failures[0])

    def test_files_outside_evidence_do_not_count(self):
        (self.root / "outside.bin").write_bytes(b"x" * 6_000_000)
        self.assertEqual(inspect({}, tree(self.root, self.snapshot()), {})[:2], ([], 0))

    def test_missing_ref_fails_closed(self):
        with self.assertRaises(subprocess.CalledProcessError):
            tree(self.root, "missing")


if __name__ == "__main__":
    unittest.main()
