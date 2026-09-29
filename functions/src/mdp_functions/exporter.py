"""Deterministic, minimal dbt artifacts generated from function declarations."""

import csv
import json
import re
from io import StringIO
from pathlib import Path
from typing import Any

import yaml

from mdp_functions.costsync import COST_LEDGER_COLUMNS, COST_LEDGER_DDL
from mdp_functions.name_folding import dbt_macros as name_folding_macros
from mdp_functions.owners import dbt_macros as owner_macros
from mdp_functions.registry import discover
from mdp_functions.schemas import (
    COMPLETION_COLUMNS,
    GOLD_COLUMNS,
    LINEAGE,
    RUN_COMPLETION,
    RUN_COMPLETION_COLUMNS,
    SILVER_INPUT_COLUMNS,
    SQL_TYPES,
    declared,
)
from mdp_functions.settings import PACKAGE, REPO, Settings
from mdp_functions.streamline_defaults import RETIRED
from mdp_functions.warehouse.postgres import MIRRORS, PostgresWarehouse


def raw_schemas(catalog: dict, schema_root: Path) -> dict[str, dict[str, str]]:
    """Every raw table a function writes: its declared columns (or the last inferred
    schema) plus lineage, gold runtime columns and the runtime completion tables. Local
    bootstrap DDL and the deployed raw bootstrap share it."""
    schemas: dict[str, dict[str, str]] = {}
    for manifest in catalog.values():
        if manifest.kind != "invoke":
            continue
        for table in manifest.writes:
            declaration = (
                manifest.schema.get(table, "infer")
                if isinstance(manifest.schema, dict)
                else manifest.schema
            )
            columns = declared(declaration) if isinstance(declaration, type) else {}
            if not columns:
                history = sorted(
                    (schema_root / manifest.source_key).glob("*.json"),
                    key=lambda p: p.stat().st_mtime,
                )
                for path in history:
                    schema = json.loads(path.read_text())
                    if schema["table"] == table or table in schema.get("tables", []):
                        columns = schema["columns"]
            if manifest.per_input:
                # The runtime columns and the appended input_version components exist before
                # a first landing, so staging never waits on one.
                columns = {
                    **columns,
                    **(
                        GOLD_COLUMNS
                        if manifest.layer == "gold"
                        else SILVER_INPUT_COLUMNS
                    ),
                }
                for key in [*manifest.table_key(table), *manifest.input_version]:
                    columns.setdefault(key, "text")
            if manifest.tenant_bound:
                columns = {**columns, "tenant_id": "uuid"}
            # A table two functions write with compatible declarations
            # is the union of their columns; the first writer's type and order stand.
            merged = {
                k: v for k, v in schemas.get(table, {}).items() if k not in LINEAGE
            }
            for column, typ in columns.items():
                merged.setdefault(column, typ)
            schemas[table] = {**merged, **LINEAGE}
    for table, runtime in (
        ("raw._enrichment_completion", COMPLETION_COLUMNS),
        (RUN_COMPLETION, RUN_COMPLETION_COLUMNS),
    ):
        schemas[table] = {**runtime, **LINEAGE}
    # A declared raw schema that no function writes (an operator load, through staging) is created empty in that shape, with no lineage.
    for manifest in catalog.values():
        declarations = manifest.schema if isinstance(manifest.schema, dict) else {}
        for table, model in declarations.items():
            if (
                table.startswith("raw.")
                and table not in schemas
                and isinstance(model, type)
            ):
                schemas[table] = declared(model)
    return schemas


def ensure_raw(warehouse_url: str, schema_root: Path) -> list[str]:
    """The deployed twin of bootstrap_raw: before any transform runs, every raw table and
    column a function declares exists, whether or not a landing has created it yet, and
    so does the cost ledger mirror that marts read before the first cost sync. The control
    mirrors (raw.cycles, raw.targets and the rest) get their current columns too: a weekly
    transform can run before any export of the new image has mirrored targets."""
    warehouse = PostgresWarehouse(warehouse_url)
    created = warehouse.ensure_declared(
        {
            **raw_schemas(discover(), schema_root),
            **{"raw." + name: columns for name, (columns, _) in MIRRORS.items()},
        }
    )
    # Empty upserts add the mirrors' key and manifest indexes.
    warehouse.mirror({name: [] for name in MIRRORS})
    with warehouse.connect() as conn:
        if not conn.execute("SELECT to_regclass('raw.cost_ledger') AS r").fetchone()[
            "r"
        ]:
            conn.execute(COST_LEDGER_DDL)
            created.append("raw.cost_ledger (created)")
    return created


def widened_seeds(conn, seeds: Path, schema: str = "reference") -> list[str]:
    """Seeds whose CSV has a column their warehouse table lacks. A plain dbt seed truncates an existing
    table and inserts into its columns, so the deployed bootstrap recreates these (--full-refresh) first."""
    widened = []
    for seed in sorted(seeds.glob("*.csv")):
        with seed.open() as stream:
            header = next(csv.reader(stream))
        have = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
                (schema, seed.stem),
            ).fetchall()
        }
        if have and set(header) - have:
            widened.append(seed.stem)
    return widened


def export_sources(settings: Settings, root: Path = REPO) -> list[Path]:
    catalog = discover()
    templates = PACKAGE / "templates"
    files: dict[Path, str] = {}
    source_path = root / "dbt/models/sources/_raw__sources.yml"
    document = (
        yaml.safe_load(source_path.read_text())
        if source_path.exists()
        else {"version": 2, "sources": [{"name": "raw", "schema": "raw", "tables": []}]}
    )
    raw = next(s for s in document["sources"] if s["name"] == "raw")
    # Function declarations and runtime mirrors own the current raw schema catalog.
    schemas = raw_schemas(catalog, settings.schema_root)
    tables = {t["name"]: t for t in raw["tables"] if "raw." + t["name"] in schemas or t["name"] in MIRRORS}
    # Runtime-owned mirror: use its producer's shape, with no function writer or dump lineage.
    schemas["raw.cost_ledger"] = COST_LEDGER_COLUMNS
    declared_dedupe: set[str] = set()
    # The source keys that write each raw table: lineage checks read them (ops/ci/review_gate.py).
    writers: dict[str, set[str]] = {}
    for manifest in catalog.values():
        if manifest.kind != "invoke":
            continue
        for table in manifest.writes:
            name = table.split(".")[1]
            tables.setdefault(name, {"name": name})
            # The staging dedupe order (mdp_dedupe_order) reads it here; writers of one table agree.
            dedupe = tables[name].setdefault("meta", {}).get("dedupe")
            if dedupe not in (None, manifest.dedupe) and name in declared_dedupe:
                raise ValueError(
                    f"raw.{name}: its writers declare different dedupe orders"
                )
            declared_dedupe.add(name)
            tables[name]["meta"]["dedupe"] = manifest.dedupe
            writers.setdefault(name, set()).add(manifest.source_key)
            tables[name]["meta"]["writers"] = sorted(writers[name])
            existing_columns = {c["name"]: c for c in tables[name].get("columns", [])}
            tables[name]["columns"] = [
                {
                    **existing_columns.get(column, {}),
                    "name": column,
                    "data_type": SQL_TYPES[typ],
                }
                for column, typ in schemas[table].items()
            ]
            if manifest.key:
                physical_key = list(
                    dict.fromkeys(
                        [
                            "_dump_id",
                            *manifest.table_physical_key(table),
                            *(["tenant_id"] if manifest.tenant_bound else []),
                        ]
                    )
                )
                keys = ", ".join('"' + k + '"' for k in physical_key)
                test = f"-- Generated physical uniqueness per dump and declaring source.\nselect {keys}, count(*) as n\nfrom {{{{ source('raw', '{name}') }}}}\nwhere _source_key = '{manifest.source_key}'\ngroup by {keys}\nhaving count(*) > 1\n"
                files[
                    root
                    / f"dbt/tests/generated/unique__{manifest.source_key}__{name}.sql"
                ] = test
        body = (templates / "invoke.sql").read_text()
        scope = "tenant" if manifest.tenant_bound else "global"
        suffix = "_tenant" if manifest.tenant_bound else ""
        dependency = f"-- depends_on: {{{{ ref('bronze_export__targets_{manifest.cadence}{suffix}') }}}}"
        # A declared read of a dbt model is an edge, so the model is built before the
        # invoke reads it; raw reads have no model to wait for.
        models = [r for r in manifest.reads if not r.startswith("raw.")]
        if manifest.layer in {"silver", "gold", "universal"} and models:
            dependency += "\n" + "\n".join(
                f"-- depends_on: {{{{ ref('{r.split('.')[1]}') }}}}" for r in models
            )
        input_argument = (
            f", input_relation=ref('{manifest.reads[0].split('.')[1]}')"
            if manifest.reads and manifest.layer in {"silver", "gold", "universal"}
            else ""
        )
        for key, value in {
            "__CADENCE__": manifest.cadence,
            "__SCOPE__": scope,
            "__LAYER__": manifest.layer,
            "__SOURCE__": manifest.source_key,
            "__DEPENDENCY__": dependency,
            "__TARGET__": f", target_set='{manifest.targets.kind}'"
            if manifest.targets
            else input_argument,
        }.items():
            body = body.replace(key, value)
        files[
            root
            / f"dbt/models/bronze/{manifest.layer}_invoke__{manifest.source_key}.sql"
        ] = body
    written = {t for m in catalog.values() if m.kind == "invoke" for t in m.writes}
    for table, columns in schemas.items():
        if table in written:
            continue
        name = table.split(".")[1]
        tables[name] = {
            "name": name,
            "freshness": None,
            "loaded_at_field": None,
            "columns": [
                {"name": k, "data_type": SQL_TYPES[v]} for k, v in columns.items()
            ],
        }
    for mirror_name in MIRRORS:
        tables.setdefault(mirror_name, {"name": mirror_name})
        tables[mirror_name].update({"freshness": None, "loaded_at_field": None})
    raw["tables"] = [tables[k] for k in sorted(tables)]
    files[source_path] = yaml.safe_dump(document, sort_keys=False)
    tenant_work = tenant_cadences(root, catalog)
    for cadence in ("hourly", "daily", "weekly"):
        for scope in ("global", "tenant"):
            suffix = "_tenant" if scope == "tenant" else ""
            export_name = f"bronze_export__targets_{cadence}{suffix}"
            if scope == "tenant" and cadence not in tenant_work:
                # No tenant function or model at this cadence: no export or close, so no job and no
                # Run Now opens an empty tenant cycle.
                for name in (export_name, f"bronze_close__{cadence}{suffix}"):
                    (root / f"dbt/models/bronze/{name}.sql").unlink(missing_ok=True)
                continue
            dependencies = "\n".join(
                f"-- depends_on: {{{{ ref('bronze_invoke__{m.source_key}') }}}}"
                for m in sorted(catalog.values(), key=lambda m: m.source_key)
                if m.kind == "invoke"
                and m.layer == "bronze"
                and m.cadence == cadence
                and m.tenant_bound == (scope == "tenant")
            )
            for kind in ("export", "close"):
                body = (templates / f"{kind}.sql").read_text()
                for key, value in {
                    "__CADENCE__": cadence,
                    "__SCOPE__": scope,
                    "__EXPORT__": export_name,
                    "__DEPENDENCIES__": dependencies,
                }.items():
                    body = body.replace(key, value)
                name = (
                    export_name
                    if kind == "export"
                    else f"bronze_close__{cadence}{suffix}"
                )
                files[root / f"dbt/models/bronze/{name}.sql"] = body
    for name, (columns, _) in MIRRORS.items():
        schemas["raw." + name] = columns
    schemas["raw._fixture_receipts"] = {
        "source_key": "text",
        "run_id": "text",
        "status": "text",
        "coverage": "text",
        "rows_written": "bigint",
        "rows_rejected": "bigint",
        "dump_id": "text",
        "landed_seq": "bigint",
        "trace_url": "text",
        "message": "text",
    }
    ddl = []
    for table, columns in sorted(schemas.items()):
        definitions = ", ".join(
            f'\\"{key}\\" {SQL_TYPES[typ].replace("jsonb", "json")}'
            for key, typ in columns.items()
        )
        ddl.append(
            '    {% do run_query("create table if not exists '
            + table
            + " ("
            + definitions
            + ')") %}'
        )
    files[root / "dbt/macros/bootstrap_raw.sql"] = (
        (templates / "bootstrap.sql").read_text().replace("__DDL__", "\n".join(ddl))
    )
    global_tables = sorted(
        {
            t
            for m in catalog.values()
            if m.kind == "invoke" and not m.tenant_bound
            for t in m.writes
        }
    )
    files[root / "dbt/macros/mdp_global_inputs.sql"] = (
        (templates / "global_inputs.sql")
        .read_text()
        .replace(
            "__DECLARED__",
            json.dumps(tenant_global_reads(root, global_tables), sort_keys=True),
        )
        .replace("__GLOBAL__", json.dumps(global_tables))
    )
    files[root / "dbt/macros/mdp_owner_rules.sql"] = owner_macros()
    files[root / "dbt/macros/mdp_fold_name.sql"] = name_folding_macros()
    files[root / "dbt/macros/mdp_export_kinds.sql"] = (
        (templates / "export_kinds.sql")
        .read_text()
        .replace(
            "__DECLARED__", json.dumps(export_kinds(root, catalog), sort_keys=True)
        )
    )
    files[root / "control/packages/contracts/src/source-readers.generated.json"] = (
        json.dumps(reader_units(catalog), indent=2) + "\n"
    )
    seed = root / "dbt/seeds/rights_registry.csv"
    reader = csv.DictReader(StringIO(seed.read_text()))
    rows = list(reader)
    fields = reader.fieldnames or []
    known = {r["source_key"] for r in rows}
    for manifest in sorted(catalog.values(), key=lambda m: m.source_key):
        if manifest.kind == "invoke" and manifest.source_key not in known:
            rows.append(
                {
                    **dict.fromkeys(fields, ""),
                    "source_key": manifest.source_key,
                    "provider": manifest.source_key,
                    "learning_eligible": "false",
                    "resale_permitted": "false",
                    "refresh_cadence": manifest.cadence,
                }
            )
    playlist_surfaces = {
        "apple_song_duration": (
            "Apple",
            "Public iTunes Lookup by exact song id; learning and resale permission unknown, treated as false.",
        ),
        "am_playlist": (
            "Apple Music",
            "Canonical public playlist HTML / serialized-server-data",
        ),
        "sp_playlist": ("Spotify", "Paired public playlist embed and page"),
        "sp_playlist_embed": ("Spotify", "Retired; historical public embed rows"),
        "sp_playlist_page": ("Spotify", "Retired; historical public page rows"),


    }
    for row in rows:
        if row["source_key"] in playlist_surfaces:
            provider, surface = playlist_surfaces[row["source_key"]]
            row.update(
                provider=provider,
                category="platform playlists",
                license_ref="public-surface",
                learning_eligible="false",
                resale_permitted="false",
                notes=surface,
            )
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    files[seed] = buffer.getvalue()
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != content:
            path.write_text(content)
    for retired in RETIRED:
        for stale in (
            *(root / "dbt/models/bronze").glob(f"*_invoke__{retired}.sql"),
            *(root / "dbt/tests/generated").glob(f"unique__{retired}__*.sql"),
        ):
            stale.unlink()
    return sorted(files)


def tenant_cadences(root: Path, catalog) -> set[str]:
    """Cadences with tenant work: a tenant-bound invoke function, or a tenant model other than the
    generated export and close (ops/fly/core-runner/machines.py reads the models the same way)."""
    found = {
        m.cadence for m in catalog.values() if m.kind == "invoke" and m.tenant_bound
    }
    for path in (root / "dbt/models").rglob("*.sql"):
        if "tenant" in path.relative_to(root / "dbt/models").parts[:-1]:
            text = path.read_text()
            if not re.search(r"'(export|close)'", text):
                found.update(re.findall(r"cadence:(hourly|daily|weekly)", text))
    return found


MODEL_READS = re.compile(
    r"source\(\s*'raw'\s*,\s*'([a-z_][a-z0-9_]*)'\s*\)|manifest_filter\(\s*'_dump_id'\s*,\s*'raw\.([a-z_][a-z0-9_]*)'"
)


def tenant_global_reads(root: Path, global_tables: list[str]) -> dict[str, list[str]]:
    """Per cadence, the global raw tables that tenant models read (7.3 mdp_global_inputs)."""
    declared: dict[str, set[str]] = {c: set() for c in ("hourly", "daily", "weekly")}
    for path in sorted((root / "dbt/models").rglob("*.sql")):
        text = path.read_text()
        tenant = (
            "scope:tenant" in text
            or "tenant" in path.relative_to(root / "dbt/models").parts[:-1]
        )
        cadence = re.search(r"cadence:(hourly|daily|weekly)", text)
        if not tenant or not cadence:
            continue
        for match in MODEL_READS.finditer(text):
            table = "raw." + (match.group(1) or match.group(2))
            if table in global_tables:
                declared[cadence.group(1)].add(table)
    return {cadence: sorted(tables) for cadence, tables in declared.items()}


def declared_global_inputs(root: Path = REPO) -> dict[str, list[str]]:
    """Read the generated mdp_global_inputs() for job registration and bind checks."""
    text = (root / "dbt/macros/mdp_global_inputs.sql").read_text()
    return json.loads(re.search(r"set declared = (\{.*?\}) %\}", text).group(1))


def model_target_kinds(root: Path) -> list[tuple[str, str, list[str]]]:
    """(cadence, scope, kinds) for every model declaring meta.target_kinds, in SQL config or YAML."""
    sql_files = {p.stem: p for p in (root / "dbt/models").rglob("*.sql")}
    by_model: dict[str, list[str]] = {}
    for name, path in sql_files.items():
        found = re.search(
            r"['\"]?target_kinds['\"]?\s*:\s*\[([^\]]*)\]", path.read_text()
        )
        if found:
            by_model[name] = re.findall(
                r"['\"]([a-z_][a-z0-9_:]*)['\"]", found.group(1)
            )
    for path in (root / "dbt/models").rglob("*.yml"):
        for model in (yaml.safe_load(path.read_text()) or {}).get("models", []) or []:
            meta = {
                **(model.get("meta") or {}),
                **((model.get("config") or {}).get("meta") or {}),
            }
            if meta.get("target_kinds"):
                by_model[model["name"]] = list(meta["target_kinds"])
    result = []
    for name, kinds in by_model.items():
        text = sql_files[name].read_text() if name in sql_files else ""
        cadence = re.search(r"cadence:(hourly|daily|weekly)", text)
        scope = re.search(r"scope:(global|tenant)", text)
        # Models under a tenant directory take scope:tenant from dbt_project.yml, as in tenant_global_reads.
        if (
            name in sql_files
            and "tenant" in sql_files[name].relative_to(root / "dbt/models").parts[:-1]
        ):
            scope_name = "tenant"
        else:
            scope_name = scope.group(1) if scope else None
        if cadence and scope_name:
            result.append((cadence.group(1), scope_name, kinds))
    return result


def export_kinds(root: Path | None, catalog: dict[str, Any]) -> dict[str, list[str]]:
    """Per cadence and scope, the kinds its export freezes: function Targets plus model meta."""
    kinds: dict[str, set[str]] = {
        f"{cadence}:{scope}": set()
        for cadence in ("hourly", "daily", "weekly")
        for scope in ("global", "tenant")
    }
    for manifest in catalog.values():
        if manifest.kind != "invoke" or not manifest.targets:
            continue
        scope = "tenant" if manifest.tenant_bound else "global"
        prefix = (
            "global:"
            if manifest.tenant_bound and manifest.targets.scope == "global"
            else ""
        )
        kinds[f"{manifest.cadence}:{scope}"].add(prefix + manifest.targets.kind)
    for cadence, scope, model_kinds in model_target_kinds(root) if root else []:
        kinds[f"{cadence}:{scope}"].update(model_kinds)
    return {key: sorted(value) for key, value in kinds.items()}


def declared_export_kinds(cadence: str, scope: str, root: Path = REPO) -> list[str]:
    """Kinds for an export that carries no list (manual Run Now): the generated mdp_export_kinds()
    where the image has it, and the registered functions' Targets."""
    key = f"{cadence}:{'tenant' if scope.startswith('tenant') else 'global'}"
    kinds = set(export_kinds(None, discover()).get(key, []))
    path = root / "dbt/macros/mdp_export_kinds.sql"
    if path.exists():
        kinds.update(
            json.loads(
                re.search(r"set declared = (\{.*?\}) %\}", path.read_text()).group(1)
            ).get(key, [])
        )
    return sorted(kinds)


def reader_units(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Export each reader's count selector; absent selectors stay unmeasured."""
    units = {
        "playlist": "playlists",
        "chart": "charts",
        "account": "accounts",
        "track": "songs",
        "curator": "curators",
        "artist_page": "artists",
    }
    return [
        {
            "source_key": manifest.source_key,
            "unit": units.get(target.kind) if target and target.platforms else None,
            "platforms": list(target.platforms) if target else [],
            "member_cadence": target.member_cadence if target else None,
            "id_prefix": target.id_prefix if target else None,
        }
        for manifest in sorted(catalog.values(), key=lambda item: item.source_key)
        if manifest.kind == "invoke" and not manifest.tenant_bound
        for target in [manifest.targets]
    ]
