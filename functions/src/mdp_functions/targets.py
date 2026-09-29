"""Write-once membership exports and the two built-in function manifests."""

from typing import Any

from psycopg.types.json import Jsonb

from mdp_functions.control_db import ControlDB
from mdp_functions.errors import ServiceError
from mdp_functions.registry import Manifest, register
from mdp_functions.warehouse.base import Warehouse, mirror

register(
    Manifest(
        source_key="targets_export",
        layer="universal",
        writes=["raw.targets"],
        external=False,
        kind="export",
    )
)
register(
    Manifest(
        source_key="cycle_close",
        layer="universal",
        writes=[],
        external=False,
        kind="close",
    )
)


def export_targets(
    db: ControlDB,
    warehouse: Warehouse,
    cycle_id: Any,
    kind: str = "account",
    tenant_id: Any = None,
) -> dict[str, Any]:
    with db.transaction() as conn:
        cycle = conn.execute(
            "SELECT * FROM control.cycle WHERE id=%s FOR UPDATE", (cycle_id,)
        ).fetchone()
        target_set = conn.execute(
            "SELECT * FROM control.target_set WHERE kind=%s AND tenant_id IS NOT DISTINCT FROM %s::uuid",
            (kind, tenant_id),
        ).fetchone()
        if not target_set:
            raise ServiceError(
                "scope_mismatch", f"No target set for {kind} and this scope"
            )
        revision = conn.execute(
            "SELECT * FROM control.target_export WHERE cycle_id=%s AND target_set_id=%s",
            (cycle_id, target_set["id"]),
        ).fetchone()
        if not revision:
            if cycle["status"] != "open":
                raise ServiceError(
                    "replay_refused", "Membership may only be frozen on an open cycle"
                )
            # The export copies each member's current role into its frozen spec, so a role patch
            # always reaches it and a spec that never held a role gets one.
            members = conn.execute(
                "SELECT t.id,to_jsonb(t) AS target_json,s.resource_kind,s.canonical_key,"
                "CASE WHEN t.role IS NOT NULL THEN jsonb_set(coalesce(s.params_json,'{}'::jsonb),'{role}',to_jsonb(t.role)) "
                "ELSE coalesce(s.params_json,'{}'::jsonb) END AS params_json "
                "FROM control.target t LEFT JOIN control.target_spec s ON s.target_id=t.id "
                "WHERE t.target_set_id=%s AND t.resolution_status='resolved' AND t.activated_at<=now() AND t.deactivated_at IS NULL ORDER BY t.id",
                (target_set["id"],),
            ).fetchall()
            revision = conn.execute(
                "INSERT INTO control.target_export(cycle_id,target_set_id,member_count) VALUES (%s,%s,%s) RETURNING *",
                (cycle_id, target_set["id"], len(members)),
            ).fetchone()
            for member in members:
                conn.execute(
                    "INSERT INTO control.target_export_member(revision_id,target_id,resource_kind,canonical_key,params_json,target_json) VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        revision["id"],
                        member["id"],
                        member["resource_kind"],
                        member["canonical_key"],
                        Jsonb(member["params_json"]),
                        Jsonb(member["target_json"]),
                    ),
                )
        members = conn.execute(
            "SELECT * FROM control.target_export_member WHERE revision_id=%s",
            (revision["id"],),
        ).fetchall()
        if any(not member["spec_recovered"] for member in members):
            # Never manufacture immutable warehouse history from migration placeholders.
            raise ServiceError(
                "export_spec_missing", "Frozen export specification is not recovered"
            )
        # Frozen specs and the revision's taken_at reach SQL through raw.targets.
        rows = [
            {
                **m["target_json"],
                "resource_kind": m["resource_kind"],
                "canonical_key": m["canonical_key"],
                "params_json": m["params_json"],
                "taken_at": revision["taken_at"],
                "_cycle_id": cycle_id,
                "_revision_id": revision["id"],
            }
            for m in members
        ]
    mirror(warehouse, {"targets": rows})
    return revision
