"""Bootstrap declared raw tables and disposable API keys on the local fixture stack."""

import hashlib
import os

import psycopg
from mdp_functions.exporter import ensure_raw
from mdp_functions.sandbox import create_sandbox
from mdp_functions.settings import Settings

settings = Settings()
ensure_raw(settings.warehouse_url, settings.schema_root)
with psycopg.connect(os.environ["MDP_WAREHOUSE_ADMIN_URL"]) as conn:
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='analyst_local'").fetchone():
        conn.execute(
            "CREATE ROLE analyst_local LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS PASSWORD 'analyst_local' IN ROLE analyst_ro"
        )
    conn.execute("ALTER ROLE analyst_local SET search_path = marts, intermediate, staging")
    conn.execute("ALTER ROLE analyst_local SET timezone = 'UTC'")
    create_sandbox(conn, "local", "analyst_local")
# The fixture keys are written directly, not through `mdp keys create`: env.sh gives every local
# client their fixed values, and the control API mints random keys and starts after this seed.
# The reader has no tenant, the same row `keys create --role reader --global` writes.
with psycopg.connect(os.environ["MDP_CONTROL_RT_URL"]) as conn:
    for variable, role in (("MDP_DATA_API_KEY", "reader"), ("MDP_CONTROL_API_KEY", "promoter")):
        conn.execute(
            "INSERT INTO control.api_key(key_hash,label,role) VALUES (%s,%s,%s) "
            "ON CONFLICT(key_hash) DO NOTHING",
            (hashlib.sha256(os.environ[variable].encode()).hexdigest(), f"Local fixture {role}", role),
        )
print("Local fixture keys and declared raw tables ready")
