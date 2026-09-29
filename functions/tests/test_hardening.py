"""Cross-entry concurrency, restricted grants and page transaction boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions.admission import acquire, attempt
from mdp_functions.budget import draw
from mdp_functions.errors import ServiceError
from mdp_functions.http import FixtureTransport
from mdp_functions.layers import Ctx, bronze, gold, silver
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.runs import Runtime
from mdp_functions.schemas import shape
from pydantic import BaseModel


@pytest.mark.docker
async def test_durable_permit_across_workers(rt: Runtime) -> None:
    _, run = await bound(rt)
    active = attempt(rt.db, run["id"])
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(acquire, rt.db, run, active, 30) for _ in range(2)]
        batches = [f.result(5) for f in futures]
    assert sum(b is not None for b in batches) == 1
    assert (
        rt.db.one("SELECT count(*) AS n FROM control.batch WHERE status='running'")["n"]
        == 1
    )


@pytest.mark.docker
async def test_multitable_page_atomic_registration_and_cursor(rt: Runtime) -> None:
    @bronze(
        source_key="multi_output",
        writes=["raw.multi_a", "raw.multi_b"],
        cadence="daily",
    )
    async def function(ctx: Ctx) -> None:
        ctx.observed(2)
        ctx.emit("raw.multi_a", {"value": 1})
        ctx.emit("raw.multi_b", {"value": 2})
        ctx.set_cursor(None, {"page": 1})

    try:
        sync(rt.db)
        _, run = await bound(rt, "multi_output")
        await rt.execute(run["id"])
        batch = rt.db.one("SELECT * FROM control.batch WHERE run_id=%s", (run["id"],))
        assert len(batch["dump_ids"]) == 2
        assert rt.db.one(
            "SELECT cursor_value FROM control.cursor WHERE streamline_id=%s",
            (run["streamline_id"],),
        )["cursor_value"] == {"page": 1}
        assert len(rt.receipts(run["id"])["receipts"]) == 1
        assert rt.receipts(run["id"])["receipts"][0]["rows_written"] == "2"
        files = rt.store.list("dumps")
        manifests = [
            json.loads(rt.store.get(p)) for p in files if p.endswith("manifest.json")
        ]
        assert (
            len(manifests) == 2
            and manifests[0]["page_manifests"] == manifests[1]["page_manifests"]
        )
        assert {m["target_table"] for m in manifests} == {"raw.multi_a", "raw.multi_b"}
    finally:
        REGISTRY.pop("multi_output", None)


@pytest.mark.docker
async def test_cursor_reset_rejects_whole_page(
    rt: Runtime, databases: dict[str, str]
) -> None:
    _, run = await bound(rt, "billboard_hot100")
    active = attempt(rt.db, run["id"])
    batch = acquire(rt.db, run, active, 30)
    rt.db.execute(
        "INSERT INTO control.cursor(streamline_id,cursor_key,cursor_value) VALUES (%s,'default','0')",
        (run["streamline_id"],),
    )
    before = rt.db.one(
        "SELECT * FROM control.cursor WHERE streamline_id=%s", (run["streamline_id"],)
    )
    ctx = Ctx(REGISTRY["billboard_hot100"], run, {"": before})
    ctx.observed(1)
    ctx.yield_row(
        {
            "chart": "hot-100",
            "week": "2026-09-12",
            "position": 1,
            "title": "Fixture",
            "artist": "Fixture Ensemble",
        }
    )
    ctx.set_cursor(None, 1)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.cursor SET reset_generation=reset_generation+1 WHERE streamline_id=%s",
            (run["streamline_id"],),
        )
    with pytest.raises(ServiceError, match="Cursor advanced or was reset"):
        rt.dumps.publish(ctx, run, batch, [])
    assert rt.db.one("SELECT count(*) AS n FROM control.dump")["n"] == 0
    assert (
        rt.db.one("SELECT cursor_value,reset_generation FROM control.cursor")[
            "cursor_value"
        ]
        == 0
    )


@pytest.mark.docker
@pytest.mark.parametrize("excluded", [0, 9])
async def test_recovery_registers_complete_orphan_page(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch, excluded: int
) -> None:
    _, run = await bound(rt, "billboard_hot100")
    active = attempt(rt.db, run["id"])
    batch = acquire(rt.db, run, active, 30)
    ctx = Ctx(REGISTRY["billboard_hot100"], run)
    ctx.observed(1)
    ctx.yield_row(
        {
            "chart": "hot-100",
            "week": "2026-09-12",
            "position": 1,
            "title": "Fixture",
            "artist": "Fixture Ensemble",
        }
    )
    from dataclasses import replace

    ctx.manifest = replace(ctx.manifest, exclusion_reasons=("fixture:outside_scope",))
    ctx.observed(excluded)
    for n in range(excluded):
        ctx.exclude({"position": n}, reason="fixture:outside_scope")
    register = rt.dumps.register

    def crash(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated process loss before registration")

    monkeypatch.setattr(rt.dumps, "register", crash)
    with pytest.raises(RuntimeError, match="simulated process loss"):
        rt.dumps.publish(ctx, run, batch, [], True)
    monkeypatch.setattr(rt.dumps, "register", register)
    manifest = next(p for p in rt.store.list("dumps") if p.endswith("manifest.json"))
    rt.dumps.recover_manifest(manifest)
    rt.dumps.recover_manifest(manifest)
    assert rt.db.one("SELECT count(*) AS n FROM control.dump")["n"] == 1
    assert (
        rt.db.one("SELECT last_part_uploaded FROM control.batch")["last_part_uploaded"]
        == 1
    )
    rt.landing(run["warehouse_id"]).drain(run["id"])
    assert rt.db.one("SELECT status FROM control.load")["status"] == "loaded"
    receipt = rt.receipts(run["id"])["receipts"][0]
    assert receipt["rows_excluded"] == str(excluded)
    assert receipt["row_rejection_share"] == 0 and receipt["row_coverage_met"]
    assert receipt["row_exclusions"] == ({"fixture:outside_scope": excluded} if excluded else {})


@pytest.mark.docker
async def test_reservation_once_and_budget_draw_is_idempotent(
    rt: Runtime, databases: dict[str, str]
) -> None:
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.budget(scope,period,cap_cents,soft_pct,hard_action) VALUES ('global','daily',2,80,'pause')"
        )
    dbt_id, run = await bound(rt, "billboard_hot100")
    rt.admit("billboard_hot100", dbt_run_id=dbt_id)
    assert rt.db.one("SELECT count(*) AS n FROM control.budget_reservation")["n"] == 1
    draw(rt.db, run, "fixture", "once", 1)
    draw(rt.db, run, "fixture", "once", 1)
    assert (
        rt.db.one("SELECT consumed_cents FROM control.budget_reservation")[
            "consumed_cents"
        ]
        == 1
    )
    draw(rt.db, run, "fixture", "twice", 1)
    with pytest.raises(ServiceError, match="configured budget"):
        draw(rt.db, run, "fixture", "third", 1)
    assert rt.db.one("SELECT count(*) AS n FROM control.cost_ledger")["n"] == 2


def test_model_extra_preserved_and_schema_validation_rejects() -> None:
    class Record(BaseModel):
        value: int

    accepted, columns, rejected, _ = shape(
        [{"value": 1, "new": "preserve"}, {"value": "bad"}], Record, None
    )
    assert accepted[0]["_extra"] == {"new": "preserve"}
    assert len(rejected) == 1 and rejected[0]["record"]["value"] == "bad"
    assert columns == {"value": "bigint"}


def test_registration_named_errors() -> None:
    with pytest.raises(ServiceError, match="Gold requires"):
        gold(source_key="bad_gold", writes=["raw.x"], cadence="daily")(lambda: None)
    with pytest.raises(ServiceError, match="under raw"):
        bronze(source_key="bad_table", writes=["marts.x"], cadence="daily")(
            lambda: None
        )
    with pytest.raises(ServiceError, match="hourly, daily, or weekly"):
        bronze(source_key="bad_cadence", writes=[], cadence="minute")(lambda: None)


@pytest.mark.docker
@pytest.mark.parametrize("layer", ["silver", "gold"])
async def test_derived_layers_refuse_undeclared_inputs(
    rt: Runtime, layer: str
) -> None:
    options = {
        "source_key": "later_" + layer,
        "writes": ["raw.later"],
        "cadence": "daily",
        "reads": ["staging.inputs"],
    }
    dec = silver if layer == "silver" else gold
    if layer == "gold":
        options["external"] = True

    @dec(**options)
    async def function(ctx: Ctx, rows: list) -> None:
        raise AssertionError("must not execute")

    try:
        sync(rt.db)
        with pytest.raises(ServiceError, match="must be declared"):
            rt.admit("later_" + layer, manual=True, input_relation="staging.undeclared")
    finally:
        REGISTRY.pop("later_" + layer, None)


async def test_fixture_transport_miss_raises(tmp_path: Path) -> None:
    async with httpx.AsyncClient(transport=FixtureTransport([])) as client:
        with pytest.raises(ServiceError, match="No fixture"):
            await client.get("https://fixture.invalid/missing")


@pytest.mark.docker
async def test_configured_estimate_flows_through_reservation_and_call(
    rt: Runtime, databases: dict[str, str]
) -> None:
    rt.settings.vendor_estimates = {"billboard_hot100": 2}
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.budget(scope,period,cap_cents,soft_pct,hard_action) VALUES ('global','daily',8,80,'pause')"
        )
    _, run = await bound(rt, "billboard_hot100")
    assert (
        rt.db.one("SELECT reserved_cents FROM control.budget_reservation")[
            "reserved_cents"
        ]
        == 8
    )
    await rt.execute(run["id"])
    reservation = rt.db.one(
        "SELECT reserved_cents,consumed_cents,settled_at FROM control.budget_reservation"
    )
    assert reservation["reserved_cents"] == reservation["consumed_cents"] == 2
    assert reservation["settled_at"] is not None
    assert rt.db.one("SELECT cost_cents FROM control.call_ledger")["cost_cents"] == 2
    assert rt.receipts(run["id"])["run"]["cost_cents"] == 2


@pytest.mark.docker
async def test_vendor_retry_exhaustion_dead_letters(rt: Runtime) -> None:
    rt.transport = httpx.MockTransport(
        lambda request: httpx.Response(429, json={"error": "retry"})
    )
    _, run = await bound(rt, "billboard_hot100")
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])["run"]
    assert result["status"] == "failed" and result["error_class"] == "vendor_retryable"
    assert rt.receipts(run["id"])["receipts"][0]["target_coverage_met"] is False
    assert rt.db.one("SELECT count(*) AS n FROM control.call_ledger")["n"] == 4
    assert rt.db.one("SELECT count(*) AS n FROM control.dead_letter")["n"] == 1


def test_numeric_widening_is_additive_and_unknown_fields_survive() -> None:
    accepted, columns, rejected, drift = shape(
        [{"value": 1.5, "_vendor_extension": True}], "infer", {"value": "bigint"}
    )
    assert columns == {"value": "double"} and drift
    assert accepted[0]["_extra"] == {"_vendor_extension": True}
    assert not rejected
