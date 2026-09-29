"""Reconcile the showcase login on existing warehouse volumes."""
from pathlib import Path

from psycopg import Connection, sql


def reconcile_showcase_role(conn: Connection, root: Path, password: str) -> None:
    conn.execute((root / "control/packages/control-db/sql/showcase-role.sql").read_text())
    conn.execute(sql.SQL("ALTER ROLE showcase_wh PASSWORD {}").format(sql.Literal(password)))
