"""Missing local relations and refused reads retain a useful next step."""

import json
from unittest.mock import Mock

import psycopg
import pytest
from mdp_functions import workbench
from mdp_functions.analyst_errors import local_build_command
from mdp_functions.errors import ServiceError


@pytest.mark.parametrize(
    "exception, code, text",
    [
        (
            psycopg.errors.UndefinedTable('relation "marts.absent" does not exist'),
            "model_not_built",
            "marts.absent",
        ),
        (
            psycopg.errors.InsufficientPrivilege("permission denied for schema raw"),
            "raw_read_denied",
            "marts.mart_chart_history",
        ),
        (
            psycopg.errors.DivisionByZero("division by zero"),
            "workbench_query_failed",
            "division by zero",
        ),
    ],
)
def test_database_message_survives(exception, code, text):
    result = workbench.database_failure(exception)
    assert result["error_class"] == code
    assert text in result["message"]
    assert str(exception) in result["message"]
    assert result["next_step"]


def test_tenant_database_failures_are_indistinguishable():
    failures = [
        workbench.database_failure(exception)
        for exception in (
            psycopg.errors.UndefinedTable('relation "tenant_absent_marts.probe" does not exist'),
            psycopg.errors.InvalidSchemaName('schema "tenant_absent_marts" does not exist'),
            psycopg.errors.InsufficientPrivilege("permission denied for schema tenant_private_marts"),
        )
    ]
    assert all(result == failures[0] for result in failures)
    assert failures[0]["error_class"] == "tenant_read_denied"
    assert "Tenant data is not available to this role" in failures[0]["message"]
    assert "#demo-tenant" in failures[0]["next_step"]


@pytest.mark.parametrize("schema", ["tenant_private_marts", "tenant_absent_marts", "explore_tenant_private_marts", "explore_tenant_absent_marts"])
def test_staff_tenant_refusal_never_checks_elevated_existence(schema):
    from mdp_functions.analyst_errors import failure
    from mdp_functions.workbench_access import check_staff_query

    connection = Mock()
    with pytest.raises(ServiceError) as caught:
        check_staff_query(connection, f"select * from {schema}.probe", "wb_fixture")
    assert caught.value.status_code == 403
    assert caught.value.message == failure("tenant_read_denied")["message"]
    connection.execute.assert_not_called()


def test_direct_ephemeral_read_names_a_stored_global_descendant(tmp_path, monkeypatch):
    from mdp_functions import settings
    from mdp_functions.workbench_access import check_staff_query

    manifest = tmp_path / "dbt/target/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"nodes": {
        "model.fixture.exact": {
            "name": "int_identity__exact",
            "config": {"schema": "intermediate", "materialized": "ephemeral"},
        },
        "model.fixture.a_tenant": {
            "name": "private_identity", "tags": ["scope:tenant"],
            "config": {"schema": "marts", "materialized": "table"},
            "depends_on": {"nodes": ["model.fixture.exact"]},
        },
        "model.fixture.identity": {
            "name": "int_track_identity", "alias": "int_identity_copy",
            "config": {"schema": "intermediate", "materialized": "table"},
            "depends_on": {"nodes": ["model.fixture.exact"]},
        },
    }}))
    monkeypatch.setattr(settings, "REPO", tmp_path)
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = None
    render_identifier = workbench.sql.Identifier.as_string
    monkeypatch.setattr(workbench.sql.Identifier, "as_string", lambda self, context=None: render_identifier(self))
    with pytest.raises(ServiceError) as caught:
        check_staff_query(connection, "select * from explore_intermediate.int_identity__exact", "wb_fixture")
    assert caught.value.error_class == "model_ephemeral"
    assert "SELECT * FROM intermediate.int_identity_copy" in caught.value.message
    assert "private_identity" not in caught.value.message
    assert "--select +int_identity__exact" not in caught.value.message



@pytest.mark.parametrize(
    "adapter, schema, tags, expected_schema",
    [
        ("duckdb", "main_marts", [], "marts"),
        ("postgres", "marts", [], "marts"),
        ("postgres", "marts", ["scope:tenant"], None),
        ("postgres", "tenant_live_marts", ["scope:tenant"], None),
        ("duckdb", "main_marts", ["scope:tenant"], None),
    ],
)
def test_missing_checked_in_model_precedes_cycle_validation(
    tmp_path, monkeypatch, adapter, schema, tags, expected_schema
):
    manifest = tmp_path / "dbt/target/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "metadata": {"adapter_type": adapter},
                "nodes": {
                    "model.music_data_platform.mart_absent": {
                        "schema": schema,
                        "alias": "mart_alias",
                        "tags": tags,
                        "config": {"schema": "marts", "materialized": "table"},
                    }
                }
            }
        )
    )
    monkeypatch.setattr(workbench, "REPO", tmp_path)
    connect = Mock()
    connect.return_value.__enter__ = Mock(return_value=connect)
    connect.return_value.__exit__ = Mock(return_value=False)
    connect.execute.return_value.fetchone.return_value = (None,)
    monkeypatch.setattr(workbench.psycopg, "connect", connect)
    # Identifier rendering does not need a server connection.
    render_identifier = workbench.sql.Identifier.as_string
    monkeypatch.setattr(
        workbench.sql.Identifier,
        "as_string",
        lambda self, context=None: render_identifier(self),
    )
    wb = workbench.Workbench.__new__(workbench.Workbench)
    wb.settings = Mock(workbench_admin_url="local")
    if expected_schema is None:
        wb.require_built_model("mart_absent")
        connect.assert_not_called()
        return
    with pytest.raises(ServiceError, match="not built here") as error:
        wb.require_built_model("mart_absent")
    assert local_build_command("mart_absent", bool(tags)) in str(error.value)
    assert connect.execute.call_args.args[1] == (f'"{expected_schema}"."mart_alias"',)
    wb.require_built_model("mart_new_draft")
