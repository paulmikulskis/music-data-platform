"""Console staff requests use global analyst grants at the SQL execution boundary."""

import asyncio
import json
import os
import subprocess
from unittest.mock import Mock

import httpx
import psycopg
import pytest
from mdp_functions import workbench
from mdp_functions.settings import REPO, Settings
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from workbench_model_fixture import (
    workbench_models as workbench_models,  # noqa: PLC0414
)


async def test_staff_requests_cannot_read_raw_tenants_or_admin_results(catalog_databases, tmp_path, monkeypatch):
    db = catalog_databases
    connection = conninfo_to_dict(db["warehouse_url"])
    connection.update(user="workbench_wh", password="workbench_wh")
    settings = Settings(control_url=db["control_url"], control_rt_url=db["admin_control"],
                        workbench_wh_url=make_conninfo(**connection),
                        workbench_admin_url=db["admin_warehouse"],
                        service_token="fixture", dump_root=tmp_path.as_uri())
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute("CREATE SCHEMA tenant_fixture_marts")
        for schema in ("marts", "intermediate", "staging", "raw", "tenant_fixture_marts"):
            conn.execute("INSERT INTO catalog.label_definitions(relation,labels) VALUES (%s,%s::jsonb) ON CONFLICT(relation) DO UPDATE SET labels=excluded.labels",
                         (("tenant_*_marts" if schema.startswith("tenant_") else schema) + ".staff_probe", '{"shared_privacy":{"id":"non_personal"}}'))
            conn.execute(sql.SQL("CREATE TABLE {}.staff_probe(id int)").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("INSERT INTO {}.staff_probe VALUES (42)").format(sql.Identifier(schema)))
        conn.execute("CREATE VIEW explore_raw.staff_probe AS SELECT id FROM raw.staff_probe")
        conn.execute("GRANT SELECT ON explore_raw.staff_probe TO explorer_ro")
    wb = workbench.Workbench(settings)
    monkeypatch.setattr(workbench, "Workbench", Mock(return_value=wb))
    app = workbench.create_app(settings)
    sessions = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local",
                                     headers={"authorization": "Bearer fixture"}) as client:
            async def call(operation, user="staff:access-test", staff=True, **body):
                return await client.post("/v1/workbench/" + operation, json={"userId": user, "staff": staff, **body})

            for user, staff in (("access-test", False), ("staff:access-test", True)):
                response = await call("createSession", user=user, staff=staff)
                assert response.status_code == 200, response.text
                sessions.append(wb.session(response.json()["sessionId"], user))
            admin, session = sessions
            for schema in ("marts", "intermediate", "staging"):
                response = await call("query", sessionId=str(session["id"]), model="mart_draft",
                                      sql=f"select count(*) as n from {schema}.staff_probe")
                assert response.status_code == 200, response.text
                run_id = response.json()["runId"]
                await wb.tasks[run_id]
                state = await call("status", runId=run_id)
                assert state.json()["status"] == "succeeded", state.text
                result = await call("result", runId=run_id)
                assert result.json()["rows"] == [{"n": 1}], result.text
            assert wb.query(session, "select * from marts.staff_probe")["rows"] == [{"id": 42}]
            assert wb.query(session, "select * from staff_probe")["rows"] == [{"id": 42}]
            tenant_refusals = []
            for relation in ("raw.staff_probe", "explore_raw.staff_probe", "tenant_fixture_marts.staff_probe",
                             "explore_tenant_fixture_marts.staff_probe", "tenant_absent_marts.staff_probe",
                             "explore_tenant_absent_marts.staff_probe"):
                response = await call("query", sessionId=str(session["id"]), model="mart_draft",
                                      sql=f"select * from {relation}")
                run_id = response.json()["runId"]
                await wb.tasks[run_id]
                status = await call("status", runId=run_id)
                assert status.json()["status"] == "failed", status.text
                assert status.json()["error"]["error_class"] == (
                    "raw_read_denied" if "raw." in relation else "tenant_read_denied"
                )
                if "tenant_" in relation:
                    tenant_refusals.append(status.json()["error"])
            assert all(error == tenant_refusals[0] for error in tenant_refusals)
            assert wb.query(admin, "select count(*) as n from tenant_fixture_marts.staff_probe")["rows"] == [{"n": 1}]
            assert (await call("history", sessionId=str(admin["id"]))).status_code == 404
            refused = await call("sandboxAction", schema="sandbox_fixture", action="disable")
            assert refused.status_code == 403
            assert refused.json()["next_step"]
            # An expression can hide a relation from SQL rewriting; database grants still refuse it.
            with psycopg.connect(settings.workbench_wh_url) as conn:
                conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(session["scratch_schema"])))
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute("select query_to_xml('select * from tenant_fixture_marts.staff_probe',true,false,'')")
            # Preview and Backtest use this same role on their dbt connection.
            assert f"role={session['scratch_schema']}" in wb.build_env(session["scratch_schema"])["PGOPTIONS"]
            assert (await call("sandboxStatus", warehouseRole=None)).json() == {"sandboxes": []}
    finally:
        await asyncio.gather(*wb.tasks.values(), return_exceptions=True)
        with psycopg.connect(db["admin_warehouse"]) as conn:
            for session in sessions:
                role = sql.Identifier(session["scratch_schema"])
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(role))
                conn.execute(sql.SQL("DROP OWNED BY {}").format(role))
                conn.execute(sql.SQL("DROP ROLE {}").format(role))
        wb.db.close()
        wb.owner.close()


async def test_staff_sandbox_status_uses_only_the_operator_mapping(catalog_databases, tmp_path, monkeypatch):
    from mdp_functions.sandbox import create_sandbox

    db = catalog_databases
    owners = ("analyst_staffown", "analyst_staffother")
    with psycopg.connect(db["admin_warehouse"]) as conn:
        for owner in owners:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(owner)))
            conn.execute(sql.SQL("GRANT analyst_ro TO {}").format(sql.Identifier(owner)))
            create_sandbox(conn, owner.removeprefix("analyst_"), owner)
    monkeypatch.setattr(workbench, "Workbench", Mock())
    app = workbench.create_app(Settings(service_token="fixture", workbench_admin_url=db["admin_warehouse"]))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local",
                                     headers={"authorization": "Bearer fixture"}) as client:
            response = await client.post("/v1/workbench/sandboxStatus", json={
                "userId": "staff:access-test", "staff": True, "warehouseRole": owners[0], "schema": "sandbox_staffother"})
            assert response.status_code == 200, response.text
            assert [row["owner_role"] for row in response.json()["sandboxes"]] == [owners[0]]
            admin = await client.post("/v1/workbench/sandboxStatus", json={"userId": "admin", "staff": False})
            assert {row["owner_role"] for row in admin.json()["sandboxes"]} == set(owners)
    finally:
        with psycopg.connect(db["admin_warehouse"]) as conn:
            for owner in owners:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier("sandbox_" + owner.removeprefix("analyst_"))))
                conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(owner)))
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(owner)))


async def test_staff_checked_in_builds_and_live_revocation(catalog_databases, tmp_path, monkeypatch, workbench_models):
    """Exercise Run, dbt Preview/Backtest and the PR gate through the service caller."""
    from uuid import uuid4

    from mdp_functions import workbench_pr

    preview_role = "analyst_preview_" + uuid4().hex[:8]
    staff_role = "analyst_staffshared_" + uuid4().hex[:8]
    db = catalog_databases
    connection = conninfo_to_dict(db["warehouse_url"])
    connection.update(user="workbench_wh", password="workbench_wh")
    settings = Settings(control_url=db["control_url"], control_rt_url=db["admin_control"],
                        workbench_wh_url=make_conninfo(**connection),
                        workbench_admin_url=db["admin_warehouse"],
                        service_token="fixture", dump_root=tmp_path.as_uri())
    with psycopg.connect(db["admin_warehouse"]) as conn:
        # No raw or explore_raw relation exists. The checked-in upstream normally reads raw.
        conn.execute("""CREATE TABLE staging.stg_fixture_accounts__account_snapshots AS
            SELECT 'fixture'::text AS platform, key AS platform_account_id, key AS handle,
              timestamp '2026-01-01' AS snapshot_at, 10::bigint AS followers,
              key AS source_key, NULL::text AS _target_id, NULL::text AS _revision_id
            FROM unnest(ARRAY['preview_allowed','preview_no_learning','preview_no_resale',
                              'preview_unknown']) key""")
        conn.execute("""CREATE TABLE staging.stg_control__targets_hourly(
            platform text, platform_account_id text, display_name text, role text)""")
        conn.execute("""CREATE TABLE staging.stg_control__target_history(
            id text, _revision_id text, display_name text, role text)""")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA staging TO dbt_transform")
        conn.execute("CREATE TABLE intermediate.int_track_identity(platform text)")
        conn.execute("INSERT INTO intermediate.int_track_identity VALUES ('fixture')")
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fixture'").format(sql.Identifier(preview_role)))
        conn.execute(sql.SQL("GRANT analyst_ro TO {}").format(sql.Identifier(preview_role)))
        from mdp_functions.sandbox import create_sandbox

        conn.execute(f"CREATE ROLE {staff_role} NOLOGIN")
        conn.execute(f"GRANT analyst_ro TO {staff_role}")
        create_sandbox(conn, "staffshared", staff_role)
        conn.execute(f"SET LOCAL ROLE {staff_role}")
        conn.execute("CREATE TABLE sandbox_staffshared.private(id int)")
        conn.execute("GRANT SELECT ON sandbox_staffshared.private TO analyst_ro")
        conn.execute("RESET ROLE")
        conn.execute("""CREATE TABLE staging.stg_kexp__plays AS
            SELECT 'fixture'::text AS station, 'recording-'||n AS recording_mbid,
              timestamp '2026-01-01' + n * interval '1 day' AS airdate,
              n::bigint AS play_id, 'Light'::text AS rotation_status, 1 AS rotation_rank,
              '[]'::text AS artist_mbids, NULL::text AS release_group_mbid
            FROM generate_series(1,91) n""")
    # Build the same checked-in mart with production ref routing and dbt_transform.
    import yaml

    profile = tmp_path / "profiles"
    profile.mkdir()
    parts = conninfo_to_dict(db["admin_warehouse"])
    (profile / "profiles.yml").write_text(yaml.safe_dump({"music_data_platform": {
        "target": "production_fixture", "outputs": {"production_fixture": {
            "type": "postgres", "host": parts["host"], "port": int(parts["port"]),
            "dbname": parts["dbname"], "user": "dbt_transform", "pass": "dbt_transform",
            "schema": "dbt", "threads": 1, "sslmode": "prefer",
        }}}}))
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO dbt_transform").format(
            sql.Identifier(parts["dbname"])))
    def production(command, model):
        result = subprocess.run([
            "uv", "run", "--project", str(REPO / "dbt"), "dbt", command,
            "--project-dir", str(REPO / "dbt"), "--profiles-dir", str(profile),
            "--select", model, "--vars", "{dry_run: true}",
        ], env=dict(os.environ, DBT_MDP_SCOPE="global", DBT_TARGET_PATH=str(tmp_path / "target"),
                    DBT_LOG_PATH=str(tmp_path / "logs")), capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr

    production("seed", "rights_registry")
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute("UPDATE reference.rights_registry SET learning_eligible=true,resale_permitted=true")
        conn.execute("""INSERT INTO reference.rights_registry(source_key,learning_eligible,resale_permitted)
            VALUES ('preview_allowed',true,true), ('preview_no_learning',false,true),
                   ('preview_no_resale',true,false)""")
    production("run", "mart_enrichment_fixture")
    manifest = json.loads((tmp_path / "target/manifest.json").read_text())
    compiled = manifest["nodes"]["model.music_data_platform.mart_enrichment_fixture"]["compiled_code"]
    assert '"reference"."rights_registry"' in compiled
    with psycopg.connect(db["admin_warehouse"], row_factory=dict_row) as conn:
        expected = conn.execute("SELECT source_key,learning_eligible,resale_permitted,source_keys FROM marts.mart_enrichment_fixture ORDER BY source_key").fetchall()
    assert [(r["learning_eligible"], r["resale_permitted"]) for r in expected] == [
        (True, True), (False, True), (True, False), (False, False)]
    wb = workbench.Workbench(settings)
    monkeypatch.setattr(workbench, "Workbench", Mock(return_value=wb))
    # Keep publication local; successful real dbt results must unlock this caller.
    publish = Mock(return_value={"diff": "+draft", "pr_opened": False})
    monkeypatch.setattr(workbench_pr, "publish", publish)
    app = workbench.create_app(settings)
    session = None
    model = "int_radio__rotation_events"
    cycle = str(uuid4())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local",
                                     headers={"authorization": "Bearer fixture"}) as client:
            async def call(operation, **body):
                response = await client.post("/v1/workbench/" + operation, json={
                    "userId": "staff:build-test", "staff": True, **body})
                assert response.status_code == 200, response.text
                return response.json()

            created = await call("createSession")
            session = wb.session(created["sessionId"], "staff:build-test")
            draft = await call("draft", sessionId=created["sessionId"], model=model)
            # Analyst membership can include shared sandboxes; console input checks exclude them.
            mixed = await client.post("/v1/workbench/previewModel", json={
                "userId": "staff:build-test", "staff": True, "sessionId": created["sessionId"],
                "model": "mart_sandbox", "cycleId": cycle,
                "sql": "select * from {{ ref('stg_kexp__plays') }} p join sandbox_staffshared.private s on true"})
            assert mixed.status_code == 403
            assert mixed.json()["error_class"] == "workbench_permission_denied"

            async def run(operation, **body):
                submitted = await call(operation, sessionId=created["sessionId"], **body)
                await wb.tasks[submitted["runId"]]
                return await call("status", runId=submitted["runId"]), submitted["runId"]

            state, _ = await run(
                "previewModel", model="mart_tenant_probe", cycleId=cycle,
                sql="select * from {{ ref('mart_scoped_fixture') }}",
            )
            assert state["status"] == "failed", state
            assert state["error"]["error_class"] == "tenant_read_denied", state
            assert "Tenant data is not available to this role" in state["error"]["message"]

            state, _ = await run(
                "previewModel", model="mart_identity_probe", cycleId=cycle,
                sql="select * from {{ ref('int_identity__exact') }}",
            )
            assert state["status"] == "failed", state
            assert state["error"]["error_class"] == "model_ephemeral", state
            assert "ephemeral SQL and has no table" in state["error"]["message"]
            assert "SELECT * FROM intermediate.int_track_identity" in state["error"]["message"]
            assert "--select +int_identity__exact" not in state["error"]["message"]
            state, run_id = await run(
                "previewModel", model="mart_identity_probe", cycleId=cycle,
                sql="SELECT * FROM intermediate.int_track_identity",
            )
            assert state["status"] == "succeeded", state
            assert (await call("result", runId=run_id))["rows"] == [{"platform": "fixture"}]

            for operation, args in (
                ("query", {"sql": "select count(*) from staging.stg_kexp__plays"}),
                ("previewModel", {**draft, "cycleId": cycle}),
                ("backtest", {**draft, "cycleA": cycle, "cycleB": str(uuid4()), "keyColumns": ["play_id", "event_type"]}),
            ):
                state, run_id = await run(operation, **args)
                assert state["status"] == "succeeded", state
                result = await call("result", runId=run_id)
                if operation == "previewModel":
                    assert len(result["rows"]) == 2
                    assert "explore_staging" in result["compiledSql"]
                    assert "explore_raw" not in result["compiledSql"]
                if operation == "backtest":
                    assert result["summary"] == {"added": 0, "removed": 0, "changed": 0}
            await call("saveAsPr", sessionId=created["sessionId"], **draft)
            publish.assert_called_once()

            # The unchanged default mart uses only the safe registry flags. Both
            # compiled cycle queries also run through an individual analyst login.
            growth = await call("draft", sessionId=created["sessionId"], model="mart_enrichment_fixture")
            assert growth["sql"] == wb.model_path("mart_enrichment_fixture").read_text()
            analyst = make_conninfo(db["admin_warehouse"], user=preview_role, password="fixture")
            for operation, args in (
                ("previewModel", {"cycleId": cycle}),
                ("backtest", {"cycleA": cycle, "cycleB": str(uuid4()),
                              "keyColumns": ["platform", "platform_account_id", "snapshot_at"]}),
            ):
                state, run_id = await run(operation, **growth, **args)
                assert state["status"] == "succeeded", state
                result = await call("result", runId=run_id)
                builds = [result] if operation == "previewModel" else [result["buildA"], result["buildB"]]
                analyst_rows = []
                for built in builds:
                    assert '"catalog"."learning_rights"' in built["compiledSql"]
                    assert '"reference"."rights_registry"' not in built["compiledSql"]
                    flags = lambda rows: sorted([
                        {key: row[key] for key in expected[0]} for row in rows
                    ], key=lambda row: row["source_key"])
                    assert flags(built["rows"]) == expected
                    with psycopg.connect(analyst, row_factory=dict_row) as conn:
                        rows = conn.execute(built["compiledSql"]).fetchall()
                        assert flags(rows) == expected
                        analyst_rows.append(rows)
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):
                            conn.execute("SELECT * FROM reference.rights_registry")
                if operation == "backtest":
                    assert result["summary"] == {"added": 0, "removed": 0, "changed": 0}
                    assert workbench.diff_rows(*analyst_rows, result["keyColumns"],
                                               result["comparedColumns"]) == ([], [], [])

            # Simulate an existing session from before inheritance replaced copied grants.
            with psycopg.connect(db["admin_warehouse"]) as conn:
                conn.execute(sql.SQL("GRANT SELECT ON explore_staging.stg_kexp__plays TO {}").format(
                    sql.Identifier(session["scratch_schema"])))
                conn.execute("REVOKE SELECT ON explore_staging.stg_kexp__plays FROM analyst_ro")
            for operation, args in (
                ("query", {"sql": "with p as (select * from staging.stg_kexp__plays) select count(*) from p"}),
                ("previewModel", {**draft, "cycleId": cycle}),
                ("backtest", {**draft, "cycleA": cycle, "cycleB": cycle, "keyColumns": ["play_id", "event_type"]}),
            ):
                state, _ = await run(operation, **args)
                assert state["status"] == "failed", state
                assert state["error"]["error_class"] == "workbench_permission_denied", state
                assert state["error"]["next_step"] and state["error"]["runbook"]
                with psycopg.connect(db["admin_warehouse"]) as conn:
                    assert not conn.execute("SELECT has_table_privilege(%s,'explore_staging.stg_kexp__plays','SELECT')",
                                            (session["scratch_schema"],)).fetchone()[0]
                    assert not conn.execute("SELECT has_table_privilege('analyst_ro','explore_staging.stg_kexp__plays','SELECT')").fetchone()[0]
            # A further revocation needs no service call to reach the already-open role.
            with psycopg.connect(db["admin_warehouse"]) as conn:
                conn.execute("GRANT SELECT ON explore_staging.stg_kexp__plays TO analyst_ro")
            with psycopg.connect(settings.workbench_wh_url, autocommit=True) as conn:
                conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(session["scratch_schema"])))
                assert conn.execute("SELECT count(*) FROM explore_staging.stg_kexp__plays").fetchone()[0] == 91
                with psycopg.connect(db["admin_warehouse"]) as admin:
                    admin.execute("REVOKE SELECT ON explore_staging.stg_kexp__plays FROM analyst_ro")
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute("SELECT count(*) FROM explore_staging.stg_kexp__plays")
    finally:
        await asyncio.gather(*wb.tasks.values(), return_exceptions=True)
        if session:
            with psycopg.connect(db["admin_warehouse"]) as conn:
                role = sql.Identifier(session["scratch_schema"])
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(role))
                conn.execute(sql.SQL("DROP OWNED BY {}").format(role))
                conn.execute(sql.SQL("DROP ROLE {}").format(role))
                conn.execute("DROP SCHEMA sandbox_staffshared CASCADE")
                conn.execute(f"DROP OWNED BY {staff_role}")
                conn.execute(f"DROP ROLE {staff_role}")
                conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(preview_role)))
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(preview_role)))
        wb.db.close()
        wb.owner.close()


def test_rights_view_tracks_registry_updates_and_rebuilds(catalog_databases):
    with psycopg.connect(catalog_databases["admin_warehouse"]) as conn:
        conn.execute("CREATE SCHEMA reference AUTHORIZATION dbt_transform")
        definition = """CREATE TABLE reference.rights_registry(
            source_key text, learning_eligible boolean, resale_permitted boolean,
            licence_text text, price text, contact text)"""
        conn.execute(definition)
        # Upgrade the existing two-column view without breaking its readers.
        conn.execute("DROP VIEW catalog.learning_rights")
        conn.execute("CREATE VIEW catalog.learning_rights AS SELECT source_key,learning_eligible FROM reference.rights_registry")
        conn.execute((REPO / "ops/fly/postgres/boot/init/17-explore-views.sql").read_text())
        for rebuild in (False, True):
            if rebuild:
                conn.execute("DROP TABLE reference.rights_registry CASCADE")
                conn.execute(definition)
            conn.execute("INSERT INTO reference.rights_registry VALUES ('fixture',true,false,'private','private','private')")
            for learning, resale in ((True, False), (False, True)):
                conn.execute("UPDATE reference.rights_registry SET learning_eligible=%s,resale_permitted=%s", (learning, resale))
                for role in ("analyst_ro", "explorer_ro"):
                    with conn.transaction():
                        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                        result = conn.execute("SELECT * FROM catalog.learning_rights")
                        assert [c.name for c in result.description] == ["source_key", "learning_eligible", "resale_permitted"]
                        assert result.fetchall() == [("fixture", learning, resale)]
                    conn.execute("RESET ROLE")


def test_preview_allowed_schemas_match_direct_sql():
    import re

    from mdp_functions.workbench_access import STAFF_SCHEMAS, input_refs

    allowed = re.search(r"{% set allowed = (\[.*?\]) %}", input_refs(staff=True)).group(1)
    assert tuple(json.loads(allowed)) == STAFF_SCHEMAS
