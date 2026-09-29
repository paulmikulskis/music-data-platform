"""The author-facing function contract. State machinery stays in the runtime."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

import structlog

from mdp_functions.errors import ServiceError
from mdp_functions.registry import Manifest, register

if TYPE_CHECKING:
    from mdp_functions.http import TracedClient


@dataclass(frozen=True)
class Targets:
    kind: str
    scope: str | None = None
    # Frozen membership counted for this reader, in the same units it selects.
    platforms: tuple[str, ...] = ()
    member_cadence: str | None = None
    id_prefix: str | None = None
    Batch: ClassVar[type] = list


class Target(dict[str, Any]):
    def __getattr__(self, name: str) -> Any:
        return self[name]

    @classmethod
    def from_export(cls, member: dict[str, Any]) -> "Target":
        """Restore historical target field types without reading a live target."""
        if not member.get("spec_recovered", False):
            raise ServiceError(
                "export_spec_missing", "Frozen export specification is not recovered"
            )
        value = dict(member["target_json"])
        if (
            not all(
                value.get(k) is not None
                for k in ("target_set_id", "platform", "platform_account_id")
            )
            or "handle" not in value
        ):
            raise ServiceError(
                "export_spec_missing", "Frozen export identity is incomplete"
            )
        for name in ("id", "target_set_id"):
            if isinstance(value.get(name), str):
                value[name] = UUID(value[name])
        for name in ("activated_at", "deactivated_at", "created_at", "updated_at"):
            if isinstance(value.get(name), str):
                value[name] = datetime.fromisoformat(value[name])
        return cls(
            value,
            id=member["target_id"],
            resource_kind=member["resource_kind"],
            canonical_key=member["canonical_key"],
            params_json=member["params_json"],
        )


def decorator(layer: str, **options: Any) -> Callable[..., Any]:
    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        options.setdefault("external", layer == "bronze")
        manifest = Manifest(layer=layer, function=fn, **options)
        register(manifest)
        fn.__mdp_manifest__ = manifest
        return fn

    return wrap


def bronze(**options: Any) -> Callable[..., Any]:
    return decorator("bronze", **options)


def silver(**options: Any) -> Callable[..., Any]:
    return decorator("silver", **options)


def gold(**options: Any) -> Callable[..., Any]:
    return decorator("gold", **options)


def universal(**options: Any) -> Callable[..., Any]:
    return decorator("universal", **options)


def input_identity(row: dict[str, Any]) -> dict[str, Any]:
    if not row.get("input_ref") or not row.get("input_version"):
        raise ServiceError(
            "input_identity_missing",
            "Apply mdp_input_identity to the input relation",
        )
    return {k: row[k] for k in ("input_ref", "input_version")}


class Ctx:
    def __init__(
        self,
        manifest: Manifest,
        run: dict[str, Any],
        cursors: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.manifest = manifest
        self.run_id = str(run["id"])
        self.cycle_id = str(run["cycle_id"])
        # The bound cycle's open time: the day a weekly key's bucket is drawn from.
        self.cycle_opened_at: datetime | None = run.get("cycle_opened_at")
        # The bound cycle (opened_at, opened_by_dbt_run_id, local_weekday, and a tenant cycle's frozen
        # timezone) and, for a tenant run, its tenant with its configuration; the runtime fills
        # both before the function runs.
        self.cycle: dict[str, Any] = dict(run.get("cycle") or {})
        self.tenant: dict[str, Any] | None = run.get("tenant")
        # Half-open UTC source window, persisted with a backfill's admission.
        self.window: dict[str, str] | None = run.get("backfill_window")
        # True for a run admitted in fixture mode (MDP_FIXTURE=true freezes it in resolved_config).
        # A function without live provider access serves sample rows only then.
        self.fixture: bool = bool((run.get("resolved_config") or {}).get("fixture"))
        self.logger = structlog.get_logger().bind(
            run_id=self.run_id, cycle_id=self.cycle_id
        )
        self.http: TracedClient
        self.relations: dict[str, list[dict[str, Any]]] = {}
        self.input_row: dict[str, Any] = {}
        # The gold input relation's column types, which type the input_version components.
        self.input_types: dict[str, str] = {}
        # Monotonic gold deadline. Sources use it to bound each private database lookup.
        self.deadline: float | None = None
        # A per-input run's scope, step and config_version (and gold's step metadata) on every emitted row.
        self.input_metadata: dict[str, Any] = {}
        self.prompt = ""
        self.observed_count = 0
        self.yielded_count = 0
        self.outputs: dict[str, list[dict[str, Any]]] = {}
        self.rejected: list[dict[str, Any]] = []
        self.exclusions: dict[str, int] = {}
        self.payloads: list[Any] = []
        self.published: dict[str, list[dict[str, Any]]] = {}
        self.completion: dict[str, Any] = {}
        # A bronze manifest's prepare() result (registry.Manifest.prepare).
        self.prepared: dict[str, Any] = {}
        self.cursors = cursors or {}
        self.pending_cursors: dict[str, Any] = {}
        # Alerts the body opens (ctx.alert); they commit with the page that carries them.
        self.alerts: list[dict[str, Any]] = []
        self.target: Target | None = None
        self.target_id: str | None = None
        self.request_id: str = self.run_id

    @property
    def jev(self):
        if self.manifest.layer != "gold" or not hasattr(self, "_jev"):
            raise ServiceError(
                "jev_layer_refused",
                "Use @gold(provider='typesafe', llm_step=...) and ctx.jev.ask; see docs/jev.md",
            )
        return self._jev

    def read(self, relation: str) -> list[dict[str, Any]]:
        if relation not in self.manifest.reads:
            raise ServiceError(
                "undeclared_read", "Relation is absent from declared reads"
            )
        return self.relations[relation]

    def input_identity(self, row: dict[str, Any]) -> dict[str, Any]:
        return input_identity(row)

    def record_completion(self, **fields: Any) -> None:
        """Range or generation for this run's raw._run_completion row (completion=True bronze)."""
        self.completion.update(fields)

    def observed(self, n: int) -> None:
        if n < 0:
            raise ValueError("Observed count cannot be negative")
        self.observed_count += n

    def lineage(self) -> dict[str, Any]:
        return {"_target_id": self.target_id, "_request_id": self.request_id}

    def reject(self, record: Any, *, reason: str) -> None:
        if not reason:
            raise ValueError("Rejection needs a reason")
        self.rejected.append({"record": record, "reason": reason, **self.lineage()})

    def exclude(self, record: Any, *, reason: str) -> None:
        """Keep a deliberate filter's reason without treating it as a delivery error."""
        if reason not in self.manifest.exclusion_reasons:
            raise ServiceError(
                "undeclared_exclusion",
                "Exclusion reason is not declared. Use ctx.reject() for unexpected rows, "
                "or declare a stable non-error reason in exclusion_reasons. See functions/CLAUDE.md.",
            )
        self.exclusions[reason] = self.exclusions.get(reason, 0) + 1

    def require(self, obj: Any, path: str, surface: str) -> Any:
        """Return a required envelope or reject and stop this target."""
        from mdp_functions.fetch.detectors import envelope_value

        try:
            return envelope_value(obj, path)
        except (KeyError, IndexError, TypeError, ValueError):
            reason = f"envelope_mismatch:{surface}:{path}"
            if hasattr(self, "http") and hasattr(self.http, "envelope_miss"):
                self.http.envelope_miss(surface, path)
            from mdp_functions.playlist import sanitized_identifiers

            identifiers = sanitized_identifiers(obj) if isinstance(obj, dict) else {}
            self.reject(
                {"surface": surface, "missing_path": path, **identifiers}, reason=reason
            )
            # Envelope checks happen before a parser observes its records.
            self.observed(1)
            raise ServiceError("envelope_mismatch", reason) from None

    def emit(self, table: str, row: dict[str, Any]) -> None:
        if table not in self.manifest.writes:
            raise ServiceError("undeclared_write", f"{table} is not declared in writes")
        if table in {"raw.playlist_snapshots", "raw.playlist_items"}:
            from mdp_functions.playlist import sanitize_playlist_output

            row = sanitize_playlist_output(row)
        metadata = {}
        if self.manifest.layer in {"silver", "gold"}:
            sources = (
                self.input_row.get("_source_keys")
                or self.input_row.get("source_keys")
                or [
                    self.input_row.get("source_key")
                    or self.input_row.get("_source_key")
                ]
            )
            if isinstance(sources, str):
                # A mart carries _source_keys as JSON array text (mdp_annotate).
                sources = json.loads(sources)
            metadata = {
                "_input_cycle": self.input_row.get("_cycle_id", self.cycle_id),
                "_source_keys": list(
                    dict.fromkeys(
                        [s for s in sources if s]
                        + (["typesafe"] if self.manifest.provider == "typesafe" else [])
                    )
                ),
            }
        if self.manifest.per_input:
            metadata.update(self.input_identity(self.input_row))
            # Every declared input_version component rides beside input_version, whatever the body yields.
            metadata.update(
                {c: self.input_row.get(c) for c in self.manifest.input_version}
            )
            metadata.update(self.input_metadata)
        self.outputs.setdefault(table, []).append({**row, **metadata, **self.lineage()})
        self.yielded_count += 1

    def yield_row(self, row: dict[str, Any]) -> None:
        if len(self.manifest.writes) != 1:
            raise ServiceError(
                "undeclared_write", "Bare yield requires exactly one declared table"
            )
        self.emit(self.manifest.writes[0], row)

    @staticmethod
    def target_key(target: Any) -> str:
        if target is None:
            return ""
        return str(target["id"] if isinstance(target, dict) else target)

    def cursor(self, target: Any) -> Any:
        key = self.target_key(target)
        return self.pending_cursors.get(
            key, self.cursors.get(key, {}).get("cursor_value")
        )

    def set_cursor(self, target: Any, value: Any) -> None:
        self.pending_cursors[self.target_key(target)] = value

    def alert(self, kind: str, message: str, once: bool = False, **attrs: Any) -> None:
        """Open a `kind` alert on the current target (or the run) with a run event carrying `attrs`. It
        commits in the transaction that registers the page, so a page that never publishes opens nothing.
        With `once`, a subject that already holds an unresolved `kind` alert gets the run event only.
        `kind` needs its runbook row; attrs hold counts and codes, never a record's values."""
        self.alerts.append(
            {
                "kind": kind,
                "message": message,
                "target_id": self.target_id,
                "once": once,
                "attrs": attrs,
            }
        )

    def clear_page(self) -> None:
        self.observed_count = self.yielded_count = 0
        self.outputs = {}
        self.rejected = []
        self.exclusions = {}
        self.payloads = []
        self.pending_cursors = {}
        self.alerts = []
