"""Shared fixtures for runtime admission tests."""


import psycopg


def admin(databases: dict[str, str], sql: str, params: tuple = ()) -> None:
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(sql, params)
