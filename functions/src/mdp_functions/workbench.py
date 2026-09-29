"""Service-mediated workbench: durable jobs, isolated Core builds and bounded reads."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import psycopg
import yaml
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mdp_functions.control_db import ControlDB
from mdp_functions.errors import ServiceError, error_hint
from mdp_functions.settings import REPO, Settings
from mdp_functions.store import LocalFsStore, make_store


def validate_sql(query, schema):
    # A deliberately restricted SELECT surface; transaction permissions enforce writes too.
    plain = query.replace('"', "").lower()
    if not re.match(r"^\s*(select|with)\b", plain) or ";" in plain:
        raise ServiceError("workbench_query_refused", "Submit one read-only SELECT")
    if re.search(
        r"\b(insert|update|delete|merge|call|copy|create|alter|drop|set|reset|do)\b",
        plain,
    ):
        raise ServiceError(
            "workbench_query_refused", "Only read-only queries are allowed"
        )
    if re.search(
        r"\b(set_config|query_to_xml|dblink|pg_read_file|pg_read_binary_file|lo_import)\b|u&",
        plain,
    ):
        raise ServiceError(
            "workbench_query_refused",
            "Dynamic execution and escaped SQL are not supported",
        )
    if any(name != schema for name in re.findall(r"\bwb_[a-z0-9_]+\b", plain)):
        raise ServiceError(
            "workbench_isolation", "Another session schema cannot be referenced"
        )


def business_columns(a, b):
    return sorted(
        {column for row in a + b for column in row if not column.startswith("_")}
    )


def backtest_columns(model, a, b):
    """Use the checked-in contract even though draft builds relax its enforcement."""
    columns = None
    for path in sorted((REPO / "dbt/models").rglob("*")):
        if path.suffix not in {".yml", ".yaml"}:
            continue
        document = yaml.safe_load(path.read_text()) or {}
        for entry in document.get("models", []):
            if entry.get("name") == model and entry.get("config", {}).get(
                "contract", {}
            ).get("enforced"):
                columns = [
                    c["name"]
                    for c in entry.get("columns", [])
                    if not c["name"].startswith("_")
                ]
                break
    available = [{c["name"] for c in build["columns"]} for build in (a, b)]
    if columns is None:
        columns = sorted(c for c in set.union(*available) if not c.startswith("_"))
    if any(set(columns) - names for names in available):
        raise ServiceError(
            "backtest_column_missing", "Every compared column must exist in both builds"
        )
    return columns


def selection_invokes(manifest, node_id):
    """The +model selection includes every ancestor in the manifest parent map."""
    pending, visited = [node_id], set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        node = manifest["nodes"].get(current, {})
        if node.get("resource_type") == "model" and (
            "invoke" in node.get("tags", [])
            or node.get("name", "").startswith("invoke__")
        ):
            return True
        pending.extend(manifest.get("parent_map", {}).get(current, []))
    return False


def diff_rows(a, b, keys, columns=None):
    columns = business_columns(a, b) if columns is None else columns
    if not keys or any(not key for key in keys):
        raise ServiceError("backtest_key_missing", "Declare at least one key column")

    def keyed(rows):
        found = {}
        for row in rows:
            if any(k not in row for k in keys):
                raise ServiceError(
                    "backtest_key_missing", "Every key column must be present"
                )
            key = tuple(json.dumps(row[k], sort_keys=True, default=str) for k in keys)
            if key in found:
                raise ServiceError(
                    "backtest_duplicate_key",
                    "Backtest key columns must uniquely identify rows",
                )
            found[key] = row
        return found

    left, right = keyed(a), keyed(b)
    added = [right[k] for k in right.keys() - left.keys()]
    removed = [left[k] for k in left.keys() - right.keys()]
    changed = [
        {"before": left[k], "after": right[k]}
        for k in left.keys() & right.keys()
        if any(left[k].get(c) != right[k].get(c) for c in columns)
    ]
    return added, removed, changed


def database_failure(exc):
    from mdp_functions.analyst_errors import failure

    detail = exc.diag.message_primary or str(exc)
    if exc.sqlstate == "42501" and re.search(r"\b(?:explore_)?raw\b", detail):
        return failure("raw_read_denied", detail)
    if exc.sqlstate in {"42501", "42P01", "3F000"} and re.search(r"\b(?:explore_)?tenant_", detail):
        return failure("tenant_read_denied")
    kind = {"42501": "workbench_permission_denied", "57014": "workbench_statement_timeout",
            "42P01": "model_not_built"}.get(exc.sqlstate, "workbench_query_failed")
    return failure(kind, detail)



class Workbench:
    def __init__(self, settings):
        self.settings = settings
        self.db = ControlDB(settings.control_url)
        self.owner = ControlDB(settings.control_rt_url)
        self.store = make_store(settings)
        self.tasks = {}
        self.processes = {}

    def require_built_model(self, model):
        from mdp_functions.analyst_errors import failure, local_build_command

        manifest_path = REPO / "dbt/target/manifest.json"
        if not manifest_path.exists():
            return
        manifest = json.loads(manifest_path.read_text())
        node = manifest.get("nodes", {}).get("model.music_data_platform." + model)
        if not node or node.get("config", {}).get("materialized") == "ephemeral":
            return
        schema = node.get("config", {}).get("schema") or node["schema"]
        if manifest.get("metadata", {}).get("adapter_type") == "postgres":
            schema = node["schema"]
        # A global manifest has no caller-specific tenant schema to inspect.
        if "scope:tenant" in node.get("tags", []) or schema.startswith("tenant_"):
            return
        with psycopg.connect(self.settings.workbench_admin_url) as conn:
            relation = sql.Identifier(schema, node.get("alias") or model).as_string(conn)
            built = conn.execute("SELECT to_regclass(%s)", (relation,)).fetchone()[0]
        if built is None:
            raise ServiceError(
                "model_not_built",
                failure(
                    "model_not_built",
                    next_step=f"Run {local_build_command(model)}, then retry Preview.",
                )["message"],
            )

    def session(self, session_id, user):
        session = self.db.one(
            "SELECT * FROM control.workbench_session WHERE id=%s AND user_id=%s AND expires_at>now()",
            (UUID(session_id), user),
        )
        if not session:
            raise ServiceError(
                "workbench_session_missing",
                "Session expired or belongs to another user",
                404,
            )
        return session

    def create_session(self, user):
        identity = uuid4()
        schema = "wb_" + identity.hex
        with psycopg.connect(self.settings.workbench_admin_url) as conn:
            conn.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT").format(
                    sql.Identifier(schema)
                )
            )
            conn.execute(
                sql.SQL("GRANT {} TO workbench_wh WITH INHERIT FALSE").format(
                    sql.Identifier(schema)
                )
            )
            conn.execute(
                sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                    sql.Identifier(schema), sql.Identifier(schema)
                )
            )
        self.grant_inputs(schema, user)
        self.owner.execute(
            "INSERT INTO control.workbench_session(id,user_id,scratch_schema,expires_at) VALUES (%s,%s,%s,now()+interval '1 day')",
            (identity, user, schema),
        )
        return {"sessionId": str(identity), "scratchSchema": schema}

    def grant_inputs(self, schema, user=""):
        from mdp_functions.workbench_access import grant_staff_inputs
        # Only production/reference inputs; never another scratch schema or mdp functions.
        with psycopg.connect(self.settings.workbench_admin_url) as conn:
            from mdp_functions.explore import install

            if not user.startswith("staff:"):
                install(conn)
            conn.execute(sql.SQL("REVOKE ALL ON SCHEMA raw FROM {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA raw FROM {}").format(sql.Identifier(schema)))
            if user.startswith("staff:"):
                grant_staff_inputs(conn, schema)
                return
            conn.execute(sql.SQL("GRANT explorer_ro TO {} WITH INHERIT TRUE, SET FALSE").format(sql.Identifier(schema)))

    def draft(self, session, model=None, source=None, persist=True):
        latest = self.db.one(
            "SELECT input FROM control.workbench_run WHERE session_id=%s AND input ? 'sql' ORDER BY created_at DESC LIMIT 1",
            (session["id"],),
        )
        if model is None:
            return (
                {key: latest["input"][key] for key in ("model", "sql")}
                if latest
                else {
                    "model": "mart_chart_history",
                    "sql": self.model_path("mart_chart_history").read_text(),
                }
            )
        if not re.fullmatch("[a-z][a-z0-9_]*", model):
            raise ServiceError("workbench_model_missing", "Invalid model name")
        if source is None:
            if latest and latest["input"]["model"] == model:
                return {key: latest["input"][key] for key in ("model", "sql")}
            source = self.model_path(model).read_text()
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        if not source.strip() or len(source) > 100000:
            raise ServiceError(
                "workbench_draft_invalid", "A nonempty SQL draft is required"
            )
        # Jinja runs inside the service: permit only the repository's trusted template,
        # or literal ref/source calls. Never arbitrary Python/Jinja evaluation.
        trusted = any(
            path.read_text() == source
            for path in (REPO / "dbt/models").rglob(model + ".sql")
        )
        if not trusted:
            if "{%" in source or "{#" in source:
                raise ServiceError(
                    "workbench_draft_invalid",
                    "Edited drafts cannot contain Jinja statement ({% %}) or comment ({# #}) blocks. "
                    "Remove those blocks, or reload the unchanged checked-in model. "
                    "Literal ref/source calls are supported.",
                )

            def template(match):
                expression = match.group(1).strip()
                if not re.fullmatch(
                    r"(?:ref|source)\(\s*'[a-z][a-z0-9_]*'\s*(?:,\s*'[a-z][a-z0-9_]*'\s*)?\)",
                    expression,
                ):
                    raise ServiceError(
                        "workbench_draft_invalid",
                        "Edited drafts support only literal ref/source expressions with single-quoted names. "
                        "Remove other Jinja expressions, or reload the unchanged checked-in model.",
                    )
                return session["scratch_schema"] + ".draft_reference"

            literal_sql = re.sub(r"\{\{(.*?)\}\}", template, source, flags=re.DOTALL)
            validate_sql(literal_sql, session["scratch_schema"])
            if session["user_id"].startswith("staff:"):
                from mdp_functions.explore import rewrite
                from mdp_functions.workbench_access import check_staff_query

                self.grant_inputs(session["scratch_schema"], session["user_id"])
                with psycopg.connect(self.settings.workbench_admin_url) as access:
                    check_staff_query(access, rewrite(literal_sql), session["scratch_schema"])
        body = {"model": model, "sql": source}
        if persist:
            self.db.execute(
                "INSERT INTO control.workbench_run(id,session_id,kind,input,status) VALUES (%s,%s,'query',%s,'succeeded')",
                (uuid4(), session["id"], Jsonb(body)),
            )
        return body

    def project(self, session, draft, folder, historical=False):
        project = folder / "project"
        shutil.copytree(
            REPO / "dbt",
            project,
            ignore=shutil.ignore_patterns(
                ".venv", "target", "logs", ".git", "__pycache__"
            ),
            dirs_exist_ok=True,
        )
        paths = list((project / "models").rglob(draft["model"] + ".sql"))
        path = (
            paths[0] if paths else project / "models/marts" / (draft["model"] + ".sql")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        source = draft["sql"]
        if "{{" not in source and "{%" not in source:
            from mdp_functions.explore import rewrite

            source = rewrite(source)
        path.write_text(source)
        # The draft defines its own columns; checked-in contracts describe the old SQL.
        for properties in (project / "models").rglob("*.yml"):
            document = yaml.safe_load(properties.read_text())
            if isinstance(document, dict) and "sources" in document:
                for source in document["sources"]:
                    if source.get("schema") == "raw":
                        source["schema"] = "explore_raw"
                properties.write_text(yaml.safe_dump(document, sort_keys=False))
            if isinstance(document, dict) and "models" in document:
                for item in document["models"]:
                    if item.get("name") == draft["model"]:
                        item.setdefault("config", {})["contract"] = {"enforced": False}
                        item.pop("tests", None)
                        item.pop("data_tests", None)
                        for column in item.get("columns", []):
                            column.pop("tests", None)
                            column.pop("data_tests", None)
                properties.write_text(yaml.safe_dump(document, sort_keys=False))
        config = yaml.safe_load((project / "dbt_project.yml").read_text())
        config["models"]["music_data_platform"]["+pre-hook"] = (
            'SET LOCAL ROLE "' + session["scratch_schema"] + '"'
        )
        (project / "dbt_project.yml").write_text(
            yaml.safe_dump(config, sort_keys=False)
        )
        from mdp_functions.workbench_access import input_refs

        overrides = input_refs(session["user_id"].startswith("staff:"))
        if historical:
            from mdp_functions.workbench_history import macros

            overrides = overrides.split("{% macro ref() %}")[0] + macros()
        (project / "macros/workbench_inputs.sql").write_text(overrides)
        return project

    def build_env(self, schema):
        from psycopg.conninfo import conninfo_to_dict

        env = dict(os.environ)
        # The command's explicit Python path refuses a missing environment.
        # Temporary projects read the installed dbt environment without changing it.
        env["UV_PROJECT_ENVIRONMENT"] = str(REPO / "dbt/.venv")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        connection = conninfo_to_dict(self.settings.workbench_wh_url)
        for key, value in {
            "MDP_PG_HOST": connection.get("host", "127.0.0.1"),
            "MDP_PG_PORT": connection.get("port", "5432"),
            "MDP_PG_DB": connection["dbname"],
            "MDP_WB_USER": connection["user"],
            "MDP_WB_PASSWORD": connection.get("password", ""),
            "MDP_WB_SSLMODE": connection.get("sslmode", "require"),
        }.items():
            env[key] = value
        env["PGOPTIONS"] = (
            f"-c role={schema} -c statement_timeout={self.settings.workbench_timeout_s * 1000}"
        )
        return env

    def query(self, session, query, preview=False, full=False, provenance_query=None, input_queries=None, audit=True):
        schema = session["scratch_schema"]
        validate_sql(query, schema)
        from mdp_functions.explore import rewrite

        provenance_query = provenance_query or query
        if session["user_id"].startswith("staff:"):
            provenance_query = rewrite(provenance_query)
        query = rewrite(query)
        if session["user_id"].startswith("staff:"):
            from mdp_functions.workbench_access import check_staff_query

            self.grant_inputs(schema, session["user_id"])
            with psycopg.connect(self.settings.workbench_admin_url) as access:
                check_staff_query(access, query, schema)
                check_staff_query(access, provenance_query, schema)
        start = time.monotonic()
        with psycopg.connect(
            self.settings.workbench_wh_url,
            row_factory=dict_row,
        ) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(schema)))
            read_schema = "explore_marts" if session["user_id"].startswith("staff:") else "marts"
            conn.execute(
                sql.SQL("SET LOCAL search_path TO {},{},pg_catalog").format(
                    sql.Identifier(schema), sql.Identifier(read_schema)
                )
            )
            conn.execute(
                "SELECT set_config('statement_timeout',%s,true)",
                (str(self.settings.workbench_timeout_s * 1000),),
            )
            cap = self.settings.workbench_row_cap if not full else 100000
            cursor = conn.execute(
                f"SELECT * FROM ({query}) AS bounded_result LIMIT {cap + 1}"
            )
            columns = [
                {
                    "name": c.name,
                    "type": str(c.type_code),
                    "nullable": True,
                    "source": schema if preview else "marts",
                }
                for c in cursor.description
            ]
            # The materialized draft owns every output physically. Describe its
            # compiled SELECT without executing rows to recover pass-through origins.
            origin_cursor = cursor
            if provenance_query:
                origin_cursor = conn.execute(
                    f"SELECT * FROM ({provenance_query.rstrip().rstrip(';')}) AS origins LIMIT 0"
                )
            for index, col in enumerate(columns):
                origin = conn.execute(
                    "SELECT n.nspname||'.'||c.relname AS source, NOT a.attnotnull AS nullable FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid WHERE c.oid=%s AND a.attnum=%s",
                    (
                        origin_cursor.pgresult.ftable(index),
                        origin_cursor.pgresult.ftablecol(index),
                    ),
                ).fetchone()
                col.update(origin or {"source": None})
            cap = self.settings.workbench_row_cap if not full else 100000
            rows = cursor.fetchmany(cap + 1)
            if full and len(rows) > cap:
                raise ServiceError(
                    "workbench_row_cap", "Backtest exceeds the full comparison row cap"
                )
            truncated = len(rows) > cap
            rows = rows[:cap]
            types = conn.execute(
                "SELECT oid,typname FROM pg_type WHERE oid=ANY(%s)",
                ([int(c["type"]) for c in columns],),
            ).fetchall()
            names = {str(r["oid"]): r["typname"] for r in types}
            for col in columns:
                col["type"] = names.get(col["type"], col["type"])
            plan = conn.execute("EXPLAIN (FORMAT JSON) " + query).fetchone()[
                "QUERY PLAN"
            ]
        from mdp_functions.query_labels import describe, record

        result_labels = None
        if audit:
            with psycopg.connect(self.settings.workbench_admin_url) as audit_conn:
                try:
                    with audit_conn.transaction():
                        audit_conn.execute("SET LOCAL statement_timeout='5s'")
                        result_labels = describe(audit_conn, provenance_query or query, (schema, "marts", "intermediate", "staging"), input_queries)
                except psycopg.Error:
                    from mdp_functions.relation_labels import UNKNOWN

                    result_labels = dict(UNKNOWN, tenants=[], cross_tenant=False, unresolved=True)
                observed_tenants = {str(row[key]) for row in rows for key in row if key == "tenant_id" and row[key] is not None}
                if len(observed_tenants) > 1:
                    result_labels["cross_tenant"] = True
                    result_labels["tenants"] = sorted(set(result_labels["tenants"]) | observed_tenants)
                record(audit_conn, session["user_id"], provenance_query or query, result_labels)
        return jsonable_encoder(
            {
                "labels": result_labels,
                "columns": columns,
                "rows": rows,
                "truncated": truncated,
                "timingMs": int((time.monotonic() - start) * 1000),
                "plan": plan,
            }
        )

    def model_path(self, model):
        if not re.fullmatch("[a-z][a-z0-9_]*", model):
            raise ServiceError("workbench_model_missing", "Invalid model")
        matches = list((REPO / "dbt/models").rglob(model + ".sql"))
        if len(matches) != 1:
            raise ServiceError(
                "workbench_model_missing", "Select a checked-in model", 404
            )
        return matches[0]

    def explain(self, session, model, draft=None):
        draft = draft or self.draft(session, model, persist=False)
        latest = self.db.one(
            "SELECT result FROM control.workbench_run WHERE session_id=%s AND input->>'model'=%s AND input->>'sql'=%s AND kind IN ('preview','backtest') AND status='succeeded' ORDER BY created_at DESC LIMIT 1",
            (session["id"], model, draft["sql"]),
        )
        if latest:
            result = latest["result"]
            built = result.get("buildB", result)
            compiled = built["compiledSql"]
            upstream = built["upstream"]
        else:
            folder = Path("/tmp/mdp-workbench") / str(uuid4())
            project = self.project(session, draft, folder)
            self.grant_inputs(session["scratch_schema"], session["user_id"])
            cycle = self.db.one(
                "SELECT id FROM control.cycle WHERE status='closed' ORDER BY opened_at DESC LIMIT 1"
            )
            variables = {
                "wb_schema": session["scratch_schema"],
                "cycle_id": str(cycle["id"]) if cycle else str(uuid4()),
            }
            command = [
                "uv",
                "run",
                "--no-sync",
                "--offline",
                "--python",
                str(REPO / "dbt/.venv/bin/python"),
                "--project",
                str(project),
                "dbt",
                "compile",
                "--project-dir",
                str(project),
                "--profiles-dir",
                str(project / "profiles"),
                "--target",
                "workbench",
                "--target-path",
                str(folder),
                "--log-path",
                str(folder / "logs"),
                "--select",
                model,
                "--vars",
                json.dumps(variables),
            ]
            result = subprocess.run(
                command,
                cwd=project,
                env=self.build_env(session["scratch_schema"]),
                capture_output=True,
                timeout=35,
                check=False,
            )
            if result.returncode:
                raise ServiceError(
                    "workbench_compile_failed",
                    "Compile the model successfully before inspecting lineage",
                )
            manifest = json.loads((folder / "manifest.json").read_text())
            node = manifest["nodes"]["model.music_data_platform." + model]
            compiled = node["compiled_code"]
            upstream = node["depends_on"]["nodes"]
        downstream = [
            p.stem
            for p in (REPO / "dbt/models").rglob("*.sql")
            if model in re.findall(r"ref\(['\"]([^'\"]+)", p.read_text())
        ]
        from mdp_functions.registry import discover

        visited, tables = set(), set()

        def inputs(name):
            if name in visited:
                return
            visited.add(name)
            paths = list((REPO / "dbt/models").rglob(name + ".sql"))
            if not paths and name != model:
                return
            source = draft["sql"] if name == model else paths[0].read_text()
            for schema, table in re.findall(
                r"source\(['\"]([^'\"]+)['\"],\s*['\"]([^'\"]+)", source
            ):
                tables.add(schema + "." + table)
            for ref in re.findall(r"ref\(['\"]([^'\"]+)", source):
                inputs(ref)

        inputs(model)
        source_keys = [
            m.source_key for m in discover().values() if tables.intersection(m.writes)
        ]
        runs = self.db.all(
            "SELECT r.id,r.status,r.cycle_id,s.source_key,r.updated_at FROM control.run r JOIN control.streamline s ON s.id=r.streamline_id WHERE r.kind='invoke' AND s.source_key=ANY(%s) AND r.status IN ('succeeded','partial') ORDER BY r.updated_at DESC LIMIT 20",
            (source_keys,),
        )
        freshness = self.db.all(
            "SELECT s.source_key,max(d.published_at) AS last_published_at FROM control.dump d JOIN control.streamline s ON s.id=d.streamline_id WHERE d.kind='output' AND s.source_key=ANY(%s) GROUP BY s.source_key",
            (source_keys,),
        )
        return jsonable_encoder(
            {
                "compiledSql": compiled,
                "upstream": upstream,
                "downstream": downstream,
                "sourceFreshness": freshness,
                "producingRuns": runs,
            }
        )

    async def submit(self, session, kind, body):
        identity = str(uuid4())
        with self.db.transaction() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("workbench:" + session["user_id"],),
            )
            active = conn.execute(
                "SELECT r.session_id FROM control.workbench_run r JOIN control.workbench_session s ON s.id=r.session_id WHERE s.user_id=%s AND r.status IN ('queued','running') AND r.kind IN ('query','preview','backtest')",
                (session["user_id"],),
            ).fetchall()
            if (
                any(r["session_id"] == session["id"] for r in active)
                or len(active) >= 2
            ):
                raise ServiceError(
                    "workbench_preview_limit",
                    "One preview per session and two per user are allowed",
                    409,
                )
            conn.execute(
                "INSERT INTO control.workbench_run(id,session_id,kind,input) VALUES (%s,%s,%s,%s)",
                (identity, session["id"], kind, Jsonb(body)),
            )
        self.tasks[identity] = asyncio.create_task(
            self.execute(identity, session, kind, body)
        )
        return {"runId": identity, "status": "queued", "progress": 0, "error": None}

    async def build(
        self, run_id, session, model, cycle, full=False, draft=None, side="preview"
    ):
        draft = draft or self.draft(session, model)
        schema = session["scratch_schema"]
        with psycopg.connect(
            self.settings.workbench_wh_url, row_factory=dict_row
        ) as conn:
            size = conn.execute(
                "SELECT coalesce(sum(pg_total_relation_size(c.oid)),0) AS n FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s",
                (schema,),
            ).fetchone()["n"]
            if size > self.settings.workbench_schema_cap_bytes:
                raise ServiceError(
                    "workbench_schema_cap", "Scratch schema exceeds its size cap"
                )
        self.grant_inputs(schema, session["user_id"])
        folder = Path("/tmp/mdp-workbench") / run_id / side
        folder.mkdir(parents=True, exist_ok=True)
        historical = side in {"A", "B"} and not session["user_id"].startswith("staff:")
        project = self.project(session, draft, folder, historical=historical)
        variables = {"wb_schema": schema, "cycle_id": str(UUID(cycle))}
        if historical:
            from mdp_functions import workbench_history

            models, sources = workbench_history.prepare(project, model)
            sources = workbench_history.input_names(variables["cycle_id"], sources)
            context = workbench_history.inputs(
                self.settings.workbench_wh_url, schema, variables["cycle_id"], sources,
                self.settings.workbench_timeout_s,
            )
            variables.update(history_models=models, history_sources=sources, history_cycle=context)
        args = [
            "uv",
            "run",
            "--no-sync",
            "--offline",
            "--python",
            str(REPO / "dbt/.venv/bin/python"),
            "--project",
            str(project),
            "dbt",
            "compile" if historical else "build",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project / "profiles"),
            "--target",
            "workbench",
            "--target-path",
            str(folder),
            "--log-path",
            str(folder / "logs"),
            "--select",
            model,
            "--indirect-selection",
            "empty",
            "--vars",
            json.dumps(variables),
        ]
        env = self.build_env(schema)
        start = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=project,
        )
        self.processes[run_id] = process
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=180)
        except BaseException:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        finally:
            self.processes.pop(run_id, None)
        if process.returncode:
            (folder / "error.log").write_bytes(output)
            if historical:
                raise workbench_history.refuse()
            clean = re.sub(r"\x1b\[[0-9;]*m", "", output.decode(errors="replace"))
            denied = re.search(r"(workbench_permission_denied|model_not_built|tenant_read_denied|model_ephemeral): ([^\n]+)", clean)
            if denied:
                if denied.group(1) == "tenant_read_denied":
                    from mdp_functions.analyst_errors import failure

                    raise ServiceError(
                        "tenant_read_denied", failure("tenant_read_denied")["message"], 403
                    )
                if denied.group(1) == "model_ephemeral":
                    raise ServiceError("model_ephemeral", denied.group(2).strip(), 409)
                if denied.group(1) == "workbench_permission_denied":
                    raise ServiceError("workbench_permission_denied", denied.group(2).strip(), 403)
                raise ServiceError("model_not_built", denied.group(2).strip(), 409)
            detail = re.search(r"Database Error in model[^\n]*\n\s*([^\n]+)", clean)
            message = (
                detail.group(1).strip()
                if detail
                else "Check the SQL and upstream model references."
            )
            message = re.sub(r"/(?:Users|home)/[^/\s]+", "<home>", message)
            raise ServiceError(
                "workbench_build_failed", "Could not build this SQL: " + message[:300]
            )
        manifest = json.loads((folder / "manifest.json").read_text())
        node = manifest["nodes"]["model.music_data_platform." + model]
        if historical:
            if any(
                "scope:tenant" in manifest["nodes"]["model.music_data_platform." + name]["tags"]
                for name in models
            ):
                raise workbench_history.refuse()
            await asyncio.to_thread(
                workbench_history.materialize, self.settings.workbench_wh_url, schema,
                node["alias"], node["compiled_code"], sources, self.settings.workbench_timeout_s,
            )
        result = await asyncio.to_thread(
            self.query,
            session,
            f'SELECT * FROM "{schema}"."{node["alias"]}"',
            True,
            full,
            node["compiled_code"],
            {(n["schema"], n["alias"]): n["compiled_code"] for n in manifest["nodes"].values()
             if n.get("compiled_code") and n.get("schema") == schema and n.get("resource_type") == "model"},
        )
        relations = {
            f"{item['schema']}.{item.get('alias') or item.get('identifier') or item['name']}": item[
                "unique_id"
            ]
            for item in [
                *manifest["nodes"].values(),
                *manifest.get("sources", {}).values(),
            ]
            if item.get("schema") and item.get("resource_type") in {"model", "source"}
        }
        for column in result["columns"]:
            column["source"] = relations.get(column["source"], column["source"])
        # Count the already materialized scratch relation, not the draft query again.
        count_result = await asyncio.to_thread(
            self.query,
            session,
            f'SELECT count(*) AS total FROM "{schema}"."{node["alias"]}"',
            True,
            audit=False,
        )
        total_rows = int(count_result["rows"][0]["total"])
        key = f"workbench/{session['id']}/{run_id}/{side}-{cycle}.json"
        self.store.put(key, json.dumps(result, default=str).encode())
        return {
            "labels": result["labels"],
            "columns": result["columns"],
            "rows": result["rows"],
            "truncated": result["truncated"],
            "totalRows": total_rows,
            "compiledSql": node.get("compiled_code", ""),
            "upstream": node["depends_on"]["nodes"],
            "timingMs": int((time.monotonic() - start) * 1000),
            "artifactRef": key,
            "invokeSkipped": selection_invokes(manifest, node["unique_id"]),
        }

    async def direct(self, identity, session, body):
        query = body.get("sql", "").strip().rstrip(";")
        if "{{" in query or "{%" in query:
            raise ServiceError("workbench_query_refused", "Run uses plain SQL with schema names. Use Preview for dbt SQL.")
        result = await asyncio.to_thread(self.query, session, query)
        key = f"workbench/{session['id']}/{identity}/query.json"
        result.update(compiledSql=query, upstream=[], artifactRef=key)
        self.store.put(key, json.dumps(result, default=str).encode())
        return result

    async def execute(self, identity, session, kind, body):
        self.db.execute(
            "UPDATE control.workbench_run SET status='running' WHERE id=%s", (identity,)
        )
        start = time.monotonic()
        try:
            if kind == "query":
                result = await self.direct(identity, session, body)
            elif kind == "preview":
                result = await self.build(
                    identity, session, body["model"], body["cycleId"], draft=body
                )
            else:
                a = await self.build(
                    identity, session, body["model"], body["cycleA"], True, body, "A"
                )
                b = await self.build(
                    identity, session, body["model"], body["cycleB"], True, body, "B"
                )
                columns = backtest_columns(body["model"], a, b)
                if any(
                    set(body["keyColumns"]) - {c["name"] for c in build["columns"]}
                    for build in (a, b)
                ):
                    raise ServiceError(
                        "backtest_key_missing",
                        "Every key column must exist in both builds",
                    )
                added, removed, changed = diff_rows(
                    a["rows"], b["rows"], body["keyColumns"], columns
                )
                result = {
                    "buildA": a,
                    "buildB": b,
                    "keyColumns": body["keyColumns"],
                    "comparedColumns": columns,
                    "added": added,
                    "removed": removed,
                    "changed": changed,
                    "summary": {
                        "added": len(added),
                        "removed": len(removed),
                        "changed": len(changed),
                    },
                }
            ref = f"workbench/{session['id']}/{identity}/result.json"
            self.store.put(ref, json.dumps(result, default=str).encode())
            self.db.execute(
                "UPDATE control.workbench_run SET status='succeeded',result=%s,artifact_ref=%s,duration_ms=%s WHERE id=%s",
                (Jsonb(result), ref, int((time.monotonic() - start) * 1000), identity),
            )
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - durable job boundary
            cancelled = isinstance(exc, asyncio.CancelledError)
            error = {
                "error_class": "workbench_cancelled"
                if cancelled
                else getattr(exc, "error_class", "workbench_failed"),
                "message": "Build cancelled. Your draft is saved."
                if cancelled
                else (
                    str(exc)
                    if isinstance(exc, ServiceError)
                    else "Build could not finish. Check the draft and try again."
                ),
            }
            if isinstance(exc, psycopg.Error):
                error = database_failure(exc)
            else:
                hint = error_hint(error["error_class"])
                error.update(next_step=hint["next_step"],
                             runbook=("/runbooks/" + hint["runbook"]) if hint["runbook"] else None)
            self.db.execute(
                "UPDATE control.workbench_run SET status='failed',error=%s,duration_ms=%s WHERE id=%s",
                (Jsonb(error), int((time.monotonic() - start) * 1000), identity),
            )

    def save_as_pr(self, session, draft, *, dry_run=False):
        from mdp_functions.workbench_pr import publish

        latest = self.db.one(
            "SELECT result FROM control.workbench_run WHERE session_id=%s AND kind IN ('preview','backtest') AND input->>'model'=%s AND input->>'sql'=%s AND status='succeeded' ORDER BY created_at DESC LIMIT 1",
            (session["id"], draft["model"], draft["sql"]),
        )
        if not latest:
            raise ServiceError(
                "workbench_review_required",
                "Preview this exact draft successfully before opening its PR",
            )
        reviewed = latest["result"].get("buildB", latest["result"])
        return publish(session, draft, reviewed["columns"], dry_run=dry_run)

    def job(self, identity, user):
        run = self.db.one(
            "SELECT r.* FROM control.workbench_run r JOIN control.workbench_session s ON s.id=r.session_id WHERE r.id=%s AND s.user_id=%s",
            (UUID(identity), user),
        )
        if not run:
            raise ServiceError(
                "workbench_run_missing", "Run belongs to another session", 404
            )
        return run

    def artifact(self, ref, user):
        match = re.fullmatch(
            r"workbench/([a-f0-9-]+)/([a-f0-9-]+)/[a-zA-Z0-9_.-]+", ref
        )
        if not match:
            raise ServiceError(
                "workbench_artifact_invalid", "Invalid artifact reference"
            )
        self.session(match[1], user)
        self.job(match[2], user)
        if isinstance(self.store, LocalFsStore):
            return {"url": "/workbench/artifact?ref=" + quote(ref), "expiresIn": 300}
        return {
            "url": self.store.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.store.bucket, "Key": self.store.key(ref)},
                ExpiresIn=300,
            ),
            "expiresIn": 300,
        }

    def expire(self):
        sessions = self.db.all(
            "SELECT * FROM control.workbench_session s WHERE expires_at<=now() AND NOT EXISTS (SELECT 1 FROM control.workbench_run r WHERE r.session_id=s.id AND r.status IN ('queued','running'))"
        )
        with psycopg.connect(self.settings.workbench_admin_url) as conn:
            for session in sessions:
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(session["scratch_schema"])
                    )
                )
                role = session["scratch_schema"]
                if conn.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
                ).fetchone():
                    conn.execute(
                        sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role))
                    )
                    conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        return len(sessions)


def create_app(settings=None):
    settings = settings or Settings()
    wb = Workbench(settings)

    async def cleanup():
        while True:
            await asyncio.to_thread(wb.expire)
            now = datetime.now(timezone.utc)
            midnight = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            await asyncio.sleep((midnight - now).total_seconds())

    @asynccontextmanager
    async def lifespan(app):
        wb.db.execute(
            "UPDATE control.workbench_run SET status='failed',error=%s WHERE status IN ('queued','running')",
            (
                Jsonb(
                    {
                        "error_class": "workbench_restarted",
                        "message": "The workbench restarted; submit a fresh build",
                    }
                ),
            ),
        )
        task = asyncio.create_task(cleanup())
        yield
        task.cancel()
        for running in wb.tasks.values():
            running.cancel()
        await asyncio.gather(task, *wb.tasks.values(), return_exceptions=True)
        wb.db.close()
        wb.owner.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.middleware("http")
    async def auth(request, call_next):
        import hmac

        if request.url.path == "/health":
            return await call_next(request)
        if not settings.service_token or not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + settings.service_token
        ):
            return JSONResponse(
                {"error_class": "unauthorized", "message": "Bearer token required", "next_step": error_hint("unauthorized")["next_step"]}, 401
            )
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def error(request, exc):
        return JSONResponse(
            {"error_class": exc.error_class, "message": exc.message, "next_step": exc.next_step, "runbook": exc.runbook}, exc.status_code
        )

    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        return JSONResponse(database_failure(exc), 409)

    @app.post("/v1/workbench/{action}")
    async def dispatch(action: str, request: Request):
        body = await request.json()
        user = body.pop("userId")
        staff = body.pop("staff", False)
        warehouse_role = body.pop("warehouseRole", None)
        if staff:
            # Separate ownership also prevents a demoted admin reading old broad results.
            if not user.startswith("staff:"):
                raise ServiceError("forbidden", "Staff requests need a staff session. Open Workbench and start a session.", 403)
            if action == "sandboxAction":
                raise ServiceError("forbidden", "This action needs the admin role. Ask an operator; open the access runbook.", 403)
        if action in ('sandboxStatus', 'sandboxAction'):
            from mdp_functions.sandbox import freeze, status
            from mdp_functions.sandbox_accounts import disable
            with psycopg.connect(settings.workbench_admin_url) as conn:
                if action == 'sandboxStatus':
                    from mdp_functions.workbench_access import staff_sandboxes
                    entries = staff_sandboxes(conn, warehouse_role) if staff else status(conn)
                    return jsonable_encoder({'sandboxes': entries})
                entries = status(conn, body['schema'])
                if not entries:
                    from mdp_functions.sandbox_policy import message
                    raise ServiceError('sandbox_missing', message('sandbox_missing'), 404)
                if body['action'] in ('freeze','unfreeze'):
                    freeze(conn,body['schema'],body['action']=='freeze')
                elif body['action']=='disable':
                    disable(conn,entries[0]['owner_role'])
                else:
                    raise ServiceError('invalid_request','This action is unavailable. Choose freeze, unfreeze or disable; use the operator CLI for archives and passwords.',400)
            return {'message': 'Sandbox access updated. Review its status and tell the owner before their next query.'}
        if action == "queries":
            with psycopg.connect(settings.workbench_admin_url, row_factory=dict_row) as conn:
                # Here staff means a restricted analyst; admins arrive with staff=False.
                if staff:
                    queries = conn.execute(
                        "SELECT * FROM catalog.query_audit WHERE actor=%s "
                        "ORDER BY cross_tenant DESC, unresolved DESC, occurred_at DESC LIMIT 200",
                        (user,),
                    ).fetchall()
                else:
                    queries = conn.execute(
                        "SELECT * FROM catalog.query_audit "
                        "ORDER BY cross_tenant DESC, unresolved DESC, occurred_at DESC LIMIT 200"
                    ).fetchall()
                return jsonable_encoder({"queries": queries})
        if action == "createSession":
            return await asyncio.to_thread(wb.create_session, user)
        if action in ("status", "result", "cancel"):
            run = await asyncio.to_thread(wb.job, body["runId"], user)
            if action == "result":
                return run["result"]
            if action == "cancel" and body["runId"] in wb.tasks:
                wb.tasks[body["runId"]].cancel()
                await wb.tasks[body["runId"]]
                run = wb.job(body["runId"], user)
            return {
                "runId": body["runId"],
                "status": "cancelled"
                if (run["error"] or {}).get("error_class") == "workbench_cancelled"
                else run["status"],
                "progress": 1 if run["status"] in ("succeeded", "failed") else 0,
                "error": run["error"],
                "durationMs": run["duration_ms"],
            }
        if action in ("artifact", "artifactContent"):
            reference = wb.artifact(body["artifactRef"], user)
            if action == "artifactContent":
                return json.loads(
                    await asyncio.to_thread(wb.store.get, body["artifactRef"])
                )
            return reference
        session = await asyncio.to_thread(wb.session, body["sessionId"], user)
        if action == "draft":
            return await asyncio.to_thread(
                wb.draft, session, body.get("model"), body.get("sql")
            )
        if action == "query":
            body["operation"] = "query"
            return await wb.submit(session, "query", body)
        if action in (
            "previewModel",
            "backtest",
            "explain",
            "lineage",
            "saveAsPr",
        ):
            draft = await asyncio.to_thread(
                wb.draft,
                session,
                body.get("model", "mart_chart_history"),
                body.get("sql"),
                action not in ("explain", "lineage"),
            )
            body.update(draft)
            body["operation"] = {
                "query": "query",
                "previewModel": "preview",
                "backtest": "backtest",
            }.get(action, action)
        if action in ("previewModel", "backtest"):
            from mdp_functions.analyst_errors import failure

            if action == "previewModel" and not body.get("cycleId"):
                wb.require_built_model(body["model"])
                raise ServiceError(
                    "workbench_cycle_missing", failure("workbench_cycle_missing")["message"]
                )
            return await wb.submit(
                session, "backtest" if action == "backtest" else "preview", body
            )
        if action in ("explain", "lineage"):
            return await asyncio.to_thread(wb.explain, session, body["model"], draft)
        if action == "saveAsPr":
            return await asyncio.to_thread(
                wb.save_as_pr, session, draft, dry_run=body.get("dryRun", True) is not False
            )
        if action == "history":
            return jsonable_encoder(
                {
                    "runs": wb.db.all(
                        "SELECT id,kind,CASE WHEN error->>'error_class'='workbench_cancelled' THEN 'cancelled' ELSE status::text END AS status,input,created_at,duration_ms FROM control.workbench_run WHERE session_id=%s AND (kind IN ('preview','backtest') OR (kind='query' AND input->>'operation'='query')) ORDER BY created_at DESC LIMIT 20",
                        (session["id"],),
                    )
                }
            )
        if action == "models":
            return {
                "models": sorted(p.stem for p in (REPO / "dbt/models").rglob("*.sql")),
                "cycles": [
                    str(r["id"])
                    for r in wb.db.all(
                        "SELECT id FROM control.cycle WHERE status='closed' AND cadence='hourly' ORDER BY opened_at DESC LIMIT 20"
                    )
                ],
                "cycleDetails": jsonable_encoder(
                    wb.db.all(
                        "SELECT id,cadence,opened_at FROM control.cycle WHERE status='closed' ORDER BY opened_at DESC LIMIT 20"
                    )
                ),
                "sources": sorted(
                    {
                        r
                        for manifest in __import__(
                            "mdp_functions.registry", fromlist=["discover"]
                        )
                        .discover()
                        .values()
                        for r in manifest.writes
                    }
                ),
            }
        raise ServiceError("unknown_operation", "Unknown workbench operation", 404)

    return app


def main(host="0.0.0.0", port=8085):
    import uvicorn

    uvicorn.run(
        "mdp_functions.workbench:create_app",
        factory=True,
        host=os.environ.get("MDP_WORKBENCH_HOST", host),
        port=port,
    )


if __name__ == "__main__":
    main()
