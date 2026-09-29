"""Coverage boundaries and overdue alerts use the shared health rules."""

import pytest
from mdp_functions.cadence_health import sweep
from mdp_functions.coverage import target_coverage


@pytest.mark.parametrize("total,floor", [(0, 1.0), (1, 1.0), (3, 1.0), (9, 1.0), (10, .9)])
def test_default_and_declared_coverage_floors(total, floor):
    policy = {"min_target_coverage": None}
    run = {"resolved_config": {"target_coverage": policy}}
    batch = {"target_ids": list(range(total)), "cursor_checkpoint": {"completed_targets": []}}
    result = target_coverage(run, [batch])
    assert result["min_target_coverage"] == floor
    assert result["target_coverage_met"] == (total == 0)
    policy["min_target_coverage"] = .75
    assert target_coverage(run, [batch])["min_target_coverage"] == .75


@pytest.mark.parametrize("cadence,seconds", [("hourly", 3600), ("daily", 86400), ("weekly", 604800)])
def test_overdue_alert_boundary(rt, cadence, seconds):
    with rt.db.transaction() as conn:
        cycles = {}
        for offset in (-1, 0, 1):
            cycle = conn.execute(
                "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at) "
                "VALUES (%s,%s,%s,now() - %s * interval '1 second') RETURNING id",
                (cadence, f"boundary:{offset}", f"boundary:{offset}", seconds + offset),
            ).fetchone()
            cycles[offset] = cycle["id"]
        sweep(conn)
        sweep(conn)
        alerts = conn.execute(
            "SELECT subject_id FROM control.alert WHERE class='cadence_failed'"
        ).fetchall()
        assert [a["subject_id"] for a in alerts] == [str(cycles[1])]


@pytest.mark.parametrize("artifact", ["OUTPUT", "DBT_OUTPUT", "PYTHON_OUTPUT"])
def test_generated_policy_check_detects_drift(tmp_path, monkeypatch, artifact):
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).parents[2] / "ops/ci/generate_health_policy.py"
    spec = importlib.util.spec_from_file_location("generate_health_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(path), "--check"])
    module.main()
    output = tmp_path / "health-policy.generated.ts"
    output.write_text("stale")
    monkeypatch.setattr(module, artifact, output)
    with pytest.raises(SystemExit, match="Health rules are stale. Run"):
        module.main()


@pytest.mark.parametrize("opener", ["manual:canary:probe", "manual:operator", "backfill:probe", "canary:probe"])
def test_ad_hoc_cycles_never_change_scheduled_overdue_alerts(rt, opener):
    with rt.db.transaction() as conn:
        scheduled = conn.execute(
            "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at) "
            "VALUES ('daily','global','scheduled-fixture',now()-interval '2 days') RETURNING id"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at) "
            "VALUES ('daily','global',%s,now()-interval '2 days')", (opener,)
        )
        probe = conn.execute(
            "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,manifest_mode) "
            "VALUES ('daily','global',%s,'stamp') RETURNING id", (opener + ':close',)
        ).fetchone()["id"]
    closed = rt.cycles.close(probe)
    assert closed["close_no"] is not None
    with rt.db.transaction() as conn:
        sweep(conn)
        sweep(conn)
        alerts = conn.execute("SELECT subject_id FROM control.alert WHERE class='cadence_failed'").fetchall()
    assert [row["subject_id"] for row in alerts] == [str(scheduled)]




@pytest.mark.parametrize("markers,expected", [
    (["t", "excluded:t"], ["t"]),
    (["t", "row_rejected:t", "rejected:t"], ["t"]),
    (["t", "excluded:t", "accepted:t"], []),
    (["t", "excluded:t", "skipped:t"], []),
    (["excluded:t"], []),
    (["t"], []),
])
def test_zero_yield_uses_durable_completion_markers(markers, expected):
    from mdp_functions.coverage import zero_yield_targets

    batch = {"target_ids": ["t"], "cursor_checkpoint": {"completed_targets": markers}}
    assert zero_yield_targets([batch]) == expected
