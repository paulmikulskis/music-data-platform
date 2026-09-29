"""Provision, inspect and bound scratch space without touching cycle state."""

import json

from psycopg import sql
from psycopg.rows import dict_row

from mdp_functions.sandbox_policy import POLICY, message, name


def configure_login(conn, role):
    conn.execute(
        sql.SQL("ALTER ROLE {} CONNECTION LIMIT {}").format(
            sql.Identifier(role), sql.Literal(POLICY["connection_limit"])
        )
    )
    for key in (
        "statement_timeout",
        "idle_in_transaction_session_timeout",
        "temp_file_limit",
    ):
        conn.execute(
            sql.SQL("ALTER ROLE {} SET {} = {}").format(
                sql.Identifier(role), sql.Identifier(key), sql.Literal(POLICY[key])
            )
        )


def create_sandbox(conn, handle, role):
    schema = name(handle)
    existing = conn.execute(
        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s", (schema,)
    ).fetchone()
    state = conn.execute(
        "SELECT owner_role FROM mdp.sandbox_state WHERE schema_name=%s", (schema,)
    ).fetchone()
    if (state and state[0] != role) or (existing and existing[0] != role and not state):
        raise ValueError(message("sandbox_owner_conflict"))
    explorer = conn.execute(
        "SELECT pg_has_role(%s,'explorer_ro','MEMBER')", (role,)
    ).fetchone()[0]
    configure_login(conn, role)
    conn.execute(
        "INSERT INTO mdp.sandbox_state(schema_name,owner_role,is_explorer,quota_bytes) VALUES (%s,%s,%s,%s) ON CONFLICT(schema_name) DO NOTHING",
        (schema, role, explorer, POLICY["quota_bytes"]),
    )
    conn.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(
            sql.Identifier(schema), sql.Identifier(role)
        )
    )
    for grantee in ["PUBLIC", *POLICY["runtime_roles"], "analyst_ro", "explorer_ro"]:
        ident = sql.SQL("PUBLIC") if grantee == "PUBLIC" else sql.Identifier(grantee)
        conn.execute(
            sql.SQL("REVOKE ALL ON SCHEMA {} FROM {} CASCADE").format(
                sql.Identifier(schema), ident
            )
        )
        conn.execute(
            sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {} CASCADE").format(
                sql.Identifier(schema), ident
            )
        )
    for group in ["explorer_ro"] if explorer else ["analyst_ro", "explorer_ro"]:
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(group)
            )
        )
        conn.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(group)
            )
        )
    label = {
        "layer": "sandbox",
        "category": "personal",
        "tenant": "unknown" if explorer else "global",
        "learning": False,
        "resale": False,
        "licence_status": "unverified",
        "owner": role,
        "sharing": "explorers" if explorer else "analysts and explorers",
        "description": "Personal scratch space. Keep rights columns; run mdp warehouse sandbox status before sharing or archiving.",
    }
    conn.execute(
        sql.SQL("COMMENT ON SCHEMA {} IS {}").format(
            sql.Identifier(schema), sql.Literal(json.dumps(label))
        )
    )
    return schema


def status(conn, schema=None):
    """The SQL view filters metadata with the same human sharing boundary."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT * FROM catalog.sandbox_status WHERE (%s::text IS NULL OR schema_name=%s) ORDER BY schema_name",
            (schema, schema),
        ).fetchall()
        for row in rows:
            row["objects"] = cursor.execute(
                "SELECT c.relname AS name,c.relkind AS kind,pg_total_relation_size(c.oid) AS bytes,obj_description(c.oid) AS labels FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f') ORDER BY c.relname",
                (row["schema_name"],),
            ).fetchall()
            row["dependents"] = cursor.execute(
                "SELECT DISTINCT ns.nspname||'.'||v.relname AS relation,pg_get_userbyid(v.relowner) AS owner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_depend d ON d.refobjid=c.oid AND d.refclassid='pg_class'::regclass JOIN pg_rewrite rw ON d.classid='pg_rewrite'::regclass AND rw.oid=d.objid JOIN pg_class v ON v.oid=rw.ev_class JOIN pg_namespace ns ON ns.oid=v.relnamespace WHERE n.nspname=%s AND v.oid<>c.oid ORDER BY 1",
                (row["schema_name"],),
            ).fetchall()
            # SQL function bodies, types and foreign keys also survive in pg_depend.
            # Keep their owners' work online instead of silently dropping it via CASCADE.
            row["dependents"].extend(
                cursor.execute(
                    """WITH RECURSIVE dependent(classid,objid) AS (
                  SELECT 'pg_namespace'::regclass,oid FROM pg_namespace WHERE nspname=%s
                  UNION
                  SELECT d.classid,d.objid FROM pg_depend d JOIN dependent parent
                    ON d.refclassid=parent.classid AND d.refobjid=parent.objid
                )
                SELECT DISTINCT identified.identity AS relation, identified.schema AS schema_name,
                  pg_get_userbyid(coalesce(c.relowner,p.proowner,t.typowner,n.nspowner)) AS owner
                FROM dependent d CROSS JOIN LATERAL pg_identify_object(d.classid,d.objid,0) identified
                  JOIN pg_namespace n ON n.nspname=identified.schema
                  LEFT JOIN pg_class c ON d.classid='pg_class'::regclass AND c.oid=d.objid
                  LEFT JOIN pg_proc p ON d.classid='pg_proc'::regclass AND p.oid=d.objid
                  LEFT JOIN pg_type t ON d.classid='pg_type'::regclass AND t.oid=d.objid
                WHERE identified.schema<>%s AND n.nspname NOT LIKE 'pg\\_%%' ORDER BY 1""",
                    (row["schema_name"], row["schema_name"]),
                ).fetchall()
            )
            row["notices"] = cursor.execute(
                "SELECT code,message,occurred_at FROM catalog.sandbox_notices WHERE schema_name=%s ORDER BY occurred_at DESC",
                (row["schema_name"],),
            ).fetchall()
            row["next_step"] = (
                "Copy the schema name into a SELECT, or remove unused tables before saving more."
            )
    return rows


def notice(conn, schema, code, detail=None):
    conn.execute(
        "INSERT INTO mdp.sandbox_notice(schema_name,code,message) VALUES (%s,%s,%s) ON CONFLICT(schema_name,code) DO UPDATE SET message=excluded.message,occurred_at=now()",
        (schema, code, detail or message(code)),
    )


def freeze(conn, schema, frozen=True, manual=True):
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            "SELECT * FROM mdp.sandbox_state WHERE schema_name=%s FOR UPDATE", (schema,)
        ).fetchone()
    if not row:
        raise ValueError(message("sandbox_missing"))
    field = "manual_frozen" if manual else "quota_frozen"
    conn.execute(
        sql.SQL(
            "UPDATE mdp.sandbox_state SET {}=%s,updated_at=now() WHERE schema_name=%s"
        ).format(sql.Identifier(field)),
        (frozen, schema),
    )
    row[field] = frozen
    held = row["manual_frozen"] or row["quota_frozen"]
    # Owners have implicit CREATE. A hold transfers the schema, but keeps table
    # ownership so the human can DELETE/DROP data and get below the quota.
    conn.execute(
        sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(
            sql.Identifier(schema),
            sql.Identifier("postgres" if held else row["owner_role"]),
        )
    )
    conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(schema), sql.Identifier(row["owner_role"])
        )
    )
    conn.execute(
        sql.SQL(
            "REVOKE CREATE ON SCHEMA {} FROM {}"
            if held
            else "GRANT CREATE ON SCHEMA {} TO {}"
        ).format(sql.Identifier(schema), sql.Identifier(row["owner_role"]))
    )
    if held:
        notice(conn, schema, "sandbox_frozen")
    else:
        conn.execute(
            "DELETE FROM mdp.sandbox_notice WHERE schema_name=%s AND code='sandbox_frozen'",
            (schema,),
        )


def maintain(conn):
    """Bounded periodic check on the operator connection; returns active alerts."""
    conn.execute("SET lock_timeout='250ms'")
    conn.execute("SET statement_timeout='5s'")
    alerts = []
    for row in status(conn):
        schema = row["schema_name"]
        full = row["size_bytes"] >= row["quota_bytes"]
        if full != row["quota_frozen"]:
            with conn.transaction():
                freeze(conn, schema, full, manual=False)
        if row["size_bytes"] >= row["quota_bytes"] * POLICY["warn_fraction"]:
            notice(conn, schema, "sandbox_quota_warning")
            alerts.append((schema, "sandbox_quota_warning"))
        else:
            conn.execute(
                "DELETE FROM mdp.sandbox_notice WHERE schema_name=%s AND code='sandbox_quota_warning'",
                (schema,),
            )
        if conn.execute("SELECT to_regclass('catalog.pg_stat_statements')").fetchone()[
            0
        ]:
            calls = conn.execute(
                "SELECT coalesce(sum(calls),0)::bigint FROM catalog.pg_stat_statements WHERE userid=to_regrole(%s)",
                (row["owner_role"],),
            ).fetchone()[0]
            conn.execute(
                "UPDATE mdp.sandbox_state SET last_use=CASE WHEN %s>last_calls THEN now() ELSE last_use END,last_calls=%s WHERE schema_name=%s",
                (calls, calls, schema),
            )
        conn.execute(
            "UPDATE mdp.sandbox_state SET last_use=greatest(last_use,(SELECT max(query_start) FROM pg_stat_activity WHERE usename=owner_role)) WHERE schema_name=%s",
            (schema,),
        )
    for schema, _ in conn.execute("SELECT * FROM mdp.sandbox_violations()").fetchall():
        notice(conn, schema, "sandbox_acl_alert")
        alerts.append((schema, "sandbox_acl_alert"))
    return list(set(alerts))
