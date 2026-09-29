"""Warehouse file exports under an individual login."""

import os
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True)


@app.command("snapshot")
def snapshot_command(
    schemas: str | None = None, out: Path | None = None, parquet: Path | None = None
):
    import duckdb
    import psycopg

    from mdp_functions.sandbox_policy import message as policy_message
    from mdp_functions.warehouse_snapshot import analyst_connection, snapshot

    try:
        with analyst_connection() as conn:
            typer.echo(
                snapshot(conn, out, parquet, schemas.split(",") if schemas else None)
            )
    except (ValueError, psycopg.Error, duckdb.Error) as error:
        message = (
            str(error)
            if isinstance(error, ValueError)
            else f"{type(error).__name__}: {error}\n" + policy_message("sandbox_snapshot_failed")
        )
        raise typer.BadParameter(message) from None


sandbox_app = typer.Typer(no_args_is_help=True)
app.add_typer(sandbox_app, name="sandbox")


@sandbox_app.command("status")
def sandbox_status(schema: str | None = None, all: bool = False):
    """Show objects, bytes, dependents, quota and last observed use."""
    import json

    import psycopg

    from mdp_functions.sandbox import status
    from mdp_functions.sandbox_policy import message
    from mdp_functions.warehouse_snapshot import analyst_connection

    try:
        if all:
            dsn = os.environ.get("MDP_WAREHOUSE_ADMIN_URL")
            if not dsn:
                raise ValueError(
                    "Operator status needs a warehouse administrator connection. Set MDP_WAREHOUSE_ADMIN_URL, then rerun --all."
                )
            conn = psycopg.connect(dsn, autocommit=True)
        else:
            conn = analyst_connection()
            from psycopg.rows import tuple_row
            conn.row_factory = tuple_row
            if not schema:
                row = conn.execute(
                    "SELECT schema_name FROM catalog.sandbox_status WHERE owner_role=current_user ORDER BY schema_name LIMIT 1"
                ).fetchone()
                if not row:
                    raise ValueError(message("sandbox_missing"))
                schema = row[0]
        with conn:
            typer.echo(json.dumps(status(conn, schema), indent=2, default=str))
            typer.echo(
                "Next: query a listed object, remove unused tables, or ask the operator to archive departing work."
            )
    except (ValueError, psycopg.Error) as error:
        raise typer.BadParameter(
            str(error)
            if isinstance(error, ValueError)
            else message("sandbox_status_failed")
        ) from None
