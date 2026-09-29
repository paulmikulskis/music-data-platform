"""Immutable dbt bindings, supersession, close stamps, and the warehouse mirror catch-up."""

import asyncio
from typing import Any, Protocol
from uuid import uuid4

import httpx

from mdp_functions.control_db import ControlDB
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import RefusingClient
from mdp_functions.health_policy import AD_HOC_CYCLE_PREFIXES, scheduled_cycle_sql
from mdp_functions.settings import Settings
from mdp_functions.warehouse.base import Warehouse, mirror, stripped_cycles


class AdminApi(Protocol):
    async def get_run(self, run_id: str, job_id: str) -> dict[str, Any]: ...


class CloudAdminApi:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def get_run(self, run_id: str, job_id: str) -> dict[str, Any]:
        s = self.settings
        try:
            async with RefusingClient(
                timeout=httpx.Timeout(10, connect=5)
            ) as client:
                response = await client.get(
                    f"{s.dbt_cloud_host}/api/v2/accounts/{s.dbt_cloud_account_id}/runs/{run_id}/",
                    headers={"Authorization": f"Bearer {s.dbt_cloud_token}"},
                )
                response.raise_for_status()
                return response.json()["data"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ServiceError(
                "dbt_api_unavailable", "Could not verify dbt run identity", 503
            ) from exc


class FakeAdminApi:
    def __init__(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        self.records = records

    async def get_run(self, run_id: str, job_id: str) -> dict[str, Any]:
        if self.records is not None:
            return self.records.get(run_id, {})
        return {
            "id": run_id,
            "job_definition_id": job_id,
            "in_progress": True,
            "git_sha": None,
        }


class Cycles:
    def __init__(
        self,
        db: ControlDB,
        warehouse: Warehouse,
        settings: Settings,
        admin: AdminApi | None = None,
    ) -> None:
        self.db, self.warehouse, self.settings = db, warehouse, settings
        self.admin = admin or (
            CloudAdminApi(settings) if settings.dbt_cloud_verify else FakeAdminApi()
        )

    def mirror(self) -> None:
        """Recovery's catch-up: every lagging scope's closes, then unmirrored derived rows."""
        catch_up(self.db, self.warehouse)
        mirror_derived(self.db, self.warehouse)

    async def bind_cycle(
        self,
        cadence: str,
        scope: str,
        dbt_run_id: str,
        reason_category: str,
        job_id: str,
        cycle_id: str | None = None,
        runner: str = "cloud",
        global_inputs: list[str] | None = None,
        lock_runner: bool = False,
    ) -> dict[str, Any]:
        if runner not in {"core", "cloud"}:
            raise ServiceError("scope_mismatch", "Runner must be core or cloud")
        existing = await asyncio.to_thread(
            self.db.one,
            "SELECT * FROM control.cycle_attempt WHERE dbt_run_id=%s",
            (dbt_run_id,),
        )
        identity: dict[str, Any] = {}
        if runner == "cloud" and not existing:
            identity = await self.admin.get_run(dbt_run_id, job_id)
            if (
                str(identity.get("id")) != dbt_run_id
                or str(identity.get("job_definition_id")) != job_id
                or not identity.get("in_progress")
            ):
                raise ServiceError(
                    "scope_mismatch",
                    "Admin API run identity or in-progress state does not match",
                )
        return await asyncio.to_thread(
            self.bind_verified,
            cadence,
            scope,
            dbt_run_id,
            reason_category,
            job_id,
            cycle_id,
            runner,
            identity,
            global_inputs,
            lock_runner,
        )

    def bind_verified(
        self,
        cadence: str,
        scope: str,
        dbt_run_id: str,
        reason_category: str,
        job_id: str,
        cycle_id: str | None,
        runner: str,
        identity: dict[str, Any],
        global_inputs: list[str] | None = None,
        lock_runner: bool = False,
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            if lock_runner and not conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS locked", (f"core:{cadence}:{scope}",)
            ).fetchone()["locked"]:
                # Run Now refuses rather than waits: the control-api call aborts after 20 s.
                raise ServiceError(
                    "core_run_in_progress",
                    f"A Core run holds core:{cadence}:{scope}; no cycle is bound; press again after the run",
                    409,
                )
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"cycle:{cadence}:{scope}",),
            )
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", ("binding:" + dbt_run_id,)
            )
            job = conn.execute(
                "SELECT * FROM control.dbt_job WHERE job_id=%s", (job_id,)
            ).fetchone()
            if not job or (job["cadence"], job["scope"], job["runner"]) != (
                cadence,
                scope,
                runner,
            ):
                raise ServiceError(
                    "scope_mismatch",
                    "job_id, cadence and scope or runner do not match control.dbt_job",
                )
            if global_inputs is not None and sorted(global_inputs) != sorted(job["global_inputs"]):
                raise ServiceError(
                    "global_inputs_mismatch",
                    "The job's registered global inputs differ from its generated mdp_global_inputs()",
                )
            existing = conn.execute(
                "SELECT * FROM control.cycle_attempt WHERE dbt_run_id=%s", (dbt_run_id,)
            ).fetchone()
            touched = []
            if existing:
                bound = conn.execute(
                    "SELECT * FROM control.cycle WHERE id=%s", (existing["cycle_id"],)
                ).fetchone()
                if (
                    (bound["cadence"], bound["scope"]) != (cadence, scope)
                    or existing["runner"] != runner
                    or existing["job_id"] != job_id
                    or (cycle_id and str(existing["cycle_id"]) != cycle_id)
                ):
                    raise ServiceError(
                        "scope_mismatch",
                        "Existing binding has different runner, job, cycle or scope",
                    )
                result = existing
            else:
                mode = conn.execute(
                    "SELECT runner FROM control.runner_mode WHERE id=true"
                ).fetchone()
                if not mode or mode["runner"] != runner:
                    raise ServiceError(
                        "runner_inactive",
                        "New bindings require the active configured runner",
                    )
                if cycle_id:
                    cycle = conn.execute(
                        "SELECT * FROM control.cycle WHERE id=%s AND cadence=%s AND scope=%s",
                        (cycle_id, cadence, scope),
                    ).fetchone()
                    if not cycle or cycle["status"] != "closed":
                        raise ServiceError(
                            "replay_refused",
                            "Replay requires a closed cycle in this cadence and scope",
                        )
                elif reason_category == "scheduled":
                    old = conn.execute(
                        "UPDATE control.cycle SET status='superseded' WHERE cadence=%s AND scope=%s AND status='open' AND opened_by_dbt_run_id NOT LIKE 'backfill:%%' RETURNING id",
                        (cadence, scope),
                    ).fetchall()
                    for item in old:
                        touched.append(item["id"])
                        conn.execute(
                            "UPDATE control.batch b SET status='draining' FROM control.run r WHERE b.run_id=r.id AND r.cycle_id=%s AND b.status='running'",
                            (item["id"],),
                        )
                        conn.execute(
                            "UPDATE control.batch b SET status='failed' FROM control.run r WHERE b.run_id=r.id AND r.cycle_id=%s AND b.status='queued'",
                            (item["id"],),
                        )
                    # A tenant daily cycle a scheduled run opens freezes its weekday in the tenant's
                    # tenant timezone; a manual or backfill run id freezes none.
                    weekday = (
                        cadence == "daily"
                        and scope.startswith("tenant:")
                        and not dbt_run_id.startswith(AD_HOC_CYCLE_PREFIXES)
                    )
                    cycle = conn.execute(
                        f"""INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,git_sha,image_digest,manifest_mode,local_weekday,
                        tenant_close_nos,timezone)
                        VALUES (%s,%s,%s,%s,%s,'stamp',CASE WHEN %s THEN extract(isodow FROM now() AT TIME ZONE coalesce(
                            (SELECT timezone FROM control.dbt_job WHERE scope=%s ORDER BY job_id LIMIT 1),'UTC'))::int END,
                        {TENANT_CLOSE_NOS},{TENANT_TIMEZONE})
                        RETURNING *""",
                        (
                            cadence,
                            scope,
                            dbt_run_id,
                            identity.get("git_sha"),
                            self.settings.image_digest,
                            weekday,
                            scope,
                            scope,
                            scope,
                            scope,
                        ),
                    ).fetchone()
                else:
                    # A Retry or the Core restore attaches to the newest scheduled cycle, never to one
                    # a backfill or a manual (Run Now) run id opened.
                    cycle = conn.execute(
                        "SELECT * FROM control.cycle WHERE cadence=%s AND scope=%s AND status<>'superseded' "
                        f"AND {scheduled_cycle_sql()} "
                        "ORDER BY opened_at DESC LIMIT 1",
                        (cadence, scope),
                    ).fetchone()
                    if not cycle:
                        raise ServiceError(
                            "replay_refused", "No non-superseded cycle exists to attach"
                        )
                result = conn.execute(
                    """INSERT INTO control.cycle_attempt(dbt_run_id,cycle_id,reason_category,git_sha,image_digest,runner,job_id,stamp_protocol)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,true) RETURNING *""",
                    (
                        dbt_run_id,
                        cycle["id"],
                        reason_category,
                        identity.get("git_sha"),
                        self.settings.image_digest,
                        runner,
                        job_id,
                    ),
                ).fetchone()
            settled = settle_unstamped(conn, result["cycle_id"])
        if settled:
            catch_up(self.db, self.warehouse, settled[0]["scope"])
        mirror_bindings(self.db, self.warehouse, [result["cycle_id"], *touched], [dbt_run_id])
        return result

    def manual(self, cadence: str, scope: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"cycle:{cadence}:{scope}",),
            )
            cycle = conn.execute(
                "SELECT * FROM control.cycle WHERE cadence=%s AND scope=%s AND status<>'superseded' AND opened_by_dbt_run_id NOT LIKE 'backfill:%%' ORDER BY opened_at DESC LIMIT 1",
                (cadence, scope),
            ).fetchone()
            if not cycle:
                cycle = conn.execute(
                    f"INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,image_digest,manifest_mode,tenant_close_nos,timezone) "
                    f"VALUES (%s,%s,%s,%s,'stamp',{TENANT_CLOSE_NOS},{TENANT_TIMEZONE}) RETURNING *",
                    (cadence, scope, f"manual:{uuid4()}", self.settings.image_digest, scope, scope, scope),
                ).fetchone()
        mirror_bindings(self.db, self.warehouse, [cycle["id"]])
        return cycle

    def close(self, cycle_id: Any) -> dict[str, Any]:
        with self.db.transaction() as conn:
            cycle = conn.execute(
                "SELECT * FROM control.cycle WHERE id=%s FOR UPDATE", (cycle_id,)
            ).fetchone()
            if not cycle:
                raise ServiceError("cycle_not_found", "Unknown cycle", 404)
            if cycle["status"] == "superseded":
                raise ServiceError("scope_mismatch", "Cannot close a superseded cycle")
            settled = settle_unstamped(conn, cycle_id)
            if settled:
                cycle = settled[0]
            elif cycle["status"] == "open":
                scope = cycle["scope"]
                conn.execute(
                    "INSERT INTO control.scope_close(scope) VALUES (%s) ON CONFLICT(scope) DO NOTHING",
                    (scope,),
                )
                # Every close in the scope holds this row lock through commit, so commit order
                # equals close_no order across cadences.
                close_no = conn.execute(
                    "SELECT last_close_no FROM control.scope_close WHERE scope=%s FOR UPDATE", (scope,)
                ).fetchone()["last_close_no"] + 1
                conn.execute(
                    "UPDATE control.scope_close SET last_close_no=%s WHERE scope=%s", (close_no, scope)
                )
                # One control-only UPDATE: a dump is committed when its load to the run's pinned
                # warehouse is loaded (Appendix C sets that after the warehouse commit). Loads to
                # other warehouses, such as a migrate destination, never hold a stamp back.
                # Unstamped candidates come from a partial index.
                conn.execute(
                    """UPDATE control.dump d SET close_no=%s FROM control.run r
                    WHERE r.id=d.run_id AND d.scope=%s AND d.close_no IS NULL AND d.kind='output'
                    AND d.quarantined_at IS NULL AND d.rejected_at IS NULL
                    AND EXISTS (SELECT 1 FROM control.load l WHERE l.dump_id=d.id
                        AND l.warehouse_id=r.warehouse_id AND l.status='loaded')""",
                    (close_no, scope),
                )
                global_close_no, inputs = None, []
                if scope.startswith("tenant:"):
                    job = conn.execute(
                        "SELECT j.global_inputs FROM control.dbt_job j JOIN control.cycle_attempt a ON a.job_id=j.job_id WHERE a.dbt_run_id=%s",
                        (cycle["opened_by_dbt_run_id"],),
                    ).fetchone()
                    inputs = sorted(job["global_inputs"]) if job else []
                    # Only global closes whose stamps are already in the warehouse.
                    mirrored = conn.execute(
                        "SELECT mirrored_close_no FROM control.scope_close WHERE scope='global'"
                    ).fetchone()
                    global_close_no = mirrored["mirrored_close_no"] if mirrored else -1
                cycle = conn.execute(
                    """UPDATE control.cycle SET status='closed',closed_at=coalesce(closed_at,now()),manifest_mode='stamp',
                    close_no=%s,global_close_no=%s,global_inputs=%s WHERE id=%s RETURNING *""",
                    (close_no, global_close_no, inputs, cycle_id),
                ).fetchone()
        # Open or already closed, every close call runs the catch-up. The closed raw.cycles row
        # lands with its stamps, and the close is terminal once mirrored_close_no reaches it.
        catch_up(self.db, self.warehouse, cycle["scope"])
        if settled:
            mirror_bindings(self.db, self.warehouse, [cycle_id])
        mirrored = self.db.one(
            "SELECT mirrored_close_no FROM control.scope_close WHERE scope=%s", (cycle["scope"],)
        )
        if cycle["close_no"] is not None and (not mirrored or mirrored["mirrored_close_no"] < cycle["close_no"]):
            raise ServiceError("warehouse_unavailable", "Close stamps are not mirrored yet", 503)
        return cycle


CYCLE_COLUMNS = (
    "id,cadence,scope,opened_at,opened_by_dbt_run_id,closed_at,status,git_sha,image_digest,"
    "manifest_mode,close_no,global_close_no,global_inputs,tenant_close_nos,timezone"
)
# A tenant cycle freezes the scope's scheduled timezone at bind (UTC without one), so its local week never
# moves on a Replay. Its two parameters are the cycle's scope; a global cycle records none.
TENANT_TIMEZONE = (
    "CASE WHEN %s LIKE 'tenant:%%' THEN coalesce((SELECT timezone FROM control.dbt_job "
    "WHERE scope=%s ORDER BY job_id LIMIT 1),'UTC') END"
)
# A global cycle freezes each tenant scope's mirrored close at bind (cycle.tenant_close_nos), so its
# scope reads count the same tenant revisions on a replay. Its one parameter is the
# cycle's scope; a tenant cycle records none.
TENANT_CLOSE_NOS = (
    "CASE WHEN %s='global' THEN coalesce((SELECT jsonb_object_agg(scope,mirrored_close_no) "
    "FROM control.scope_close WHERE scope LIKE 'tenant:%%' AND mirrored_close_no>=0),'{}'::jsonb) "
    "ELSE '{}'::jsonb END"
)
MIRROR_LOCK = "SELECT pg_advisory_xact_lock(hashtext('mdp-cycle-mirror'))"


def settle_unstamped(conn: Any, cycle_id: Any = None) -> list[dict[str, Any]]:
    """A cycle the previous image closed (in a deploy window, or after a functions rollback) has no
    close_no, whatever its manifest_mode. It holds that close's full list, so it becomes list/0:
    stamp-mode revision reads count it, and a Replay reads its list, not stamps from a later close."""
    rows = conn.execute(
        """UPDATE control.cycle SET manifest_mode='list',close_no=0
        WHERE status='closed' AND close_no IS NULL AND (%s::uuid IS NULL OR id=%s::uuid) RETURNING *""",
        (cycle_id, cycle_id),
    ).fetchall()
    for scope in sorted({r["scope"] for r in rows}):
        conn.execute("INSERT INTO control.scope_close(scope) VALUES (%s) ON CONFLICT(scope) DO NOTHING", (scope,))
    return rows


def mirror_bindings(
    db: ControlDB, warehouse: Warehouse, cycle_ids: list[Any], dbt_run_ids: list[str] | None = None
) -> None:
    """Upsert the cycles a state change touched, read under the mirror lock so the newest state wins.
    A closed row the catch-up has not mirrored yet is left to the catch-up, which writes it with its
    stamps."""
    with db.transaction() as conn:
        conn.execute(MIRROR_LOCK)
        tables = {
            "cycles": conn.execute(
                f"""SELECT {CYCLE_COLUMNS} FROM control.cycle c WHERE id=ANY(%s::uuid[])
                AND NOT (status='closed' AND close_no IS NOT NULL AND close_no > coalesce(
                    (SELECT mirrored_close_no FROM control.scope_close s WHERE s.scope=c.scope), -1))""",
                ([str(c) for c in cycle_ids],),
            ).fetchall(),
            # Empty upserts create the manifest tables that bound readers join.
            "dump_stamps": [],
            "cycle_inputs": [],
        }
        if dbt_run_ids:
            tables["cycle_attempts"] = conn.execute(
                "SELECT * FROM control.cycle_attempt WHERE dbt_run_id=ANY(%s)", (dbt_run_ids,)
            ).fetchall()
        mirror(warehouse, tables)


def catch_up(db: ControlDB, warehouse: Warehouse, scope: str | None = None) -> None:
    """Mirror each lagging scope's closes under its mirror lock: one warehouse transaction writes every
    stamp and raw.cycles row with close_no in (mirrored_close_no, last_close_no], and only after it
    commits does mirrored_close_no advance. Upserts are idempotent, so a catch-up that dies after the
    warehouse commit repeats safely, and no close can pass an earlier one that is not mirrored.
    In every scope, it first settles cycles the previous image closed, and afterwards it rewrites them
    and every raw.cycles row the previous image's mirror stripped of the stamp columns."""
    with db.transaction() as conn:
        settled = [r["id"] for r in settle_unstamped(conn)]
    stripped = stripped_cycles(warehouse)
    for row in db.all(
        "SELECT scope FROM control.scope_close WHERE mirrored_close_no<last_close_no AND (%s::text IS NULL OR scope=%s)",
        (scope, scope),
    ):
        with db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("mdp-stamp-mirror:" + row["scope"],))
            window = conn.execute(
                "SELECT mirrored_close_no,last_close_no FROM control.scope_close WHERE scope=%s", (row["scope"],)
            ).fetchone()
            low, high = window["mirrored_close_no"], window["last_close_no"]
            if high <= low:
                continue
            mirror(
                warehouse,
                {
                    "cycles": conn.execute(
                        f"""SELECT {CYCLE_COLUMNS} FROM control.cycle
                        WHERE scope=%s AND close_no>%s AND close_no<=%s""",
                        (row["scope"], low, high),
                    ).fetchall(),
                    "dump_stamps": conn.execute(
                        """SELECT d.id AS dump_id,d.scope,d.close_no,s.source_key,
                        (SELECT min(l.target_table) FROM control.load l JOIN control.run r ON r.id=d.run_id
                         WHERE l.dump_id=d.id AND l.warehouse_id=r.warehouse_id) AS target_table
                        FROM control.dump d LEFT JOIN control.streamline s ON s.id=d.streamline_id
                        WHERE d.scope=%s AND d.close_no>%s AND d.close_no<=%s""",
                        (row["scope"], low, high),
                    ).fetchall(),
                },
            )
            conn.execute(
                "UPDATE control.scope_close SET mirrored_close_no=%s WHERE scope=%s AND mirrored_close_no<%s",
                (high, row["scope"], high),
            )
    if settled or stripped:
        mirror_bindings(db, warehouse, [*settled, *stripped])


def mirror_derived(db: ControlDB, warehouse: Warehouse, run_id: Any = None) -> None:
    """Upsert unmirrored derived rows (of one run, or all), then mark them after the warehouse commit."""
    with db.transaction() as conn:
        conn.execute(MIRROR_LOCK)
        rows = conn.execute(
            """SELECT i.id,i.cycle_id,i.dump_id,i.phase,i.added_at FROM control.cycle_input i
            WHERE i.mirrored_at IS NULL AND (%s::uuid IS NULL OR i.dump_id IN
            (SELECT d.id FROM control.dump d WHERE d.run_id=%s::uuid))""",
            (run_id, run_id),
        ).fetchall()
        if not rows:
            return
        mirror(warehouse, {"cycle_inputs": rows})
        conn.execute(
            "UPDATE control.cycle_input SET mirrored_at=now() WHERE id=ANY(%s::uuid[])",
            ([r["id"] for r in rows],),
        )
