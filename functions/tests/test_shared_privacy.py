"""A private value never survives a shared raw, staging or served read."""

import importlib.util
import os
import subprocess
from types import SimpleNamespace

import psycopg
import yaml
from mdp_functions.explore import install
from mdp_functions.exporter import ensure_raw
from mdp_functions.settings import REPO
from mdp_functions.workbench import Workbench
from psycopg.conninfo import conninfo_to_dict


def test_privacy_lint_rejects_unmasked_values_and_unkeyed_hashes():
    spec = importlib.util.spec_from_file_location(
        "privacy_lint", REPO / "ops/ci/privacy.py"
    )
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    for expression, expected in [
        ("author_id", True),
        ("sha256(author_id)", True),
        ("sha256('mdp-local-pseudonym' || ':' || author_id)", False),
        ("cast(null as text)", False),
        (
            "case when n=1 then sha256('mdp-local-pseudonym' || ':' || author_id) else author_id end",
            True,
        ),
    ]:
        manifest = {
            "nodes": {
                "model.test": {
                    "name": "stg_probe",
                    "resource_type": "model",
                    "compiled_code": f"SELECT {expression} AS author_id FROM raw.probe",
                }
            }
        }
        assert (
            bool(lint.check(manifest, {("stg_probe", "author_id"): "pseudonym"}))
            == expected
        )


def test_every_shared_reader_gets_pseudonyms(catalog_databases, tmp_path):
    databases = catalog_databases
    admin = databases["admin_warehouse"]
    ensure_raw(databases["warehouse_url"], REPO / "functions/schemas")
    private = "private-person-fixture"
    from mdp_functions.shared_privacy import scan_shared
    with psycopg.connect(admin) as conn:
        conn.execute(
            psycopg.sql.SQL(
                "GRANT CREATE,TEMPORARY ON DATABASE {} TO dbt_transform"
            ).format(psycopg.sql.Identifier(conn.info.dbname))
        )
        conn.execute("UPDATE mdp.pseudonym_key SET key='mdp-local-pseudonym'")
        conn.execute(
            "INSERT INTO raw.playlist_items(platform,playlist_id,variant,stream,snapshot_id,position,added_by,occurrence,occurrence_key,item_type) VALUES ('fixture','list','','full','snapshot',1,%s,1,'item','track')",
            (private,),
        )
        conn.execute(
            "INSERT INTO raw.playlist_snapshots(platform,playlist_id,variant,stream,snapshot_id,owner_id,owner_name,owner_class,owner_class_observed,fetch_surface) VALUES ('fixture','list','','full','snapshot',%s,%s,'user','user','fixture')",
            (private, private),
        )
        install(conn)
    parts = conninfo_to_dict(admin)
    profile = tmp_path / "profiles"
    profile.mkdir()
    (profile / "profiles.yml").write_text(
        yaml.safe_dump(
            {
                "music_data_platform": {
                    "target": "privacy",
                    "outputs": {
                        "privacy": {
                            "type": "postgres",
                            "host": parts["host"],
                            "port": int(parts["port"]),
                            "dbname": parts["dbname"],
                            "user": "dbt_transform",
                            "pass": "dbt_transform",
                            "schema": "dbt",
                            "threads": 2,
                            "sslmode": parts.get("sslmode", "prefer"),
                        }
                    },
                }
            }
        )
    )
    env = dict(
        os.environ,
        DBT_TARGET_PATH=str(tmp_path / "target"),
        DBT_LOG_PATH=str(tmp_path / "logs"),
        DBT_MDP_SCOPE="global",
    )

    def dbt(command, models, scope="global"):
        result = subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(REPO / "dbt"),
                "dbt",
                command,
                "--project-dir",
                str(REPO / "dbt"),
                "--profiles-dir",
                str(profile),
                "--select",
                *models,
                "--vars",
                "{dry_run: true, tenant_slug: privacy}",
            ],
            cwd=REPO,
            env=dict(env, DBT_MDP_SCOPE=scope),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-1000:]

    dbt("seed", ["rights_registry"])
    dbt(
        "run",
        [
            "stg_playlist__items",
            "stg_playlist__snapshots",
        ],
    )
    import hashlib

    expected = hashlib.sha256(("mdp-local-pseudonym:" + private).encode()).hexdigest()
    wb_role = "wb_privacy_" + parts["dbname"].rsplit("_", 1)[-1]
    with psycopg.connect(admin) as conn:
        conn.execute(
            psycopg.sql.SQL("CREATE ROLE {} NOLOGIN").format(
                psycopg.sql.Identifier(wb_role)
            )
        )
    try:
        Workbench.grant_inputs(
            SimpleNamespace(settings=SimpleNamespace(workbench_admin_url=admin)),
            wb_role,
        )
        with psycopg.connect(admin) as conn:
            # Inspect every accessible relation and every physical column, not a model list.
            seen, checked = scan_shared(
                conn,
                ("analyst_ro", "explorer_ro", "workbench_wh", wb_role),
                (private,),
            )
            assert seen > 20 and checked > 100
            for role in ("analyst_ro", "explorer_ro", wb_role):
                with conn.transaction():
                    conn.execute(
                        psycopg.sql.SQL("SET LOCAL ROLE {}").format(
                            psycopg.sql.Identifier(role)
                        )
                    )
                    assert conn.execute(
                        "SELECT added_by FROM explore_staging.stg_playlist__items"
                    ).fetchall() == [(expected,)]
                    assert conn.execute(
                        "SELECT owner_id,owner_name,owner_key FROM explore_staging.stg_playlist__snapshots"
                    ).fetchall() == [(None, None, expected)]
                    if role == "explorer_ro":
                        assert conn.execute(
                            "SELECT added_by FROM explore_raw.playlist_items"
                        ).fetchall() == [(expected,)]
    finally:
        with psycopg.connect(admin) as conn:
            conn.execute(
                psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(wb_role))
            )


def test_unknown_shared_columns_never_inherit_schema_grants(catalog_databases):
    import pytest
    from mdp_functions.shared_privacy import scan_shared

    with psycopg.connect(catalog_databases["admin_warehouse"]) as conn:
        conn.execute("CREATE TABLE staging.privacy_unknown(person text, _extra jsonb)")
        conn.execute(
            "INSERT INTO staging.privacy_unknown VALUES ('private-person-fixture','{\"username\":\"private-person-fixture\"}')"
        )
        conn.commit()
        try:
            for role in ("analyst_ro", "explorer_ro", "workbench_wh"):
                with conn.transaction():
                    conn.execute(
                        psycopg.sql.SQL("SET LOCAL ROLE {}").format(
                            psycopg.sql.Identifier(role)
                        )
                    )
                    with (
                        pytest.raises(psycopg.errors.InsufficientPrivilege),
                        conn.transaction(),
                    ):
                        conn.execute("SELECT * FROM staging.privacy_unknown")
                    assert conn.execute(
                        "SELECT * FROM explore_staging.privacy_unknown"
                    ).fetchall() == [(None, None)]
            scan_shared(conn, forbidden=("private-person-fixture",))
            conn.execute(
                "ALTER TABLE staging.privacy_unknown RENAME COLUMN person TO handle"
            )
            scan_shared(conn, forbidden=("private-person-fixture",))
            # The scanner checks definitions, so an empty relation cannot hide an unsafe column.
            conn.execute(
                "CREATE TABLE staging.privacy_empty(person text, _extra jsonb)"
            )
            conn.execute("GRANT SELECT ON staging.privacy_empty TO analyst_ro")
            with pytest.raises(AssertionError, match="undeclared or personal"):
                scan_shared(conn)
        finally:
            conn.execute(
                "DROP TABLE IF EXISTS staging.privacy_unknown,staging.privacy_empty CASCADE"
            )
            conn.commit()


def test_retained_text_never_reaches_the_enrichment_worker(monkeypatch):
    import pytest
    from mdp_functions.derived import require_shared_input
    from mdp_functions.errors import ServiceError

    monkeypatch.setattr("mdp_functions.relation_labels.relation_label", lambda *args: {"shared_privacy": {"text": "private"}})
    with pytest.raises(ServiceError, match="private to dbt"):
        require_shared_input("tenant_tenant_a_staging.stg_private_fixture")
