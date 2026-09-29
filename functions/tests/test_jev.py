"""Typed wire requests, privacy, cassettes, evaluation and gold accounting."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import RefusingClient
from mdp_functions.jev import Jev, connection, question_set, retry_delay
from mdp_functions.jev_cassette import EXAMPLES, Cassette, load_set
from mdp_functions.jev_cli import app
from mdp_functions.jev_config import install
from mdp_functions.jev_eval import labeled_rows, metrics, missed_floors
from mdp_functions.jev_types import QuestionSet, Response
from mdp_functions.layers import Ctx, bronze
from mdp_functions.registry import discover, sync
from mdp_functions.settings import REPO, Settings
from pydantic import SecretStr
from typer.testing import CliRunner


@pytest.fixture
def questions():
    return load_set("instruments")


@pytest.fixture
def saved():
    return Cassette(EXAMPLES / "instruments/cassette.json")


@pytest.fixture
def payload(questions, saved):
    return saved.read(questions, {"instrument": "violin"}).model_dump(mode="json")


def step(questions):
    return {
        "id": str(uuid4()),
        "body": questions.model_dump_json(exclude_none=True),
        "model": questions.model,
        "params": {
            "question_set_version": questions.version,
            "max_cost_cents": 1,
            "input_token_microcents": 2,
        },
    }


async def test_cassette_has_twenty_rows_and_no_key(questions, saved):
    rows = labeled_rows(REPO / "dbt/seeds/jev_instruments.csv", questions)
    answers = [await Jev(cassette=saved).ask(row, questions) for row in rows]
    result = metrics(questions, rows, answers)["family"]
    assert len(rows) == 20 and result["accuracy"] == 0.9
    assert next(r for r in result["coverage"] if r["threshold"] == 0.8) == {
        "threshold": 0.8,
        "coverage": 0.9,
        "accuracy": 1,
    }
    assert result["classes"]["strings"]["recall"] == 0.8
    assert sum(b["count"] for b in result["reliability"]) == 80


def test_fast_offline_cli_eval(questions, tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "instruments",
            "--labels",
            str(REPO / "dbt/seeds/jev_instruments.csv"),
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert report["mode"] == "synthetic" and report["missed_floors"] == []
    assert "Confidence threshold" in result.output


def test_try_and_diff_use_rows_and_same_metrics():
    runner = CliRunner()
    result = runner.invoke(app, ["try", "instruments", "--state", "instrument_01"])
    assert result.exit_code == 0 and "strings" in result.output
    result = runner.invoke(
        app,
        [
            "diff",
            "instruments",
            "instruments",
            "--labels",
            str(REPO / "dbt/seeds/jev_instruments.csv"),
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert (
        data["changed_rows"] == [] and data["metric_deltas"]["family"]["accuracy"] == 0
    )


def test_missing_cassette_is_actionable_and_versions_include_all_fields(
    questions, saved
):
    raw = questions.model_dump(mode="json")
    for field, replacement in [
        ("thresholds", {"family": 0.9}),
        ("model", "jev-1.14.0"),
        ("fields", ["title"]),
    ]:
        changed = QuestionSet.model_validate({**raw, field: replacement})
        assert changed.version != questions.version
    changed = deepcopy(raw)
    changed["questions"]["family"]["instructions"] += " Choose carefully."
    with pytest.raises(ServiceError, match="mdp jev record"):
        saved.read(QuestionSet.model_validate(changed), {"instrument": "violin"})
    assert (
        QuestionSet.model_validate(dict(reversed(list(raw.items())))).version
        == questions.version
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"data_class": "tenant"},
        {"data_class": "personal"},
        {"fields": ["email"]},
        {"fields": ["id"]},
        {"fields": ["name"]},
        {"fields": ["nested"]},
    ],
)
async def test_restricted_state_never_reaches_transport(questions, updates):
    changed = QuestionSet.model_validate({**questions.model_dump(), **updates})
    with pytest.raises(ServiceError):
        await Jev().ask(
            {
                "instrument": "violin",
                "email": "redacted",
                "nested": {"tenant_id": "hidden"},
            },
            changed,
        )


@pytest.mark.parametrize(
    "row",
    [
        {"instrument": "violin", "tenant_id": "private"},
        {"instrument": "violin", "owner_class": "person"},
        {"instrument": "contact@example.test"},
        {"instrument": "@listener"},
    ],
)
async def test_input_privacy_is_checked_before_replay(questions, saved, row):
    with pytest.raises(ServiceError, match="Jev|State|Playlist"):
        await Jev(cassette=saved).ask(row, questions)


def test_bronze_builder_points_to_gold():
    with pytest.raises(ServiceError, match="@gold"):

        @bronze(source_key="jev_bronze_test", writes=["raw.nope"], provider="typesafe")
        async def nope(ctx):
            yield {}


@pytest.mark.parametrize(
    "url,method",
    [
        ("https://api.typesafe.ai/v1/systemone", "GET"),
        ("https://api.typesafe.ai/v1/other", "POST"),
        ("https://api.typesafe.ai/v1/systemone?state=secret", "POST"),
        ("http://proxy.test/typesafe/v1/systemone/extra", "POST"),
        ("http://proxy.test/typesafe/v1/systemone", "GET"),
        ("http://proxy.test/typesafe%2fv1/systemone", "POST"),
        ("http://proxy.test/%74ypesafe/v1/../other", "POST"),
    ],
)
async def test_jev_transport_refuses_paths_and_methods(url, method):
    seen = []
    async with RefusingClient(
        transport=httpx.MockTransport(
            lambda request: seen.append(request) or httpx.Response(200)
        )
    ) as client:
        with pytest.raises(ServiceError, match="forbidden|Jev|TypeSafe"):
            await client.request(method, url, json={})
    assert not seen


@pytest.mark.parametrize(
    "url",
    ["https://api.typesafe.ai/v1/systemone", "http://proxy.test/typesafe/v1/systemone"],
)
async def test_jev_transport_allows_exact_posts(url):
    async with RefusingClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    ) as client:
        assert (await client.post(url, json={})).status_code == 200


async def test_live_wire_retries_and_records_usage(questions, payload, monkeypatch):
    requests, reservations, recordings, delays = [], [], [], []

    def handle(request):
        requests.append(request)
        return (
            httpx.Response(
                429,
                headers={
                    "retry-after": "0.25",
                    "set-cookie": "session=ignored; Path=/",
                },
            )
            if len(requests) == 1
            else httpx.Response(200, json=payload)
        )

    client = Jev(
        SimpleNamespace(settings=Settings(typesafe_api_key=SecretStr("fixture"))),
        {"tenant_id": None},
        step(questions),
        transport=httpx.MockTransport(handle),
    )
    monkeypatch.setattr(client, "reserve", reservations.append)
    monkeypatch.setattr(
        client, "record", lambda request, tokens: recordings.append(tokens)
    )

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("mdp_functions.jev.asyncio.sleep", sleep)
    result = await client.ask(
        {"instrument": "violin", "expected_family": "strings"}, questions
    )
    assert (
        result.model == questions.model
        and len(reservations) == 2
        and recordings == [120]
        and delays == [0.25]
    )
    body = json.loads(requests[-1].content)
    assert set(body) == {"state", "model", "questions"} and body["state"] == {
        "instrument": "violin"
    }
    assert body["questions"]["family"]["type"] == "choice"
    assert requests[-1].headers["Authorization"] == "Bearer fixture"
    assert all("cookie" not in request.headers for request in requests)


def test_http_date_retry_and_proxy_url(questions):
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    assert (
        8
        <= retry_delay(
            httpx.Response(503, headers={"retry-after": format_datetime(future)}), 0
        )
        <= 10
    )
    assert connection(
        Settings(
            litellm_base_url="http://local:1234/v1", litellm_keys={"demo": "fixture"}
        ),
        {"litellm_key_alias": "demo"},
    ) == ("http://local:1234/typesafe/v1/systemone", "fixture")


@pytest.mark.parametrize(
    "mutation", ["alias", "options", "sum", "confidence", "type", "missing"]
)
def test_invalid_vendor_answers_fail(questions, payload, mutation):
    if mutation == "alias":
        payload["model"] = "jev-latest"
    elif mutation == "options":
        payload["answers"]["family"]["choice"] = "other"
    elif mutation == "sum":
        payload["answers"]["family"]["probabilities"]["strings"] = 0.4
    elif mutation == "confidence":
        payload["answers"]["family"]["confidence"] = 2
    elif mutation == "type":
        payload["answers"]["family"] = {"type": "noul", "noul": 0.7}
    else:
        payload["answers"] = {}
    with pytest.raises(ValueError):
        Response.model_validate(payload).check(questions)


def test_score_noul_metrics_have_known_values():
    questions = QuestionSet(
        name="scores",
        model="jev-1.13.0",
        fields=["text"],
        data_class="public",
        questions={
            "quality": {
                "type": "score",
                "instructions": "Rate it",
                "criteria": ["low", "high"],
            },
            "yes": {"type": "noul", "instructions": "Is it present?"},
        },
    )
    response = Response.model_validate(
        {
            "model": questions.model,
            "usage": {"input_tokens": 1},
            "answers": {
                "quality": {
                    "type": "score",
                    "score": 0.75,
                    "legend": {"0": "low", "1": "high"},
                    "probabilities": {"0": 0.25, "1": 0.75},
                    "confidence": 0.6,
                },
                "yes": {"type": "noul", "noul": 0.8},
            },
        }
    ).check(questions)
    result = metrics(
        questions, [{"expected_quality": "1", "expected_yes": "1"}], [response]
    )
    assert result["quality"]["mae"] == 0.25 and result["quality"]["brier"] == 0.125
    assert (
        result["yes"]["brier"] == pytest.approx(0.04)
        and "coverage" not in result["yes"]
    )
    fractional = metrics(
        questions, [{"expected_quality": "0.75", "expected_yes": "1"}], [response]
    )
    assert fractional["quality"]["mae"] == 0 and fractional["quality"]["accuracy"] == 1
    assert fractional["quality"]["brier"] is None
    strict = questions.model_copy(update={"floors": {"quality.mae": 0.1}})
    assert missed_floors(strict, result) == ["quality.mae"]


async def configured(rt, databases, questions, *, fixture=False):
    discover()
    sync(rt.db)
    with psycopg.connect(
        databases["admin_control"], row_factory=psycopg.rows.dict_row
    ) as conn:
        version = install(
            conn, questions, "jev_instrument_family", params=step(questions)["params"]
        )
        conn.execute(
            "UPDATE control.llm_step SET enabled=true WHERE source_key='jev_instrument_family' AND step_version=%s",
            (version,),
        )
        conn.execute(
            "UPDATE control.streamline SET enabled=true WHERE source_key='jev_instrument_family'"
        )
    rt.settings = rt.settings.model_copy(
        update={
            "fixture": fixture,
            "typesafe_api_key": SecretStr("fixture"),
            "litellm_base_url": "",
        }
    )
    _, run = await bound(rt, "jev_instrument_family")
    run = rt.db.one("SELECT * FROM control.run WHERE id=%s", (run["id"],))
    return run


async def test_missing_provider_budget_sends_nothing(rt, databases, questions, payload):
    run = await configured(rt, databases, questions)
    seen = []
    client = Jev(
        rt,
        run,
        run["resolved_config"]["llm_step"],
        transport=httpx.MockTransport(
            lambda request: seen.append(request) or httpx.Response(200, json=payload)
        ),
    )
    with pytest.raises(ServiceError, match="no budget row"):
        await client.ask({"instrument": "violin"}, questions)
    assert not seen


async def test_provider_cap_and_tokens_persist(rt, databases, questions, payload):
    from mdp_functions.budget import vendor_scope_id

    run = await configured(rt, databases, questions)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.budget(scope,scope_id,period,cap_requests,cap_cents,soft_pct,hard_action) VALUES ('provider',%s,'daily',1,100,80,'pause')",
            (vendor_scope_id("typesafe"),),
        )
    client = Jev(
        rt,
        run,
        run["resolved_config"]["llm_step"],
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    await client.ask({"instrument": "violin"}, questions)
    rows = rt.db.all(
        "SELECT unit,quantity,cost_microcents FROM control.cost_ledger WHERE run_id=%s ORDER BY unit",
        (run["id"],),
    )
    assert [(r["unit"], int(r["quantity"])) for r in rows] == [
        ("input_token", 120),
        ("request", 1),
    ]
    assert rows[1]["cost_microcents"] == 240
    with pytest.raises(ServiceError, match="request cap"):
        await client.ask({"instrument": "violin"}, questions)


async def test_gold_fixture_keeps_versions_and_rights(rt, databases, questions):
    run = await configured(rt, databases, questions, fixture=True)
    manifest = discover()["jev_instrument_family"]
    ctx = Ctx(manifest, run)
    from mdp_functions.jev import for_run

    ctx._jev = for_run(rt, run, run["resolved_config"]["llm_step"], ctx)
    ctx.question_set = question_set(run["resolved_config"]["llm_step"])
    ctx.input_row = {
        "state_id": "instrument_01",
        "instrument": "violin",
        "input_ref": "one",
        "input_version": "v1",
        "source_keys": '["jev_instruments"]',
    }
    async for row in manifest.function(ctx, [ctx.input_row]):
        ctx.yield_row(row)
    row = ctx.outputs["raw.jev_instrument_family"][0]
    assert row["_source_keys"] == ["jev_instruments", "typesafe"]
    assert (
        row["question_set_version"] == questions.version
        and row["model_id"] == "jev-1.13.0"
    )
    assert not rt.db.all(
        "SELECT * FROM control.cost_ledger WHERE run_id=%s", (run["id"],)
    )


async def test_fixture_runs_through_gold_landing(rt, databases, questions):
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS intermediate")
        conn.execute("DROP TABLE IF EXISTS intermediate.int_jev_instruments")
        conn.execute(
            "CREATE TABLE intermediate.int_jev_instruments(state_id text,instrument text,input_ref text,input_version text,_source_keys text)"
        )
        conn.execute(
            "INSERT INTO intermediate.int_jev_instruments VALUES ('instrument_01','violin','one','v1','[\"jev_instruments\"]')"
        )
        conn.execute("GRANT USAGE ON SCHEMA intermediate TO service_read")
        conn.execute("GRANT SELECT ON intermediate.int_jev_instruments TO service_read")
    run = await configured(rt, databases, questions, fixture=True)
    await rt.execute(run["id"])
    receipt = rt.db.one(
        "SELECT status,error_class,error_message,rows_written FROM control.run WHERE id=%s",
        (run["id"],),
    )
    assert receipt["status"] == "succeeded" and receipt["rows_written"] == 1, receipt
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        answer = conn.execute(
            "SELECT answer,question_set_version,model_id,_source_keys FROM raw.jev_instrument_family WHERE _run_id=%s",
            (str(run["id"]),),
        ).fetchone()
    assert answer[:3] == ("strings", questions.version, questions.model)
    assert "jev_instruments" in answer[3] and "typesafe" in answer[3]


async def test_invalid_answer_still_records_paid_tokens(
    questions, payload, monkeypatch
):
    payload["model"] = "jev-latest"
    client = Jev(
        SimpleNamespace(settings=Settings(typesafe_api_key=SecretStr("fixture"))),
        {"tenant_id": None},
        step(questions),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    recorded = []
    monkeypatch.setattr(client, "reserve", lambda _: None)
    monkeypatch.setattr(client, "record", lambda _, tokens: recorded.append(tokens))
    with pytest.raises(ServiceError, match="invalid"):
        await client.ask({"instrument": "violin"}, questions)
    assert recorded == [120]


async def test_gold_refuses_a_changed_frozen_set_and_tenant_scope(questions, saved):
    manifest = discover()["jev_instrument_family"]
    run = {"id": uuid4(), "cycle_id": uuid4(), "tenant_id": None}
    ctx = Ctx(manifest, run)
    ctx.input_row = {"instrument": "violin"}
    client = Jev(run=run, step=step(questions), ctx=ctx, cassette=saved)
    with pytest.raises(ServiceError, match="frozen"):
        await client.ask(
            ctx.input_row, questions.model_copy(update={"thresholds": {"family": 0.95}})
        )
    run["tenant_id"] = uuid4()
    with pytest.raises(ServiceError, match="opt-ins"):
        await client.ask(ctx.input_row, questions)


async def test_overrun_records_actual_and_blocks_the_next_call(
    rt, databases, questions, payload
):
    from mdp_functions.budget import vendor_scope_id

    run = await configured(rt, databases, questions)
    config = run["resolved_config"]["llm_step"]
    config["params"]["input_token_microcents"] = 20_000
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.budget(scope,scope_id,period,cap_requests,cap_cents,soft_pct,hard_action) VALUES ('provider',%s,'daily',5,100,80,'pause')",
            (vendor_scope_id("typesafe"),),
        )
    calls = []
    client = Jev(
        rt,
        run,
        config,
        transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200, json=payload)
        ),
    )
    with pytest.raises(ServiceError, match="ceiling"):
        await client.ask({"instrument": "violin"}, questions)
    with pytest.raises(ServiceError, match="overrun"):
        await client.ask({"instrument": "violin"}, questions)
    assert len(calls) == 1
    assert (
        rt.db.one("SELECT cost_cents FROM control.run WHERE id=%s", (run["id"],))[
            "cost_cents"
        ]
        == 3
    )


def test_playlist_state_uses_shared_observed_owner_rules(questions):
    assert questions.state({"instrument": "violin", "owner_class": "editorial"}) == {
        "instrument": "violin"
    }
    for observed in ("user", "algotorial", None):
        with pytest.raises(ServiceError, match="platform-owned"):
            questions.state(
                {
                    "instrument": "violin",
                    "owner_class": "editorial",
                    "owner_class_observed": observed,
                }
            )
    with pytest.raises(ServiceError, match="platform-owned"):
        questions.state({"instrument": "violin", "playlist_id": "unknown"})


async def test_global_declaration_cannot_send_a_tenant_relation(questions, saved):
    from dataclasses import replace

    manifest = replace(
        discover()["jev_instrument_family"], reads=["tenant_example_marts.private_rows"]
    )
    run = {"id": uuid4(), "cycle_id": uuid4(), "tenant_id": None}
    ctx = Ctx(manifest, run)
    ctx.input_row = {"instrument": "violin"}
    with pytest.raises(ServiceError, match="opt-ins"):
        await Jev(run=run, step=step(questions), ctx=ctx, cassette=saved).ask(
            ctx.input_row, questions
        )
