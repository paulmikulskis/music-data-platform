"""Tests for cycle input mirrors."""

import json
from uuid import uuid4

import psycopg
from cycle_mirror_fixture import render


async def test_a_tenant_cycle_mirrors_and_reads_its_frozen_global_inputs(rt, databases):
    scope, job, dbt_run = "tenant:" + str(uuid4()), "tenant-job:" + uuid4().hex, "tenant:" + uuid4().hex
    frozen = ["raw.shazam_chart_entries"]
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.dbt_job(job_id,runner,cadence,scope,global_inputs) VALUES (%s,'core','daily',%s,%s)",
            (job, scope, frozen),
        )
    binding = await rt.cycles.bind_cycle("daily", scope, dbt_run, "scheduled", job, runner="core", global_inputs=frozen)
    closed = rt.cycles.close(binding["cycle_id"])
    assert closed["global_inputs"] == frozen
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        row = conn.execute(
            "SELECT cast(c.id as text),c.cadence,c.scope,c.manifest_mode,c.close_no,c.global_close_no,"
            "cast(c.global_inputs as text),cast(c.tenant_close_nos as text) FROM raw.cycles c WHERE c.id=%s",
            (binding["cycle_id"],),
        ).fetchone()
    assert json.loads(row[6]) == frozen
    cycle = render("mdp_cycle_row", list(row))
    assert cycle["global_inputs"] == frozen
    # The generated mdp_global_inputs('daily') declares nothing, so only the frozen list admits the
    # global stamps; the same table without it is an undeclared global input and does not render.
    manifest = render("mdp_manifest_sql", cycle, "raw.shazam_chart_entries")
    assert "scope = 'global' and close_no <=" in manifest
    assert render("mdp_manifest_sql", {**cycle, "global_inputs": []}, "raw.shazam_chart_entries") is None
    # A cycle mirrored before the column existed falls back to the generated list.
    assert render("mdp_cycle_row", [*row[:6], None])["global_inputs"] is None
