"""A new code or a stale generated hint must fail before it reaches a reader."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lint_error_catalog as lint


class ErrorCatalogTest(unittest.TestCase):
    def test_new_code_without_a_row_fails(self):
        with (
            patch.object(lint.generator, "read_catalog", return_value={"unmapped": {}}),
            patch.object(lint, "error_codes", return_value={"new_code": {"source.py"}}),
            self.assertRaisesRegex(SystemExit, "Add catalog rows with next_step"),
        ):
            lint.main()

    def test_scans_named_errors_across_languages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "functions/src/example.py": """
class Lost(ServiceError):
    def __init__(self):
        super().__init__("lost", "message")
raise ServiceError(
    "new_service", "message")
ServiceError(error_class="keyword_code", message="message")
x = {"error_class": "response_code"}
CONSTANT = "constant_code"
raise ServiceError(CONSTANT, "message")
code = "conditional_code" if result == "not_a_code" else "other_code"
raise ServiceError(code, "message")
RUNBOOKS = {"registered_alert": "Read the service log."}
conn.execute("INSERT INTO control.alert(class,runbook_slug) VALUES (%s,'sql-alert')")
alert(conn, run_id, item["kind"], subject)
ServiceError(("indexed_code", "alternate_code")[index], "message")
""",
                "control/apps/control-api/src/router.test.ts": """
throw new AppError(
  "new_app", "message");
throw new AppError(ok ? "left_code" : "right_code", "message");
""",
                "dbt/macros/example.sql": "{{ exceptions.raise_compiler_error('new_dbt: use a valid scope') }}",
            }
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            self.assertEqual(
                set(lint.error_codes(root)),
                {
                    "lost",
                    "new_service",
                    "keyword_code",
                    "response_code",
                    "new_app",
                    "left_code",
                    "right_code",
                    "new_dbt",
                    "constant_code",
                    "conditional_code",
                    "other_code",
                    "registered_alert",
                    "sql_alert",
                    "indexed_code",
                    "alternate_code",
                },
            )

    def test_requires_next_step_and_real_runbook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.yml"
            for row in (
                "summary: Failed\n  runbook: null",
                "summary: Failed\n  next_step: ''\n  runbook: null",
                "summary: Failed\n  next_step: Retry\n  runbook: missing-guide",
            ):
                path.write_text("unmapped:\n  " + row + "\n")
                with (
                    patch.object(lint.generator, "CATALOG", path),
                    self.assertRaises(ValueError),
                ):
                    lint.generator.read_catalog()

    def test_generated_drift_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "error_catalog.json"
            path.write_text("stale")
            with (
                patch.object(lint.generator, "ROOT", Path(directory)),
                patch.object(lint.generator, "read_catalog", return_value={}),
                patch.object(lint.generator, "outputs", return_value={path: "current"}),
                self.assertRaisesRegex(ValueError, "Stale"),
            ):
                lint.generator.generate(check=True)

    def test_prose_requires_an_instruction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "r/mdpr/R/example.R"
            path.parent.mkdir(parents=True)
            path.write_text(
                'stop("Bare failure")\nstop("Failed", paste(x, collapse=","), "; run mdp_setup()")'
            )
            self.assertEqual(lint.prose_errors(root), ["r/mdpr/R/example.R:1"])

    def test_r_catalog_lookup_requires_a_known_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "r/mdpr/R/example.R"
            path.parent.mkdir(parents=True)
            path.write_text('stop(sandbox_message("sandbox_missing"))')
            self.assertEqual(lint.prose_errors(root), ["r/mdpr/R/example.R:1"])
            self.assertEqual(
                lint.prose_errors(
                    root,
                    {
                        "sandbox_missing": {
                            "next_step": "Ask the operator to create a login."
                        }
                    },
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
