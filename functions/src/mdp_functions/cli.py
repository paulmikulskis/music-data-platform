"""Service commands and the local export → function → close fixture loop."""

import asyncio
import os
import subprocess
from typing import Any
from uuid import uuid4

import psycopg
import typer
from psycopg import sql
from rich.console import Console
from rich.table import Table

from mdp_functions import __version__
from mdp_functions.jev_cli import app as jev_app
from mdp_functions.runbooks import seed_runbooks
from mdp_functions.settings import REPO, Settings
from mdp_functions.warehouse_cli import app as warehouse_app

app = typer.Typer(no_args_is_help=True)
control_app = typer.Typer()
registry_app = typer.Typer()
sources_app = typer.Typer()
openapi_app = typer.Typer()
app.add_typer(jev_app, name="jev")
app.add_typer(warehouse_app, name="warehouse")
app.add_typer(control_app, name="control")
app.add_typer(registry_app, name="registry")
app.add_typer(sources_app, name="sources")
app.add_typer(openapi_app, name="openapi")
workbench_app = typer.Typer()
app.add_typer(workbench_app, name="workbench")


@workbench_app.command("serve")
def workbench_serve(host: str = "127.0.0.1", port: int = 8085) -> None:
    from mdp_functions.workbench import main

    main(host, port)


def show_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=show_version, is_eager=True
    ),
) -> None:
    """Music Data Platform functions."""


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def ready(ctx: typer.Context) -> None:
    """Check this branch's changes locally."""
    raise typer.Exit(subprocess.call(["bash", str(REPO / "ops/ready.sh"), *ctx.args]))


def seed_local_budgets(conn: psycopg.Connection) -> None:
    """Seed explicitly configured defaults once; never overwrite operator caps."""

    configured = [
        (scope, os.environ.get(f"MDP_BUDGET_{scope.upper()}_CENTS"))
        for scope in ("global", "streamline", "llm_step")
    ]
    if not any(value is not None for _, value in configured):
        return
    ceiling = int(os.environ["MDP_BUDGET_CEILING_CENTS"])
    # Serialize the check/insert even though budget has no natural unique key.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('mdp-local-budget-seed'))")
    for scope, value in configured:
        if value is None:
            continue
        cap = int(value)
        if cap < 0 or cap > ceiling:
            raise ValueError("Local budget cap must be nonnegative and within ceiling")
        identities = (
            [(None,)]
            if scope == "global"
            else conn.execute(
                sql.SQL("SELECT id FROM control.{}").format(sql.Identifier(scope))
            ).fetchall()
        )
        for (scope_id,) in identities:
            conn.execute(
                """INSERT INTO control.budget(scope,scope_id,period,cap_cents,soft_pct,hard_action,ceiling_cents)
                SELECT %s,%s,'monthly',%s,80,'warn',%s WHERE NOT EXISTS
                (SELECT 1 FROM control.budget WHERE scope=%s AND scope_id IS NOT DISTINCT FROM %s::uuid AND period='monthly')""",
                (scope, scope_id, cap, ceiling, scope, scope_id),
            )


@control_app.command("init")
def init_local(local: bool = typer.Option(False, "--local")) -> None:
    if not local:
        raise typer.BadParameter("Use --local to initialize local control. For deployment setup, follow docs/operating.md.")
    subprocess.run(["bash", str(REPO / "ops/local/init.sh")], check=True)

    with psycopg.connect(
        os.environ.get("MDP_CONTROL_RT_URL")
        or os.environ["MDP_CONTROL_RT_DATABASE_URL"]
    ) as conn:
        seed_local_budgets(conn)
        seed_runbooks(conn)
        set_id = conn.execute(
            "INSERT INTO control.target_set(kind,name) VALUES ('account','Local fixture accounts') ON CONFLICT(kind,tenant_id) DO UPDATE SET name=EXCLUDED.name RETURNING id"
        ).fetchone()[0]
        targets = [
            {"platform": "fixture", "platform_account_id": f"acceptance-{n:03}",
             "handle": f"acceptance_account_{n:03}"} for n in range(1, 7)
        ]
        for target in targets:
            conn.execute(
                """INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,resolution_status,activated_at)
                VALUES (%s,%s,%s,%s,'resolved',now()) ON CONFLICT(target_set_id,platform,platform_account_id)
                WHERE resolution_status='resolved' DO NOTHING""",
                (
                    set_id,
                    target.get("platform", "fixture"),
                    target["platform_account_id"],
                    target["handle"],
                ),
            )
        from mdp_functions.playlist_targets import seed_playlist_targets

        # Match deployment's empty global track set before any daily export.
        seed_playlist_targets(conn, REPO / "inputs/playlist_fixture_seed.csv")
        from mdp_functions.chart_targets import seed_chart_targets

        seed_chart_targets(conn, REPO / "inputs/chart_fixture_seed.csv")
        for cadence in ("hourly", "daily", "weekly"):
            conn.execute(
                "INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES (%s,'core',%s,'global') ON CONFLICT(job_id) DO NOTHING",
                (f"local:{cadence}", cadence),
            )
    typer.echo(
        "Local control initialized. Use MDP_DBT_CLOUD_VERIFY=false for local commands."
    )


@control_app.command("runbooks")
def provision_runbooks() -> None:
    """Install missing error runbooks through the configured control owner role."""

    with psycopg.connect(
        os.environ.get("MDP_CONTROL_RT_URL")
        or os.environ["MDP_CONTROL_RT_DATABASE_URL"]
    ) as conn:
        seed_runbooks(conn)
    typer.echo("Missing service runbooks installed; existing content preserved")


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    import uvicorn

    uvicorn.run("mdp_functions.api:factory", factory=True, host=host, port=port)


@registry_app.command("sync")
def registry_sync() -> None:
    from mdp_functions.control_db import ControlDB
    from mdp_functions.registry import sync
    from mdp_functions.warehouse.postgres import PostgresWarehouse

    settings = Settings()
    db = ControlDB(settings.control_url)
    try:
        sync(db, PostgresWarehouse(settings.warehouse_url))
    finally:
        db.close()
    typer.echo("Registry synchronized; runtime knobs preserved")


@sources_app.command("export")
def sources_export() -> None:
    from mdp_functions.exporter import export_sources

    for path in export_sources(Settings()):
        typer.echo(str(path.relative_to(REPO)))


@sources_app.command("doc")
def sources_doc(check: bool = typer.Option(False, "--check")) -> None:
    """Regenerate docs/sources.md, or with --check fail when it or its annotations are stale."""
    from mdp_functions import sources_doc

    found = sources_doc.check() if check else sources_doc.write()
    for problem in found:
        typer.echo(problem, err=True)
    if found:
        raise typer.Exit(1)
    typer.echo(f"{sources_doc.OUTPUT} is current" if check else sources_doc.OUTPUT)


async def local_run(source_key: str, fixture: bool, target: str) -> dict[str, Any]:
    from mdp_functions.registry import REGISTRY
    from mdp_functions.runs import Runtime
    from mdp_functions.warehouse.duckdb import DuckDBWarehouse

    settings = Settings().local().model_copy(update={"fixture": fixture})
    if target == "dev":
        from mdp_functions.local import dev_control

        settings = dev_control(settings)
    warehouse = DuckDBWarehouse(settings.dev_db) if target == "dev" else None
    rt = Runtime(settings, warehouse=warehouse)
    try:
        rt.initialize()
        from mdp_functions.recovery import Recovery

        for expired_run in Recovery(rt).reap():
            rt.settle(expired_run)
        cadence = REGISTRY[source_key].cadence
        dbt_id = f"local:{uuid4()}"
        await rt.cycles.bind_cycle(
            cadence, "global", dbt_id, "scheduled", f"local:{cadence}", runner="core"
        )
        for key in ("targets_export", source_key, "cycle_close"):
            run = rt.admit(
                key, cadence=cadence, dbt_run_id=dbt_id, manual=key == source_key
            )
            await rt.execute(run["id"])
            if key == source_key:
                result = rt.receipts(run["id"])
        if warehouse:
            warehouse.fixture_receipts(source_key, result["receipts"])
        return result
    finally:
        await rt.close()


@app.command("run")
def run_source(
    source_key: str,
    fixture: bool = typer.Option(False, "--fixture"),
    target: str = "dev",
) -> None:
    if target not in {"dev", "pg_local"}:
        raise typer.BadParameter("Target must be dev or pg_local")
    result = asyncio.run(local_run(source_key, fixture, target))
    table = Table(title=source_key)
    fields = (
        "run_id",
        "status",
        "coverage",
        "rows_written",
        "rows_rejected",
        "dump_id",
    )
    for name in fields:
        table.add_column(name)
    for receipt in result["receipts"]:
        table.add_row(*(str(receipt[f]) for f in fields))
    Console(width=180).print(table)
    typer.echo(f"status={result['run']['status']} coverage={result['run']['coverage']}")
    if result["run"].get("error_class"):
        typer.echo(f"{result['run']['error_class']}: {result['run'].get('error_message') or 'See the run details'}")
        from mdp_functions.errors import error_hint

        typer.echo(error_hint(result["run"]["error_class"])["next_step"])
    if result["run"]["status"] not in {"succeeded", "paused"} and not (
        result["run"]["status"] == "partial"
        and all(r["allow_partial"] for r in result["receipts"])
    ):
        raise typer.Exit(1)


@openapi_app.command("export")
def export_openapi(output: str = "functions/openapi/service.json") -> None:
    """Export the service contract without connecting to a database."""
    import json
    from pathlib import Path

    from mdp_functions.api import create_app

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(create_app().openapi(), indent=2) + "\n")
    typer.echo(f"Service OpenAPI exported to {path}")


new_app = typer.Typer()
host_app = typer.Typer()
app.add_typer(new_app, name="new")
app.add_typer(host_app, name="host")


@new_app.command("llm-step")
def new_llm_step(source_key: str):
    """Create a local gold source, prompt, hashed step, budget and model fixture."""
    from mdp_functions.llm_scaffold import scaffold

    version = scaffold(source_key, Settings())
    typer.echo(f"Created {source_key}; step hash {version}")
    typer.echo(f"Run: uv run --project functions mdp run {source_key} --fixture --target pg_local")


@host_app.command("unpause")
def host_unpause(host: str):
    """Clear a host pause in the disposable local stack."""
    from mdp_functions.llm_scaffold import local_url

    settings = Settings()
    with psycopg.connect(local_url(settings.control_url, settings)) as conn:
        result = conn.execute(
            "UPDATE control.host_health SET blocked_until=NULL,last_signature=NULL WHERE host=%s",
            (host.lower().rstrip("."),),
        )
        typer.echo(f"Unpaused {result.rowcount} local host(s)")
