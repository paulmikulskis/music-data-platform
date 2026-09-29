"""Contract-aware backtests, reviewable PR files and transitive invoke notices."""

import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest
import yaml
from mdp_functions import workbench, workbench_pr
from mdp_functions.errors import ServiceError


def build(rows, names=None):
    names = names or ["id", "value", "extra", "_lineage", "_built_by"]
    return {"columns": [{"name": name} for name in names], "rows": rows}


@pytest.fixture
def contract_repo(tmp_path, monkeypatch):
    models = tmp_path / "dbt/models/marts"
    models.mkdir(parents=True)
    (models / "models.yml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "models": [
                    {
                        "name": "mart_contracted",
                        "config": {"contract": {"enforced": True}},
                        "columns": [
                            {"name": name}
                            for name in ["id", "value", "_lineage", "_built_by"]
                        ],
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(workbench, "REPO", tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "model,compared,changes",
    [
        ("mart_contracted", ["id", "value"], 0),
        ("mart_draft", ["extra", "id", "value"], 1),
    ],
)
async def test_workbench_backtest_contract_and_draft(
    contract_repo, model, compared, changes
):
    a = build([{"id": 1, "value": 3, "extra": 10, "_lineage": "a", "_built_by": "a"}])
    b = build([{"id": 1, "value": 3, "extra": 20, "_lineage": "b", "_built_by": "b"}])
    wb = workbench.Workbench.__new__(workbench.Workbench)
    wb.db, wb.store = Mock(), Mock()
    wb.build = AsyncMock(side_effect=[a, b])
    await wb.execute(
        str(uuid4()),
        {"id": uuid4()},
        "backtest",
        {
            "model": model,
            "cycleA": str(uuid4()),
            "cycleB": str(uuid4()),
            "keyColumns": ["id"],
        },
    )
    result = json.loads(wb.store.put.call_args.args[1])
    assert result["comparedColumns"] == compared
    assert result["keyColumns"] == ["id"]
    assert result["summary"] == {"added": 0, "removed": 0, "changed": changes}
    assert result["buildA"]["rows"][0]["_built_by"] == "a"
    b["rows"][0]["value"] = 4
    assert len(workbench.diff_rows(a["rows"], b["rows"], ["id"], compared)[2]) == 1


@pytest.mark.parametrize(
    "model,expected",
    [
        ("mart_contracted", ["id", "value"]),
        ("mart_draft", ["extra", "id", "value"]),
    ],
)
def test_workbench_empty_backtest_reports_columns(contract_repo, model, expected):
    assert workbench.backtest_columns(model, build([]), build([])) == expected


@pytest.mark.parametrize("model", ["mart_contracted", "mart_draft"])
def test_workbench_missing_compared_column_fails_closed(contract_repo, model):
    with pytest.raises(ServiceError, match="Every compared column"):
        workbench.backtest_columns(model, build([]), build([], ["id"]))


def test_workbench_keys_and_explicit_columns():
    with pytest.raises(ServiceError, match="at least one"):
        workbench.diff_rows([], [], [])
    with pytest.raises(ServiceError, match="uniquely"):
        workbench.diff_rows([{"id": 1}, {"id": 1}], [], ["id"])
    with pytest.raises(ServiceError, match="Every key"):
        workbench.diff_rows([{"value": 1}], [], ["id"])
    added, removed, changed = workbench.diff_rows(
        [{"id": 1, "value": 2}, {"id": 3, "value": 2}],
        [{"id": 1, "value": 4}, {"id": 2, "value": 2}],
        ["id"],
        ["id", "value"],
    )
    assert added == [{"id": 2, "value": 2}]
    assert removed == [{"id": 3, "value": 2}]
    assert changed == [
        {"before": {"id": 1, "value": 2}, "after": {"id": 1, "value": 4}}
    ]


@pytest.mark.parametrize(
    "target,expected", [("mart", True), ("invoke", True), ("plain", False)]
)
def test_workbench_transitive_invoke_selection(target, expected):
    manifest = {
        "nodes": {
            "mart": {"resource_type": "model", "name": "mart"},
            "stage": {"resource_type": "model", "name": "stage"},
            "invoke": {"resource_type": "model", "name": "invoke__fixture"},
            "plain": {"resource_type": "model", "name": "plain"},
        },
        "parent_map": {
            "mart": ["stage"],
            "stage": ["invoke"],
            "invoke": ["source"],
            "plain": ["source"],
        },
    }
    assert workbench.selection_invokes(manifest, target) is expected
    manifest["nodes"]["invoke"].update(name="custom_receipt", tags=["invoke"])
    assert workbench.selection_invokes(manifest, target) is expected
    manifest["parent_map"]["source"] = [
        "plain"
    ]  # A defensive visited set handles cycles.
    assert not workbench.selection_invokes(manifest, "plain")


def test_workbench_pr_merges_metadata_and_preserves_other_models(tmp_path):
    models = tmp_path / "dbt/models/marts"
    models.mkdir(parents=True)
    (models / "mart_existing.sql").write_text("select 1 as id")
    entry = {
        "name": "mart_existing",
        "config": {"contract": {"enforced": True}},
        "meta": {"reviewed": True},
        "tests": ["fixture_test"],
        "columns": [
            {
                "name": "id",
                "data_type": "integer",
                "tests": ["not_null", "unique"],
                "meta": {"key": True},
            },
            {"name": "retained", "data_type": "text"},
        ],
    }
    other = {"name": "mart_other", "description": "Keep this entry"}
    (models / "_marts__models.yml").write_text(
        yaml.safe_dump({"version": 2, "models": [entry, other]})
    )
    original = copy.deepcopy(entry)
    files, patch = workbench_pr.prepare_files(
        tmp_path,
        {"model": "mart_existing", "sql": "select 2 as id, 3 as added"},
        [{"name": "id", "type": "bigint"}, {"name": "added", "type": "integer"}],
    )
    document = yaml.safe_load(files["dbt/models/marts/_marts__models.yml"])
    assert document["models"][1] == other
    merged = document["models"][0]
    assert merged["config"] == original["config"]
    assert merged["tests"] == original["tests"] and merged["meta"] == original["meta"]
    assert merged["columns"][0] == {**original["columns"][0], "data_type": "bigint"}
    # A column the reviewed SQL no longer produces leaves the enforced contract.
    assert merged["columns"][1] == {"name": "added", "data_type": "integer"}
    assert [c["name"] for c in merged["columns"]] == ["id", "added"]
    assert "retained" not in files["dbt/models/marts/_marts__models.yml"]
    assert any(
        line.startswith("-") and "retained" in line for line in patch.splitlines()
    )
    assert len(document["models"]) == 2
    assert "-select 1 as id" in patch and "+select 2 as id" in patch
    assert not (models / "mart_existing.yml").exists()


@pytest.mark.parametrize("model", ["mart_workbench_dry_run", "mart_chart_history"])
def test_workbench_pr_dry_run_dbt_parse(model, monkeypatch):
    # Use the full checked-in project and real dbt parser, without any git/gh command.
    real_run = workbench_pr.subprocess.run
    commands = []
    lockfile = workbench_pr.REPO / "dbt/uv.lock"
    original_lock = lockfile.read_bytes()

    def installed_environment():
        return {
            path: (path.stat().st_size, path.stat().st_mtime_ns)
            for path in (workbench_pr.REPO / "dbt/.venv").rglob("*")
            if path.is_file()
        }

    original_environment = installed_environment()

    def run(args, **kwargs):
        assert args[0] not in {"git", "gh"}
        project = Path(args[args.index("--project") + 1])
        assert project == kwargs["cwd"] / "dbt"
        assert project != workbench_pr.REPO / "dbt"
        assert args[args.index("--project-dir") + 1] == str(project)
        assert "--no-sync" in args and "--offline" in args
        assert args[args.index("--python") + 1] == str(
            workbench_pr.REPO / "dbt/.venv/bin/python"
        )
        assert kwargs["env"]["UV_PROJECT_ENVIRONMENT"] == str(
            workbench_pr.REPO / "dbt/.venv"
        )
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        commands.append(args)
        result = real_run(args, **kwargs)
        assert not (project / ".venv").exists()
        return result

    monkeypatch.setattr(workbench_pr.subprocess, "run", run)
    result = workbench_pr.publish(
        {"id": uuid4()},
        {"model": model, "sql": "select 1 as draft_marker"},
        [{"name": "draft_marker", "type": "integer"}],
        dry_run=True,
    )
    assert not result["pr_opened"] and result["url"] is None
    assert len(commands) == 1 and "parse" in commands[0]
    document = yaml.safe_load(result["files"]["dbt/models/marts/_marts__models.yml"])
    entries = [entry for entry in document["models"] if entry["name"] == model]
    assert len(entries) == 1
    assert any(c["name"] == "draft_marker" for c in entries[0]["columns"])
    assert entries[0]["config"]["contract"]["enforced"] is (
        model == "mart_chart_history"
    )
    assert len(result["files"]) == 2
    assert lockfile.read_bytes() == original_lock
    assert installed_environment() == original_environment


def test_workbench_pr_missing_environment_is_not_created(tmp_path, monkeypatch):
    project = tmp_path / "dbt"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.0"\n'
    )
    monkeypatch.setattr(workbench_pr, "REPO", tmp_path)
    with pytest.raises(ServiceError, match="Repository operation failed: uv run"):
        workbench_pr.publish(
            {"id": uuid4()},
            {"model": "mart_fixture", "sql": "select 1 as id"},
            [{"name": "id", "type": "integer"}],
            dry_run=True,
        )
    assert not (project / ".venv").exists()
    assert not (project / "uv.lock").exists()


@pytest.mark.parametrize(
    "gh_present", [False, True], ids=["missing-gh", "failed-auth"]
)
def test_workbench_pr_without_auth_returns_reviewed_patch(monkeypatch, gh_present):
    real_run = workbench_pr.subprocess.run
    commands = []

    def run(args, **kwargs):
        if args[0] == "gh":
            assert gh_present and args == ["gh", "auth", "status"]
            assert not any(command[0] == "gh" for command in commands)
            commands.append(args)
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")
        assert args[0] != "git"
        commands.append(args)
        return real_run(args, **kwargs)

    monkeypatch.setattr(
        workbench_pr.shutil, "which", lambda _: "gh" if gh_present else None
    )
    monkeypatch.setattr(workbench_pr.subprocess, "run", run)
    session = {"id": uuid4()}
    draft = {"model": "mart_chart_history", "sql": "select 1 as draft_marker"}
    columns = [{"name": "draft_marker", "type": "integer"}]
    reviewed = workbench_pr.publish(session, draft, columns, dry_run=True)
    result = workbench_pr.publish(session, draft, columns, dry_run=False)
    assert result["pr_opened"] is False
    assert result["url"] is None
    assert result["diff"] == reviewed["diff"]
    assert result["branch"] == reviewed["branch"]
    assert result["message"] == (
        "No pull request opened: GitHub access is not set. Apply the patch in your clone."
    )
    auth_commands = [command for command in commands if command[0] == "gh"]
    assert auth_commands == ([["gh", "auth", "status"]] if gh_present else [])
    parse_commands = [command for command in commands if command[0] == "uv"]
    assert len(parse_commands) == 2
    assert all("parse" in command for command in parse_commands)
    for path in (
        "dbt/models/marts/global/mart_chart_history.sql",
        "dbt/models/marts/_marts__models.yml",
    ):
        assert f"--- a/{path}\n+++ b/{path}\n" in result["diff"]


@pytest.mark.parametrize("operation", ["explain", "preview", "backtest"])
async def test_workbench_dbt_uses_temporary_project_without_sync(
    monkeypatch, tmp_path, operation
):
    from mdp_functions import workbench_history

    wb = workbench.Workbench.__new__(workbench.Workbench)
    wb.db = Mock()
    wb.db.one.return_value = None
    wb.settings = SimpleNamespace(
        workbench_wh_url="postgresql://workbench_wh:fixture@localhost/warehouse",
        workbench_timeout_s=30,
        workbench_schema_cap_bytes=1024,
    )
    wb.grant_inputs = Mock()
    project = tmp_path / "project"
    project.mkdir()
    wb.project = Mock(return_value=project)
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchone.return_value = {
        "n": 0
    }
    monkeypatch.setattr(workbench.psycopg, "connect", Mock(return_value=connection))
    monkeypatch.setattr(workbench_history, "prepare", Mock(return_value=([], [])))
    monkeypatch.setattr(workbench_history, "input_names", Mock(return_value=[]))
    monkeypatch.setattr(workbench_history, "inputs", Mock(return_value={}))

    class CommandChecked(Exception):
        pass

    def check_command(args, **kwargs):
        assert args[:2] == ["uv", "run"]
        assert "--no-sync" in args and "--offline" in args
        assert args[args.index("--python") + 1] == str(
            workbench.REPO / "dbt/.venv/bin/python"
        )
        for flag in ("--project", "--project-dir"):
            assert args[args.index(flag) + 1] == str(project)
        assert args[args.index("--profiles-dir") + 1] == str(project / "profiles")
        assert kwargs["cwd"] == project
        assert kwargs["env"]["UV_PROJECT_ENVIRONMENT"] == str(
            workbench.REPO / "dbt/.venv"
        )
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert ("build" if operation == "preview" else "compile") in args
        raise CommandChecked

    async def check_async(*args, **kwargs):
        check_command(list(args), **kwargs)

    monkeypatch.setattr(workbench.subprocess, "run", check_command)
    monkeypatch.setattr(workbench.asyncio, "create_subprocess_exec", check_async)
    session = {"id": uuid4(), "user_id": "fixture", "scratch_schema": "wb_fixture"}
    draft = {"model": "mart_fixture", "sql": "select 1 as id"}
    with pytest.raises(CommandChecked):
        if operation == "explain":
            wb.explain(session, draft["model"], draft)
        else:
            await wb.build(
                str(uuid4()),
                session,
                draft["model"],
                str(uuid4()),
                draft=draft,
                side="A" if operation == "backtest" else "preview",
            )


@pytest.mark.parametrize("model", ["mart_chart_history", "mart_workbench_patch_new"])
def test_workbench_pr_patch_applies(tmp_path, model):
    checkout = tmp_path / "repository"
    shutil.copytree(
        workbench_pr.REPO / "dbt",
        checkout / "dbt",
        ignore=shutil.ignore_patterns(".venv", "target", "logs", ".git", "__pycache__"),
    )
    subprocess.run(["git", "init", "--quiet"], cwd=checkout, check=True)
    subprocess.run(["git", "add", "dbt"], cwd=checkout, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
            "commit", "--quiet", "-m", "Add fixture project",
        ],
        cwd=checkout,
        check=True,
    )
    _, patch = workbench_pr.prepare_files(
        checkout,
        {"model": model, "sql": "select 1 as draft_marker"},
        [{"name": "draft_marker", "type": "integer"}],
    )
    subprocess.run(["git", "restore", "dbt"], cwd=checkout, check=True)
    if model == "mart_workbench_patch_new":
        (checkout / f"dbt/models/marts/{model}.sql").unlink()
        assert f"--- /dev/null\n+++ b/dbt/models/marts/{model}.sql\n" in patch
    subprocess.run(
        ["git", "apply", "--check"], input=patch, text=True, cwd=checkout, check=True
    )


def test_workbench_pr_parse_failure_blocks_publication(monkeypatch):
    real_run = workbench_pr.subprocess.run
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        if args[0] == "uv":
            return real_run(args, **kwargs)
        if args[:3] == ["git", "worktree", "add"]:
            checkout = workbench_pr.Path(args[-2])
            shutil.copytree(
                workbench_pr.REPO / "dbt",
                checkout / "dbt",
                ignore=shutil.ignore_patterns(".venv", "target", "logs", ".git"),
            )
        return SimpleNamespace(returncode=0, stdout="fixture", stderr="")

    monkeypatch.setattr(workbench_pr.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(workbench_pr.subprocess, "run", run)
    with pytest.raises(ServiceError, match="Repository operation failed: uv run"):
        workbench_pr.publish(
            {"id": uuid4()},
            {
                "model": "mart_workbench_invalid",
                "sql": "select * from {{ ref('missing_workbench_dependency') }}",
            },
            [{"name": "id", "type": "integer"}],
        )
    assert any("parse" in command for command in commands)
    assert not any(
        "push" in command
        or "commit" in command
        or command[:3] == ["gh", "pr", "create"]
        for command in commands
    )


def test_workbench_save_forwards_dry_run_and_keeps_exact_build_gate(monkeypatch):
    wb = workbench.Workbench.__new__(workbench.Workbench)
    wb.db = Mock()
    columns = [{"name": "id", "type": "integer"}]
    wb.db.one.return_value = {"result": {"columns": columns}}
    publish = Mock(return_value={"pr_opened": False, "diff": "+select 1"})
    monkeypatch.setattr(workbench_pr, "publish", publish)
    session, draft = {"id": uuid4()}, {"model": "mart_review", "sql": "select 1"}
    wb.save_as_pr(session, draft, dry_run=True)
    publish.assert_called_once_with(session, draft, columns, dry_run=True)
    wb.db.one.return_value = None
    with pytest.raises(ServiceError, match="Preview this exact draft"):
        wb.save_as_pr(session, draft, dry_run=True)
    assert publish.call_count == 1


def test_workbench_diff_separates_files_without_trailing_sql_newline(tmp_path):
    models = tmp_path / "dbt/models/marts"
    models.mkdir(parents=True)
    _, diff = workbench_pr.prepare_files(
        tmp_path, {"model": "mart_review", "sql": "select 1 as id"},
        [{"name": "id", "type": "integer"}],
    )
    assert "+select 1 as id\n\\ No newline at end of file\n--- /dev/null\n" in diff


async def test_direct_query_never_builds_and_keeps_labels():
    wb = workbench.Workbench.__new__(workbench.Workbench)
    wb.db, wb.store = Mock(), Mock()
    wb.build = AsyncMock(side_effect=AssertionError("Direct SQL must not build dbt"))
    labels = {"cross_tenant": True, "tenants": ["fixture_a", "fixture_b"]}
    wb.query = Mock(return_value={"columns": [], "rows": [], "timingMs": 1, "labels": labels})
    session = {"id": uuid4(), "scratch_schema": "wb_fixture"}
    await wb.execute(str(uuid4()), session, "query", {"sql": "select 1", "model": "mart_draft"})
    wb.build.assert_not_called()
    result = wb.db.execute.call_args.args[1][0].obj
    assert result["labels"]["cross_tenant"] is True
    assert result["labels"] == labels
    assert result["compiledSql"] == "select 1"
    with pytest.raises(ServiceError, match="plain SQL"):
        await wb.direct(str(uuid4()), session, {"sql": "select * from {{ ref('mart_x') }}"})


def test_direct_database_refusals_name_a_recovery():
    import psycopg
    from mdp_functions.workbench import database_failure
    denied = database_failure(psycopg.errors.InsufficientPrivilege())
    timeout = database_failure(psycopg.errors.QueryCanceled())
    assert denied['error_class'] == 'workbench_permission_denied'
    assert 'Explorer' in denied['next_step']
    assert denied['runbook'] == '/runbooks/forbidden'
    assert 'Explorer' in denied['message'] and 'operator' in denied['message']
    assert timeout['error_class'] == 'workbench_statement_timeout'
    assert 'snapshot' in timeout['next_step']
    assert 'Filter earlier' in timeout['message'] and 'snapshot' in timeout['message']
