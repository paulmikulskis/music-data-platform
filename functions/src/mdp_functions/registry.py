"""Import-time function catalog and code-owned registry synchronization."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from pydantic import BaseModel

from mdp_functions.control_db import ControlDB
from mdp_functions.errors import ServiceError
from mdp_functions.health_policy import MIN_ROW_COVERAGE
from mdp_functions.settings import PACKAGE
from mdp_functions.warehouse.base import Warehouse, mirror


@dataclass
class Manifest:
    source_key: str
    layer: str
    writes: list[str]
    cadence: str = "daily"
    reads: list[str] = field(default_factory=list)
    targets: Any = None
    key: list[str] = field(default_factory=list)
    schema: Any = "infer"
    storage: str = "heap"
    tenant_bound: bool = False
    external: bool = True
    # Opt in only for free public probe requests: no key, quota, balance or request cost.
    canary: bool = False
    keep_payload: bool = False
    provider: str | None = None
    llm_step: str | None = None
    output_key: list[str] = field(default_factory=list)
    # Gold: a written table's own output key where it differs from output_key, for a table that holds
    # several rows per input beside one that holds one (mb_resolve's closure rows).
    output_keys: dict[str, list[str]] = field(default_factory=dict)
    model_steps: list[dict[str, Any]] = field(default_factory=list)
    preprocessing: dict[str, Any] = field(default_factory=dict)
    input_eligibility: dict[str, str] = field(default_factory=dict)
    input_key: list[str] = field(default_factory=list)
    input_version: list[str] = field(default_factory=list)
    kind: str = "invoke"
    # Gold: the declared implementation version; config_version is it plus the declared parameters.
    # A PR bumps it when outputs should change.
    version: str = "1"
    # Expected non-error classes only; exact reasons, never a wildcard.
    exclusion_reasons: tuple[str, ...] = ()
    min_row_coverage: float = MIN_ROW_COVERAGE
    min_target_coverage: float | None = None
    # Bronze: False lets the cycle close past this run's own failure or floor miss. The run still
    # fails and opens its alerts; declare it only for a lookup that fills optional values.
    blocks_cycle: bool = True
    stale_target_cycles: int = 2
    # Gold: stop new inputs after this budget; requires allow_partial.
    time_budget_s: float | None = None
    # Gold: input columns the paged read orders by, so a time budget ends on the newest inputs and
    # none starves (oldest retry_week, then lowest first_landed_seq).
    input_order: list[str] = field(default_factory=list)
    # Gold: an input this streamline tried and rejected in this many runs within the last 28 days
    # is parked, left out of the read until its failures age out (derived.parked_inputs).
    park_after: int | None = None
    # Bronze or universal: the runtime writes raw._run_completion as the run's last dump. A universal
    # completion run reads its declared relations off-job into an input dump that its retries reuse.
    completion: bool = False
    # Universal: called as prepare(runtime, run, ctx) after the declared reads load and before the body;
    # its dict is ctx.prepared. mb_spine prunes old generations and guards the volume there.
    prepare: Callable[..., dict[str, Any]] | None = field(default=None, repr=False)
    # Admitted only for a cycle a scheduled run opened, and the Retries and Replays bound to it; a
    # manual work key or a manual/backfill cycle is refused with scheduled_only_refused.
    scheduled_only: bool = False
    # Staging keeps the latest _landed_seq per logical key, or the earliest for a write-once record
    # (the weekly calls); `mdp sources export` writes it into the sources YAML (meta.dedupe).
    dedupe: str = "latest"
    # Days this function's raw rows and its runs' dump objects exist; the retention sweep deletes
    # older ones, so text a person wrote (comments) lives that long and then only counts remain.
    retain_days: int | None = None
    # A derived function whose input rows carry text a person wrote: the input column holding each row's
    # landing time. The sweep deletes a run's stored objects retain_days after its oldest input row
    # landed, not after the run (retained input snapshots).
    retain_from: str | None = None
    # Silver per-input: the inputs the bound cycle owns (calls_freeze's call week), as input_key and
    # input_version values; each gets its completion row even when no row of it is pending, so an empty
    # input counts as done.
    cycle_inputs: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = field(default=None, repr=False)
    function: Callable[..., Any] | None = field(default=None, repr=False)
    hosts: list[str] = field(default_factory=list)
    expect: dict[str, list[str]] = field(default_factory=dict)
    transport: str = "direct"
    # Control-owned streamline knobs a new streamline starts with; sync never writes
    # them. The bootstrap seed applies them once (streamline_defaults).
    knobs: dict[str, Any] = field(default_factory=dict)

    @property
    def per_input(self) -> bool:
        """Step 1b per-input completion: every gold function, and a silver one declaring input_key."""
        return self.layer == "gold" or (self.layer == "silver" and bool(self.input_key))

    def table_key(self, table: str) -> list[str]:
        """The output key of one written table; a silver input's outputs are told apart by the declared key."""
        if self.layer == "silver":
            return self.key
        return self.output_keys.get(table, self.output_key)

    def table_physical_key(self, table: str) -> list[str]:
        """The declared key of one written table: a gold table's own output key replaces output_key."""
        if table not in self.output_keys:
            return self.key
        return [k for k in self.key if k not in self.output_key] + self.output_keys[table]

    def public(self) -> dict[str, Any]:
        def mode(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: mode(v) for k, v in value.items()}
            if isinstance(value, type) and issubclass(value, BaseModel):
                return value.model_json_schema()
            return value

        return {
            "source_key": self.source_key,
            "class": self.layer,
            "reads": self.reads,
            "writes": self.writes,
            "cadence": self.cadence,
            "targets_kind": self.targets.kind if self.targets else None,
            "key": self.key,
            "schema_mode": mode(self.schema),
            "storage": self.storage,
            "tenant_bound": self.tenant_bound,
            "external": self.external,
            "kind": self.kind,
            "canary": self.canary,
            "hosts": self.hosts,
            "expect": self.expect,
            "transport": self.transport,
            "provider": self.provider,
            "llm_step": self.llm_step,
            "output_key": self.output_key,
            "output_keys": self.output_keys,
            "model_steps": self.model_steps,
            "preprocessing": self.preprocessing,
            "input_eligibility": self.input_eligibility,
            "input_key": self.input_key,
            "input_version": self.input_version,
            "completion": self.completion,
            "version": self.version,
            "min_row_coverage": self.min_row_coverage,
            "min_target_coverage": self.min_target_coverage,
            "blocks_cycle": self.blocks_cycle,
            "stale_target_cycles": self.stale_target_cycles,
            "time_budget_s": self.time_budget_s,
            "input_order": self.input_order,
            "park_after": self.park_after,
            "scheduled_only": self.scheduled_only,
            "dedupe": self.dedupe,
            "retain_days": self.retain_days,
            "retain_from": self.retain_from,
        }


REGISTRY: dict[str, Manifest] = {}


def register(manifest: Manifest) -> None:
    if isinstance(manifest.min_row_coverage, bool) or not 0 < manifest.min_row_coverage <= 1:
        raise ServiceError("invalid_row_coverage", "Set min_row_coverage above 0 and at most 1. Run mdp sources export.")
    if manifest.min_target_coverage is not None and (
        (not manifest.targets and manifest.layer != "bronze") or isinstance(manifest.min_target_coverage, bool)
        or not 0 < manifest.min_target_coverage <= 1
    ):
        raise ServiceError("invalid_target_coverage", "Use a coverage fraction above 0 and at most 1 for target sets or a bronze endpoint. Run mdp sources export.")
    if type(manifest.blocks_cycle) is not bool or (not manifest.blocks_cycle and manifest.layer != "bronze"):
        raise ServiceError("invalid_blocks_cycle", "blocks_cycle=False is a bronze declaration. Remove it or move the lookup to @bronze.")
    if type(manifest.stale_target_cycles) is not int or manifest.stale_target_cycles < 1:
        raise ServiceError("invalid_stale_cycles", "stale_target_cycles is a positive whole number")
    if manifest.provider == "typesafe" and (manifest.layer != "gold" or not manifest.llm_step):
        raise ServiceError("jev_layer_refused", "Jev needs @gold(provider='typesafe', llm_step=...); bronze only fetches. See docs/jev.md")
    if manifest.layer != "gold" and manifest.llm_step:
        from mdp_functions.fetch.forbidden import MODEL_MESSAGE
        raise ServiceError("forbidden_path", MODEL_MESSAGE)

    if manifest.transport not in {"direct", "residential"}:
        raise ServiceError("invalid_transport", "Render tiers are refused because page scripts and challenges cannot run; use direct public HTML (docs/operating.md#what-the-platform-refuses).")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", manifest.source_key):
        raise ServiceError(
            "invalid_source_key", "source_key must be a lowercase identifier"
        )
    if any(not re.fullmatch(r"raw\.[a-z_][a-z0-9_]*", t) for t in manifest.writes):
        raise ServiceError(
            "undeclared_write", "Every declared output must be under raw"
        )
    if any(
        not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", t)
        for t in manifest.reads
    ):
        raise ServiceError(
            "undeclared_read", "Reads must name schema-qualified relations"
        )
    if manifest.layer in {"silver", "gold", "universal"} and any(
        relation.startswith("raw.") for relation in manifest.reads
    ):
        raise ServiceError("undeclared_read", "Function inputs must be manifest-filtered dbt relations, never raw.*")
    if any(not re.fullmatch(r"[a-z_][a-z0-9_]*", k) for k in manifest.input_order):
        raise ServiceError("invalid_input_order", "Input order columns must be column identifiers")
    if manifest.input_order and manifest.layer != "gold":
        raise ServiceError("invalid_input_order", "input_order is a gold declaration")
    table_keys = [k for keys in manifest.output_keys.values() for k in keys]
    if any(not re.fullmatch(r"[a-z_][a-z0-9_]*", k) for k in [*manifest.output_key, *table_keys]):
        raise ServiceError("invalid_output_key", "Output keys must be column identifiers")
    if manifest.output_keys and (manifest.layer != "gold" or not set(manifest.output_keys) <= set(manifest.writes)):
        raise ServiceError("invalid_output_key", "output_keys is a gold declaration over declared writes")
    if not isinstance(manifest.version, str) or not manifest.version.strip():
        raise ServiceError("invalid_version", "version must be a non-empty string")
    if manifest.park_after is not None and (manifest.layer != "gold" or manifest.park_after < 1):
        raise ServiceError("invalid_park_after", "park_after is a positive gold declaration")
    if manifest.time_budget_s is not None and (manifest.layer != "gold" or manifest.time_budget_s <= 0):
        raise ServiceError("invalid_time_budget", "time_budget_s is a positive gold declaration")
    if manifest.time_budget_s is not None and manifest.knobs.get("allow_partial") is not True:
        # A run whose budget ends is partial; without allow_partial the UDF fails its model every time.
        raise ServiceError("invalid_time_budget", "time_budget_s needs knobs={'allow_partial': True}")
    if manifest.prepare is not None and manifest.layer != "universal":
        raise ServiceError("invalid_prepare", "prepare is a universal declaration")
    if manifest.completion and manifest.layer not in {"bronze", "universal"}:
        raise ServiceError("invalid_completion", "completion=True is a bronze or universal declaration")
    if manifest.layer == "silver" and manifest.external:
        raise ServiceError("egress_blocked", "Silver cannot declare external egress")
    if manifest.retain_days is not None and (isinstance(manifest.retain_days, bool)
                                             or not isinstance(manifest.retain_days, int) or manifest.retain_days < 1):
        raise ServiceError("invalid_retain_days", "retain_days is a positive whole number of days")
    if manifest.retain_from is not None and (manifest.retain_days is None or manifest.layer not in {"silver", "gold"}
                                             or not re.fullmatch(r"[a-z_][a-z0-9_]*", manifest.retain_from)):
        raise ServiceError("invalid_retain_days", "retain_from names an input column of a silver or gold function "
                           "that declares retain_days")
    if manifest.dedupe not in {"latest", "earliest"}:
        raise ServiceError("invalid_dedupe", "dedupe must be latest or earliest")
    if manifest.dedupe == "earliest" and not manifest.key:
        # Without a key, staging dedupes on _request_id and a row hash; there is no first copy to keep.
        raise ServiceError("invalid_dedupe", "dedupe='earliest' needs key=")
    if manifest.cycle_inputs is not None and not (manifest.layer == "silver" and manifest.per_input):
        raise ServiceError("invalid_cycle_inputs", "cycle_inputs is a silver input_key declaration")
    if manifest.per_input and manifest.layer == "silver" and (not manifest.key or "tenant_id" in manifest.key):
        # The key tells one input's outputs apart; tenant_id is added by the runtime, never by the body.
        raise ServiceError("invalid_output_key", "A silver input_key needs key= over the body's own columns")
    if manifest.layer == "gold":
        manifest.key = ["_source_key", "scope", "input_ref", "input_version", "step", "config_version", *manifest.output_key] + (["tenant_id"] if manifest.tenant_bound else [])
    if manifest.cadence not in {"hourly", "daily", "weekly"}:
        raise ServiceError(
            "invalid_cadence", "Cadence must be hourly, daily, or weekly"
        )
    if manifest.layer == "gold" and not (manifest.llm_step or manifest.model_steps or manifest.external):
        raise ServiceError(
            "gold_requires_egress", "Gold requires llm_step, model_steps, or external=True"
        )
    if manifest.source_key in REGISTRY and REGISTRY[manifest.source_key] != manifest:
        raise ServiceError("duplicate_source", manifest.source_key)
    REGISTRY[manifest.source_key] = manifest


def discover() -> dict[str, Manifest]:
    for path in sorted((PACKAGE / "sources").glob("*/function.py")):
        import_module(f"mdp_functions.sources.{path.parent.name}.function")
    import_module("mdp_functions.targets")
    return REGISTRY


def sync(db: ControlDB, warehouse: Warehouse | None = None) -> None:
    for item in discover().values():
        with db.transaction() as conn:
            step = None
            if item.llm_step:
                found = conn.execute(
                    "SELECT id FROM control.llm_step WHERE source_key=%s AND enabled "
                    "ORDER BY created_at DESC, step_version DESC LIMIT 1",
                    (item.llm_step,),
                ).fetchone()
                if found:
                    step = found["id"]
            conn.execute(
                """INSERT INTO control.streamline
                (source_key,layer,tenant_bound,writes,reads,external,llm_step_id,cadence_tag)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(source_key) DO UPDATE SET layer=EXCLUDED.layer,
                tenant_bound=EXCLUDED.tenant_bound,writes=EXCLUDED.writes,reads=EXCLUDED.reads,
                external=EXCLUDED.external,llm_step_id=EXCLUDED.llm_step_id,
                cadence_tag=EXCLUDED.cadence_tag""",
                (
                    item.source_key,
                    item.layer,
                    item.tenant_bound,
                    item.writes,
                    item.reads,
                    item.external,
                    step,
                    item.cadence,
                ),
            )

    if warehouse is not None:
        mirror_streamlines(db, warehouse)


def mirror_streamlines(
    db: ControlDB, warehouse: Warehouse, source_key: str | None = None
) -> None:
    with db.transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('mdp-streamline-mirror'))")
        rows = conn.execute(
            "SELECT source_key,enabled,allow_partial,timeout_s,batch_size,max_concurrency,storage,updated_at FROM control.streamline WHERE (%s::text IS NULL OR source_key=%s)",
            (source_key, source_key),
        ).fetchall()
        mirror(warehouse, {"streamlines": rows})
