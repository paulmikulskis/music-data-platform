"""Isolated databases using the checked-in Drizzle schema, never runtime control DDL."""

import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from mdp_functions.runs import Runtime
from mdp_functions.settings import REPO, Settings
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # Any test on the disposable databases needs PostgreSQL; `-m "not docker"` runs the rest.
    for item in items:
        if "databases" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.docker)


def url_database(url: str, database: str) -> str:
    options = conninfo_to_dict(url)
    options["dbname"] = database
    return make_conninfo(**options)


def copy_source_inputs(root: Path) -> None:
    """Copy the declarations that source generation requires in a fixture project."""
    for relative in ("dbt/seeds/rights_registry.csv",):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO / relative).read_bytes())


def isolated_databases() -> Iterator[dict[str, str]]:
    """Provision a fresh pair for tests that need an empty warehouse."""
    control = os.environ.get("MDP_CONTROL_URL")
    if not control:
        pytest.skip("Docker integration tests require MDP_CONTROL_URL")
    admin_url = os.environ["MDP_CONTROL_ADMIN_URL"]
    warehouse_url = os.environ["MDP_WAREHOUSE_URL"]
    suffix = uuid4().hex[:12]
    control_name, warehouse_name = (
        f"mdp_test_control_{suffix}",
        f"mdp_test_warehouse_{suffix}",
    )
    admin_control = url_database(admin_url, control_name)
    admin_warehouse = url_database(admin_url, warehouse_name)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        # pg_dump refuses a newer server. Check before creating databases so a
        # local client mismatch gives setup guidance instead of a subprocess error.
        server_major = admin.info.server_version // 10000
        client = shutil.which("pg_dump")
        version = subprocess.run(
            [client, "--version"], check=True, capture_output=True, text=True
        ).stdout.strip() if client else "pg_dump is missing"
        if not client or int(version.split()[2].split(".")[0]) < server_major:
            pytest.skip(
                f"{version}; schema copies require pg_dump {server_major} or newer. "
                f"Install postgresql-client-{server_major}, then run "
                f"PATH=/usr/lib/postgresql/{server_major}/bin:$PATH "
                "uv run --project functions pytest functions/tests -m docker -q "
                "with the same MDP database URLs."
            )
        for name in (control_name, warehouse_name):
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        # Copy schema via pg_dump so other workers need not disconnect from the template DB.
        template = url_database(admin_url, conninfo_to_dict(control)["dbname"])
        dump = subprocess.run(
            ["pg_dump", "--schema-only", "--schema=control", "--dbname", template],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "--dbname", admin_control],
            input=dump.stdout,
            check=True,
            capture_output=True,
        )
        with psycopg.connect(admin_control) as conn:
            from mdp_functions.runbooks import seed_runbooks

            seed_runbooks(conn)
            conn.execute(
                "INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) VALUES ('postgres',%s,'MDP_WAREHOUSE_URL',true)",
                (warehouse_name,),
            )
        with psycopg.connect(admin_warehouse) as conn:
            conn.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO loader_wh").format(sql.Identifier(warehouse_name)))
            conn.execute("CREATE SCHEMA raw AUTHORIZATION loader_wh")
            conn.execute(
                "GRANT USAGE ON SCHEMA raw TO service_read,reader_wh,dbt_transform"
            )
            conn.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE loader_wh IN SCHEMA raw GRANT SELECT ON TABLES TO service_read,dbt_transform"
            )
        yield {
            "control_url": url_database(control, control_name),
            "warehouse_url": url_database(warehouse_url, warehouse_name),
            "service_read_url": url_database(
                os.environ["MDP_SERVICE_READ_URL"], warehouse_name
            ),
            "admin_control": admin_control,
            "admin_warehouse": admin_warehouse,
        }
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            for name in (control_name, warehouse_name):
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )


@pytest.fixture(scope="session")
def databases() -> Iterator[dict[str, str]]:
    yield from isolated_databases()


@pytest.fixture
def catalog_databases(databases: dict[str, str]) -> Iterator[dict[str, str]]:
    """Keep privacy DDL, event triggers and private rows out of other tests' warehouse."""
    name = "mdp_test_catalog_" + uuid4().hex[:12]
    admin_url = os.environ["MDP_CONTROL_ADMIN_URL"]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    isolated = {
        key: url_database(url, name)
        if "warehouse" in key or key == "service_read_url"
        else url
        for key, url in databases.items()
    }
    try:
        with psycopg.connect(isolated["admin_warehouse"]) as conn:
            for filename in (
                "10-warehouse-grants.sql",
                "15-label-catalog.sql",
                "16-label-definitions.sql",
                "17-explore-views.sql",
            ):
                conn.execute(
                    (REPO / "ops/fly/postgres/boot/init" / filename).read_text()
                )
        yield isolated
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
async def rt(databases: dict[str, str], tmp_path: Path) -> AsyncIterator[Runtime]:
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "TRUNCATE control.cycle,control.streamline,control.target_set,control.dbt_job,control.budget,control.scope_close CASCADE"
        )
        conn.execute(
            "INSERT INTO control.runner_mode(id,runner) VALUES (true,'core') ON CONFLICT(id) DO UPDATE SET runner='core'"
        )
        set_id = conn.execute(
            "INSERT INTO control.target_set(kind,name) VALUES ('account','Fixture accounts') RETURNING id"
        ).fetchone()[0]
        for n in (1, 2):
            conn.execute(
                "INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) VALUES (%s,'fixture',%s,%s,'resolved',now())",
                (set_id, f"acceptance-{n:03}", f"acceptance_account_{n:03}"),
            )
        for cadence in ("hourly", "daily", "weekly"):
            conn.execute(
                "INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES (%s,'core',%s,'global')",
                (f"local:{cadence}", cadence),
            )
    # Close numbers restart with control, so the warehouse's scope-wide stamps restart too.
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        for table in ("raw.dump_stamps",):
            if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
                conn.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(*table.split("."))))
    settings = Settings(
        **{k: v for k, v in databases.items() if not k.startswith("admin")},
        dbt_cloud_verify=False,
        fixture=True,
        service_token=uuid4().hex,
        dump_root=(tmp_path / "dumps").as_uri(),
        schema_root=tmp_path / "schemas",
        lease_s=0.3,
        load_timeout_s=0.2,
        http_backoff_s=0,
    )
    import importlib
    from unittest.mock import patch
    with patch.dict(os.environ, {"MDP_FIXTURE_MODE": "1"}):
        from mdp_functions.registry import REGISTRY

        module = importlib.import_module("mdp_functions.sources.fixture_accounts.function")
        if "fixture_accounts" not in REGISTRY:
            importlib.reload(module)
    runtime = Runtime(settings)
    runtime.initialize()
    yield runtime
    await runtime.close()


@pytest.fixture
def evidence_dir(tmp_path: Path) -> Path:
    path = tmp_path / "evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def bound(rt: Runtime, source: str = "fixture_accounts") -> tuple[str, dict]:
    from mdp_functions.registry import REGISTRY
    from mdp_functions.targets import export_targets

    cadence = REGISTRY[source].cadence
    dbt_id = "local:" + uuid4().hex
    binding = await rt.cycles.bind_cycle(
        cadence, "global", dbt_id, "scheduled", "local:" + cadence, runner="core"
    )
    if REGISTRY[source].targets:
        export_targets(rt.db, rt.warehouse, binding["cycle_id"])
    run = rt.admit(source, cadence=cadence, dbt_run_id=dbt_id)
    return dbt_id, run
