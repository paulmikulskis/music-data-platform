"""Check Library function grants after fresh boot and an existing-volume upgrade."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.docker


@pytest.mark.parametrize("upgrade", [True, False], ids=["existing-volume", "fresh"])
def test_trigram_read_grants(upgrade):
    admin_url = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin_url:
        pytest.skip(
            "Set MDP_CONTROL_ADMIN_URL to a disposable local PostgreSQL server."
        )
    options = conninfo_to_dict(admin_url)
    assert options.get("host") in {"localhost", "127.0.0.1", "::1"}
    name = "mdp_search_grants_" + uuid4().hex[:12]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    options["dbname"] = name
    bootstrap = (
        ROOT / "ops/fly/postgres/boot/init/10-warehouse-grants.sql"
    ).read_text()
    try:
        with psycopg.connect(make_conninfo(**options), autocommit=True) as conn:
            if upgrade:
                conn.execute(bootstrap)
                # The older volume has the hardened defaults but no trigram extension.
                conn.execute("DROP EXTENSION pg_trgm")
                conn.execute(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
                )
            conn.execute(bootstrap)
            conn.execute("CREATE TABLE marts.search_grant_fixture(display_text text)")
            conn.execute("INSERT INTO marts.search_grant_fixture VALUES ('Night song')")
            conn.execute(
                "CREATE INDEX ON marts.search_grant_fixture USING gin(lower(display_text) gin_trgm_ops)"
            )
            conn.execute(
                "GRANT USAGE ON SCHEMA marts TO showcase_wh, analyst_ro, reader_wh"
            )
            conn.execute(
                "GRANT SELECT ON marts.search_grant_fixture TO showcase_wh, analyst_ro, reader_wh"
            )
            query = "SELECT display_text FROM marts.search_grant_fixture WHERE lower(display_text) % lower('night') ORDER BY similarity(lower(display_text), lower('night')) DESC LIMIT 20"
            for role in ("showcase_wh", "analyst_ro", "reader_wh"):
                with conn.transaction():
                    conn.execute(
                        sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role))
                    )
                    conn.execute("SET LOCAL enable_seqscan = off")
                    conn.execute("SET LOCAL statement_timeout = '1s'")
                    assert conn.execute(query).fetchall() == [("Night song",)]
                    plan = conn.execute("EXPLAIN " + query).fetchall()
                    assert "Bitmap Index Scan" in str(plan)
                    # The read needs two functions, not the rest of the extension.
                    assert conn.execute(
                        "SELECT has_function_privilege(current_user, 'public.word_similarity(text,text)', 'EXECUTE')"
                    ).fetchone() == (False,)
            assert conn.execute(
                "SELECT has_function_privilege('workbench_wh', 'public.similarity(text,text)', 'EXECUTE')"
            ).fetchone() == (False,)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )
