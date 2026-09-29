"""Local fixture control isolation; provisioning only, never used by the API runtime."""

import hashlib
import os
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from mdp_functions.settings import Settings


def dev_control(settings: Settings) -> Settings:
    """One local control catalog per DuckDB file, so fixtures cannot claim production landings."""
    name = (
        "mdp_dev_"
        + hashlib.sha256(str(Path(settings.dev_db).resolve()).encode()).hexdigest()[:12]
    )
    options = conninfo_to_dict(settings.control_url)
    admin_url = os.environ["MDP_CONTROL_ADMIN_URL"]
    admin_options = conninfo_to_dict(admin_url)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
        ).fetchone()
        if not exists:
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER migrator").format(
                    sql.Identifier(name)
                )
            )
    if not exists:
        destination = make_conninfo(**{**admin_options, "dbname": name})
        source = make_conninfo(**{**admin_options, "dbname": options["dbname"]})
        dump = subprocess.run(
            ["pg_dump", "--schema-only", "--schema=control", "--dbname", source],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "--dbname", destination],
            input=dump.stdout,
            check=True,
            capture_output=True,
        )
        with psycopg.connect(source) as original, psycopg.connect(destination) as clone:
            for table in (
                "warehouse",
                "tenant",
                "target_set",
                "target",
                "target_spec",
                "dbt_job",
                "runner_mode",
                "runbook",
            ):
                cursor = original.execute(
                    sql.SQL("SELECT * FROM control.{}").format(sql.Identifier(table))
                )
                names = [c.name for c in cursor.description]
                for row in cursor.fetchall():
                    clone.execute(
                        sql.SQL("INSERT INTO control.{} ({}) VALUES ({})").format(
                            sql.Identifier(table),
                            sql.SQL(",").join(map(sql.Identifier, names)),
                            sql.SQL(",").join(sql.Placeholder() for _ in names),
                        ),
                        tuple(Jsonb(v) if isinstance(v, dict) else v for v in row),
                    )
    return settings.model_copy(
        update={
            "control_url": make_conninfo(**{**options, "dbname": name}),
            "dump_root": settings.dump_root.rstrip("/") + "/dev/" + name,
        }
    )
