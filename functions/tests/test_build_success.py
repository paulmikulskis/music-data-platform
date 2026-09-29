"""Core sends the same durable completion event used by the Cloud webhook."""

import httpx
from mdp_functions.cadence_health import report_success


def test_success_notification_names_the_finished_invocation(monkeypatch, capsys):
    monkeypatch.setenv("MDP_SERVICE_URL", "https://service.invalid")
    monkeypatch.setenv("MDP_SERVICE_TOKEN", "fixture-token")
    monkeypatch.setenv("DBT_CLOUD_JOB_ID", "core-daily-global")
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr("mdp_functions.cadence_health.httpx.post", post)
    report_success("core:completed")
    assert len(calls) == 1
    assert calls[0][0].endswith("/v1/dbt/webhook")
    assert calls[0][1]["json"] == {"event_id": "core-success:core:completed", "run_id": "core:completed",
                                    "job_id": "core-daily-global", "status": "succeeded"}
    assert "Check /ops" in capsys.readouterr().out
