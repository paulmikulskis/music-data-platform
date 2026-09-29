"""Draft refs read stored inputs with the session's current grants."""

import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from mdp_functions.errors import ServiceError
from mdp_functions.settings import Settings
from mdp_functions.workbench import Workbench
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from workbench_model_fixture import (
    workbench_models as workbench_models,  # noqa: PLC0414
)


@pytest.mark.parametrize("user", ["ref-test", "staff:ref-test"])
async def test_draft_refs_use_stored_inputs(catalog_databases, tmp_path, user, workbench_models):
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
    with psycopg.connect(db["admin_warehouse"]) as conn:
        conn.execute("""CREATE TABLE marts.mart_enrichment_fixture AS
            SELECT 'stored'::text AS input_ref, 42::bigint AS followers,
              'deployed-build'::text AS _built_by""")
        conn.execute("""CREATE TABLE intermediate.int_track_identity AS
            SELECT 'stored'::text AS platform, 'track'::text AS platform_track_id""")
    created = wb.create_session(user)
    session = wb.session(created["sessionId"], user)
    schema = session["scratch_schema"]
    try:
        for upstream, columns in (
            ("mart_enrichment_fixture", "input_ref, followers, _built_by"),
            ("int_track_identity", "platform, platform_track_id"),
        ):
            draft = wb.draft(
                session,
                "mart_ref_probe",
                f"select {columns}, 7 as draft_marker from {{{{ ref('{upstream}') }}}}",
            )
            run_id = str(uuid4())
            result = await wb.build(
                run_id, session, draft["model"], str(uuid4()), draft=draft
            )
            assert result["rows"] and result["rows"][0]["draft_marker"] == 7
            assert '"explore_' in result["compiledSql"]
            assert '"raw"' not in result["compiledSql"]
            # The same SQL works through Run's read-only execution path.
            assert wb.query(session, result["compiledSql"])["rows"] == result["rows"]
            manifest = json.loads(
                (
                    Path("/tmp/mdp-workbench") / run_id / "preview/run_results.json"
                ).read_text()
            )
            built = [
                r["unique_id"]
                for r in manifest["results"]
                if r["unique_id"].startswith("model.")
            ]
            assert built == ["model.music_data_platform.mart_ref_probe"]
        with psycopg.connect(wb.settings.workbench_wh_url) as conn:
            assert not conn.execute(
                "SELECT has_schema_privilege(current_user,'raw','USAGE')"
            ).fetchone()[0]
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(schema)))
            assert not conn.execute(
                "SELECT has_schema_privilege(current_user,'raw','USAGE')"
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM raw.cycles")
        # A ref outside the permitted input schemas fails with recovery guidance.
        denied = wb.draft(
            session, "mart_ref_probe", "select * from {{ ref('bronze_close__hourly') }}"
        )
        with pytest.raises(ServiceError) as caught:
            await wb.build(
                str(uuid4()), session, denied["model"], str(uuid4()), draft=denied
            )
        assert caught.value.error_class == "workbench_permission_denied"
        assert caught.value.next_step and caught.value.runbook
        assert "Use " in str(caught.value) or "/explorer" in str(caught.value)
    finally:
        with psycopg.connect(db["admin_warehouse"]) as conn:
            role = sql.Identifier(schema)
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(role))
            conn.execute(sql.SQL("DROP OWNED BY {}").format(role))
            conn.execute(sql.SQL("DROP ROLE {}").format(role))
        wb.db.close()
        wb.owner.close()
