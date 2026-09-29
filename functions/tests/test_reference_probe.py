"""design Reference page state and the reference alert classes, judged by the probe from the mirror
(serving generation, newest import attempt, disk use) and the warehouse (newest landed generation)."""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from conftest import bound, url_database
from mdp_functions.reference import probe
from psycopg import sql
from test_identity_functions import G1, REPO, load_mirror

pytestmark = pytest.mark.docker


@pytest.fixture
def mirror(monkeypatch):
    admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin:
        pytest.skip("Docker integration tests require MDP_CONTROL_ADMIN_URL")
    name, meta = "mbt_pr_" + uuid4().hex[:10], "mbt_meta_" + uuid4().hex[:10]
    url = load_mirror(admin, name)
    subprocess.run(["psql", "-X", "-q", "-v", f"meta_db={meta}", "-f", str(REPO / "ops/fly/mb-import/meta.sql"), admin],
                   check=True, capture_output=True)
    monkeypatch.setenv("MDP_MB_META_DB", meta)
    yield {"url": url, "meta": url_database(admin, meta), "admin": url_database(admin, name)}
    with psycopg.connect(admin, autocommit=True) as conn:
        for db in (name, meta):
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db)))


def open_alerts(rt):
    return {r["class"] for r in rt.db.all(
        "SELECT class FROM control.alert WHERE subject_type='reference_source' AND subject_id='musicbrainz' AND resolved_at IS NULL")}


async def test_probe_reports_the_page_state_and_judges_each_class(rt, databases, mirror, monkeypatch):
    now = datetime(2026, 9, 30, tzinfo=UTC)
    with psycopg.connect(mirror["meta"], autocommit=True) as conn:
        conn.execute("INSERT INTO volume(total_bytes) VALUES (%s)", (10**15,))
        conn.execute("INSERT INTO import_run(generation,target_db,state,phase,finished_at) VALUES (%s,'musicbrainz_db','promoted','promote',now())", (G1,))
    # Imported but not landed: nothing to reconcile yet, and the landed generation lags the mirror.
    first = probe(rt.db, databases["service_read_url"], mirror["url"], now)
    assert first["state"]["imported_generation"] == G1 and "landed_generation" not in first["state"]
    assert open_alerts(rt) == set()
    # mb_spine lands G1 and its close stamps every dump: it reconciles.
    monkeypatch.setenv("MDP_MB_DB_URL", mirror["url"])
    _, run = await bound(rt, "mb_spine")
    await rt.execute(run["id"])
    rt.cycles.close(run["cycle_id"])
    landed = probe(rt.db, databases["service_read_url"], mirror["url"], now)["state"]
    assert (landed["landed_generation"], landed["landed_reconciled"]) == (G1, True)
    assert landed["landed_counts"] == landed["mirror_counts"] and landed["landed_counts"]["raw.mb_isrc"] == 3
    assert open_alerts(rt) == set()
    row = rt.db.one("SELECT imported_generation,landed_generation,landed_reconciled,disk_total_bytes,probe_error FROM control.reference_source WHERE source='musicbrainz'")
    assert row == {"imported_generation": G1, "landed_generation": G1, "landed_reconciled": True,
                   "disk_total_bytes": 10**15, "probe_error": None}
    # A dump its completion lists leaves the stamps: the generation no longer reconciles.
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        dump = conn.execute(
            "SELECT d.key::uuid FROM raw._run_completion c, jsonb_each(c.outputs) t, jsonb_each_text(t.value) d "
            "WHERE c.run_id=%s AND t.key='raw.mb_isrc' LIMIT 1", (str(run["id"]),)).fetchone()[0]
        saved = conn.execute("DELETE FROM raw.dump_stamps WHERE dump_id=%s RETURNING *", (dump,)).fetchone()
    probe(rt.db, databases["service_read_url"], mirror["url"], now)
    assert open_alerts(rt) == {"reference_generation_incomplete"}
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("INSERT INTO raw.dump_stamps VALUES (%s,%s,%s,%s,%s)", saved)
    # Disk at 80% of the volume opens reference_disk_high; a failed import opens reference_import_failed.
    with psycopg.connect(mirror["meta"], autocommit=True) as conn:
        conn.execute("UPDATE volume SET total_bytes=1")
        conn.execute("INSERT INTO import_run(generation,target_db,state,phase,message,started_at) VALUES "
                     "('20261021-001500','musicbrainz_next','failed','verify','reference_import_failed: counts',now()+interval '1 minute')")
    probe(rt.db, databases["service_read_url"], mirror["url"], now)
    assert open_alerts(rt) == {"reference_disk_high", "reference_import_failed"}
    # Expansion clears the disk class; the import class waits for a validated import that lands.
    with psycopg.connect(mirror["meta"], autocommit=True) as conn:
        conn.execute("UPDATE volume SET total_bytes=%s", (10**15,))
        conn.execute("INSERT INTO import_run(generation,target_db,state,phase,started_at) VALUES "
                     "(%s,'musicbrainz_db','promoted','promote',now()+interval '2 minutes')", (G1,))
    probe(rt.db, databases["service_read_url"], mirror["url"], now)
    assert open_alerts(rt) == set()
    # An old export with no newer dump is stale; an unreachable mirror records its error and judges nothing new.
    probe(rt.db, databases["service_read_url"], mirror["url"], now + timedelta(days=40))
    assert open_alerts(rt) == {"reference_dump_stale"}
    missing = probe(rt.db, databases["service_read_url"], "postgresql://mb_reader:x@127.0.0.1:1/none", now)
    assert missing["state"]["probe_error"].startswith("mirror:") and open_alerts(rt) == {"reference_dump_stale"}


async def test_a_build_on_a_pruned_generation_opens_its_alert(rt, tmp_path, monkeypatch):
    """The runner reports a dbt run that failed with reference_generation_incomplete; the service opens
    one alert for that run however often it is asked."""
    import json

    import httpx
    from mdp_functions import reference
    from mdp_functions.api import create_app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False), raise_app_exceptions=False),
        base_url="http://test", headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        first = (await client.post("/v1/alerts/reference_generation_incomplete", json={"run_id": "core:replay-1"})).json()
        again = (await client.post("/v1/alerts/reference_generation_incomplete", json={"run_id": "core:replay-1"})).json()
    assert first["opened"] is True and again == {"alert_id": first["alert_id"], "opened": False}
    alert = rt.db.one("SELECT class,severity::text,subject_type,subject_id,runbook_slug FROM control.alert WHERE id=%s",
                      (first["alert_id"],))
    assert alert == {"class": "reference_generation_incomplete", "severity": "critical", "subject_type": "dbt_run",
                     "subject_id": "core:replay-1", "runbook_slug": "reference-generation-incomplete"}
    # The runner asks only when a model failed with the class.
    sent = []

    def post(url, json, headers, timeout):
        sent.append((url, json))
        return httpx.Response(200, json={"alert_id": first["alert_id"], "opened": False}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setenv("MDP_SERVICE_URL", "http://service.internal:8080")
    monkeypatch.setenv("MDP_SERVICE_TOKEN", "token")
    (tmp_path / "run_results.json").write_text(json.dumps({"results": [{"message": "Database Error in model x"}]}))
    assert reference.report_incomplete("core:replay-1", str(tmp_path)) == "" and sent == []
    (tmp_path / "run_results.json").write_text(json.dumps({"results": [{"message": (
        "Compilation Error in model int_reference__current\n  reference_generation_incomplete: this cycle's manifest "
        "reads MusicBrainz generation 20260923-002121, whose raw.mb_* rows retention deleted")}]}))
    assert reference.report_incomplete("core:replay-1", str(tmp_path)) == f"ALERT reference_generation_incomplete {first['alert_id']}"
    assert sent == [("http://service.internal:8080/v1/alerts/reference_generation_incomplete", {"run_id": "core:replay-1"})]
