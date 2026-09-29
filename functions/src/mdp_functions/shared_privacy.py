"""One column policy for shared grants, projections and privacy checks."""

from pathlib import Path

import yaml

from mdp_functions.sandbox_policy import POLICY


def policies(root: Path):
    reviewed = yaml.safe_load((root / "dbt/shared_columns.yml").read_text())[
        "non_personal"
    ]
    result = {
        name: dict.fromkeys(columns, "non_personal")
        for name, columns in reviewed.items()
    }
    for path in (root / "dbt/models").rglob("*.yml"):
        for model in (yaml.safe_load(path.read_text()) or {}).get("models", []):
            for column in model.get("columns", []):
                meta = {
                    **column.get("meta", {}),
                    **column.get("config", {}).get("meta", {}),
                }
                if meta.get("privacy") == "personal":
                    mode = meta.get("representation")
                    if mode not in {"pseudonym", "omitted", "private"}:
                        raise ValueError(
                            f"{model['name']}.{column['name']}: personal output needs a pseudonym or omission"
                        )
                    if column["name"] in result.get(model["name"], {}):
                        raise ValueError(
                            f"{model['name']}.{column['name']}: conflicting privacy declarations"
                        )
                    result.setdefault(model["name"], {})[column["name"]] = mode
    for name, columns in result.items():
        for field in ("_extra", "text", "text_norm", "row_hash", "fields"):
            if columns.get(field) == "non_personal":
                raise ValueError(
                    f"{name}.{field}: free-form or unkeyed personal content cannot be shared"
                )
    return result


def scan_shared(
    conn, roles=("analyst_ro", "explorer_ro", "workbench_wh"), forbidden=()
):
    """Check managed contracts and forbidden values in every shared relation."""
    import sqlglot
    from psycopg import sql
    from sqlglot import exp

    from mdp_functions.relation_labels import load, relation_label

    definitions = load()
    checked = set()
    columns_checked = 0
    for role in roles:
        relations = conn.execute(
            """
            SELECT c.oid,n.nspname,c.relname,c.relkind
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname NOT LIKE 'pg_%%'
              AND n.nspname<>'information_schema' AND n.nspname NOT LIKE 'wb_%%'
              AND has_schema_privilege(%s,n.oid,'USAGE')
              AND (has_table_privilege(%s,c.oid,'SELECT') OR has_any_column_privilege(%s,c.oid,'SELECT'))
            ORDER BY n.nspname,c.relname
        """,
            (role, role, role),
        ).fetchall()
        for oid, schema, name, kind in relations:
            label = relation_label(schema, name, definitions)
            policy = label.get(
                "privacy" if schema == "explore_raw" else "shared_privacy", {}
            )
            columns = conn.execute(
                """SELECT attname FROM pg_attribute WHERE attrelid=%s
                AND attnum>0 AND NOT attisdropped ORDER BY attnum""",
                (oid,),
            ).fetchall()
            if schema.startswith(POLICY["prefix"]):
                # Scratch tables have no managed contract. Their login can only
                # copy readable inputs; keep checking forbidden values below.
                columns = []
            expressions = {}
            if schema.startswith("explore_"):
                assert kind == "v", f"{role}: {schema}.{name} is not a generated view"
                query = conn.execute(
                    "SELECT pg_get_viewdef(%s,true)", (oid,)
                ).fetchone()[0]
                tree = sqlglot.parse_one(query, read="postgres")
                source = tree.args["from_"].this
                assert isinstance(source, exp.Table), (
                    f"{schema}.{name}: expected one source"
                )
                label = relation_label(source.db, source.name, definitions)
                policy = label.get(
                    "privacy" if source.db == "raw" else "shared_privacy", {}
                )
                expressions = {e.alias_or_name: e.unalias() for e in tree.expressions}
            for (column,) in columns:
                mode = policy.get(column)
                ref = f"{role}: {schema}.{name}.{column}"
                if not schema.startswith("explore_"):
                    assert mode == "non_personal", (
                        f"{ref}: undeclared or personal shared column"
                    )
                    assert column not in {
                        "_extra",
                        "text",
                        "text_norm",
                        "row_hash",
                        "fields",
                    }, ref
                else:
                    expression = expressions.get(column)
                    assert expression is not None, f"{ref}: missing explicit projection"
                    if mode not in {"non_personal", "pseudonym"}:
                        while isinstance(expression, (exp.Cast, exp.Paren)):
                            expression = expression.this
                        assert isinstance(expression, exp.Null), (
                            f"{ref}: unknown/private column is not null"
                        )
                    elif schema == "explore_raw" and mode == "pseudonym":
                        assert any(
                            isinstance(n, exp.SHA2) for n in expression.walk()
                        ), ref
                        assert any(
                            t.db == "mdp" and t.name == "pseudonym_key"
                            for t in expression.find_all(exp.Table)
                        ), ref
                    else:
                        assert (
                            isinstance(expression, exp.Column)
                            and expression.name == column
                        ), ref
                columns_checked += 1
            if forbidden:
                with conn.transaction():
                    conn.execute(
                        sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role))
                    )
                    for value in forbidden:
                        found = conn.execute(
                            sql.SQL(
                                "SELECT EXISTS (SELECT 1 FROM {} AS r WHERE position(%s in to_jsonb(r)::text)>0)"
                            ).format(sql.Identifier(schema, name)),
                            (value,),
                        ).fetchone()[0]
                        assert not found, (
                            f"{role}: a private value or guessable digest survives in {schema}.{name}"
                        )
            conn.execute("RESET ROLE")
            checked.add((schema, name))
    return len(checked), columns_checked
