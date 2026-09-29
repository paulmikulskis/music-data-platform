"""Attack and lifecycle proofs for individual warehouse scratch space."""

import psycopg
import pytest
from mdp_functions.sandbox import create_sandbox
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from test_sandbox import sandbox_databases as sandbox_databases  # noqa: PLC0414
from test_sandbox import sandbox_logins as sandbox_logins  # noqa: PLC0414


def test_owner_cannot_rename_sandbox_out_of_its_boundary(sandbox_logins):
    schema, (owner, _) = sandbox_logins
    # A rename would otherwise evade prefix guards on later tables and grants.
    with psycopg.connect(owner) as conn:
        conn.execute("SAVEPOINT rename_probe")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    sql.SQL("ALTER SCHEMA {} RENAME TO tenant_escaped_marts").format(
                        sql.Identifier(schema)
                    )
                )
        finally:
            conn.execute("ROLLBACK TO SAVEPOINT rename_probe")


def test_privacy_scan_checks_sandbox_values_without_a_managed_contract(
    sandbox_databases, sandbox_logins
):
    from mdp_functions.shared_privacy import scan_shared

    schema, (owner, _) = sandbox_logins
    with psycopg.connect(owner) as conn:
        conn.execute(
            sql.SQL("CREATE TABLE {}.copy(person text)").format(sql.Identifier(schema))
        )
        conn.execute(
            sql.SQL("INSERT INTO {}.copy VALUES ('pseudonym')").format(sql.Identifier(schema))
        )
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as admin:
        assert scan_shared(admin, forbidden=("private-person-fixture",))[0] > 0
    with psycopg.connect(owner) as conn:
        conn.execute(
            sql.SQL("INSERT INTO {}.copy VALUES ('private-person-fixture')").format(sql.Identifier(schema))
        )
    with (
        psycopg.connect(sandbox_databases["admin_warehouse"]) as admin,
        pytest.raises(AssertionError, match="private value"),
    ):
        scan_shared(admin, forbidden=("private-person-fixture",))


@pytest.mark.parametrize(
    "target",
    [
        "reader_wh",
        "showcase_wh",
        "api_key_reader",
        "dbt_transform",
        "loader_wh",
        "service_read",
        "functions_rt",
        "control_rt",
    ],
)
def test_runtime_grants_and_defaults_are_refused(sandbox_logins, target):
    schema, (owner, _) = sandbox_logins
    with psycopg.connect(owner, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE TABLE {}.probe(id int)").format(sql.Identifier(schema))
        )
        for statement in (
            f"GRANT SELECT ON {schema}.probe TO {target}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {target}",
            f"ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO {target}",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "GRANT SELECT ON {schema}.probe TO analyst_ro WITH GRANT OPTION",
        "GRANT USAGE ON SCHEMA {schema} TO analyst_ro WITH GRANT OPTION",
        "GRANT SELECT (id) ON {schema}.probe TO analyst_ro WITH GRANT OPTION",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO analyst_ro WITH GRANT OPTION",
    ],
)
def test_team_cannot_receive_grant_option(sandbox_logins, statement):
    schema, (owner, _) = sandbox_logins
    with psycopg.connect(owner, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE TABLE {}.probe(id int)").format(sql.Identifier(schema))
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement.format(schema=schema))


def test_explorer_copy_is_not_readable_or_exportable_by_analyst(
    sandbox_databases, sandbox_logins, tmp_path
):
    from mdp_functions.warehouse_snapshot import snapshot
    from psycopg.rows import dict_row

    schema, (analyst, explorer) = sandbox_logins
    restricted = schema + "_tenant"
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
        create_sandbox(
            conn,
            restricted.removeprefix("sandbox_"),
            conninfo_to_dict(explorer)["user"],
        )
    try:
        with psycopg.connect(explorer) as conn:
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {}.copy AS SELECT 'pseudonym'::text AS person, 'tenant'::text AS tenant_id"
                ).format(sql.Identifier(restricted))
            )
        with psycopg.connect(analyst, autocommit=True, row_factory=dict_row) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    sql.SQL("SELECT * FROM {}.copy").format(sql.Identifier(restricted))
                )
            with pytest.raises(ValueError, match="No readable relations"):
                snapshot(conn, out=tmp_path / "denied.duckdb", schemas=[restricted])
    finally:
        with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(restricted))
            )


def test_login_limits_and_quota_freeze_restore(sandbox_databases, sandbox_logins):
    from mdp_functions import sandbox

    schema, (owner, _) = sandbox_logins
    role = conninfo_to_dict(owner)["user"]
    with psycopg.connect(
        sandbox_databases["admin_warehouse"], autocommit=True
    ) as admin:
        limit, settings = admin.execute(
            "SELECT rolconnlimit,rolconfig FROM pg_roles WHERE rolname=%s", (role,)
        ).fetchone()
        assert limit > 0
        assert all(
            any(s.startswith(key + "=") for s in settings)
            for key in [
                "statement_timeout",
                "temp_file_limit",
                "idle_in_transaction_session_timeout",
            ]
        )
        admin.execute(
            "UPDATE mdp.sandbox_state SET quota_bytes=1 WHERE schema_name=%s", (schema,)
        )
        with psycopg.connect(owner) as conn:
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {}.big AS SELECT generate_series(1,100) AS id"
                ).format(sql.Identifier(schema))
            )
        sandbox.maintain(admin)
        with psycopg.connect(owner, autocommit=True) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    sql.SQL("CREATE TABLE {}.another(id int)").format(
                        sql.Identifier(schema)
                    )
                )
            conn.execute(sql.SQL("DROP TABLE {}.big").format(sql.Identifier(schema)))
        sandbox.maintain(admin)
        with psycopg.connect(owner) as conn:
            conn.execute(
                sql.SQL("CREATE TABLE {}.again(id int)").format(sql.Identifier(schema))
            )


@pytest.mark.parametrize("upstream", ["staging", "explore_raw"])
@pytest.mark.parametrize("kind", ["view", "function", "procedure"])
def test_persistent_views_cannot_block_widening(
    sandbox_databases, sandbox_logins, upstream, kind
):
    schema, (owner, _) = sandbox_logins
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
        conn.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(upstream))
        )
        conn.execute(
            sql.SQL("CREATE TABLE {}.widen_probe(id int)").format(
                sql.Identifier(upstream)
            )
        )
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO analyst_ro").format(
                sql.Identifier(upstream)
            )
        )
        conn.execute(
            sql.SQL("GRANT SELECT ON {}.widen_probe TO analyst_ro").format(
                sql.Identifier(upstream)
            )
        )
    with (
        psycopg.connect(owner, autocommit=True) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        definition = (
            "CREATE VIEW {}.dependent AS SELECT * FROM {}.widen_probe"
            if kind == "view"
            else "CREATE FUNCTION {}.dependent() RETURNS SETOF int LANGUAGE SQL BEGIN ATOMIC SELECT id FROM {}.widen_probe; END"
        )
        if kind == "procedure":
            definition = "CREATE PROCEDURE {}.dependent() LANGUAGE SQL BEGIN ATOMIC SELECT id FROM {}.widen_probe; END"
        conn.execute(
            sql.SQL(definition).format(sql.Identifier(schema), sql.Identifier(upstream))
        )
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
        conn.execute(
            sql.SQL("ALTER TABLE {}.widen_probe ALTER COLUMN id TYPE bigint").format(
                sql.Identifier(upstream)
            )
        )


def test_one_privacy_aware_grant_implementation(sandbox_databases, sandbox_logins):
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
        body = conn.execute(
            "SELECT prosrc FROM pg_proc WHERE oid='mdp.grant_warehouse_table(oid)'::regprocedure"
        ).fetchone()[0]
        assert "catalog.share_relation" in body
        conn.execute("CREATE TABLE staging.unknown_payload(secret text)")
        assert not conn.execute(
            "SELECT has_table_privilege('analyst_ro','staging.unknown_payload','SELECT')"
        ).fetchone()[0]


def test_private_relation_cannot_leak_build_stamp(sandbox_databases, sandbox_logins):
    _, (owner, _) = sandbox_logins
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
        conn.execute("CREATE TABLE staging.private_stamp(secret text)")
    with (
        psycopg.connect(owner, autocommit=True) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="readable relation"),
    ):
        conn.execute("SELECT catalog.snapshot_stamp('staging.private_stamp')")


def test_legacy_delegation_cannot_be_regranted_and_is_alerted(
    sandbox_databases, sandbox_logins
):
    from mdp_functions.sandbox import maintain

    schema, (owner, _) = sandbox_logins
    with psycopg.connect(owner) as conn:
        conn.execute(
            sql.SQL("CREATE TABLE {}.probe(id int)").format(sql.Identifier(schema))
        )
    with psycopg.connect(
        sandbox_databases["admin_warehouse"], autocommit=True
    ) as admin:
        admin.execute(
            sql.SQL("GRANT SELECT ON {}.probe TO analyst_ro WITH GRANT OPTION").format(
                sql.Identifier(schema)
            )
        )
        with psycopg.connect(owner, autocommit=True) as conn:
            conn.execute("SET ROLE analyst_ro")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    sql.SQL("GRANT SELECT ON {}.probe TO reader_wh").format(
                        sql.Identifier(schema)
                    )
                )
        assert (schema, "sandbox_acl_alert") in maintain(admin)
        admin.execute(
            sql.SQL(
                "REVOKE GRANT OPTION FOR SELECT ON {}.probe FROM analyst_ro CASCADE"
            ).format(sql.Identifier(schema))
        )


def test_archive_preserves_rows_before_removal(
    sandbox_databases, sandbox_logins, tmp_path
):
    import json

    from mdp_functions.sandbox_accounts import archive

    schema, (owner, _) = sandbox_logins
    with psycopg.connect(owner) as conn:
        conn.execute(
            sql.SQL(
                "CREATE TABLE {}.probe AS SELECT false AS learning_eligible,'pseudonym'::text AS person"
            ).format(sql.Identifier(schema))
        )
        conn.execute(
            sql.SQL("CREATE VIEW {}.draft AS SELECT * FROM {}.probe").format(
                sql.Identifier(schema), sql.Identifier(schema)
            )
        )
    with psycopg.connect(
        sandbox_databases["admin_warehouse"], autocommit=True
    ) as admin:
        result = archive(admin, schema, tmp_path)
        assert "Open archive.json" in result
        assert admin.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone() == (
            None,
        )
    manifests = list(tmp_path.rglob("_mdp_snapshot.json"))
    assert (
        len(manifests) == 1
        and len(json.loads(manifests[0].read_text())["relations"]) == 2
    )


def test_empty_sandbox_archive_still_has_manifest(
    sandbox_databases, sandbox_logins, tmp_path
):
    import json

    from mdp_functions.sandbox_accounts import archive

    schema, _ = sandbox_logins
    with psycopg.connect(
        sandbox_databases["admin_warehouse"], autocommit=True
    ) as admin:
        archive(admin, schema, tmp_path)
        assert admin.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone() == (
            None,
        )
    manifests = list(tmp_path.rglob("_mdp_snapshot.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["relations"] == []


def test_archive_failure_keeps_data_and_names_next_step(
    sandbox_databases, sandbox_logins, tmp_path, monkeypatch
):
    from mdp_functions import warehouse_snapshot
    from mdp_functions.sandbox_accounts import archive

    schema, (owner, _) = sandbox_logins

    def failed_copy(*args, **kwargs):
        raise OSError("fixture disk full")

    monkeypatch.setattr(warehouse_snapshot, "snapshot", failed_copy)
    with psycopg.connect(
        sandbox_databases["admin_warehouse"], autocommit=True
    ) as admin:
        with pytest.raises(
            ValueError, match="login stays disabled and data is kept"
        ):
            archive(admin, schema, tmp_path)
        assert admin.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone()[0]
        assert admin.execute(
            "SELECT rolcanlogin FROM pg_roles WHERE rolname=%s",
            (conninfo_to_dict(owner)["user"],),
        ).fetchone() == (False,)


@pytest.mark.parametrize("kind", ["view", "function"])
def test_archive_keeps_external_dependents_and_notifies_owner(
    sandbox_databases, sandbox_logins, tmp_path, kind
):
    from mdp_functions.sandbox_accounts import archive

    schema, (owner, _) = sandbox_logins
    peer = schema + "_peer"
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
        create_sandbox(
            conn, peer.removeprefix("sandbox_"), conninfo_to_dict(owner)["user"]
        )
    try:
        with psycopg.connect(owner) as conn:
            conn.execute(
                sql.SQL("CREATE TABLE {}.probe(id int)").format(sql.Identifier(schema))
            )
            definition = (
                "CREATE VIEW {}.dependent AS SELECT * FROM {}.probe"
                if kind == "view"
                else "CREATE FUNCTION {}.dependent() RETURNS SETOF int LANGUAGE SQL BEGIN ATOMIC SELECT id FROM {}.probe; END"
            )
            conn.execute(
                sql.SQL(definition).format(sql.Identifier(peer), sql.Identifier(schema))
            )
        with psycopg.connect(
            sandbox_databases["admin_warehouse"], autocommit=True
        ) as admin:
            with pytest.raises(ValueError, match="Other objects depend"):
                archive(admin, schema, tmp_path)
            assert admin.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone()[0]
            assert admin.execute(
                "SELECT code FROM mdp.sandbox_notice WHERE schema_name=%s", (peer,)
            ).fetchone() == ("sandbox_archive_blocked",)
    finally:
        with psycopg.connect(sandbox_databases["admin_warehouse"]) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(peer))
            )


def test_archive_transfer_preserves_the_recipient_sharing_boundary(
    sandbox_databases, sandbox_logins, tmp_path
):
    from mdp_functions.sandbox_accounts import archive

    schema, (owner, explorer) = sandbox_logins
    with psycopg.connect(owner) as conn:
        conn.execute(
            sql.SQL(
                "CREATE TABLE {}.probe AS SELECT false AS learning_eligible"
            ).format(sql.Identifier(schema))
        )
        conn.execute(
            sql.SQL(
                "CREATE FUNCTION {}.saved() RETURNS int LANGUAGE SQL AS 'SELECT 1'"
            ).format(sql.Identifier(schema))
        )
    with psycopg.connect(
        sandbox_databases["admin_warehouse"], autocommit=True
    ) as admin:
        archive(admin, schema, tmp_path, conninfo_to_dict(explorer)["user"])
        assert admin.execute(
            "SELECT owner_role,is_explorer FROM mdp.sandbox_state WHERE schema_name=%s",
            (schema,),
        ).fetchone() == (conninfo_to_dict(explorer)["user"], True)
        assert not admin.execute(
            "SELECT has_table_privilege('analyst_ro',%s,'SELECT')", (schema + ".probe",)
        ).fetchone()[0]
        assert (
            admin.execute(
                "SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE pronamespace=to_regnamespace(%s) AND proname='saved'",
                (schema,),
            ).fetchone()[0]
            == conninfo_to_dict(explorer)["user"]
        )
    with psycopg.connect(explorer) as conn:
        assert conn.execute(
            sql.SQL("SELECT * FROM {}.probe").format(sql.Identifier(schema))
        ).fetchone() == (False,)
