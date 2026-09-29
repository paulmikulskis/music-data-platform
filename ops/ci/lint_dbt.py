import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml
from dbt_relations import POLICY

root = Path(os.environ["MDP_LINT_ROOT"])
project = Path(os.environ["MDP_LINT_DBT_ROOT"])
touched = json.loads(os.environ.get("MDP_LINT_FILES", "null"))


def touched_sql(folder):
    return (
        sorted(folder.rglob("*.sql"))
        if touched is None
        else sorted(
            project / name
            for name in touched
            if name.endswith(".sql") and (project / name).is_file()
        )
    )


raw_weeks = []
for path in touched_sql(project / "models"):
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if re.search(r"date_trunc\s*\(\s*'week'", line, re.IGNORECASE):
            raw_weeks.append(f"{path.relative_to(project)}:{number}: {line.strip()}")
if raw_weeks:
    raise SystemExit("weekly grains must use mdp_local_week; call {{ mdp_local_week('<column>') }}:\n" + "\n".join(raw_weeks))
print("PASS weekly grains use mdp_local_week", flush=True)

target = sys.argv[1] if len(sys.argv) > 1 else "ci"
profile_dir = Path(os.environ.get("DBT_PROFILES_DIR", str(root / "dbt/profiles")))
# Other mart contracts join this check when their descriptions are complete.
playlist = yaml.safe_load((project / "models/marts/global/playlist.yml").read_text())
described = 0
missing = []
for model in playlist["models"]:
    if not model.get("config", {}).get("meta", {}).get("grain"):
        continue
    for column in model["columns"]:
        description = column.get("description")
        if not isinstance(description, str) or not description.strip():
            missing.append(f"{model['name']}.{column['name']}")
        described += 1
if missing:
    raise SystemExit(
        ("playlist.yml: served columns need descriptions: " + ", ".join(missing)) + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
    )
print(f"PASS playlist.yml: {described} served columns have descriptions", flush=True)
for profile in {
    root / "dbt/profiles/profiles.example.yml",
    profile_dir / "profiles.yml",
}:
    config = yaml.safe_load(profile.read_text())["music_data_platform"]["outputs"]
    for name in ("pg", "pg_local"):
        value = config[name].get("autocommit", False)
        if value not in (False, "false"):
            raise SystemExit(
                f"{profile.name}:{name}: autocommit must be false for SET LOCAL"
            )
print("PASS pg/pg_local autocommit is false", flush=True)
# CI can omit this integration-only input; run locally against a Postgres --debug log.
debug_log = os.environ.get("MDP_DBT_DEBUG_LOG")
if debug_log:
    debug = Path(debug_log).read_text()
    events = list(re.finditer(r"On (model\.[^:\s]+):", debug))
    armed = set()
    checked = 0
    for i, event in enumerate(events):
        name = event.group(1)
        if ".bronze_invoke__" not in name:
            continue
        sql = debug[
            event.end() : events[i + 1].start() if i + 1 < len(events) else len(debug)
        ]
        if re.search(r"set\s+local\s+statement_timeout", sql, re.IGNORECASE):
            armed.add(name)
        if re.search(r"create\s+table", sql, re.IGNORECASE):
            if name not in armed:
                raise SystemExit(f"{name}: CREATE TABLE without preceding SET LOCAL")
            checked += 1
        if re.search(r"^\s*(COMMIT|ROLLBACK)\b", sql, re.IGNORECASE):
            armed.discard(name)
    if not checked:
        raise SystemExit(("debug log contains no invoke CREATE TABLE to verify") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model")
    print(
        f"PASS SET LOCAL before CREATE TABLE on same connection: {checked} invoke model(s)",
        flush=True,
    )

# Parse once; every selection still uses dbt's own graph and selector engine.
# Keep invocations sequential: dbt's in-process runner is not thread-safe.
from contextlib import redirect_stdout
from io import StringIO

from dbt.cli.main import dbtRunner

command = [
    "--project-dir",
    str(project),
    "--profiles-dir",
    str(profile_dir),
    "--target",
    target,
    "--quiet",
]
runner = dbtRunner()


def dbt_invoke(*args):
    output = StringIO()
    with redirect_stdout(output):
        result = runner.invoke([*args, *command])
    if not result.success:
        raise SystemExit((output.getvalue() + str(result.exception or "dbt failed")) + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model")
    return result.result


runner.manifest = dbt_invoke("parse", "--no-partial-parse")
# Check rendered metadata too: a source can assemble its schema through Jinja.
for source in runner.manifest.sources.values():
    if source.schema.lower().startswith(POLICY["prefix"]):
        raise SystemExit(f"{source.name}: sandbox relations cannot feed dbt; run pnpm --dir control mdp new mart <name> --from <sandbox.view> first")


def listing(*args: str) -> list[dict]:
    return [
        json.loads(line)
        for line in dbt_invoke(
            "ls", "--resource-type", "model", "--output", "json", *args
        )
    ]


models = listing(
    "--output-keys",
    "name",
    "config",
    "unique_id",
    "relation_name",
    "original_file_path",
    "depends_on",
    "columns",
)
# dbt ls constructs its dependency graph and refuses cycles (parse alone does not).
print("PASS acyclic-dbt-graph: dbt ls built the dependency graph", flush=True)
by_name = {m["name"]: m for m in models}
for model in models:
    tags = [t for t in model["config"]["tags"] if t.startswith("cadence:")]
    if len(tags) != 1 or tags[0] not in (
        "cadence:hourly",
        "cadence:daily",
        "cadence:weekly",
    ):
        raise SystemExit((f"{model['name']}: exactly one valid cadence tag required; use cadence:hourly, cadence:daily or cadence:weekly") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model")
    model["cadence"] = tags[0]
# A ref across cadences reads the other cadence's already-built table. A view or an
# ephemeral model would be re-evaluated under this cadence's manifest, or dropped
# (CASCADE) while the other cadence rebuilds what it selects from.
by_uid = {m["unique_id"]: m for m in models}
for model in models:
    for dependency in model.get("depends_on", {}).get("nodes", []):
        ref = by_uid.get(dependency)
        if ref is None or ref["cadence"] == model["cadence"]:
            continue
        if ref["config"]["materialized"] not in ("table", "incremental"):
            raise SystemExit(
                f"{model['name']} ({model['cadence']}) refs {ref['name']} ({ref['cadence']}, "
                f"{ref['config']['materialized']}): a cross-cadence ref must read a built table"
            )
print("PASS cross-cadence refs read built tables only", flush=True)
seen = {}
for model in models:
    if "invoke" not in model["config"]["tags"]:
        continue
    if model["config"]["materialized"] != "table":
        raise SystemExit((f"{model['name']}: invoke models are tables") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model")
    sql = (project / model["original_file_path"]).read_text()
    calls = re.findall(r"mdp_invoke\(\s*['\"]([^'\"]+)['\"]", sql)
    if len(calls) != 1:
        raise SystemExit(
            (f"{model['name']}: exactly one literal source invocation required") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
        )
    identity = (calls[0], model["cadence"])
    if identity in seen:
        raise SystemExit(
            (f"duplicate invoke: {identity}: {seen[identity]}, {model['name']}") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
        )
    seen[identity] = model["name"]
    children = [
        m
        for m in models
        if model["unique_id"] in m.get("depends_on", {}).get("nodes", [])
    ]
    if not any(
        c["name"] != model["name"] and model["cadence"] in c["config"]["tags"]
        for c in children
    ):
        raise SystemExit((f"{model['name']}: missing dependent in same cadence") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model")
    print(
        f"PASS {model['name']}: table, unique source/cadence, same-cadence dependent",
        flush=True,
    )
# Tenant-scoped shared marts must declare tenant_id in their public column contract.
# scope:tenant routes to tenant_<slug>_* in production; all other routes are shared.
for model in models:
    config = model["config"]
    if config.get("meta", {}).get("tenant_scoped") is True:
        isolated = "scope:tenant" in config.get("tags", [])
        if not isolated and "tenant_id" not in model.get("columns", {}):
            raise SystemExit(
                (f"{model['name']}: tenant_scoped model in shared schema must expose tenant_id") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
            )
        if not isolated and not config.get("contract", {}).get("enforced"):
            raise SystemExit(
                (f"{model['name']}: shared tenant_id must belong to an enforced contract") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
            )
        print(f"PASS {model['name']}: tenant scope column/schema", flush=True)
# A served mart (one declaring meta.grain) is a table: the data API locks it and pages it under
# its marts._build stamp, and a view has neither a swap to serialize nor a build to stamp.
for model in models:
    if (
        model["config"].get("meta", {}).get("grain")
        and model["config"]["materialized"] != "table"
    ):
        raise SystemExit(
            (f"{model['name']}: served marts must be tables, not {model['config']['materialized']}") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
        )
print("PASS served marts are tables", flush=True)
# A tenant model reads a global raw table only when the generated mdp_global_inputs() declares it
# for the model's cadence; the manifest filter admits global stamps for declared tables alone.
generated = project / "macros/mdp_global_inputs.sql"
if generated.exists():
    text = generated.read_text()
    declared = json.loads(re.search(r"set declared = (\{.*?\}) %\}", text).group(1))
    global_tables = set(
        json.loads(
            re.search(
                r"return\((\[.*?\])\)", text.split("macro mdp_global_tables", 1)[1]
            ).group(1)
        )
    )
    for model in models:
        if "scope:tenant" not in model["config"]["tags"]:
            continue
        reads = {
            "raw." + node.rsplit(".", 1)[1]
            for node in model.get("depends_on", {}).get("nodes", [])
            if node.startswith("source.") and node.split(".")[2] == "raw"
        }
        sql = (project / model["original_file_path"]).read_text()
        reads |= {
            "raw." + name
            for name in re.findall(
                r"manifest_filter\(\s*'_dump_id'\s*,\s*'raw\.([a-z_][a-z0-9_]*)'", sql
            )
        }
        missing = sorted(
            (reads & global_tables)
            - set(declared.get(model["cadence"].split(":")[1], []))
        )
        if missing:
            raise SystemExit(
                f"{model['name']}: tenant model reads undeclared global tables {missing}; run mdp sources export"
            )
    print("PASS tenant models read only declared global inputs", flush=True)
# Every manifest_filter call names its raw table (and may take derived_rows): a stamp cycle filters per
# table, and a call without one only fails on a deployed target, after every local gate has passed.
bare = []
for path in touched_sql(project):
    if any(
        part in ("target", "dbt_packages", "logs")
        for part in path.relative_to(project).parts
    ):
        continue
    for number, line in enumerate(path.read_text().splitlines(), 1):
        for call in re.finditer(
            r"(?<![a-z_])(?:mdp_)?manifest_filter\(([^)]*)\)", line
        ):
            if "{% macro" in line:
                continue
            if not re.fullmatch(
                r"\s*'[^']*'\s*,\s*'raw\.[a-z_][a-z0-9_]*'\s*(,\s*derived_rows\s*=\s*(true|false)\s*)?",
                call.group(1),
            ):
                bare.append(f"{path.relative_to(project)}:{number}: {call.group(0)}")
if bare:
    raise SystemExit(
        ("manifest_filter without a 'raw.<table>' argument:\n" + "\n".join(bare)) + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
    )
print("PASS every manifest_filter call names its raw table", flush=True)
if os.environ.get("MDP_LINT_RULES_ONLY") == "1":
    print("PASS model rules", flush=True)
    raise SystemExit(0)
relations = {}
for cadence in ("hourly", "daily", "weekly"):
    for phase in ("bronze", "transform"):
        selected = listing(
            "--selector",
            cadence + "_" + phase,
            "--output-keys",
            "name",
            "config",
            "relation_name",
        )
        names = sorted(m["name"] for m in selected)
        if cadence == "hourly" and phase == "bronze":
            assert names == [
                "bronze_close__hourly",
                "bronze_export__targets_hourly",
            ], names
        for model in selected:
            assert "cadence:" + cadence in model["config"]["tags"], model["name"]
            relation = model.get("relation_name")
            if relation and model["config"]["materialized"] != "ephemeral":
                if relation in relations and relations[relation] != cadence:
                    raise SystemExit((f"{relation}: selected by multiple cadences") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model")
                relations[relation] = cadence
        print(f"PASS {cadence}_{phase}: {', '.join(names)}", flush=True)
# Inspect explicit manifest edges, rather than accepting any same-cadence child.
scoped_models = models
by_id = {m["unique_id"]: m for m in scoped_models}
groups = {}
for model in scoped_models:
    tags = model["config"]["tags"]
    scopes = [t for t in tags if t.startswith("scope:")]
    if len(scopes) != 1 or scopes[0] not in ("scope:global", "scope:tenant"):
        raise SystemExit((f"{model['name']}: exactly one valid scope tag required") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model")
    cadence = next(t for t in tags if t.startswith("cadence:"))
    groups.setdefault((cadence, scopes[0]), []).append(model)
    if {"export", "invoke", "close"} & set(tags) and model["config"][
        "materialized"
    ] != "table":
        raise SystemExit(
            (f"{model['name']}: side-effecting stub materialization must be table") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
        )
for (cadence, scope), members in groups.items():
    exports = {m["unique_id"] for m in members if "export" in m["config"]["tags"]}
    closes = {m["unique_id"] for m in members if "close" in m["config"]["tags"]}
    invokes = {
        m["unique_id"]
        for m in members
        if {"bronze", "invoke"} <= set(m["config"]["tags"])
    }
    if len(exports) != 1 or len(closes) != 1:
        raise SystemExit((f"{cadence}/{scope}: exactly one export and close required") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model")
    close = by_id[next(iter(closes))]
    close_edges = {d for d in close["depends_on"]["nodes"] if d in by_id}
    if close_edges != exports | invokes:
        raise SystemExit(
            (f"{close['name']}: close must depend on exactly its scope/cadence export and bronze invokes") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
        )
    for uid in invokes:
        invoke = by_id[uid]
        if not exports <= set(invoke["depends_on"]["nodes"]):
            raise SystemExit(
                (f"{invoke['name']}: missing scope/cadence export dependency") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
            )
        staging = [
            m
            for m in members
            if m["original_file_path"].startswith("models/staging/")
            and uid in m["depends_on"]["nodes"]
        ]
        if not staging:
            raise SystemExit(
                (f"{invoke['name']}: missing explicit invoke-to-staging edge") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
            )
        for model in staging:
            # Data staging needs the closed manifest; target views read export history.
            if "mdp_context().manifest_filter" in (
                project / model["original_file_path"]
            ).read_text() and not closes <= set(model["depends_on"]["nodes"]):
                raise SystemExit(
                    (f"{model['name']}: missing scope/cadence close dependency") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model"
                )
print(
    "PASS side-effecting stubs: table, export -> invoke -> close and invoke -> staging edges",
    flush=True,
)
for cadence in ("hourly", "daily", "weekly"):
    for scope in ("global", "tenant"):
        for phase in ("bronze", "transform"):
            selector = f"{cadence}_{scope}_{phase}"
            selected = listing(
                "--selector", selector, "--output-keys", "name", "config"
            )
            for model in selected:
                tags = model["config"]["tags"]
                if f"scope:{scope}" not in tags or f"cadence:{cadence}" not in tags:
                    raise SystemExit((f"{selector}: incompatible model {model['name']}") + "; see dbt/CLAUDE.md and run bash ops/ci/lint-dbt.sh after correcting the model")
            print(
                f"PASS {selector}: {', '.join(sorted(m['name'] for m in selected))}",
                flush=True,
            )
print(
    f"PASS {len(scoped_models)} models: exactly one scope each; tenant selectors exclude global models",
    flush=True,
)
# A test whose parents span both scopes is selected by the global selectors too: dbt's eager indirect selection
# runs per intersection component (a weekly tenant mart's test by cadence:weekly, its global seed by
# scope:global), and the global build then reads a tenant relation that exists only in tenant schemas.
tested = listing(
    "--resource-type",
    "test",
    "--resource-type",
    "seed",
    "--output-keys",
    "name",
    "resource_type",
    "unique_id",
    "config",
    "depends_on",
)
scope_of = {
    m["unique_id"]: next(
        (t for t in m["config"]["tags"] if t.startswith("scope:")), None
    )
    for m in [*scoped_models, *tested]
}
tests = [t for t in tested if t["resource_type"] == "test"]
for test in tests:
    if len({scope_of.get(p) for p in test["depends_on"]["nodes"]} - {None}) > 1:
        raise SystemExit(
            f"{test['name']}: a test's parents must share one scope (the other scope's selectors select it)"
        )
print(f"PASS {len(tests)} tests: parents share one scope", flush=True)
# Render the real hook with a query trap: even allowed commands may only RETURN SQL.
from types import SimpleNamespace

from jinja2 import Environment


class Returned(Exception):
    pass


def return_value(value):
    raise Returned(value)


def reject_query(*args, **kwargs):
    raise AssertionError("bind hook executed SQL during rendering")


def compiler_error(message):
    raise ValueError(message)


hook = Environment(extensions=["jinja2.ext.do"]).from_string(
    (root / "dbt/macros/mdp_global_inputs.sql").read_text()
    + (root / "dbt/macros/mdp_bind_cycle.sql").read_text()
    + "{{ mdp_bind_cycle() }}"
)
for execute in (False, True):
    for local in (False, True):
        for which in (
            "run",
            "build",
            "seed",
            "snapshot",
            "test",
            "compile",
            "generate",
            "docs",
            "ls",
            "parse",
            "freshness",
        ):
            try:
                hook.render(
                    execute=execute,
                    mdp_is_local=lambda local=local: local,
                    flags=SimpleNamespace(
                        WHICH=which, INVOCATION_COMMAND="dbt " + which
                    ),
                    env_var=lambda key, default=None: default or "probe",
                    var=lambda key, default=None: default,
                    mdp_literal=lambda value: (
                        "null" if value is None else "'" + value + "'"
                    ),
                    run_query=reject_query,
                    return_value=return_value,
                    tojson=json.dumps,
                    exceptions=SimpleNamespace(raise_compiler_error=compiler_error),
                    **{"return": return_value},
                )
            except Returned as result:
                sql = result.args[0]
                expected = (
                    execute
                    and not local
                    and which in ("run", "build", "seed", "snapshot", "test")
                )
                assert ("select mdp.bind_cycle(" in sql) == expected, (
                    execute,
                    local,
                    which,
                )
try:
    hook.render(
        execute=True,
        mdp_is_local=lambda: False,
        flags=SimpleNamespace(WHICH="build", INVOCATION_COMMAND="dbt retry"),
        exceptions=SimpleNamespace(raise_compiler_error=compiler_error),
        **{"return": return_value},
    )
except ValueError as error:
    assert "partial_retry_refused" in str(error)
else:
    raise AssertionError("original-command retry was not refused")
print(
    "PASS bind hook: render has no SQL side effects; command/local/parse guards and original-command retry",
    flush=True,
)

# Opt-in read-only production compile regression; never invokes a build or opens a cycle.
if os.environ.get("MDP_LINT_PG_COMPILE") == "1":
    import psycopg2

    with psycopg2.connect(
        host=os.environ.get("MDP_PG_HOST", "127.0.0.1"),
        port=os.environ.get("MDP_PG_PORT", "5435"),
        user=os.environ.get("MDP_PG_USER", "dbt_transform"),
        password=os.environ["MDP_PG_PASSWORD"],
        dbname=os.environ.get("MDP_PG_DB", "warehouse"),
        sslmode="require",
    ) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute("select count(*) from raw.cycle_attempts")
            before = cursor.fetchone()[0]
            cursor.execute(
                "select a.dbt_run_id from raw.cycle_attempts a join raw.cycles c on c.id=a.cycle_id where c.scope='global' and c.cadence='hourly' limit 1"
            )
            binding = cursor.fetchone()
            env = {
                **os.environ,
                "DBT_CLOUD_RUN_ID": binding[0] if binding else "compile-compile-unbound",
                "DBT_MDP_SCOPE": "global",
                "DBT_MDP_CADENCE": "hourly",
                "DBT_CLOUD_JOB_ID": "core-hourly-global",
                "DBT_CLOUD_RUN_REASON_CATEGORY": "scheduled",
                "MDP_RUNNER": "core",
            }
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "--project",
                    str(root / "dbt"),
                    "dbt",
                    "compile",
                    "--project-dir",
                    str(project),
                    "--profiles-dir",
                    str(profile_dir),
                    "--target",
                    "pg_local",
                    "--no-partial-parse",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            cursor.execute("select count(*) from raw.cycle_attempts")
            after = cursor.fetchone()[0]
            assert before == after, (
                "production compile changed raw.cycle_attempts count (or a concurrent job bound)"
            )
            if result.returncode and (
                binding or "no_cycle_binding" not in result.stdout + result.stderr
            ):
                raise SystemExit("production compile failed; inspect the dbt log")
            print(
                f"PASS production compile: raw.cycle_attempts unchanged ({before}); exit={result.returncode}",
                flush=True,
            )

print(f"PASS {len(models)} models: one cadence each; cadence relations disjoint")
