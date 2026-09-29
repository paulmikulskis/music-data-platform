"""Selection must include edits without turning a docs change into a full suite."""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ready


class ReadyTests(unittest.TestCase):
    def test_dbt_lint_gets_a_profile_without_replacing_local_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "dbt/profiles/profiles.yml"
            profile.parent.mkdir(parents=True)
            profile.with_name("profiles.example.yml").write_text("example settings")
            for expected in ("example settings", "local settings"):

                def command(args, expected=expected, **kwargs):
                    self.assertEqual(profile.read_text(), expected)
                    return "checked"

                with (
                    self.subTest(expected=expected),
                    patch.object(ready, "ROOT", root),
                    patch.object(ready, "run", side_effect=command),
                    patch.object(ready, "dbt_build", return_value="built") as build,
                ):
                    self.assertEqual(
                        ready.dbt_checks(["dbt/models/demo.sql"]), "checkedcheckedbuilt"
                    )
                    build.assert_called_once_with(["dbt/models/demo.sql"])
                profile.write_text("local settings")

    def test_profile_setup_failure_stops_before_lint_with_a_next_step(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(ready, "ROOT", Path(directory)),
            patch.object(ready, "run") as command,
            self.assertRaisesRegex(RuntimeError, "Run cp -n .*then bash ops/ready.sh"),
        ):
            try:
                ready.dbt_checks(["dbt/models/demo.sql"])
            finally:
                command.assert_not_called()

    def test_control_setup_uses_the_lockfile_in_this_checkout(self):
        with patch.object(ready, "run") as command:
            ready.prepare_control_dependencies()
        command.assert_called_once_with(
            ["pnpm", "--dir", "control", "install", "--frozen-lockfile"], cwd=ready.ROOT
        )

    def test_control_setup_failure_stops_before_generators_with_a_next_step(self):
        for path in ("dbt/models/demo.sql", "control/packages/contracts/src/demo.ts"):
            for error in (FileNotFoundError("pnpm"), RuntimeError("lockfile mismatch")):
                with (
                    self.subTest(path=path, error=error),
                    patch.object(ready, "run", side_effect=error) as command,
                    self.assertRaisesRegex(
                        RuntimeError, "Run pnpm .*then bash ops/ready.sh"
                    ),
                ):
                    try:
                        ready.drift([path])
                    finally:
                        self.assertEqual(command.call_count, 1)
                        self.assertIn("--frozen-lockfile", command.call_args.args[0])

    def test_generator_inputs_select_drift_first(self):
        for path in (
            "ops/ci/generate_health_policy.py",
            "functions/src/mdp_functions/sources/demo/function.py",
            "ops/showcase/source_registry.py",
            "dbt/models/example.sql",
            "dbt/seeds/rights_registry.csv",
            "dbt/seeds/rights_registry.csv",
            "ops/showcase/lineage/peek.json",
            "ops/showcase/queries.json",
            "control/apps/showcase/lib/lineage.generated.json",
        ):
            with (
                self.subTest(path=path),
                patch.object(ready, "run", return_value="base"),
                patch.object(ready, "changed_paths", return_value=[path]),
                patch("sys.argv", ["ready", "--list"]),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(ready.main(), 0)
            selected = [
                line
                for line in output.getvalue().splitlines()
                if line.startswith("RUN")
            ]
            self.assertEqual(selected[0], "RUN generated drift")
        self.assertFalse(ready.lineage_changed(["dbt/CLAUDE.md"]))
        self.assertFalse(ready.source_registry_changed(["docs/guide.md"]))

    def test_drift_failure_stops_later_checks(self):
        for fail in (False, True):
            events = []

            def drift(paths, events=events, fail=fail):
                events.append("generators")
                if fail:
                    raise RuntimeError("Stale output. Run the generator.")

            with (
                self.subTest(fail=fail),
                patch.object(ready, "run", return_value="base"),
                patch.object(ready, "changed_paths", return_value=["ops/ready.py"]),
                patch.object(ready, "drift", side_effect=drift),
                patch.object(
                    ready,
                    "_pytest_checks",
                    side_effect=lambda *a, events=events, **kw: events.append("tests"),
                ),
                patch("sys.argv", ["ready"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(ready.main(), int(fail))
            self.assertEqual(
                events, ["generators"] if fail else ["generators", "tests"]
            )

    def test_drift_parses_the_copy_before_checking_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "dbt/profiles/profiles.example.yml"
            profile.parent.mkdir(parents=True)
            profile.write_text("fixture profile")
            for folder in ("control/apps", "control/packages"):
                (root / folder).mkdir(parents=True)
            calls = []

            def command(args, **kwargs):
                if args[:1] == ["pnpm"] and "install" in args:
                    self.assertEqual(kwargs["cwd"], root)
                    calls.append(args)
                    return ""
                if args[:2] == ["git", "ls-files"]:
                    return "dbt/profiles/profiles.example.yml\0"
                calls.append(args)
                copy = kwargs["cwd"]
                self.assertNotEqual(copy, root)
                self.assertEqual(
                    kwargs["env"]["PYTHONPATH"], str(copy / "functions/src")
                )
                if "parse" in args:
                    self.assertEqual(
                        (copy / "dbt/profiles/profiles.yml").read_text(),
                        "fixture profile",
                    )
                    (copy / "dbt/target").mkdir()
                    (copy / "dbt/target/manifest.json").write_text("fresh")
                if "ops/showcase/lineage/generate.py" in args:
                    self.assertEqual(
                        (copy / "dbt/target/manifest.json").read_text(), "fresh"
                    )
                return ""

            with (
                patch.object(ready, "ROOT", root),
                patch.object(ready, "run", side_effect=command),
            ):
                ready.drift(["functions/src/mdp_functions/sources/demo/function.py"])
            for script in (
                "ops/ci/generate_health_policy.py",
                "ops/showcase/source_registry.py",
                "ops/showcase/lineage/generate.py",
            ):
                self.assertTrue(
                    any(script in args and "--check" in args for args in calls)
                )
            self.assertIn("install", calls[0])
            self.assertFalse((root / "dbt/profiles/profiles.yml").exists())
            self.assertFalse((root / "dbt/target").exists())

    def test_duckdb_build_selects_descendants_and_inputs_in_a_private_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for file in ("dbt/models/example.sql", "dbt/profiles/profiles.example.yml"):
                path = root / file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture")
            for changed in (
                ["dbt/models/example.sql"],
                ["dbt/macros/shared.sql"],
                ["dbt/models/deleted.sql"],
                ["dbt/models/contract.yml"],
                ["dbt/seeds/reference.csv"],
            ):
                with (
                    self.subTest(changed=changed),
                    patch.object(ready, "ROOT", root),
                    patch.object(ready, "run", return_value="built") as command,
                ):
                    self.assertEqual(ready.dbt_build(changed), "built")
                args = command.call_args.args[0]
                env = command.call_args.kwargs["env"]
                self.assertEqual(args[args.index("--target") + 1], "ci")
                self.assertEqual(env["DBT_MDP_SCOPE"], "tenant:fixture")
                self.assertNotEqual(env["MDP_CI_DB"], "/tmp/mdp-ci.duckdb")
                self.assertFalse(Path(env["MDP_CI_DB"]).parent.exists())
                self.assertEqual(
                    "--select" in args, changed == ["dbt/models/example.sql"]
                )
                if "--select" in args:
                    self.assertEqual(args[-1], "@path:models/example.sql")

    def test_ci_prints_the_gate_without_running_checks(self):
        with (
            patch.object(ready, "run", return_value="topic") as command,
            patch("sys.argv", ["ready", "--ci"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(ready.main(), 0)
        command.assert_called_once_with(["git", "branch", "--show-current"])
        self.assertIn("ops/merge-gate.sh topic", output.getvalue())
        self.assertIn(
            "all five CI workflows and showcase-artifacts-postgres", output.getvalue()
        )

    def test_dbt_changes_run_review_after_lint_and_propagate_failure(self):
        for path in (
            "dbt/models/marts/global/mart_search_index.sql",
            "dbt/macros/mdp_annotate.sql",
            "dbt/seeds/rights_registry.csv",
            "ops/ci/review_gate.py",
        ):
            for fail in (False, True):
                commands = []

                def command(args, *, commands=commands, fail=fail, **kwargs):
                    commands.append(args)
                    if fail and args == ["python3", "ops/ci/review_gate.py"]:
                        raise RuntimeError("missing writer; use mdp_annotate")
                    return "base"

                with (
                    self.subTest(path=path, fail=fail),
                    patch.object(ready, "run", side_effect=command),
                    patch.object(ready, "changed_paths", return_value=[path]),
                    patch.object(ready, "drift"),
                    patch.object(ready, "prepare_dbt_profile"),
                    patch.object(ready, "_pytest_checks"),
                    patch("sys.argv", ["ready"]),
                    contextlib.redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(ready.main(), int(fail))
                self.assertLess(
                    commands.index(["bash", "ops/ci/lint-dbt.sh", "ci"]),
                    commands.index(["python3", "ops/ci/review_gate.py"]),
                )
                self.assertIn("review gate", output.getvalue())

    def test_backtest_edits_select_the_replay_and_metrics_tests(self):
        with (
            patch.object(ready, "run", return_value="base"),
            patch.object(
                ready, "changed_paths", return_value=["ops/backtest/labels.sql"]
            ),
            patch("sys.argv", ["ready", "--list"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(ready.main(), 0)
        self.assertIn("RUN backtest tests", output.getvalue())
        self.assertNotIn("RUN backtest lint", output.getvalue())
        self.assertNotIn("RUN functions tests", output.getvalue())
        self.assertNotIn("CI-only paths", output.getvalue())

    def test_database_checks_ignore_a_sourced_stack(self):
        with patch.dict(
            os.environ,
            {
                "MDP_CONTROL_RT_URL": "local-stack",
                "MDP_SERVICE_URL": "local-service",
                "MDP_FIXTURE": "true",
                "PATH": "tools",
            },
            clear=True,
        ):
            self.assertEqual(ready.test_environment(), {"PATH": "tools"})

    def test_disposable_dbt_ignores_sourced_scope_and_output_paths(self):
        with patch.dict(
            os.environ,
            {
                "MDP_CI_DB": "existing.duckdb",
                "DBT_TARGET_PATH": "existing-target",
                "DBT_LOG_PATH": "existing-logs",
                "DBT_MDP_SCOPE": "tenant:existing",
                "PATH": "tools",
            },
            clear=True,
        ):
            self.assertEqual(ready.dbt_environment(), {"PATH": "tools"})

    def test_git_includes_commits_index_worktree_deletions_and_new_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", directory, *args], text=True
                ).strip()

            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            git("config", "commit.gpgsign", "false")
            for name in ("committed.md", "staged.md", "dirty.md", "deleted.md"):
                (root / name).write_text("before\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (root / "committed.md").write_text("after\n")
            git("add", "committed.md")
            git("commit", "-qm", "edit")
            (root / "staged.md").write_text("after\n")
            git("add", "staged.md")
            (root / "dirty.md").write_text("after\n")
            (root / "deleted.md").unlink()
            (root / "new.md").write_text("new\n")
            original = ready.run
            with patch.object(
                ready, "run", side_effect=lambda args: original(args, cwd=root)
            ):
                self.assertEqual(
                    ready.changed_paths(base),
                    ["committed.md", "deleted.md", "dirty.md", "new.md", "staged.md"],
                )

    def test_docs_select_links_and_catalog_without_full_suites(self):
        with (
            patch.object(ready, "run", return_value="base"),
            patch.object(
                ready,
                "changed_paths",
                return_value=[
                    "docs/guide.md",
                    "dbt/README.md",
                    "functions/README.md",
                    "docs/sources/README.md",
                    "control/packages/data-sdk/README.md",
                    "ops/ci/lint-dbt.md",
                    "ops/ready-guide.md",
                ],
            ),
            patch("sys.argv", ["ready", "--list"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(ready.main(), 0)
        self.assertEqual(
            [line for line in output.getvalue().splitlines() if line.startswith("RUN")],
            [
                "RUN sandbox boundary",
                "RUN error catalog",
                "RUN glossary order",
                "RUN docs links",
            ],
        )

    def test_local_tool_check_routing(self):
        for path in (
            "ops/local/up.sh",
            "ops/local/common.sh",
            "ops/local/test_up_tools.py",
            "ops/local/down.sh",
        ):
            with (
                self.subTest(path=path),
                patch.object(ready, "run", return_value="base"),
                patch.object(ready, "changed_paths", return_value=[path]),
                patch("sys.argv", ["ready", "--list"]),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(ready.main(), 0)
                selected = path != "ops/local/down.sh"
                self.assertEqual("RUN local tool check\n" in output.getvalue(), selected)
                self.assertEqual("CI-only paths" not in output.getvalue(), selected)

    def test_r_routing_with_and_without_docker(self):
        for path in (
            "r/mdpr/R/connect.R",
            "analyses/demo/topic/R/features.R",
            "ops/r/check.sh",
            "r/mdpr/inst/templates/analysis/README.md",
        ):
            for docker in (True, False):
                with (
                    self.subTest(path=path, docker=docker),
                    patch.object(ready, "run", return_value="base"),
                    patch.object(ready, "changed_paths", return_value=[path]),
                    patch.object(ready, "docker_running", return_value=docker),
                    patch("sys.argv", ["ready", "--list"]),
                    contextlib.redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(ready.main(), 0)
                    text = output.getvalue()
                    self.assertIn(
                        "RUN R checks (Docker)"
                        if docker
                        else "Docker not running; r-ci runs it",
                        text,
                    )
                    self.assertEqual(
                        "RUN analyses guard\n" in text,
                        path.startswith(("analyses/", "r/mdpr/inst/templates/")),
                    )
                    self.assertNotIn("CI-only paths", text)

    def test_failed_check_sets_nonzero_exit(self):
        with (
            patch.object(ready, "run", return_value="base"),
            patch.object(ready, "changed_paths", return_value=["docs/guide.md"]),
            patch.object(ready, "links", side_effect=RuntimeError("missing link")),
            patch("sys.argv", ["ready"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(ready.main(), 1)
        self.assertIn("FAIL docs links", output.getvalue())

    def test_reverse_imports_include_indirect_and_relative_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "functions/src/mdp_functions/core.py": "VALUE = 1",
                "functions/src/mdp_functions/helper.py": "from .core import VALUE",
                "functions/tests/helper_fixture.py": "from mdp_functions.helper import VALUE",
                "functions/tests/test_helper.py": "from helper_fixture import VALUE",
                "functions/tests/test_other.py": "import json",
            }
            for name, text in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            with patch.object(ready, "ROOT", root):
                self.assertEqual(
                    ready.function_tests(["functions/src/mdp_functions/core.py"]),
                    ["functions/tests/test_helper.py"],
                )
                self.assertEqual(len(ready.function_tests(["functions/uv.lock"])), 2)
                self.assertEqual(
                    len(
                        ready.function_tests(["functions/src/mdp_functions/unknown.py"])
                    ),
                    2,
                )

    def test_registry_key_selects_its_test_without_unrelated_database_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "functions/src/mdp_functions/sources/demo/function.py"
            source.parent.mkdir(parents=True)
            source.write_text("def collect(): pass")
            test = root / "functions/tests/test_mixed.py"
            test.parent.mkdir(parents=True)
            test.write_text(
                'def test_demo():\n    discover()["demo"]\n'
                'def test_other(databases):\n    discover()["other"]\n'
            )
            with patch.object(ready, "ROOT", root):
                self.assertEqual(
                    ready.function_tests([str(source.relative_to(root))]),
                    ["functions/tests/test_mixed.py::test_demo"],
                )
                test.write_text('KEY = "demo"\n' + test.read_text())
                self.assertEqual(
                    ready.function_tests([str(source.relative_to(root))]),
                    ["functions/tests/test_mixed.py"],
                )

    def test_link_errors_are_real_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "doc.md").write_text(
                "# A heading\n[ok](#a-heading)\n[external](https://example.invalid)\n"
            )
            with patch.object(ready, "ROOT", root):
                ready.links(["doc.md"])
                (root / "doc.md").write_text("[bad](missing.md)")
                with self.assertRaisesRegex(RuntimeError, "missing link"):
                    ready.links(["doc.md"])

    def test_collection_paths_work_from_repository_root(self):
        from types import SimpleNamespace

        import ready_pytest

        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "items.json"
            item = SimpleNamespace(
                path=Path.cwd() / "functions/tests/test_example.py",
                nodeid="tests/test_example.py::test_example",
                get_closest_marker=lambda _: None,
            )
            with patch.dict("os.environ", {"MDP_READY_COLLECTION": str(report)}):
                ready_pytest.pytest_collection_finish(SimpleNamespace(items=[item]))
            self.assertIn(
                "functions/tests/test_example.py::test_example", report.read_text()
            )

    def test_no_postgres_without_marked_tests(self):
        with (
            patch.object(
                ready,
                "function_tests",
                return_value=["functions/tests/test_example.py"],
            ),
            patch.object(ready, "postgres") as postgres,
        ):

            def command(args, **kwargs):
                if "--collect-only" in args:
                    Path(kwargs["env"]["MDP_READY_COLLECTION"]).write_text(
                        '{"functions/tests/test_example.py::test_ok": false}'
                    )
                else:
                    self.assertIn("MDP_CI_DB", kwargs["env"])
                    self.assertNotEqual(
                        kwargs["env"]["MDP_CI_DB"], "/tmp/mdp-ci.duckdb"
                    )
                return ""

            with patch.object(ready, "run", side_effect=command):
                self.assertIn("Postgres skipped", ready.pytest_checks([]))
            postgres.assert_not_called()

    def test_postgres_required_by_collected_fixture_marker(self):
        with (
            patch.object(
                ready,
                "function_tests",
                return_value=["functions/tests/test_example.py"],
            ),
            patch.object(ready, "postgres") as postgres,
        ):

            def command(args, **kwargs):
                if "--collect-only" in args:
                    Path(kwargs["env"]["MDP_READY_COLLECTION"]).write_text(
                        '{"functions/tests/test_example.py::test_ok": true}'
                    )
                return ""

            with patch.object(ready, "run", side_effect=command):
                self.assertIn("1 Postgres", ready.pytest_checks([]))
            postgres.assert_called_once()


if __name__ == "__main__":
    unittest.main()
