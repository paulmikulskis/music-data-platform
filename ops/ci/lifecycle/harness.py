"""Executable lifecycle acceptance. Database observations use psql exclusively."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from mdp_functions.warehouse.postgres import manifest_sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[3]
CASES = ("a", "b", "c", "d", "e", "f", "g", "kill", "runner", "loop")


def lit(value):
    return "'" + str(value).replace("'", "''") + "'"


class Harness:
    def __init__(self, args):
        self.args = args
        self.log = None
        self.children = []
        self.env = dict(os.environ)
        self.service_env = self.env.copy()
        self.service_session = self.env.get(
            "MDP_LIFECYCLE_SERVICE_SESSION", "mdp-test-svc"
        )
        self.env["NO_COLOR"] = "1"
        self.env["DBT_USE_COLORS"] = "false"
        if (
            not self.env.get("MDP_CONTROL_RT_URL")
            and self.env.get("MDP_CONTROL_ADMIN_URL")
            and self.env.get("MDP_CONTROL_URL")
        ):
            options = conninfo_to_dict(self.env["MDP_CONTROL_ADMIN_URL"])
            options["dbname"] = conninfo_to_dict(self.env["MDP_CONTROL_URL"])["dbname"]
            self.env["MDP_CONTROL_RT_URL"] = make_conninfo(**options)
        self.env.setdefault(
            "MDP_SCHEMA_ROOT", tempfile.mkdtemp(prefix="mdp-lifecycle-schemas-")
        )
        self.env.setdefault("MDP_DEV_DB", str(Path(tempfile.mkdtemp()) / "dev.duckdb"))
        self.temp = Path(tempfile.mkdtemp(prefix="mdp-lifecycle-"))
        self.fixture_root = self.temp / "fixture-project"
        self.secrets = [
            v
            for k, v in self.env.items()
            if v
            and any(x in k for x in ("TOKEN", "PASSWORD", "SECRET", "_URL", "_KEY"))
        ]

    def clean(self, value):
        text = re.sub(r"\x1b\[[0-9;]*m", "", str(value)).replace(str(ROOT), "$REPO")
        text = re.sub(r"password=[^\s]+", "password=<redacted>", text)
        text = re.sub(r"./\s]+", "$USER_HOME", text)
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, "<redacted>")
        return re.sub(r"postgres(?:ql)?://[^\s]+", "<database-url>", text)

    def note(self, text):
        if self.log:
            self.log.write(self.clean(text) + "\n")
            self.log.flush()

    def check(self, condition, reason):
        self.note(f"ASSERT {'PASS' if condition else 'FAIL'}: {reason}")
        if not condition:
            raise AssertionError(reason)

    def command(
        self,
        argv,
        *,
        env=None,
        ok=True,
        timeout=360,
        background=False,
        stdin=None,
        isolated=False,
    ):
        merged = ({} if isolated else self.env) | (env or {})
        self.note("$ " + shlex.join(argv))
        if env:
            visible_env = {
                key: (
                    "<redacted>"
                    if any(
                        part in key
                        for part in ("TOKEN", "PASSWORD", "SECRET", "_URL", "_KEY")
                    )
                    else value
                )
                for key, value in env.items()
            }
            self.note("ENV " + json.dumps(visible_env, sort_keys=True))
        if background:
            path = self.temp / (uuid4().hex + ".log")
            stream = path.open("w")
            proc = subprocess.Popen(
                argv,
                cwd=ROOT,
                env=merged,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            self.children.append((proc, path, stream))
            return proc
        result = subprocess.run(
            argv,
            check=False,
            cwd=ROOT,
            env=merged,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        self.note(result.stdout + result.stderr)
        self.note(f"EXIT {result.returncode}")
        if ok:
            self.check(
                result.returncode == 0,
                ("command succeeded: " + shlex.join(argv[:6]))
                if result.returncode == 0
                else (
                    "command failed: "
                    + next(
                        (
                            line.strip()
                            for token in (
                                "plpy.Error:",
                                "Compilation Error",
                                "Database Error",
                                "syntax error",
                            )
                            for line in (result.stdout + result.stderr).splitlines()
                            if token in line
                        ),
                        shlex.join(argv[:6]),
                    )
                ),
            )
        return result

    def join(self, proc, timeout=240):
        proc.wait(timeout=timeout)
        child = next(c for c in self.children if c[0] is proc)
        child[2].close()
        output = child[1].read_text()
        self.note(output)
        self.note(f"BACKGROUND EXIT {proc.returncode}")
        self.children.remove(child)
        return output

    def database_env(self, warehouse=False, admin=False):
        key = (
            "MDP_CONTROL_ADMIN_URL"
            if admin
            else ("MDP_WAREHOUSE_URL" if warehouse else "MDP_CONTROL_URL")
        )
        self.check(bool(self.env.get(key)), f"{key} configured")
        opts = conninfo_to_dict(self.env[key])
        if warehouse and not admin:
            opts["user"] = self.env["MDP_PG_USER"]
            opts["password"] = self.env["MDP_PG_PASSWORD"]
        if admin:
            opts["dbname"] = conninfo_to_dict(
                self.env["MDP_WAREHOUSE_URL" if warehouse else "MDP_CONTROL_URL"]
            )["dbname"]
        mapping = {
            "dbname": "PGDATABASE",
            "host": "PGHOST",
            "port": "PGPORT",
            "user": "PGUSER",
            "password": "PGPASSWORD",
            "sslmode": "PGSSLMODE",
            "options": "PGOPTIONS",
        }
        return (
            self.env
            | {mapping[k]: str(v) for k, v in opts.items() if k in mapping}
            | {"PGCONNECT_TIMEOUT": "10"}
        )

    def sql(self, query, *, wh=False, admin=False, rows=True):
        self.note(
            f"$ psql ${'MDP_CONTROL_ADMIN_URL' if admin else 'MDP_WAREHOUSE_URL' if wh else 'MDP_CONTROL_URL'} [{'warehouse' if wh else 'control'}]\n{query}"
        )
        command = (
            f"SELECT coalesce(json_agg(q),'[]'::json) FROM ({query}) q;"
            if rows
            else query
        )
        result = subprocess.run(
            ["psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-f", "-"],
            input=command,
            check=False,
            env=self.database_env(wh, admin),
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.note(result.stdout + result.stderr)
        self.check(
            result.returncode == 0,
            "psql statement succeeded"
            if result.returncode == 0
            else next(
                (
                    line
                    for line in result.stderr.splitlines()
                    if "ERROR:" in line or "FATAL:" in line
                ),
                "psql failed",
            ),
        )
        return json.loads(result.stdout) if rows else result.stdout

    def scalar(self, query, **kwargs):
        rows = self.sql(query, **kwargs)
        return next(iter(rows[0].values())) if rows else None

    def api(self, path, body=None, ok=True, key=None):
        method = "GET" if body is None else "POST"
        self.note(
            f"$ HTTP {method} $MDP_SERVICE_URL{path} "
            + (json.dumps(body) if body is not None else "")
        )
        headers = {"Authorization": "Bearer " + self.env["MDP_SERVICE_TOKEN"]}
        if key:
            headers["Idempotency-Key"] = key
        retryable = method == "GET" or key is not None or path == "/v1/bind_cycle"
        deadline = time.monotonic() + 30
        while True:
            try:
                result = httpx.request(
                    method,
                    self.env["MDP_SERVICE_URL"].rstrip("/") + path,
                    json=body,
                    headers=headers,
                    timeout=20,
                    trust_env=False,
                )
                break
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if not retryable or time.monotonic() >= deadline:
                    raise
                self.note(f"HTTP transport retry {method} {path}: {type(exc).__name__}")
                time.sleep(1)
        # Full run responses include object locations; record only the public outcome fields.
        data = result.json()
        self.last_http_status = result.status_code
        if path.startswith("/v1/runs/") and isinstance(data, dict):
            self.note(
                json.dumps(
                    {
                        "status": result.status_code,
                        "run": {
                            k: data.get("run", {}).get(k)
                            for k in ("id", "status", "error_class", "rows_written")
                        },
                        "receipts": data.get("receipts", []),
                        "error_class": data.get("error_class"),
                        "message": data.get("message"),
                    }
                )
            )
        elif path.startswith("/v1/runs?") and isinstance(data, list):
            self.note(
                f"HTTP {result.status_code} "
                + json.dumps(
                    [
                        {k: row.get(k) for k in ("id", "cycle_id", "warehouse_id")}
                        for row in data
                    ]
                )
            )
        else:
            self.note(f"HTTP {result.status_code} {json.dumps(data)}")
        if ok:
            self.check(
                result.is_success,
                f"HTTP {result.status_code}: {data.get('error_class', 'request accepted') if isinstance(data, dict) else 'request accepted'}",
            )
        return data

    def plan(self, source="fixture_accounts", pages=5, fail_at=None, delay_ms=0, hold=False):
        return self.api(
            "/v1/_fixture/plan",
            {
                "source_key": source,
                "pages": pages,
                "fail_at": fail_at,
                "delay_ms": delay_ms,
                "hold": hold,
            },
        )

    def release(self, source="fixture_accounts"):
        """Open the barrier a holding plan's pages wait on."""
        released = self.api("/v1/_fixture/release", {"source_key": source})
        self.check(released.get("released") is True, f"{source} held page released")
        return released

    def wait(self, predicate, reason, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                self.note("WAIT PASS " + reason)
                return value
            time.sleep(0.5)
        raise AssertionError("timeout: " + reason)

    def identity(self, cadence="hourly", scope="global", reason="scheduled"):
        rid = "lifecycle-" + uuid4().hex
        return {
            "MDP_RUN_ID": rid,
            "DBT_CLOUD_RUN_ID": rid,
            "DBT_MDP_CADENCE": cadence,
            "DBT_MDP_SCOPE": scope,
            "DBT_CLOUD_JOB_ID": f"core-{cadence}-{scope}",
            "MDP_RUNNER": "core",
            "DBT_CLOUD_RUN_REASON_CATEGORY": reason,
            "DBT_TARGET_PATH": str(self.temp / rid),
            "DBT_LOG_PATH": str(self.temp / (rid + "-logs")),
        }

    def register(self, env):
        self.sql(
            f"INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ({lit(env['DBT_CLOUD_JOB_ID'])},'core',{lit(env['DBT_MDP_CADENCE'])},{lit(env['DBT_MDP_SCOPE'])}) ON CONFLICT DO NOTHING",
            admin=True,
            rows=False,
        )

    def dbt(self, env, *args, ok=True, background=False, project=None):
        return self.bound_command(
            [
                "uv",
                "run",
                "--project",
                "dbt",
                "dbt",
                *args,
                "--project-dir",
                str(project or self.fixture_root / "dbt"),
                "--profiles-dir",
                "dbt/profiles",
                "--target",
                self.args.target,
            ],
            env=env,
            ok=ok,
            background=background,
        )

    def bound_command(self, argv, *, env, ok=True, background=False):
        try:
            return self.command(argv, env=env, ok=ok, background=background)
        except AssertionError as exc:
            if background or not ok or "service_unreachable" not in str(exc):
                raise
            self.note(
                "RETRY service_unreachable: rerun once with the identical dbt binding/work identity"
            )
            self.wait(
                self.service_alive,
                "service available for same-identity retry",
                timeout=45,
            )
            return self.command(argv, env=env, ok=ok, background=False)

    def run(
        self,
        reason="scheduled",
        cycle=None,
        env=None,
        ok=True,
        background=False,
        variables=None,
        project_root=None,
    ):
        env = env or self.identity(reason=reason)
        argv = [
            str((project_root or self.fixture_root) / "ops/run.sh"),
            env["DBT_MDP_CADENCE"],
            "--reason-category",
            reason,
            "--target",
            self.args.target,
        ]
        if variables is not None:
            argv += ["--vars", json.dumps(variables)]
        if cycle:
            argv += ["--cycle-id", cycle]
        result = self.bound_command(argv, env=env, ok=ok, background=background)
        return env, result

    def cycle(self, env):
        result = self.scalar(
            f"SELECT cycle_id FROM control.cycle_attempt WHERE dbt_run_id={lit(env['DBT_CLOUD_RUN_ID'])}"
        )
        self.check(bool(result), "dbt identity bound to a cycle")
        return result

    def calls(self, cycle):
        return self.scalar(
            f"SELECT count(*) FROM control.call_ledger l JOIN control.run r ON r.id=l.run_id WHERE r.cycle_id={lit(cycle)}"
        )

    def manifest(self, cycle):
        return self.sql(
            f"SELECT dump_id FROM control.cycle_manifest({lit(cycle)}) ORDER BY dump_id"
        )

    def snapshot(self):
        return (
            self.sql("SELECT id,status,closed_at FROM control.cycle ORDER BY id")
            + self.sql(
                "SELECT dbt_run_id,cycle_id FROM control.cycle_attempt ORDER BY dbt_run_id"
            )
            + self.sql("SELECT id FROM control.call_ledger ORDER BY id")
        )

    def safety(self, claim=False):
        self.endpoint_safety()
        self.api("/v1/health")
        fixture = self.api("/v1/_fixture/state")
        self.guard(
            "started" in fixture,
            "authenticated fixture-only endpoint exists",
            "started" in fixture,
        )
        # A durable random run identity ties the service to this control database.
        # On a freshly provisioned empty stack the explicit claim establishes ownership.
        witness = self.sql(
            "SELECT id,cycle_id,warehouse_id FROM control.run ORDER BY created_at DESC LIMIT 1"
        )
        if witness:

            def read_witness():
                result = self.api(
                    "/v1/runs?limit=1000"
                    + (
                        "&cycle_id=" + witness[0]["cycle_id"]
                        if witness[0]["cycle_id"]
                        else ""
                    ),
                    ok=False,
                )
                if self.last_http_status == 503:
                    return None
                self.check(
                    self.last_http_status == 200, "service identity witness readable"
                )
                return next(
                    (row for row in result if row["id"] == witness[0]["id"]), None
                )

            remote = self.wait(read_witness, "service identity available", timeout=30)
            self.guard(
                all(remote.get(k) == v for k, v in witness[0].items()),
                "service and database run identities match",
                remote.get("id"),
            )
        self.check(
            self.scalar(
                "SELECT count(*) FROM control.warehouse WHERE is_production AND database='warehouse' AND adapter='postgres'"
            )
            == 1,
            "reset safety: selected control warehouse must be warehouse/postgres",
        )
        self.check(
            conninfo_to_dict(self.env["MDP_WAREHOUSE_URL"]).get("dbname")
            == "warehouse",
            "reset safety: warehouse URL selects warehouse",
        )
        self.check(
            self.scalar("SELECT runner FROM control.runner_mode") == "core",
            "reset safety: runner_mode must be core (cloud refused)",
        )
        self.stack_claim(claim)

    def fingerprint(self):
        admin = conninfo_to_dict(self.env["MDP_CONTROL_ADMIN_URL"])
        return f"{admin['host']}:{admin.get('port', '5432')}:{admin['dbname']}"

    def stack_claim(self, claim=False):
        fingerprint = self.fingerprint()
        query = "SELECT subject FROM control.audit_log WHERE action='lifecycle_stack_claimed' ORDER BY at DESC LIMIT 1"
        recorded = self.scalar(query)
        self.guard(
            recorded == fingerprint or (recorded is None and claim),
            "disposable audit claim matches stack fingerprint (first reset requires --claim)",
            {"expected": fingerprint, "recorded": recorded},
        )
        if recorded is None:
            self.sql(
                "BEGIN; SELECT pg_advisory_xact_lock(hashtext('lifecycle_stack_claim')); "
                "INSERT INTO control.audit_log(actor,action,subject,after) "
                f"SELECT 'lifecycle-harness','lifecycle_stack_claimed',{lit(fingerprint)},"
                f"jsonb_build_object('stack_fingerprint',{lit(fingerprint)}) "
                "WHERE NOT EXISTS (SELECT 1 FROM control.audit_log WHERE action='lifecycle_stack_claimed'); COMMIT;",
                admin=True,
                rows=False,
            )
            self.guard(
                self.scalar(query) == fingerprint,
                "new disposable audit claim verified",
                fingerprint,
            )

    def guard(self, condition, label, value):
        line = f"SAFETY {label}: {value} => {'PASS' if condition else 'REFUSE'}"
        self.note(line)
        print(self.clean(line), flush=True)
        self.check(condition, "reset safety: " + label)

    @staticmethod
    def authority(options):
        host = options.get("host", "")
        if host in ("localhost", "::1", "127.0.0.1"):
            host = "loopback"
        return host, str(options.get("port", "5432"))

    def endpoint_safety(self):
        admin = conninfo_to_dict(self.env["MDP_CONTROL_ADMIN_URL"])
        control = conninfo_to_dict(self.env["MDP_CONTROL_URL"])
        warehouse = conninfo_to_dict(self.env["MDP_WAREHOUSE_URL"])
        host = admin.get("host", "")
        allowed = host in ("127.0.0.1", "localhost", "::1") or (
            self.env.get("MDP_LIFECYCLE_ALLOW_REMOTE") == "1"
            and host.endswith((".internal", ".flycast"))
        )
        self.guard(allowed, "admin host is an allowed fixture endpoint", host)
        self.guard(
            admin.get("dbname") == control.get("dbname"),
            "admin database equals target control database",
            admin.get("dbname"),
        )
        endpoints = [control, warehouse]
        for key in ("MDP_CONTROL_RT_URL", "MDP_SERVICE_READ_URL"):
            if self.env.get(key):
                endpoints.append(conninfo_to_dict(self.env[key]))
        endpoints.append(
            {
                "host": self.env.get("MDP_PG_HOST", "127.0.0.1"),
                "port": self.env.get("MDP_PG_PORT", "5433"),
            }
        )
        self.guard(
            all(self.authority(e) == self.authority(admin) for e in endpoints),
            "all effective database endpoints match",
            self.authority(admin),
        )
        self.guard(
            warehouse.get("dbname")
            == self.env.get("MDP_PG_DB", "warehouse")
            == "warehouse",
            "warehouse and dbt database names match",
            warehouse.get("dbname"),
        )
        self.guard(
            not any(e.get("hostaddr") or e.get("service") for e in [admin, *endpoints])
            and not any(
                self.env.get(k) for k in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")
            ),
            "no alternate libpq authority",
            "checked URL parameters and ambient libpq settings",
        )

    def reset(self, claim=False):
        self.safety(claim=claim)
        owned = (
            "SELECT id FROM control.cycle WHERE opened_by_dbt_run_id LIKE 'lifecycle-%'"
        )
        running = self.scalar(
            f"SELECT count(*) FROM control.run r WHERE cycle_id IN ({owned}) AND status IN ('queued','running') "
            "AND (EXISTS (SELECT 1 FROM control.run_attempt a WHERE a.run_id=r.id AND a.status='running' AND a.deadline_at>now()) "
            "OR EXISTS (SELECT 1 FROM control.batch b WHERE b.run_id=r.id AND b.status IN ('running','draining') AND b.lease_expires_at>now()) "
            "OR NOT EXISTS (SELECT 1 FROM control.run_attempt a WHERE a.run_id=r.id))"
        )
        self.check(running == 0, "reset refuses active lifecycle workers")
        # Other frozen exports may still refer to this fixture target. Retire it
        # between cases, retaining their immutable membership identities.
        self.sql(
            "UPDATE control.target SET deactivated_at=coalesce(deactivated_at,now()) WHERE handle='lifecycle_new_target'",
            admin=True,
            rows=False,
        )
        cycles = [r["id"] for r in self.sql(owned)]
        if not cycles:
            return
        ids = ",".join(map(lit, cycles))
        unrelated = self.sql(
            f"SELECT id,cycle_id,run_id FROM control.dump WHERE cycle_id NOT IN ({ids}) OR cycle_id IS NULL ORDER BY id"
        )
        dumps = [
            r["id"]
            for r in self.sql(f"SELECT id FROM control.dump WHERE cycle_id IN ({ids})")
        ]
        dids = ",".join(map(lit, dumps)) or "NULL"
        foreign = self.scalar(
            f"SELECT count(*) FROM control.cycle_input WHERE dump_id IN ({dids}) AND cycle_id NOT IN ({ids})"
        )
        self.note(
            f"RESET referencing-row count (other cycles' control.cycle_input): {foreign}; "
            f"verified disposable claim lifecycle_stack_claimed={self.fingerprint()}; "
            "warehouse=warehouse; runner_mode=core; removing references in both databases"
        )
        tables = self.sql(
            "SELECT table_name,array_agg(column_name) AS columns FROM information_schema.columns "
            "WHERE table_schema='raw' AND column_name IN ('_cycle_id','_dump_id') GROUP BY table_name",
            wh=True,
        )
        raw = ["BEGIN; SET LOCAL lock_timeout='5s';"]
        for row in tables:
            name = row["table_name"].replace('"', '""')
            predicates = []
            if "_cycle_id" in row["columns"]:
                predicates.append(f"_cycle_id::text IN ({ids})")
            if "_dump_id" in row["columns"]:
                predicates.append(f"_dump_id::text IN ({dids})")
            raw.append(
                f'DELETE FROM raw."{name}" WHERE ' + " OR ".join(predicates) + ";"
            )
        for table, column, values in [
            # Match the service mirror's cycles -> attempts -> inputs order.
            ("cycles", "id", ids),
            ("cycle_attempts", "cycle_id", ids),
            ("cycle_inputs", "dump_id", dids),
            ("cycle_inputs", "cycle_id", ids),
            ("dump_stamps", "dump_id", dids),
            ("_load_receipts", "dump_id", dids),
            ("_fixture_receipts", "dump_id", dids),
        ]:
            if self.scalar(f"SELECT to_regclass('raw.{table}')", wh=True):
                raw.append(
                    f"DELETE FROM raw.{table} WHERE {column}::text IN ({values});"
                )
        self.reset_transaction("\n".join(raw) + "\nCOMMIT;", wh=True)
        # DELETE, rather than global TRUNCATE CASCADE, is intentional: preserve foreign work.
        statements = [
            f"BEGIN; CREATE TEMP TABLE own_runs AS SELECT id FROM control.run WHERE cycle_id IN ({ids});",
            "SELECT id FROM control.run WHERE id IN (SELECT id FROM own_runs) ORDER BY id FOR UPDATE;",
        ]
        for table in (
            "alert",
            "call_ledger",
            "cost_ledger",
            "dead_letter",
            "budget_reservation",
            "run_event",
            "batch",
            "run_attempt",
        ):
            statements.append(
                f"DELETE FROM control.{table} WHERE run_id IN (SELECT id FROM own_runs);"
            )
        statements += [
            f"DELETE FROM control.cycle_input WHERE cycle_id IN ({ids}) OR dump_id IN ({dids});",
            f"DELETE FROM control.cursor WHERE dump_id IN ({dids});",
            f"DELETE FROM control.load WHERE dump_id IN ({dids});",
            "UPDATE control.run SET input_dump_id=NULL WHERE id IN (SELECT id FROM own_runs);",
            f"DELETE FROM control.dump WHERE id IN ({dids});",
            "DELETE FROM control.run WHERE id IN (SELECT id FROM own_runs);",
            f"DELETE FROM control.target_export_member WHERE revision_id IN (SELECT id FROM control.target_export WHERE cycle_id IN ({ids}));",
            f"DELETE FROM control.target_export WHERE cycle_id IN ({ids});",
            f"DELETE FROM control.cycle_attempt WHERE cycle_id IN ({ids});",
            f"DELETE FROM control.cycle WHERE id IN ({ids});",
            "COMMIT;",
        ]
        self.reset_transaction("\n".join(statements))
        surviving = {
            r["id"]: r
            for r in self.sql(
                f"SELECT id,cycle_id,run_id FROM control.dump WHERE cycle_id NOT IN ({ids}) OR cycle_id IS NULL ORDER BY id"
            )
        }
        self.check(
            all(surviving.get(r["id"]) == r for r in unrelated),
            "reset preserves every pre-existing dump owned by other cycles",
        )
        self.check(
            self.scalar(
                f"SELECT count(*) FROM control.cycle_input WHERE dump_id IN ({dids})"
            )
            == 0,
            "reset removes all manifest references to deleted lifecycle dumps",
        )
        removed = (
            f"RESET removed referencing-row count (other cycles' control.cycle_input): {foreign}; "
            f"verified disposable claim lifecycle_stack_claimed={self.fingerprint()}; "
            "warehouse=warehouse; runner_mode=core"
        )
        self.note(removed)
        print(removed, flush=True)

    def reset_transaction(self, query, wh=False):
        # The live service mirrors these same tables. PostgreSQL aborts the
        # entire explicit reset transaction on deadlock, so retry is safe.
        for attempt in range(3):
            try:
                return self.sql(query, wh=wh, admin=True, rows=False)
            except AssertionError as exc:
                if "deadlock detected" not in str(exc) or attempt == 2:
                    raise
                self.note(
                    f"RESET transaction rolled back on deadlock; retry {attempt + 1}/2"
                )
                time.sleep(1)

    def configure_fixture_accounts(self, knobs):
        assignments = ",".join(f"{key}={int(value)}" for key, value in knobs.items())
        self.sql(
            "UPDATE control.streamline SET "
            + assignments
            + " WHERE source_key='fixture_accounts'",
            admin=True,
            rows=False,
        )
        self.sql(
            "UPDATE raw.streamlines SET " + assignments + " WHERE source_key='fixture_accounts'",
            wh=True,
            admin=True,
            rows=False,
        )

    def prepare(self):
        self.wait(self.service_alive, "fixture service healthy before case", timeout=45)
        self.api("/v1/health")
        self.reset()
        self.api("/v1/health")
        self.plan()
        self.select_fixture_targets()
        self.knobs_changed = True
        self.configure_fixture_accounts({"batch_size": 2, "max_concurrency": 1, "timeout_s": 120})
        self.check(
            self.scalar(
                "SELECT count(*) FROM control.target t JOIN control.target_set s ON s.id=t.target_set_id WHERE s.kind='account' AND s.tenant_id IS NULL AND t.activated_at IS NOT NULL AND t.deactivated_at IS NULL AND t.resolution_status='resolved'"
            )
            == 6,
            "exactly six active fixture accounts required",
        )

    def select_fixture_targets(self):
        active = "FROM control.target t JOIN control.target_set s ON s.id=t.target_set_id WHERE s.kind='account' AND s.tenant_id IS NULL AND t.activated_at IS NOT NULL AND t.deactivated_at IS NULL AND t.resolution_status='resolved'"
        if self.scalar("SELECT count(*) " + active) == 6:
            return
        self.check(
            self.scalar(
                "SELECT count(*) "
                + active
                + " AND t.handle ~ '^acceptance_account_[0-9]{3}$'"
            )
            == 6,
            "shared fixture membership contains six standard synthetic accounts",
        )
        extras = self.sql(
            "SELECT t.id " + active + " AND t.handle !~ '^acceptance_account_[0-9]{3}$'"
        )
        stamp = self.scalar("SELECT clock_timestamp()::text")
        self.paused_targets = (extras, stamp, self.env.copy())
        self.sql(
            "UPDATE control.target SET deactivated_at="
            + lit(stamp)
            + "::timestamptz WHERE id IN ("
            + ",".join(lit(r["id"]) for r in extras)
            + ")",
            admin=True,
            rows=False,
        )
        self.note(
            f"FIXTURE temporarily paused {len(extras)} extra accounts; membership restored after this case"
        )

    def restore_fixture_targets(self):
        if not self.paused_targets:
            return
        extras, stamp, target_env = self.paused_targets
        original_env = self.env
        try:
            self.env = target_env
            self.sql(
                "UPDATE control.target SET deactivated_at=NULL WHERE id IN ("
                + ",".join(lit(r["id"]) for r in extras)
                + ") AND deactivated_at="
                + lit(stamp)
                + "::timestamptz",
                admin=True,
                rows=False,
            )
        finally:
            self.env = original_env
        self.note(f"FIXTURE restored {len(extras)} temporarily paused accounts")
        self.paused_targets = None

    def verify_build(self, cycle):
        self.check(
            self.scalar(f"SELECT status FROM control.cycle WHERE id={lit(cycle)}")
            == "closed",
            "cycle closed",
        )
        self.check(
            self.scalar(
                f"SELECT count(*) FROM raw.account_snapshots WHERE _cycle_id::text={lit(cycle)}",
                wh=True,
            )
            > 0,
            "targeted rows landed",
        )
        lineage = [
            "_run_id",
            "_dump_id",
            "_landed_seq",
            "_cycle_id",
            "_revision_id",
            "_target_id",
            "_request_id",
            "_source_key",
            "_ingested_at",
            "_extra",
        ]
        self.check(
            self.scalar(
                f"SELECT count(*) FROM raw.account_snapshots WHERE _cycle_id::text={lit(cycle)} AND ("
                + " OR ".join(c + " IS NULL" for c in lineage)
                + ")",
                wh=True,
            )
            == 0,
            "all ten lineage columns non-null",
        )
        revisions = self.sql(
            f"SELECT id FROM control.target_export WHERE cycle_id={lit(cycle)} ORDER BY id"
        )
        self.check(bool(revisions), "target export frozen")
        self.check(
            self.sql(
                f"SELECT DISTINCT _revision_id AS id FROM raw.targets WHERE _cycle_id::text={lit(cycle)} ORDER BY id",
                wh=True,
            )
            == revisions,
            "raw.targets carries frozen revisions",
        )
        committed = self.sql(
            f"SELECT d.id AS dump_id FROM control.dump d JOIN control.run r ON r.id=d.run_id WHERE d.kind='output' AND r.scope='global' AND d.id::text IN (SELECT dump_id::text FROM control.cycle_manifest({lit(cycle)})) ORDER BY d.id"
        )
        manifest = self.manifest(cycle)
        self.check(
            manifest == committed and bool(manifest),
            "manifest contains only output dumps",
        )
        receipts = self.sql(
            "SELECT dump_id,committed_at FROM raw._load_receipts WHERE committed_at IS NOT NULL ORDER BY dump_id",
            wh=True,
        )
        closed = self.scalar(
            f"SELECT closed_at FROM control.cycle WHERE id={lit(cycle)}"
        )
        committed_ids = {r["dump_id"] for r in receipts if r["committed_at"] <= closed}
        eligible = self.sql(
            "SELECT d.id AS dump_id FROM control.dump d JOIN control.run r ON r.id=d.run_id WHERE d.kind='output' AND r.scope='global' ORDER BY d.id"
        )
        self.check(
            {r["dump_id"] for r in manifest}
            == {r["dump_id"] for r in eligible if r["dump_id"] in committed_ids},
            "manifest equals exactly committed eligible dumps at close",
        )
        self.check(
            self.sql(
                "SELECT DISTINCT _cycle_id FROM marts.mart_lifecycle_fixture", wh=True
            )
            == [{"_cycle_id": cycle}],
            "mart built against bound cycle",
        )
        self.check(
            self.scalar(
                "SELECT count(*) FROM staging.stg_fixture_accounts", wh=True
            )
            > 0,
            "staging built",
        )

    def case_a(self):
        env, _ = self.run()
        cycle = self.cycle(env)
        self.verify_build(cycle)
        self.verify_staging(cycle)
        before = self.manifest(cycle)
        # Land through the fixture transport after the first close, before rebuilding
        # its transforms. The second identity opens a distinct hourly cycle.
        self.plan()
        later = self.identity()
        self.register(later)
        self.dbt(later, "build", "--selector", "hourly_global_bronze")
        late_cycle = self.cycle(later)
        late = self.scalar(
            f"SELECT id FROM control.dump WHERE cycle_id={lit(late_cycle)} AND kind='output' ORDER BY created_at LIMIT 1"
        )
        self.check(bool(late), "fixture endpoint produced a later dump")
        self.dbt(env, "build", "--selector", "hourly_global_transform")
        self.check(
            self.manifest(cycle) == before,
            "old full manifest unchanged after late commit",
        )
        self.verify_staging(cycle)
        self.check(
            self.scalar(
                f"SELECT count(*) FROM staging.stg_fixture_accounts WHERE _dump_id::text={lit(late)}",
                wh=True,
            )
            == 0,
            "post-close dump absent from old staging",
        )
        self.dbt(later, "build", "--selector", "hourly_global_transform")
        self.verify_staging(late_cycle)
        self.check(
            self.scalar(
                f"SELECT count(*) FROM staging.stg_fixture_accounts WHERE _dump_id::text={lit(late)}",
                wh=True,
            )
            > 0,
            "post-close dump appears in next cycle staging",
        )

    def verify_staging(self, cycle):
        # D6: compare the full relation-specific manifest, without key deduplication.
        dump_sets = self.sql(
            "SELECT (SELECT array_agg(DISTINCT _dump_id::text ORDER BY _dump_id::text) "
            "FROM staging.stg_fixture_accounts WHERE _dump_id::text IN "
            "(SELECT DISTINCT _dump_id::text FROM raw.account_snapshots)) AS actual, "
            f"(SELECT array_agg(dump_id::text ORDER BY dump_id::text) FROM ({manifest_sql(lit(cycle))}) m "
            "WHERE dump_id::text IN "
            "(SELECT DISTINCT _dump_id::text FROM raw.account_snapshots)) AS expected",
            wh=True,
        )[0]
        self.check(
            bool(dump_sets["expected"])
            and dump_sets["actual"] == dump_sets["expected"],
            "staging dump array equals full bound manifest for raw.account_snapshots",
        )
        # Apply the relation's key deduplication to its eligible manifest history.
        expected = self.sql(
            "SELECT DISTINCT _dump_id FROM (SELECT _dump_id,row_number() OVER "
            "(PARTITION BY platform_account_id,snapshot_at ORDER BY _landed_seq DESC,_dump_id DESC) AS rn "
            "FROM raw.account_snapshots WHERE _dump_id IN "
            f"({manifest_sql(lit(cycle))})) q "
            "WHERE rn=1 ORDER BY _dump_id",
            wh=True,
        )
        actual = self.sql(
            "SELECT DISTINCT _dump_id FROM staging.stg_fixture_accounts ORDER BY _dump_id",
            wh=True,
        )
        manifest = {r["dump_id"] for r in self.manifest(cycle)}
        eligible = self.sql(
            "SELECT DISTINCT _dump_id FROM raw.account_snapshots WHERE _dump_id IN "
            f"({manifest_sql(lit(cycle))}) ORDER BY _dump_id",
            wh=True,
        )
        self.check(
            {r["_dump_id"] for r in eligible} <= manifest,
            "staging eligible dumps belong to queried control manifest",
        )
        self.check(
            bool(expected) and actual == expected,
            "staging distinct dump set equals exactly relation-eligible bound manifest after deduplication",
        )
        mirrored = self.sql(
            f"SELECT DISTINCT dump_id FROM ({manifest_sql(lit(cycle))}) m ORDER BY dump_id",
            wh=True,
        )
        self.check(
            mirrored == self.manifest(cycle),
            "warehouse manifest equals full control manifest",
        )

    def case_b(self):
        history, _ = self.run()
        history_cycle = self.cycle(history)
        self.sql(
            f"UPDATE raw.account_snapshots SET followers=40 WHERE _cycle_id::text={lit(history_cycle)}",
            wh=True,
            admin=True,
            rows=False,
        )
        env, failed = self.run(variables={"lifecycle_fail_transform": True}, ok=False)
        cycle = self.cycle(env)
        self.check(
            failed.returncode != 0
            and "PHASE 1 PASS" in failed.stdout
            and "lifecycle_fail_transform" in failed.stdout,
            "forced phase 3 failure observed",
        )
        before, manifest = self.calls(cycle), self.manifest(cycle)
        staging = self.sql(
            "SELECT DISTINCT _dump_id FROM staging.stg_fixture_accounts ORDER BY _dump_id",
            wh=True,
        )
        retry, _ = self.run(reason="other")
        self.check(self.cycle(retry) == cycle, "full retry binds same cycle")
        self.check(self.calls(cycle) == before, "zero new call_ledger rows")
        self.check(self.manifest(cycle) == manifest, "manifest unchanged")
        self.check(
            self.sql(
                "SELECT DISTINCT _dump_id FROM staging.stg_fixture_accounts ORDER BY _dump_id",
                wh=True,
            )
            == staging,
            "staging dump set unchanged",
        )
        self.verify_build(cycle)
        self.check(
            self.scalar(
                f"SELECT count(*) FROM marts.mart_lifecycle_fixture g JOIN raw.account_snapshots s "
                "ON s.platform_account_id=g.platform_account_id AND s.snapshot_at=g.snapshot_at "
                f"WHERE s._cycle_id::text={lit(cycle)} AND g.followers_delta=s.followers-40 "
                "AND g.followers_delta>0",
                wh=True,
            )
            == 6,
            "six current snapshots have the known historical-to-current growth delta after retry",
        )

    def case_c(self):
        self.plan(fail_at=3)
        env, failed = self.run(ok=False)
        cycle = self.cycle(env)
        self.check(failed.returncode != 0, "phase 1 ends red")
        self.check(
            self.scalar(f"SELECT status FROM control.cycle WHERE id={lit(cycle)}")
            == "open",
            "failed bronze leaves cycle open",
        )
        run = self.scalar(
            f"SELECT r.id FROM control.run r JOIN control.streamline s ON s.id=r.streamline_id WHERE r.cycle_id={lit(cycle)} AND s.source_key='fixture_accounts'"
        )
        batches = self.sql(
            f"SELECT index,status,dump_ids FROM control.batch WHERE run_id={lit(run)} ORDER BY index"
        )
        self.check(
            len(batches) == 3
            and [b["index"] for b in batches if b["status"] == "succeeded"] == [0, 1],
            "exactly batches 1-2 succeeded out of three",
        )
        revision = self.scalar(
            f"SELECT revision_id FROM control.run WHERE id={lit(run)}"
        )
        self.plan()
        retry, retried = self.run(reason="other", ok=False)
        self.check(retried.returncode == 0, "full retry completes both dbt phases")
        self.check(self.cycle(retry) == cycle, "retry reuses original open cycle")
        self.check(
            self.scalar(
                f"SELECT count(*) FROM control.run_attempt WHERE run_id={lit(run)}"
            )
            == 2,
            "exactly two attempts",
        )
        after = self.sql(
            f"SELECT index,status,dump_ids FROM control.batch WHERE run_id={lit(run)} ORDER BY index"
        )
        self.check(
            after[:2] == batches[:2] and all(b["status"] == "succeeded" for b in after),
            f"retry exit={retried.returncode}; batch statuses={[b['status'] for b in after]}; expected three succeeded with batches 1-2 dumps unchanged",
        )
        # The outage is a transport error: its page's failed attempts are the client's retries.
        counts = self.sql(
            f"SELECT b.index,count(l.id) FILTER (WHERE l.http_status=200) AS landed,count(l.id) FILTER (WHERE l.http_status IS DISTINCT FROM 200) AS failed FROM control.batch b LEFT JOIN control.call_ledger l ON l.run_id=b.run_id AND l.target_id=ANY(b.target_ids) WHERE b.run_id={lit(run)} GROUP BY b.index ORDER BY b.index"
        )
        self.check(
            all(
                r["landed"] == 2 and (r["failed"] == 0 if r["index"] < 2 else 1 <= r["failed"] <= 4)
                for r in counts
            ),
            "at most one repeated page per unfinished batch; completed pages never repeat",
        )
        self.check(
            self.sql(
                f"SELECT DISTINCT _revision_id FROM raw.account_snapshots WHERE _run_id::text={lit(run)}",
                wh=True,
            )
            == [{"_revision_id": revision}],
            "original revision retained",
        )
        self.verify_build(cycle)

    def bind(self, env):
        self.register(env)
        return self.api(
            "/v1/bind_cycle",
            {
                "runner": "core",
                "cadence": env["DBT_MDP_CADENCE"],
                "scope": env["DBT_MDP_SCOPE"],
                "dbt_run_id": env["DBT_CLOUD_RUN_ID"],
                "reason_category": env["DBT_CLOUD_RUN_REASON_CATEGORY"],
                "job_id": env["DBT_CLOUD_JOB_ID"],
                # The current hook's shape (register() leaves the job's list empty); a bind without
                # it is a pre-stamp hook, refused with runner_outdated.
                "global_inputs": [],
            },
        )["cycle_id"]

    def invoke(self, env, source):
        body = {
            "source_key": source,
            "dbt_run_id": env["DBT_CLOUD_RUN_ID"],
            "cadence": env["DBT_MDP_CADENCE"],
            "model": "lifecycle_probe",
        }
        key = hashlib.sha256(
            "|".join(
                [source, env["DBT_CLOUD_RUN_ID"], "lifecycle_probe", "", ""]
            ).encode()
        ).hexdigest()
        return self.api("/v1/invoke", body, key=key)["run_id"]

    def terminal(self, run):
        result = self.api("/v1/runs/" + run, ok=False)
        if self.last_http_status == 503:
            self.note(
                "RETRY transient 503 within the existing recovery polling deadline"
            )
            return None
        self.check(
            self.last_http_status == 200,
            f"run polling HTTP {self.last_http_status}: {result.get('error_class')}",
        )
        return (
            result
            if result["run"]["status"]
            in ("succeeded", "failed", "partial", "superseded", "cancelled")
            else None
        )

    def case_d(self):
        env = self.identity()
        self.register(env)
        self.dbt(env, "build", "--select", "bronze_export__targets_hourly")
        cycle = self.cycle(env)
        # Data-changing SQL is deliberately not wrapped in the SELECT JSON reader.
        self.sql(
            "INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) SELECT id,'fixture','lifecycle-new','lifecycle_new_target','resolved',now() FROM control.target_set WHERE kind='account' AND tenant_id IS NULL ON CONFLICT(target_set_id,platform,platform_account_id) WHERE resolution_status='resolved' DO UPDATE SET activated_at=EXCLUDED.activated_at,deactivated_at=NULL",
            admin=True,
            rows=False,
        )
        target = self.scalar(
            "SELECT id FROM control.target WHERE handle='lifecycle_new_target'"
        )
        self.plan(pages=6)
        self.dbt(
            env, "build", "--select", "bronze_invoke__fixture_accounts", "bronze_close__hourly"
        )
        self.check(
            self.scalar(
                f"SELECT count(*) FROM raw.account_snapshots WHERE _cycle_id::text={lit(cycle)} AND _target_id::text={lit(target)}",
                wh=True,
            )
            == 0,
            "target activated after export absent from this cycle",
        )
        following, _ = self.run()
        following_cycle = self.cycle(following)
        self.check(
            self.scalar(
                f"SELECT count(*) FROM raw.targets WHERE _cycle_id::text={lit(following_cycle)} AND id::text={lit(target)}",
                wh=True,
            )
            == 1,
            "new target appears in next scheduled export",
        )
        # First materialize the daily table through the real service, then block only its row landing.
        self.plan("lifecycle_daily_probe", pages=1)
        warm = self.identity(cadence="daily")
        self.bind(warm)
        warm_run = self.invoke(warm, "lifecycle_daily_probe")
        self.check(
            self.wait(lambda: self.terminal(warm_run), "daily probe warmup")["run"][
                "status"
            ]
            == "succeeded",
            "daily probe initialized",
        )
        self.api("/v1/cycles/" + self.cycle(warm) + "/close", {})
        lock_env = self.database_env(True, True)
        self.note(
            "$ psql $MDP_CONTROL_ADMIN_URL [warehouse] BEGIN; LOCK raw.lifecycle_daily_probe IN ACCESS EXCLUSIVE MODE;"
        )
        lock = subprocess.Popen(
            ["psql", "-XAt", "-v", "ON_ERROR_STOP=1"],
            env=lock_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        try:
            lock.stdin.write(
                "BEGIN; LOCK raw.lifecycle_daily_probe IN ACCESS EXCLUSIVE MODE; SELECT 'locked';\n"
            )
            lock.stdin.flush()
            while lock.stdout.readline().strip() != "locked":
                self.check(lock.poll() is None, "daily table landing lock acquired")
            self.plan("lifecycle_daily_probe", pages=1, delay_ms=1000)
            late = self.identity(cadence="daily")
            self.bind(late)
            late_run = self.invoke(late, "lifecycle_daily_probe")
            dump = self.wait(
                lambda: self.scalar(
                    f"SELECT id FROM control.dump WHERE run_id={lit(late_run)} AND kind='output'"
                ),
                "daily dump reserves landed_seq before hourly close",
            )
            self.check(
                self.scalar(
                    f"SELECT count(*) FROM raw._load_receipts WHERE dump_id::text={lit(dump)} AND committed_at IS NOT NULL",
                    wh=True,
                )
                == 0,
                "daily receipt still uncommitted",
            )
            boundary = self.identity()
            self.register(boundary)
            self.dbt(boundary, "build", "--selector", "hourly_global_bronze")
            boundary_cycle = self.cycle(boundary)
            old_manifest = self.manifest(boundary_cycle)
            self.check(
                self.scalar(
                    f"SELECT count(*) FROM control.cycle_manifest({lit(boundary_cycle)}) WHERE dump_id={lit(dump)}"
                )
                == 0,
                "uncommitted daily dump absent at hourly close",
            )
            self.check(
                self.scalar(
                    f"SELECT created_at < (SELECT closed_at FROM control.cycle WHERE id={lit(boundary_cycle)}) FROM control.dump WHERE id={lit(dump)}"
                ),
                "daily sequence allocation precedes hourly close",
            )
        finally:
            self.note("$ psql [daily landing lock] COMMIT;")
            lock.stdin.write("COMMIT;\n\\q\n")
            lock.stdin.flush()
            lock.wait(timeout=10)
        self.check(
            self.wait(lambda: self.terminal(late_run), "late daily landing completes")[
                "run"
            ]["status"]
            == "succeeded",
            "late daily output committed",
        )
        self.check(
            self.scalar(
                f"SELECT clock_timestamp() > (SELECT closed_at FROM raw.cycles WHERE id::text={lit(boundary_cycle)}) AND committed_at IS NOT NULL "
                f"FROM raw._load_receipts WHERE dump_id::text={lit(dump)}",
                wh=True,
            ),
            "daily receipt is visible after releasing the transaction blocked across hourly close",
        )
        self.note(
            "Receipt committed_at uses transaction time; commit ordering is proved by absence while locked across close and visibility after release."
        )
        self.dbt(boundary, "build", "--selector", "hourly_global_transform")
        project = self.temp / "race-project"
        shutil.copytree(
            ROOT / "dbt",
            project,
            ignore=shutil.ignore_patterns(
                ".venv", "target", "logs", ".git", "dbt_packages"
            ),
        )
        shutil.copy(
            ROOT / "ops/ci/lifecycle/race_staging.sql",
            project / "models/lifecycle_race_staging.sql",
        )
        self.dbt(
            boundary, "build", "--select", "lifecycle_race_staging", project=project
        )
        self.check(
            self.manifest(boundary_cycle) == old_manifest,
            "old manifest remains unchanged after late commit and transform",
        )
        self.check(
            self.scalar(
                f"SELECT count(*) FROM staging.lifecycle_race_staging WHERE _dump_id::text={lit(dump)}",
                wh=True,
            )
            == 0,
            "hourly transform consuming daily source excludes late-committed dump",
        )
        self.check(
            self.scalar("SELECT count(*) FROM staging.lifecycle_race_staging", wh=True)
            > 0,
            "race transform consumed committed daily history",
        )
        self.verify_staging(boundary_cycle)
        self.api("/v1/cycles/" + self.cycle(late) + "/close", {})
        after = self.identity(cadence="daily")
        after_cycle = self.bind(after)
        self.api("/v1/cycles/" + after_cycle + "/close", {})
        self.check(
            self.scalar(
                f"SELECT count(*) FROM control.cycle_manifest({lit(after_cycle)}) WHERE dump_id={lit(dump)}"
            )
            == 1,
            "next daily manifest includes late daily dump exactly once",
        )
        self.dbt(after, "build", "--select", "lifecycle_race_staging", project=project)
        self.check(
            self.scalar(
                f"SELECT count(*) FROM staging.lifecycle_race_staging WHERE _dump_id::text={lit(dump)}",
                wh=True,
            )
            > 0,
            "next daily transform includes the late dump",
        )

    def case_e(self):
        # The old page is held on the fake vendor's barrier, not a sleep, until the harness has seen
        # the new cycle supersede the old one and the batch read draining: no timing race under load.
        # Keep the fixture attempt alive beyond the supersession poll's startup budget.
        self.configure_fixture_accounts({"timeout_s": 600})
        self.plan(hold=True)
        first, old_process = self.run(background=True)
        self.wait(
            lambda: self.api("/v1/_fixture/state")["started"].get("fixture_accounts", 0) >= 1,
            "old page is in flight",
        )
        old = self.cycle(first)
        run = self.scalar(
            f"SELECT r.id FROM control.run r JOIN control.streamline s ON s.id=r.streamline_id WHERE r.cycle_id={lit(old)} AND s.source_key='fixture_accounts'"
        )
        count = self.scalar(
            f"SELECT count(*) FROM control.batch WHERE run_id={lit(run)}"
        )
        active = self.scalar(
            f"SELECT id FROM control.batch WHERE run_id={lit(run)} AND status='running'"
        )
        # The new cycle's pages run at once; the old page stays held.
        self.plan()
        second, new_process = self.run(background=True)
        log = next(c[1] for c in self.children if c[0] is new_process)
        # Core runs of one cadence and scope serialize: the next scheduled run waits on the
        # runner lock while the old job is alive.
        self.wait(lambda: "RUNNER LOCK WAIT" in log.read_text(), "next scheduled run waits on core lock")
        # The old dbt job dies with its page still in flight; its lock goes with its session,
        # and the service keeps the page running.
        self.note("KILL old Core job process group while its page is in flight")
        os.killpg(old_process.pid, signal.SIGTERM)
        # Cancellation releases the runner lock before the next job parses and binds.
        # Poll the database through those steps; the shared 90-second wait is too short
        # when dbt startup competes for CPU. The held page is released only after proof.
        self.wait(
            lambda: (
                self.scalar(f"SELECT status FROM control.cycle WHERE id={lit(old)}")
                == "superseded"
            ),
            "previous cycle superseded",
            timeout=300,
        )
        self.check(
            self.scalar(f"SELECT status FROM control.batch WHERE id={lit(active)}")
            == "draining",
            "in-flight batch transitions to draining",
        )
        # Only now may the paid page finish: it lands, and its batch starts nothing after it.
        self.release()
        self.join(old_process)
        self.join(new_process)
        self.check(
            old_process.returncode != 0 and new_process.returncode == 0,
            "superseded job red and new job green",
        )
        self.check(
            self.scalar(f"SELECT status FROM control.run WHERE id={lit(run)}")
            == "superseded",
            "old run ends superseded",
        )
        self.check(
            self.scalar(f"SELECT count(*) FROM control.batch WHERE run_id={lit(run)}")
            == count,
            "old batch count unchanged",
        )
        self.check(
            self.scalar(
                f"SELECT count(*) FROM control.call_ledger WHERE run_id={lit(run)} AND target_id NOT IN (SELECT unnest(target_ids) FROM control.batch WHERE id={lit(active)})"
            )
            == 0,
            "no subsequent old batch starts",
        )
        self.check(
            self.scalar(
                f"SELECT count(*) FROM raw.account_snapshots WHERE _run_id::text={lit(run)}",
                wh=True,
            )
            >= 1,
            "paid in-flight page landed",
        )
        self.check(
            self.scalar(
                f"SELECT count(*) FROM raw._load_receipts WHERE dump_id IN (SELECT _dump_id FROM raw.account_snapshots WHERE _run_id::text={lit(run)}) AND committed_at IS NOT NULL",
                wh=True,
            )
            >= 1,
            "drained page has committed warehouse receipt",
        )
        new = self.cycle(second)
        self.check(
            self.scalar(
                f"SELECT count(*) FROM control.run_attempt a JOIN control.run r ON r.id=a.run_id WHERE a.dbt_run_id={lit(second['DBT_CLOUD_RUN_ID'])} AND r.cycle_id<>{lit(new)}"
            )
            == 0,
            "new attempts reference only the new cycle",
        )
        # Separate the paid-page drain proof above from startup recovery: interrupt
        # another superseded run while it still has unfinished draining work.
        pending = self.identity()
        self.register(pending)
        self.dbt(pending, "build", "--select", "bronze_export__targets_hourly")
        self.plan(delay_ms=15000)
        run = self.invoke(pending, "fixture_accounts")
        self.wait(
            lambda: (
                self.api("/v1/_fixture/state")["started"].get("fixture_accounts", 0) >= 1
                and self.scalar(
                    f"SELECT count(*) FROM control.batch WHERE run_id={lit(run)} AND status='running'"
                )
                == 1
            ),
            "pending recovery fixture has an active batch",
        )
        self.bind(self.identity())
        self.check(
            self.scalar(
                f"SELECT count(*) FROM control.batch WHERE run_id={lit(run)} AND status='draining'"
            )
            == 1,
            "superseded unfinished batch is draining before restart",
        )
        old_attempts = self.sql(
            f"SELECT id,deadline_at FROM control.run_attempt WHERE run_id={lit(run)} ORDER BY id"
        )
        if self.args.target == "pg_local":
            self.command(["tmux", "send-keys", "-t", self.service_session, "C-c"])
            try:
                self.wait(
                    lambda: not self.service_alive(),
                    "service stopped for supersession recovery",
                    timeout=40,
                )
                self.wait(
                    lambda: self.scalar(
                        f"SELECT now()>max(lease_expires_at) FROM control.batch WHERE run_id={lit(run)}"
                    ),
                    "superseded lease elapsed before startup recovery",
                    timeout=60,
                )
                old_calls = self.sql(
                    f"SELECT id FROM control.call_ledger WHERE run_id={lit(run)} ORDER BY id"
                )
            finally:
                self.restart_local()
        else:
            self.check(
                bool(self.env.get("MDP_SERVICE_RESTART_COMMAND"))
                and bool(self.env.get("MDP_SERVICE_STOP_COMMAND")),
                "supersession recovery needs stop and restart commands",
            )
            self.command(["sh", "-c", self.env["MDP_SERVICE_STOP_COMMAND"]])
            try:
                self.wait(
                    lambda: not self.service_alive(), "service stopped", timeout=40
                )
                self.wait(
                    lambda: self.scalar(
                        f"SELECT now()>max(lease_expires_at) FROM control.batch WHERE run_id={lit(run)}"
                    ),
                    "superseded lease elapsed",
                    timeout=60,
                )
                old_calls = self.sql(
                    f"SELECT id FROM control.call_ledger WHERE run_id={lit(run)} ORDER BY id"
                )
            finally:
                self.command(["sh", "-c", self.env["MDP_SERVICE_RESTART_COMMAND"]])
        self.wait(self.service_alive, "service healthy after restart", timeout=45)
        self.wait(
            lambda: (
                self.scalar(f"SELECT status FROM control.run WHERE id={lit(run)}")
                == "superseded"
            ),
            "asynchronous recovery settled superseded unfinished work",
            timeout=90,
        )
        self.check(
            self.sql(
                f"SELECT id,deadline_at FROM control.run_attempt WHERE run_id={lit(run)} ORDER BY id"
            )
            == old_attempts,
            "recovery does not alter superseded attempts",
        )
        self.check(
            self.sql(
                f"SELECT id FROM control.call_ledger WHERE run_id={lit(run)} ORDER BY id"
            )
            == old_calls,
            "recovery makes no calls for superseded unfinished work",
        )
        self.check(
            self.scalar(f"SELECT status FROM control.run WHERE id={lit(run)}")
            == "superseded",
            "startup recovery settles unfinished superseded work without resuming it",
        )

    def case_f(self):
        first, _ = self.run()
        cycle = self.cycle(first)
        calls = self.calls(cycle)
        rerun, _ = self.run(reason="other")
        self.check(
            self.cycle(rerun) == cycle and self.calls(cycle) == calls,
            "manual full rerun reuses closed cycle with zero vendor calls",
        )
        failed = self.dbt(
            rerun,
            "build",
            "--selector",
            "hourly_global_transform",
            "--vars",
            '{"lifecycle_fail_transform": true}',
            ok=False,
        )
        self.check(failed.returncode != 0, "real failed build creates retry artifacts")
        before = self.snapshot()
        retry = self.dbt(rerun, "retry", ok=False)
        self.check(
            retry.returncode != 0
            and "partial_retry_refused" in retry.stdout + retry.stderr,
            "real dbt retry refused at on-run-start",
        )
        self.check(
            self.snapshot() == before,
            "partial retry touches no cycles, bindings, or calls",
        )
        self.plan("lifecycle_tenant_probe", pages=1)
        overlay_root = self.temp / "tenant-project"
        project = overlay_root / "dbt"
        shutil.copytree(
            ROOT / "dbt",
            project,
            ignore=shutil.ignore_patterns(
                ".venv", "target", "logs", ".git", "dbt_packages"
            ),
        )
        fixtures = ROOT / "ops/ci/lifecycle/models"
        shutil.copytree(fixtures, project / "models/lifecycle", dirs_exist_ok=True)
        shutil.copy(
            ROOT / "ops/ci/lifecycle/tenant-selectors.yml", project / "selectors.yml"
        )
        self.command(
            [
                "uv",
                "run",
                "--project",
                "functions",
                "python",
                "ops/ci/lifecycle/export_overlay.py",
                str(overlay_root),
            ],
            env={"MDP_FIXTURE_MODE": "1"},
        )
        # The real runner is copied without edits so its project root is the fixture overlay.
        (overlay_root / "ops").mkdir(exist_ok=True)
        shutil.copy(ROOT / "ops/run.sh", overlay_root / "ops/run.sh")
        for helper in ("runner_lock.py", "runner_restore.py", "heartbeat.py"):
            shutil.copy(ROOT / "ops" / helper, overlay_root / "ops" / helper)
        (overlay_root / "functions").symlink_to(
            ROOT / "functions", target_is_directory=True
        )
        (project / ".venv").symlink_to(ROOT / "dbt/.venv", target_is_directory=True)
        tenants = []
        for slug in ("lifecycle_one", "lifecycle_two"):
            self.sql(
                f"INSERT INTO control.tenant(slug,name) VALUES ({lit(slug)},{lit(slug)}) ON CONFLICT(slug) DO NOTHING",
                admin=True,
                rows=False,
            )
            tenant = self.scalar(
                f"SELECT id FROM control.tenant WHERE slug={lit(slug)}"
            )
            env = self.identity(cadence="daily", scope="tenant:" + tenant)
            self.register(env)
            self.run(
                env=env, variables={"tenant_slug": slug}, project_root=overlay_root
            )
            c = self.cycle(env)
            run = self.scalar(
                f"SELECT r.id FROM control.run r JOIN control.streamline s ON s.id=r.streamline_id WHERE r.cycle_id={lit(c)} AND s.source_key='lifecycle_tenant_probe'"
            )
            response = self.api("/v1/runs/" + run)
            self.check(
                bool(response["receipts"])
                and all(r["run_id"] == run for r in response["receipts"]),
                "tenant receipts reference only their own run",
            )
            self.check(
                self.sql(
                    f"SELECT DISTINCT tenant_id FROM tenant_{slug}_staging.lifecycle_tenant_staging",
                    wh=True,
                )
                == [{"tenant_id": tenant}],
                "tenant staging contains only own tenant",
            )
            self.check(
                self.sql(
                    f"SELECT DISTINCT _cycle_id FROM tenant_{slug}_marts.lifecycle_tenant_mart",
                    wh=True,
                )
                == [{"_cycle_id": c}],
                "tenant mart uses isolated schema and cycle",
            )
            tenants.append(
                (
                    c,
                    self.scalar(
                        f"SELECT work_key FROM control.run WHERE id={lit(run)}"
                    ),
                )
            )
        self.check(
            tenants[0][0] != tenants[1][0] and tenants[0][1] != tenants[1][1],
            "two tenant jobs have disjoint cycles and work keys",
        )

    def case_g(self):
        self.plan("lifecycle_daily_probe", pages=1)
        daily = self.identity(cadence="daily")
        self.bind(daily)
        probe = self.invoke(daily, "lifecycle_daily_probe")
        self.check(
            self.wait(lambda: self.terminal(probe), "other-source history committed")[
                "run"
            ]["status"]
            == "succeeded",
            "Replay fixture includes another source's committed output",
        )
        self.api("/v1/cycles/" + self.cycle(daily) + "/close", {})
        first, _ = self.run()
        cycle = self.cycle(first)
        manifest = self.manifest(cycle)
        staged = self.sql(
            "SELECT DISTINCT _dump_id FROM staging.stg_fixture_accounts ORDER BY _dump_id",
            wh=True,
        )
        revision = self.sql(
            "SELECT DISTINCT _revision_id FROM staging.stg_control__targets_hourly ORDER BY _revision_id",
            wh=True,
        )
        self.run()
        current = self.sql(
            "SELECT DISTINCT _dump_id FROM staging.stg_fixture_accounts ORDER BY _dump_id",
            wh=True,
        )
        before = self.scalar("SELECT count(*) FROM control.call_ledger")
        replay, result = self.run(reason="other", cycle=cycle)
        self.check(self.cycle(replay) == cycle, "Replay binds older closed cycle")
        self.check(
            self.scalar("SELECT count(*) FROM control.call_ledger") == before,
            "Replay makes zero vendor calls",
        )
        self.check(self.manifest(cycle) == manifest, "Replay manifest unchanged")
        # run.sh --cycle-id then restores the current build under the same lock, with a new run id.
        self.check(
            "RESTORE" in result.stdout
            and self.sql(
                "SELECT DISTINCT _dump_id FROM staging.stg_fixture_accounts ORDER BY _dump_id",
                wh=True,
            )
            == current,
            "Replay restore rebuilds the current build",
        )
        # The Replay's own build, rebuilt with its binding: the older cycle's manifest.
        self.dbt(replay, "build", "--select", "stg_fixture_accounts", "stg_control__targets_hourly")
        self.check(
            self.sql(
                "SELECT DISTINCT _dump_id FROM staging.stg_fixture_accounts ORDER BY _dump_id",
                wh=True,
            )
            == staged,
            "Replay staging dump set equals first build",
        )
        self.verify_staging(cycle)
        self.check(
            self.sql(
                "SELECT DISTINCT _revision_id FROM staging.stg_control__targets_hourly ORDER BY _revision_id",
                wh=True,
            )
            == revision,
            "Replay targets show first revision",
        )
        self.run(reason="other")
        opened = self.bind(self.identity())
        superseding = self.bind(self.identity())
        for rejected in (opened, superseding):
            before_state = self.snapshot()
            _, result = self.run(reason="other", cycle=rejected, ok=False)
            self.check(
                result.returncode != 0
                and "replay_refused" in result.stdout + result.stderr,
                "Replay refuses superseded/open cycle",
            )
            self.check(
                self.snapshot() == before_state, "refused Replay touches nothing"
            )

    def case_runner(self):
        bound = self.identity()
        cycle = self.bind(bound)
        self.dbt(bound, "build", "--selector", "hourly_global_bronze")
        before = self.snapshot()
        try:
            self.sql(
                "UPDATE control.runner_mode SET runner='cloud'", admin=True, rows=False
            )
            try:
                self.reset()
            except AssertionError as exc:
                self.check(
                    "runner_mode must be core" in str(exc),
                    "reset refuses Cloud mode before deleting",
                )
            else:
                raise AssertionError("reset unexpectedly accepted Cloud mode")
            self.check(
                self.snapshot() == before,
                "refused Cloud reset changes no lifecycle state",
            )
            _, failed = self.run(ok=False)
            self.check(
                failed.returncode != 0
                and "runner_inactive" in failed.stdout + failed.stderr,
                "Core on-run-start refuses runner_mode=cloud",
            )
            self.check(self.snapshot() == before, "inactive runner opens no cycle")
            self.dbt(bound, "build", "--selector", "hourly_global_transform")
            self.check(
                self.cycle(bound) == cycle,
                "already-bound Core work finishes on original cycle after switch",
            )
            self.verify_staging(cycle)
        finally:
            self.sql(
                "UPDATE control.runner_mode SET runner='core'", admin=True, rows=False
            )
        env, _ = self.run()
        self.check(bool(self.cycle(env)), "runner_mode=core opens a cycle")

    def case_kill(self):
        self.check(
            self.args.target == "pg_local"
            or bool(self.env.get("MDP_SERVICE_RESTART_COMMAND")),
            "deployed restart requires MDP_SERVICE_RESTART_COMMAND",
        )
        self.configure_fixture_accounts({"timeout_s": 45})
        self.plan(delay_ms=2000)
        env, process = self.run(background=True)
        run = self.wait(
            lambda: self.scalar(
                "SELECT r.id FROM control.run r JOIN control.cycle c ON c.id=r.cycle_id "
                "JOIN control.streamline s ON s.id=r.streamline_id "
                f"WHERE c.opened_by_dbt_run_id={lit(env['DBT_CLOUD_RUN_ID'])} AND s.source_key='fixture_accounts'"
            ),
            "fixture run admitted",
        )
        cycle = self.cycle(env)
        self.wait(
            lambda: self.api("/v1/_fixture/state")["started"].get("fixture_accounts", 0) >= 1,
            "first page started before switching to a slow fixture plan",
        )
        self.plan(delay_ms=15000)
        checkpoint = self.wait(
            lambda: self.sql(
                f"SELECT id,index,last_part_uploaded,cursor_checkpoint,dump_ids FROM control.batch WHERE run_id={lit(run)} "
                "AND status='running' AND last_part_uploaded>0"
            ),
            "partial batch has a durable uploaded page",
        )[0]
        self.wait(
            lambda: (
                self.api("/v1/_fixture/state")["started"].get("fixture_accounts", 0) >= 1
                and self.scalar(
                    f"SELECT count(*) FROM control.batch WHERE run_id={lit(run)} AND status='running'"
                )
                == 1
            ),
            "slow request started in this run's active batch",
        )
        original = self.sql(
            f"SELECT id,deadline_at FROM control.run_attempt WHERE run_id={lit(run)}"
        )[0]
        self.check(
            self.scalar(
                f"SELECT now()<deadline_at FROM control.run_attempt WHERE id={lit(original['id'])}"
            ),
            "interruption occurs strictly before the real deadline",
        )
        if self.args.target == "pg_local":
            self.command(["tmux", "send-keys", "-t", self.service_session, "C-c"])
            try:
                self.wait(
                    lambda: not self.service_alive(), "service stopped", timeout=40
                )
                self.join(process, timeout=40)
                self.wait(
                    lambda: self.scalar(
                        f"SELECT now()>deadline_at AND now()>coalesce((SELECT max(lease_expires_at) FROM control.batch WHERE run_id={lit(run)}),deadline_at) "
                        f"FROM control.run_attempt WHERE id={lit(original['id'])}"
                    ),
                    "real deadline and lease elapsed while service was down",
                    timeout=60,
                )
            finally:
                self.restart_local()
        else:
            self.check(
                bool(self.env.get("MDP_SERVICE_STOP_COMMAND")),
                "deadline test on pg requires MDP_SERVICE_STOP_COMMAND",
            )
            self.command(["sh", "-c", self.env["MDP_SERVICE_STOP_COMMAND"]])
            try:
                self.wait(
                    lambda: not self.service_alive(), "service stopped", timeout=40
                )
                self.join(process, timeout=40)
                self.wait(
                    lambda: self.scalar(
                        f"SELECT now()>deadline_at FROM control.run_attempt WHERE id={lit(original['id'])}"
                    ),
                    "real deadline elapsed",
                    timeout=60,
                )
            finally:
                self.command(["sh", "-c", self.env["MDP_SERVICE_RESTART_COMMAND"]])
        self.check(process.returncode != 0, "service interruption leaves dbt job red")
        self.wait(self.service_alive, "service restarted", timeout=45)
        self.api("/v1/health")
        self.plan(delay_ms=15000)
        before_calls = self.calls(cycle)
        time.sleep(3)
        self.check(
            self.calls(cycle) == before_calls,
            "recovery spends no calls on expired attempt",
        )
        self.check(
            self.scalar(
                f"SELECT count(*) FROM control.batch WHERE run_id={lit(run)} AND heartbeat_at>{lit(original['deadline_at'])}::timestamptz"
            )
            == 0,
            "no worker heartbeat after deadline",
        )
        self.verify_deadline_calls(run, original["deadline_at"])
        expired = self.sql(
            "SELECT a.id,a.deadline_at,a.status,r.error_class,r.error_message FROM control.run_attempt a "
            f"JOIN control.run r ON r.id=a.run_id WHERE a.id={lit(original['id'])}"
        )[0]
        self.check(
            expired["id"] == original["id"]
            and expired["deadline_at"] == original["deadline_at"],
            "restart preserves original attempt identity and deadline",
        )
        self.check(
            expired["status"] == "failed"
            and expired["error_class"] == "invoke_timeout"
            and "deadline" in (expired["error_message"] or "").lower(),
            "expired attempt ended failed with the deadline reason before retry",
        )
        saved = self.sql(
            f"SELECT id,index,last_part_uploaded,cursor_checkpoint,dump_ids FROM control.batch WHERE run_id={lit(run)} ORDER BY index"
        )
        self.check(
            any(
                b["last_part_uploaded"]
                and b["last_part_uploaded"] >= checkpoint["last_part_uploaded"]
                for b in saved
            ),
            "durable page checkpoint survives restart",
        )
        ledger = self.sql(
            f"SELECT target_id,count(*) AS calls FROM control.call_ledger WHERE run_id={lit(run)} GROUP BY target_id ORDER BY target_id"
        )
        landed = self.sql(
            f"SELECT _target_id FROM raw.account_snapshots WHERE _run_id::text={lit(run)} ORDER BY _target_id",
            wh=True,
        )
        self.configure_fixture_accounts({"timeout_s": 120})
        self.plan()
        retry, _ = self.run(reason="other")
        self.check(self.cycle(retry) == cycle, "retry binds the original cycle")
        attempts = self.sql(
            f"SELECT id,attempt_no,deadline_at,status FROM control.run_attempt WHERE run_id={lit(run)} ORDER BY attempt_no"
        )
        self.check(
            len(attempts) == 2
            and attempts[1]["attempt_no"] == attempts[0]["attempt_no"] + 1
            and attempts[1]["id"] != original["id"]
            and attempts[1]["deadline_at"] > original["deadline_at"]
            and attempts[1]["status"] == "succeeded",
            "retry opens a new successful attempt with a fresh later deadline",
        )
        resumed = {
            b["id"]: b
            for b in self.sql(
                f"SELECT id,last_part_uploaded,dump_ids FROM control.batch WHERE run_id={lit(run)} ORDER BY index"
            )
        }
        self.check(
            all(
                (resumed[b["id"]]["last_part_uploaded"] or 0)
                >= (b["last_part_uploaded"] or 0)
                and set(b["dump_ids"] or []) <= set(resumed[b["id"]]["dump_ids"] or [])
                for b in saved
            ),
            "retry resumes durable uploaded checkpoints and retains every pre-restart dump",
        )
        later_ledger = self.sql(
            f"SELECT target_id,count(*) AS calls FROM control.call_ledger WHERE run_id={lit(run)} GROUP BY target_id ORDER BY target_id"
        )
        complete_ids = {r["_target_id"] for r in landed}
        self.check(
            [r for r in ledger if r["target_id"] in complete_ids]
            == [r for r in later_ledger if r["target_id"] in complete_ids],
            "retry does not call already uploaded targets",
        )
        counts = self.sql(
            f"SELECT count(*) AS total,count(DISTINCT _target_id) AS targets,count(DISTINCT (platform_account_id,snapshot_at)) AS distinct_rows FROM raw.account_snapshots WHERE _run_id::text={lit(run)}",
            wh=True,
        )[0]
        self.check(
            counts["total"] == counts["targets"] == counts["distinct_rows"] == 6,
            "resumed run has six distinct targets and no duplicate rows",
        )
        # Membership, not cardinality: the landed target ids are exactly the ids the
        # cycle froze in raw.targets (a swapped target would keep the count at ten).
        frozen = self.sql(
            f"SELECT DISTINCT id::text AS id FROM raw.targets WHERE _cycle_id::text={lit(cycle)} ORDER BY id",
            wh=True,
        )
        landed_ids = self.sql(
            f"SELECT DISTINCT _target_id::text AS id FROM raw.account_snapshots WHERE _run_id::text={lit(run)} ORDER BY id",
            wh=True,
        )
        self.check(
            bool(frozen) and landed_ids == frozen,
            "resumed run landed exactly the cycle's frozen target ids",
        )

    def verify_deadline_calls(self, run, deadline):
        self.note(
            "design, invoke_timeout: the run continues until deadline_at, "
            "then stops and lands what it has; the next attempt resumes the work key. "
            "control.call_ledger records call completion in created_at; "
            "the queries alias it as occurred_at (a conservative call-time bound)."
        )
        self.check(
            self.scalar(
                "WITH calls AS (SELECT *,created_at AS occurred_at FROM control.call_ledger) "
                f"SELECT count(*) FROM calls WHERE run_id={lit(run)} "
                f"AND occurred_at>{lit(deadline)}::timestamptz"
            )
            == 0,
            "no vendor call recorded after the expired attempt deadline (before retry)",
        )
        # The fixture emits one row per page, retaining the precise request identity.
        pages = self.sql(
            "SELECT DISTINCT _dump_id::text AS dump_id,_request_id AS request_id "
            f"FROM raw.account_snapshots WHERE _run_id::text={lit(run)} ORDER BY 1,2",
            wh=True,
        )
        self.check(bool(pages), "deadline provenance includes durable fixture pages")
        late = self.scalar(
            f"SELECT count(*) FROM control.dump WHERE run_id={lit(run)} "
            f"AND created_at>{lit(deadline)}::timestamptz"
        )
        self.note(
            f"dumps registered after the expired deadline: {late} "
            "(0 means late registration did not occur in this run; the provenance "
            "check below is then vacuous and the regression file carries the positive case)"
        )
        values = ",".join(
            f"({lit(p['dump_id'])},{lit(p['request_id']) if p['request_id'] else 'NULL'})"
            for p in pages
        )
        violations = self.sql(
            f"WITH pages(dump_id,request_id) AS (VALUES {values}), "
            "calls AS (SELECT *,created_at AS occurred_at FROM control.call_ledger) "
            f"SELECT d.id FROM control.dump d WHERE d.run_id={lit(run)} "
            f"AND d.created_at>{lit(deadline)}::timestamptz AND ("
            "NOT EXISTS (SELECT 1 FROM pages p WHERE p.dump_id=d.id::text) OR "
            "EXISTS (SELECT 1 FROM pages p WHERE p.dump_id=d.id::text AND NOT EXISTS "
            "(SELECT 1 FROM calls c WHERE c.run_id=d.run_id AND c.request_id=p.request_id "
            f"AND c.occurred_at<={lit(deadline)}::timestamptz "
            "AND c.occurred_at<d.created_at AND c.http_status BETWEEN 200 AND 299)))"
        )
        self.check(
            not violations,
            "every post-deadline registered dump maps to page calls before deadline and registration",
        )

    def service_alive(self):
        try:
            return (
                httpx.get(
                    self.env["MDP_SERVICE_URL"] + "/v1/health",
                    headers={
                        "Authorization": "Bearer " + self.env["MDP_SERVICE_TOKEN"]
                    },
                    timeout=2,
                ).status_code
                == 200
            )
        except httpx.HTTPError:
            return False

    def restart_local(self):
        command = self.env.get(
            "MDP_SERVICE_COMMAND", "uv run --project functions mdp serve"
        )
        unset = [
            key
            for key in ("MDP_SCHEMA_ROOT", "MDP_DEV_DB")
            if key not in self.service_env
        ]
        if unset:
            command = (
                shlex.join(["env", *[arg for key in unset for arg in ("-u", key)]])
                + " "
                + command
            )
        # tmux receives only this process's explicit child environment; no secret command text.
        exists = self.command(
            ["tmux", "has-session", "-t", self.service_session], ok=False
        )
        launcher = (
            ["tmux", "respawn-pane", "-k", "-t", self.service_session]
            if exists.returncode == 0
            else ["tmux", "new-session", "-d", "-s", self.service_session]
        )
        for key, value in self.service_env.items():
            if key.startswith(("MDP_", "DBT_")):
                launcher += ["-e", key + "=" + value]
        launcher += ["-c", str(ROOT), command]
        self.command(launcher)

    def case_loop(self):
        start = time.monotonic()
        self.check(
            self.args.target == "pg_local", "local initialization refuses target pg"
        )
        original_env = self.env
        loop_env = {
            "MDP_" + k.removeprefix("MDP_LOOP_"): v
            for k, v in self.env.items()
            if k.startswith("MDP_LOOP_")
        }
        self.env = self.env | loop_env
        try:
            self.safety()
            admin = conninfo_to_dict(self.env["MDP_CONTROL_ADMIN_URL"])
            host = admin.get("host", "")
            self.check(
                host in ("127.0.0.1", "localhost", "::1")
                or (
                    self.env.get("MDP_LIFECYCLE_ALLOW_REMOTE") == "1"
                    and host.endswith((".internal", ".flycast"))
                ),
                "initializer requires the validated loopback or claimed Fly-internal cluster",
            )
            self.check(
                admin.get("dbname") == "control",
                "initializer control database is control",
            )
            self.check(
                self.scalar(
                    "SELECT count(*) FROM control.run WHERE status IN ('queued','running')"
                )
                == 0,
                "initializer refuses active workers on the disposable cluster",
            )
            self.select_fixture_targets()
            env = {
                k: v
                for k, v in self.env.items()
                if k in ("PATH", "HOME", "TMPDIR", "LANG")
                or k.startswith(("UV_", "LC_", "XDG_"))
            }
            for key in ("MDP_CONTROL_URL", "MDP_WAREHOUSE_URL", "MDP_SERVICE_READ_URL"):
                env[key] = self.env[key]
            env.update(
                {
                    "MDP_CONTROL_ADMIN_URL": self.env["MDP_CONTROL_ADMIN_URL"],
                    "MDP_CONTROL_RT_URL": self.env["MDP_CONTROL_ADMIN_URL"],
                    "MDP_PG_HOST": admin["host"],
                    "MDP_PG_PORT": admin.get("port", "5432"),
                    "MDP_PG_USER": admin["user"],
                    "MDP_PG_PASSWORD": admin.get("password", ""),
                    "POSTGRES_PASSWORD": admin.get("password", ""),
                    "MDP_DEV_DB": str(self.temp / "loop.duckdb"),
                    "MDP_DUMP_ROOT": (self.temp / "loop-dumps").as_uri(),
                    "MDP_SCHEMA_ROOT": str(self.temp / "schemas"),
                    "MDP_FIXTURE_MODE": "1",
                }
            )
            self.check(
                self.authority(admin)
                == self.authority(
                    {"host": env["MDP_PG_HOST"], "port": env["MDP_PG_PORT"]}
                ),
                "explicit initializer URL and actual bootstrap host/port match validated disposable cluster",
            )
        finally:
            self.env = original_env
        self.command(
            [
                "uv",
                "run",
                "--project",
                "functions",
                "mdp",
                "control",
                "init",
                "--local",
            ],
            env=env,
            isolated=True,
        )
        self.command(
            [
                "uv",
                "run",
                "--project",
                "functions",
                "mdp",
                "run",
                "fixture_accounts",
                "--fixture",
                "--target",
                "dev",
            ],
            env=env,
            isolated=True,
        )
        # The loop verifies accounting and history using only synthetic fixture models.
        for args in (["seed"], ["build", "--select", "+mart_lifecycle_fixture"]):
            self.command(
                [
                    "uv",
                    "run",
                    "--project",
                    "dbt",
                    "dbt",
                    *args,
                    "--project-dir",
                    str(self.fixture_root / "dbt"),
                    "--profiles-dir",
                    "dbt/profiles",
                    "--target",
                    "dev",
                ],
                env=env,
                isolated=True,
            )
        elapsed = time.monotonic() - start
        self.note(f"LOOP elapsed_s={elapsed:.2f}")
        self.check(elapsed < 300, "local DuckDB loop green under five minutes")

    def execute(self):
        # Synthetic models live in a disposable overlay.
        self.fixture_root = self.temp / "fixture-project"
        self.fixture_root.mkdir()
        shutil.copytree(ROOT / "dbt", self.fixture_root / "dbt",
                        ignore=shutil.ignore_patterns(".venv", "target", "logs", "dbt_packages"))
        shutil.copytree(ROOT / "ops/ci/lifecycle/global-models",
                        self.fixture_root / "dbt/models/lifecycle", dirs_exist_ok=True)
        shutil.copytree(ROOT / "ops", self.fixture_root / "ops",
                        ignore=shutil.ignore_patterns("evidence", "__pycache__"))
        for name in ("functions",):
            (self.fixture_root / name).symlink_to(ROOT / name, target_is_directory=True)
        self.env["MDP_FIXTURE_MODE"] = "1"
        self.service_env["MDP_FIXTURE_MODE"] = "1"
        subprocess.run(["uv", "run", "--project", str(ROOT / "functions"), "python", "-c",
                        "from pathlib import Path; from mdp_functions.registry import discover, REGISTRY; "
                        "from mdp_functions.exporter import export_sources; from mdp_functions.settings import Settings; "
                        "discover(); REGISTRY.pop('fixture_enrichment', None); export_sources(Settings(), Path(__import__('sys').argv[1]))",
                        str(self.fixture_root)], env=self.env, check=True, capture_output=True)
        shutil.rmtree(self.fixture_root / "control")
        (self.fixture_root / "control").symlink_to(ROOT / "control", target_is_directory=True)
        evidence = Path(self.args.evidence_dir)
        evidence.mkdir(parents=True, exist_ok=True)
        run_dir = evidence / (
            "lifecycle-run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        )
        run_dir.mkdir(exist_ok=False)
        chosen = CASES if self.args.case == "all" else (self.args.case,)
        results = []
        # Save operator knobs in memory and restore after the suite, including failed cases.
        knobs = None
        for case in chosen:
            self.knobs_changed = False
            self.paused_targets = None
            with (run_dir / f"lifecycle-{case}.txt").open("x") as self.log:
                self.note(
                    f"Lifecycle {case}; target={self.args.target}; UTC={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"
                )
                try:
                    if case != "loop":
                        if knobs is None:
                            knobs = self.sql(
                                "SELECT batch_size,max_concurrency,timeout_s FROM control.streamline WHERE source_key='fixture_accounts'"
                            )[0]
                        self.prepare()
                    getattr(self, "case_" + case)()
                    status, reason = "PASS", "all assertions satisfied"
                except Exception as exc:  # noqa: BLE001 - each independent case must produce evidence
                    status, reason = (
                        "FAIL",
                        self.clean(str(exc)).replace("\n", " ")[:600],
                    )
                    self.note(type(exc).__name__ + ": " + reason)
                finally:
                    for proc, _, _ in list(self.children):
                        try:
                            self.join(proc, timeout=180)
                        except subprocess.TimeoutExpired:
                            import signal

                            os.killpg(proc.pid, signal.SIGTERM)
                            self.join(proc, timeout=10)
                    if knobs is not None and self.knobs_changed:
                        try:
                            self.configure_fixture_accounts(knobs)
                        except Exception as exc:  # noqa: BLE001 - each independent case must produce evidence
                            status, reason = (
                                "FAIL",
                                "knob restore failed: " + self.clean(exc),
                            )
                    try:
                        self.restore_fixture_targets()
                    except Exception as exc:  # noqa: BLE001 - preserve case evidence
                        status, reason = (
                            "FAIL",
                            "fixture membership restore failed: " + self.clean(exc),
                        )
                line = f"CASE {case} {status} {reason}"
                self.note(line)
                print(line, flush=True)
                results.append(status)
            shutil.copyfile(
                run_dir / f"lifecycle-{case}.txt", evidence / f"lifecycle-{case}.txt"
            )
        summary = "\n".join(
            (run_dir / f"lifecycle-{c}.txt").read_text().splitlines()[-1]
            for c in chosen
        )
        (run_dir / "lifecycle-report.txt").write_text(summary + "\n")
        report = (
            f"UTC {datetime.now(timezone.utc).isoformat()}\nImmutable suite: {run_dir}\n"
            + summary
            + "\n"
        )
        acceptances = list(evidence.glob("accept-platform-20*.txt"))
        if acceptances:
            latest = max(acceptances, key=lambda p: p.stat().st_mtime)
            outcomes = [
                line
                for line in latest.read_text().splitlines()
                if line.startswith("ACCEPT platform ")
            ]
            report += (
                f"Latest dated acceptance: {latest}\n"
                + (outcomes[-1] if outcomes else "Acceptance in progress")
                + "\n"
            )
        (evidence / "lifecycle-report.txt").write_text(report)
        print("LIFECYCLE EVIDENCE " + str(run_dir), flush=True)
        return 0 if all(s == "PASS" for s in results) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("pg_local", "pg"), required=True)
    parser.add_argument("--case", choices=(*CASES, "all"), default="all")
    parser.add_argument("--evidence-dir", default="ops/evidence/platform")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete only lifecycle-owned rows after database/runner safety checks",
    )
    parser.add_argument(
        "--claim",
        action="store_true",
        help="claim this disposable stack on its first --reset",
    )
    parser.add_argument(
        "--kill-service",
        action="store_true",
        help="explicit local tmux restart (also implied by case kill/all on pg_local)",
    )
    args = parser.parse_args()
    if args.claim and not args.reset:
        parser.error("--claim requires --reset")
    if args.kill_service and args.target != "pg_local":
        parser.error(
            "--kill-service is local-only; use MDP_SERVICE_RESTART_COMMAND for pg"
        )
    harness = Harness(args)
    if args.reset:
        path = Path(args.evidence_dir)
        path.mkdir(parents=True, exist_ok=True)
        with (path / "lifecycle-reset.txt").open("w") as harness.log:
            try:
                harness.reset(claim=args.claim)
                print("RESET PASS lifecycle-owned rows cleared")
                return 0
            except Exception as exc:  # noqa: BLE001 - each independent case must produce evidence
                harness.note(str(exc))
                print("RESET FAIL " + harness.clean(exc))
                return 1
    return harness.execute()


if __name__ == "__main__":
    raise SystemExit(main())
