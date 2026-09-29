"""Install raw projections from the generated, default-deny field declarations."""

from mdp_functions.relation_labels import load


def install(conn):
    for relation in load():
        if relation.startswith("raw."):
            row = conn.execute("SELECT to_regclass(%s)", (relation,)).fetchone()
            if (row["to_regclass"] if isinstance(row, dict) else row[0]) is not None:
                conn.execute(
                    "SELECT catalog.refresh_explore(%s::regclass)", (relation,)
                )

    conn.execute("""SELECT catalog.share_relation(c.oid) FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname NOT LIKE 'explore_%' AND n.nspname NOT LIKE 'sandbox_%'
          AND n.nspname NOT IN ('information_schema','control','mdp','raw','catalog')""")


def schema_name(schema):
    """Match catalog.explore_schema, including Postgres's identifier length limit."""
    import hashlib

    if len(schema) <= 55:
        return "explore_" + schema
    return "explore_" + schema[:45] + "_" + hashlib.md5(schema.encode()).hexdigest()[:8]


def rewrite(query):
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(query, read="postgres")
    for table in tree.find_all(exp.Table):
        schema = table.db
        if schema in {
            "raw",
            "staging",
            "intermediate",
            "marts",
            "reference",
        } or schema.startswith("tenant_"):
            table.set("db", exp.to_identifier(schema_name(schema)))
    return tree.sql(dialect="postgres")
