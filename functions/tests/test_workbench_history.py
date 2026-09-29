"""Admin comparisons reconstruct each cycle through the restricted warehouse role."""

from uuid import uuid4

import psycopg
import pytest
from mdp_functions.errors import ServiceError
from mdp_functions.relation_labels import HISTORY_METADATA, load
from mdp_functions.schemas import LINEAGE
from mdp_functions.settings import Settings
from mdp_functions.warehouse.postgres import MIRRORS, PostgresWarehouse
from mdp_functions.workbench import Workbench
from mdp_functions.workbench_history import input_names, inputs, materialize
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from workbench_model_fixture import (
    workbench_models as workbench_models,  # noqa: PLC0414
)


def test_safe_history_metadata_has_no_target_values():
    labels = load()
    for table, columns in HISTORY_METADATA.items():
        assert all(labels[table]["privacy"][name] == "non_personal" for name in columns)
    for column in ("handle", "display_name", "platform_account_id", "params_json"):
        assert labels["raw.targets"]["privacy"][column] == "omit"
    assert labels["raw.cycles"]["privacy"]["opened_by_dbt_run_id"] == "omit"


@pytest.mark.parametrize(
    "query",
    [
        "select * from explore_marts.mart_enrichment_fixture",
        "select * from explore_raw.account_snapshots",
        "with current_rows as (select * from marts.mart_enrichment_fixture) select * from current_rows",
        "select table_to_xml('explore_marts.mart_enrichment_fixture',true,false,'') as rows",
        "select catalog.jsonb_typeof('{}'::jsonb) as hidden_read",
    ],
)
def test_unfrozen_sql_refuses_before_connecting(query):
    with pytest.raises(ServiceError, match="Choose Preview") as caught:
        materialize(
            "invalid connection",
            "wb_test",
            "draft",
            query,
            input_names(str(uuid4()), ["account_snapshots"]),
        )
    assert caught.value.error_class == "workbench_history_unavailable"


@pytest.mark.parametrize("side", [0, 1], ids=["A", "B"])
@pytest.mark.parametrize("remaining", [0, 1], ids=["missing", "partial"])
def test_incomplete_dump_refuses(historical_workbench, side, remaining):
    wb, session, cycles, dumps, _, db = historical_workbench
    schema = session["scratch_schema"]
    wb.grant_inputs(schema, session["user_id"])
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute(
            "DELETE FROM raw.account_snapshots WHERE _dump_id=%s AND ctid IN "
            "(SELECT ctid FROM raw.account_snapshots WHERE _dump_id=%s LIMIT %s)",
            (dumps[side], dumps[side], 2 - remaining),
        )
    with pytest.raises(ServiceError, match="Choose Preview") as caught:
        inputs(
            wb.settings.workbench_wh_url,
            schema,
            str(cycles[side]),
            input_names(str(cycles[side]), ["account_snapshots"]),
        )
    assert caught.value.error_class == "workbench_history_unavailable"


def test_checked_inputs_survive_later_deletion(historical_workbench):
    wb, session, cycles, dumps, _, db = historical_workbench
    schema = session["scratch_schema"]
    wb.grant_inputs(schema, session["user_id"])
    sources = input_names(str(cycles[0]), ["account_snapshots"])
    inputs(wb.settings.workbench_wh_url, schema, str(cycles[0]), sources)
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute("DELETE FROM raw.account_snapshots WHERE _dump_id=%s", (dumps[0],))
    query = (
        sql.SQL("SELECT followers FROM {}")
        .format(sql.Identifier(schema, sources["account_snapshots"]))
        .as_string()
    )
    materialize(wb.settings.workbench_wh_url, schema, "checked", query, sources)
    with psycopg.connect(wb.settings.workbench_wh_url) as conn:
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(schema)))
        assert conn.execute(
            sql.SQL("SELECT followers FROM {}.checked").format(sql.Identifier(schema))
        ).fetchall() == [(10,), (10,)]


@pytest.mark.parametrize(
    "problem", ["missing", "uncommitted", "unknown_count", "deduplicated"]
)
def test_unproven_dump_receipt_refuses(historical_workbench, problem):
    wb, session, cycles, dumps, _, db = historical_workbench
    schema = session["scratch_schema"]
    wb.grant_inputs(schema, session["user_id"])
    updates = {
        "missing": "DELETE FROM raw._load_receipts WHERE dump_id=%s",
        "uncommitted": "UPDATE raw._load_receipts SET committed_at=NULL WHERE dump_id=%s",
        "unknown_count": "UPDATE raw._load_receipts SET rows=NULL WHERE dump_id=%s",
        "deduplicated": "UPDATE raw._load_receipts SET rows_deduped=1 WHERE dump_id=%s",
    }
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute(updates[problem], (dumps[0],))
    with pytest.raises(ServiceError, match="Choose Preview") as caught:
        inputs(
            wb.settings.workbench_wh_url,
            schema,
            str(cycles[0]),
            input_names(str(cycles[0]), ["account_snapshots"]),
        )
    assert caught.value.error_class == "workbench_history_unavailable"


def test_tenant_history_is_hidden_from_workbench(historical_workbench):
    wb, session, cycles, dumps, _, db = historical_workbench
    tenant_cycle, tenant_dump = uuid4(), uuid4()
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute(
            "INSERT INTO raw.cycles(id,scope,cadence,status,opened_at,closed_at,manifest_mode,close_no) "
            "VALUES (%s,'tenant:history-fixture','hourly','closed','2026-01-01','2026-01-01','stamp',1)",
            (tenant_cycle,),
        )
        conn.execute(
            "INSERT INTO raw.cycle_inputs(cycle_id,dump_id) VALUES (%s,%s),(%s,%s)",
            (tenant_cycle, tenant_dump, cycles[0], dumps[0]),
        )
        conn.execute(
            "INSERT INTO raw.dump_stamps(dump_id,scope,close_no,target_table) "
            "VALUES (%s,'tenant:history-fixture',1,'raw.account_snapshots')",
            (tenant_dump,),
        )
        conn.execute(
            "INSERT INTO raw._load_receipts(dump_id,warehouse_id,target_table,generation,claim_token,rows,committed_at) "
            "VALUES (%s,%s,'raw.account_snapshots',1,%s,1,now())",
            (tenant_dump, uuid4(), uuid4()),
        )
    schema = session["scratch_schema"]
    wb.grant_inputs(schema, session["user_id"])
    with psycopg.connect(wb.settings.workbench_wh_url) as conn:
        for role in (None, schema):
            if role:
                conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            assert conn.execute(
                "SELECT id FROM explore_raw.cycles ORDER BY id"
            ).fetchall() == [(cycle,) for cycle in sorted(cycles)]
            members = conn.execute(
                "SELECT cycle_id,dump_id FROM explore_raw.cycle_inputs"
            ).fetchall()
            assert (cycles[0], dumps[0]) in members
            assert all(
                cycle != tenant_cycle and dump != tenant_dump for cycle, dump in members
            )
            for table in ("dump_stamps", "_load_receipts"):
                assert (
                    conn.execute(
                        sql.SQL("SELECT count(*) FROM {} WHERE dump_id=%s").format(
                            sql.Identifier("explore_raw", table)
                        ),
                        (tenant_dump,),
                    ).fetchone()[0]
                    == 0
                )
            assert not conn.execute(
                "SELECT has_schema_privilege(current_user,'raw','USAGE')"
            ).fetchone()[0]
            assert (
                conn.execute(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='mdp' AND has_function_privilege(current_user,p.oid,'EXECUTE')"
                ).fetchone()[0]
                == 0
            )


def test_each_dump_count_is_checked(historical_workbench):
    wb, session, cycles, dumps, mode, db = historical_workbench
    schema = session["scratch_schema"]
    with psycopg.connect(db["admin_warehouse"]) as conn:
        if mode == "list":
            conn.execute(
                "INSERT INTO raw.cycle_inputs(cycle_id,dump_id) VALUES (%s,%s)",
                (cycles[1], dumps[0]),
            )
        # The total stays four, but each dump now has the wrong number of rows.
        conn.execute(
            "UPDATE raw.account_snapshots SET _dump_id=%s WHERE ctid IN "
            "(SELECT ctid FROM raw.account_snapshots WHERE _dump_id=%s LIMIT 1)",
            (dumps[1], dumps[0]),
        )
    wb.grant_inputs(schema, session["user_id"])
    with pytest.raises(ServiceError, match="Choose Preview"):
        inputs(
            wb.settings.workbench_wh_url,
            schema,
            str(cycles[1]),
            input_names(str(cycles[1]), ["account_snapshots"]),
        )


def test_recorded_empty_dump_is_complete(historical_workbench):
    wb, session, cycles, dumps, _, db = historical_workbench
    schema = session["scratch_schema"]
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute("DELETE FROM raw.account_snapshots WHERE _dump_id=%s", (dumps[0],))
        conn.execute(
            "UPDATE raw._load_receipts SET rows=0 WHERE dump_id=%s", (dumps[0],)
        )
    wb.grant_inputs(schema, session["user_id"])
    sources = input_names(str(cycles[0]), ["account_snapshots"])
    inputs(wb.settings.workbench_wh_url, schema, str(cycles[0]), sources)
    with psycopg.connect(wb.settings.workbench_wh_url) as conn:
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(schema)))
        assert (
            conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(
                    sql.Identifier(schema, sources["account_snapshots"])
                )
            ).fetchone()[0]
            == 0
        )


@pytest.fixture(params=["stamp", "list"])
def historical_workbench(catalog_databases, tmp_path, request, workbench_models):
    mode = request.param
    db = catalog_databases
    connection = conninfo_to_dict(db["warehouse_url"])
    connection.update(user="workbench_wh", password="workbench_wh")
    wb = Workbench(
        Settings(
            control_url=db["control_url"],
            control_rt_url=db["admin_control"],
            workbench_wh_url=make_conninfo(**connection),
            workbench_admin_url=db["admin_warehouse"],
            dump_root=tmp_path.as_uri(),
        )
    )
    cycle_a, cycle_b, cycle_future = [uuid4() for _ in range(3)]
    dump_a, dump_b, dump_future = [uuid4() for _ in range(3)]
    PostgresWarehouse(db["warehouse_url"]).ensure(
        "raw.account_snapshots",
        {**LINEAGE, "platform": "text", "platform_account_id": "text", "handle": "text", "followers": "bigint", "snapshot_at": "timestamptz", "source_key": "text"},
    )
    with psycopg.connect(db["admin_warehouse"]) as conn:
        tables = {
            name: MIRRORS[name][0]
            for name in ("cycles", "cycle_inputs", "dump_stamps", "targets")
        }
        for name, columns in tables.items():
            conn.execute(
                sql.SQL("CREATE TABLE raw.{} ({})").format(
                    sql.Identifier(name),
                    sql.SQL(", ").join(
                        sql.SQL("{} {}").format(sql.Identifier(column), sql.SQL(kind))
                        for column, kind in columns.items()
                    ),
                )
            )
        conn.execute("CREATE SCHEMA reference AUTHORIZATION dbt_transform")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA raw TO dbt_transform")
        conn.execute(
            "CREATE TABLE reference.rights_registry(source_key text, learning_eligible boolean, resale_permitted boolean)"
        )
        conn.execute("GRANT SELECT ON reference.rights_registry TO dbt_transform")
        conn.execute(
            "INSERT INTO reference.rights_registry VALUES ('fixture_accounts',true,false)"
        )
        conn.execute(
            "CREATE TABLE marts.mart_enrichment_fixture AS SELECT 'current'::text AS input_ref, 999::bigint AS followers, 'current-build'::text AS _built_by, 'Current label'::text AS display_name"
        )
        target_id = uuid4()
        for index, (cycle, dump, followers) in enumerate(
            (
                (cycle_a, dump_a, 10),
                (cycle_b, dump_b, 25),
                (cycle_future, dump_future, 999),
            ),
            1,
        ):
            conn.execute(
                """INSERT INTO raw.cycles(id,cadence,scope,status,opened_at,closed_at,manifest_mode,close_no)
                VALUES (%s,'hourly','global','closed','2026-01-01', '2026-01-01'::timestamptz + %s * interval '1 hour',%s,%s)""",
                (cycle, index, mode, index if mode == "stamp" else None),
            )
            if mode == "stamp":
                conn.execute(
                    "INSERT INTO raw.dump_stamps(dump_id,scope,close_no,target_table) VALUES (%s,'global',%s,'raw.account_snapshots')",
                    (dump, index),
                )
            else:
                conn.execute(
                    "INSERT INTO raw.cycle_inputs(cycle_id,dump_id,phase) VALUES (%s,%s,'bronze')",
                    (cycle, dump),
                )
            conn.execute(
                "INSERT INTO raw._load_receipts(dump_id,warehouse_id,target_table,generation,claim_token,rows,committed_at) "
                "VALUES (%s,%s,'raw.account_snapshots',1,%s,2,now())",
                (dump, uuid4(), uuid4()),
            )
            revision = uuid4()
            conn.execute(
                "INSERT INTO raw.targets(id,platform,platform_account_id,display_name,_cycle_id,_revision_id) "
                "VALUES (%s,'fixture','history-account',%s,%s,%s)",
                (target_id, f"Label {index}", cycle, revision),
            )
            conn.execute(
                """INSERT INTO raw.account_snapshots(platform,platform_account_id,handle,followers,snapshot_at,source_key,_dump_id,_landed_seq,_target_id,_revision_id)
                SELECT 'fixture','history-account','history_account',%s,'2026-01-01'::timestamptz - n * interval '1 hour','fixture_accounts',%s,%s,%s,%s FROM generate_series(0,1) n""",
                (followers, dump, index, target_id, revision),
            )
        conn.execute(
            "CREATE TABLE staging.stg_control__target_history AS SELECT * FROM raw.targets"
        )
        conn.execute(
            "GRANT SELECT ON staging.stg_control__target_history TO dbt_transform"
        )
    user = "history-test"
    created = wb.create_session(user)
    session = wb.session(created["sessionId"], user)
    schema = session["scratch_schema"]
    try:
        yield (
            wb,
            session,
            (cycle_a, cycle_b, cycle_future),
            (dump_a, dump_b, dump_future),
            mode,
            db,
        )
    finally:
        with psycopg.connect(db["admin_warehouse"]) as conn:
            role = sql.Identifier(schema)
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(role))
            conn.execute(sql.SQL("DROP OWNED BY {}").format(role))
            conn.execute(sql.SQL("DROP ROLE {}").format(role))
        wb.db.close()
        wb.owner.close()


async def test_admin_backtest_uses_each_cycles_inputs(
    historical_workbench, monkeypatch
):
    wb, session, (cycle_a, cycle_b, _), _, mode, db = historical_workbench
    user = session["user_id"]
    schema = session["scratch_schema"]
    draft = wb.draft(
        session,
        "mart_history_probe",
        "select input_ref, followers, _built_by, 7 as draft_marker from {{ ref('mart_enrichment_fixture') }}",
    )

    async def compare(first, second, sql_text=None):
        body = {
            **draft,
            "cycleA": str(first),
            "cycleB": str(second),
            "keyColumns": ["input_ref"],
        }
        if sql_text:
            body["sql"] = sql_text
        run = await wb.submit(session, "backtest", body)
        await wb.tasks[run["runId"]]
        return wb.job(run["runId"], user)

    preview = await wb.build(
        str(uuid4()), session, draft["model"], str(cycle_a), draft=draft
    )
    assert preview["rows"][0]["followers"] == 999
    different = await compare(cycle_a, cycle_b)
    assert different["status"] == "succeeded", different["error"]
    result = different["result"]
    assert result["summary"] == {"added": 0, "removed": 0, "changed": 2}
    assert {row["followers"] for row in result["buildA"]["rows"]} == {10}
    assert {row["followers"] for row in result["buildB"]["rows"]} == {25}
    with psycopg.connect(wb.settings.workbench_wh_url, row_factory=dict_row) as conn:
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(schema)))
        assert (
            conn.execute(result["buildA"]["compiledSql"]).fetchone()["followers"] == 10
        )
    assert '"raw"' not in result["buildA"]["compiledSql"]
    assert '"explore_marts"' not in result["buildA"]["compiledSql"]
    identical = await compare(cycle_a, cycle_a)
    assert identical["status"] == "succeeded", identical["error"]
    assert identical["result"]["summary"] == {
        "added": 0,
        "removed": 0,
        "changed": 0,
    }
    refused = await compare(
        cycle_a, cycle_b, "select * from explore_marts.mart_enrichment_fixture"
    )
    assert refused["status"] == "failed"
    assert refused["error"]["error_class"] == "workbench_history_unavailable"
    assert (
        refused["error"]["next_step"]
        == "Choose Preview to read the current permitted inputs."
    )
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute(
            "DELETE FROM staging.stg_control__target_history WHERE _cycle_id=%s",
            (cycle_b,),
        )
    incomplete = await compare(cycle_a, cycle_b)
    assert incomplete["status"] == "failed"
    assert incomplete["error"]["error_class"] == "workbench_history_unavailable"
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute(
            "INSERT INTO staging.stg_control__target_history SELECT * FROM raw.targets WHERE _cycle_id=%s",
            (cycle_b,),
        )
    if mode == "stamp":
        project = wb.project

        def tenant_project(*args, **kwargs):
            folder = project(*args, **kwargs)
            path = next((folder / "models").rglob(draft["model"] + ".sql"))
            path.write_text(
                path.read_text() + "\n{{ config(tags=['scope:tenant']) }}\n"
            )
            return folder

        monkeypatch.setattr(wb, "project", tenant_project)
        tenant = await compare(cycle_a, cycle_b)
        assert tenant["status"] == "failed"
        assert tenant["error"]["error_class"] == "workbench_history_unavailable"
    with psycopg.connect(wb.settings.workbench_wh_url) as conn:
        for role in (None, schema):
            if role:
                conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            assert not conn.execute(
                "SELECT has_schema_privilege(current_user,'raw','USAGE')"
            ).fetchone()[0]
            assert (
                conn.execute(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mdp' AND has_function_privilege(current_user,p.oid,'EXECUTE')"
                ).fetchone()[0]
                == 0
            )
