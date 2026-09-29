"""Acceptance must fail closed when a detector exits cleanly without its proof."""

import pytest
import runner


@pytest.mark.parametrize(
    "case,expected", [("d", [{"timeout_s": 300}]), ("kill", []), ("c", [])]
)
def test_only_commit_order_fixture_gets_larger_time_budget(monkeypatch, case, expected):
    from types import SimpleNamespace

    import lifecycle

    calls = []
    prepared = []
    monkeypatch.setattr(
        lifecycle.harness.Harness, "prepare", lambda self: prepared.append(True)
    )
    monkeypatch.setattr(
        lifecycle.AdversarialHarness,
        "configure_fixture_accounts",
        lambda self, knobs: calls.append(knobs),
    )
    monkeypatch.setattr(lifecycle.AdversarialHarness, "note", lambda *args: None)
    fixture = object.__new__(lifecycle.AdversarialHarness)
    fixture.args = SimpleNamespace(case=case)
    fixture.prepare()
    assert prepared == [True]
    assert calls == expected


def test_catalog_covers_plan_and_runbooks():
    runner.inventory()


@pytest.mark.parametrize(
    "code,output", [(0, ""), (1, "literal detector proof"), (0, "near miss")]
)
def test_missing_proof_and_nonzero_exit_are_failures(
    tmp_path, monkeypatch, code, output
):
    case = {"slug": "fixture", "rule": "fixture-rule", "runbook": "dbt-failure"}
    monkeypatch.setattr(runner, "BASE", tmp_path)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "expected.txt").write_text("literal detector proof\n")
    monkeypatch.setattr(runner, "execute", lambda *args: (code, output))
    passed, line = runner.run_case(case, tmp_path, {})
    assert not passed
    assert line.startswith("CASE fixture FAIL rule=fixture-rule runbook=dbt-failure ")


def test_success_names_rule_and_runbook(tmp_path, monkeypatch):
    case = {"slug": "fixture", "rule": "fixture-rule", "runbook": "dbt-failure"}
    monkeypatch.setattr(runner, "BASE", tmp_path)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "expected.txt").write_text("literal detector proof\n")
    monkeypatch.setattr(
        runner, "execute", lambda *args: (0, "literal detector proof\n")
    )
    passed, line = runner.run_case(case, tmp_path, {})
    assert passed
    assert line.startswith("CASE fixture PASS rule=fixture-rule runbook=dbt-failure ")


def test_names_and_secrets_redacted(monkeypatch):
    monkeypatch.setenv("MDP_SERVICE_TOKEN", "test-sensitive-token")
    assert (
        runner.clean("/home/operator/file test-sensitive-token password=hidden")
        == "<home>/file <redacted> password=<redacted>"
    )


@pytest.mark.parametrize(
    "code,output",
    [
        (0, "CASE fixture PASS\n"),
        (0, "CASE fixture PASS rule=wrong runbook=dbt-failure proof\n"),
        (1, "CASE fixture PASS rule=fixture-rule runbook=dbt-failure proof\n"),
        (0, ""),
    ],
)
def test_wrapper_output_requires_rule_runbook_and_success_exit(
    tmp_path, monkeypatch, code, output
):
    case = {"slug": "fixture", "rule": "fixture-rule", "runbook": "dbt-failure"}
    monkeypatch.setattr(runner, "process", lambda *args, **kwargs: (code, output))
    passed, line = runner.wrapped_case(case, tmp_path, "suite")
    assert not passed
    assert " FAIL rule=fixture-rule runbook=dbt-failure " in line
