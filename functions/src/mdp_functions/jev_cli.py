"""The same question sets and cassettes drive try, eval, diff and record."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from mdp_functions.jev import Jev
from mdp_functions.jev_cassette import EXAMPLES, Cassette, load_set
from mdp_functions.jev_eval import (
    labeled_rows,
    metrics,
    missed_floors,
    report_text,
    value,
)
from mdp_functions.settings import REPO

app = typer.Typer(no_args_is_help=True)


def cassette_for(questions, path):
    return Cassette(path or EXAMPLES / questions.name / "cassette.json")


async def responses_for(questions, rows, cassette):
    client = Jev(cassette=cassette)
    return [await client.ask(row, questions) for row in rows]


@app.command("try")
def try_state(
    question_set: str,
    state: Annotated[str, typer.Option()],
    labels: Path | None = None,
    cassette: Path | None = None,
):
    """Try one JSON state file or a state_id from --labels. Replays offline."""
    questions = load_set(question_set)
    if Path(state).is_file():
        row = json.loads(Path(state).read_text())
    else:
        rows = labeled_rows(labels or REPO / "dbt/seeds/jev_instruments.csv", questions)
        row = next((r for r in rows if r["state_id"] == state), None)
        if row is None:
            raise typer.BadParameter(
                "State id is absent from --labels; pass a JSON file or a listed id"
            )
    response = asyncio.run(
        Jev(cassette=cassette_for(questions, cassette)).ask(row, questions)
    )
    table = Table("Question", "Answer", "Probabilities", "Confidence", "Route")
    for key, answer in response.answers.items():
        confidence = getattr(answer, "confidence", None)
        route = (
            "review"
            if confidence is not None and confidence < questions.thresholds.get(key, 0)
            else "act"
        )
        table.add_row(
            key,
            str(value(answer)),
            ", ".join(f"{option}={probability:.3f}" for option, probability in getattr(answer, "probabilities", {"true": value(answer)}).items()),
            "—" if confidence is None else f"{confidence:.3f}",
            route if confidence is not None else "use probability",
        )
    Console().print(table)
    typer.echo(f"Question version: {questions.version}\nModel: {response.model}")


@app.command("eval")
def evaluate(
    question_set: str,
    labels: Annotated[Path, typer.Option(exists=True)],
    cassette: Path | None = None,
    output: Path | None = None,
):
    """Evaluate labeled rows offline. Only missed author floors fail a valid evaluation."""
    questions = load_set(question_set)
    rows = labeled_rows(labels, questions)
    saved = cassette_for(questions, cassette)
    responses = asyncio.run(responses_for(questions, rows, saved))
    results = metrics(questions, rows, responses)
    report = {
        "question_set": questions.name,
        "version": questions.version,
        "model": questions.model,
        "mode": json.loads(saved.path.read_text())["kind"],
        "metrics": results,
        "missed_floors": missed_floors(questions, results),
    }
    folder = output or REPO / "ops/evidence/jev" / questions.name
    folder.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S%fZ")
    (folder / f"{stem}.json").write_text(json.dumps(report, indent=2) + "\n")
    rendered = report_text(report)
    (folder / f"{stem}.md").write_text(rendered)
    typer.echo(rendered)
    typer.echo(f"Report: {folder / (stem + '.md')}")
    if report["missed_floors"]:
        raise typer.Exit(1)


@app.command("diff")
def diff(
    set_a: str,
    set_b: str,
    labels: Annotated[Path, typer.Option(exists=True)],
    cassette_a: Path | None = None,
    cassette_b: Path | None = None,
):
    """Show changed answers and metric deltas on the same labeled rows."""
    left, right = load_set(set_a), load_set(set_b)
    if set(left.questions) != set(right.questions):
        raise typer.BadParameter(
            f"Diff needs the same question ids in both sets; add to {set_a}: "
            f"{sorted(set(right.questions) - set(left.questions))}; add to {set_b}: "
            f"{sorted(set(left.questions) - set(right.questions))}"
        )
    rows = labeled_rows(labels, left)
    labeled_rows(labels, right)
    a = asyncio.run(responses_for(left, rows, cassette_for(left, cassette_a)))
    b = asyncio.run(responses_for(right, rows, cassette_for(right, cassette_b)))
    changes = [
        {
            "state_id": row["state_id"],
            "question": k,
            "before": value(x.answers[k]),
            "after": value(y.answers[k]),
        }
        for row, x, y in zip(rows, a, b, strict=True)
        for k in left.questions
        if value(x.answers[k]) != value(y.answers[k])
    ]
    before, after = metrics(left, rows, a), metrics(right, rows, b)

    def deltas(x, y):
        if isinstance(x, dict) and isinstance(y, dict):
            return {k: deltas(x[k], y[k]) for k in x.keys() & y.keys()}
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return y - x
        return None

    typer.echo(
        json.dumps(
            {
                "changed_rows": changes,
                "metric_deltas": deltas(before, after),
                "before": before,
                "after": after,
            },
            indent=2,
        )
    )


@app.command("config")
def config(question_set: str):
    """Print the immutable prompt body and step params for the existing control prompt workflow."""
    questions = load_set(question_set)
    typer.echo(
        json.dumps(
            {
                "body": questions.model_dump_json(exclude_none=True),
                "model": questions.model,
                "params": {"question_set_version": questions.version},
                "enabled": False,
            },
            indent=2,
        )
    )


@app.command("record")
def record(
    question_set: str,
    labels: Annotated[Path, typer.Option(exists=True)],
    run_id: Annotated[UUID, typer.Option()],
    cassette: Annotated[Path, typer.Option()],
):
    """Owner setup only: record responses against an admitted global run and its budgets."""
    from mdp_functions.control_db import ControlDB
    from mdp_functions.settings import Settings

    settings = Settings()
    questions = load_set(question_set)
    rows = labeled_rows(labels, questions)
    db = ControlDB(settings.control_url)
    try:
        run = db.one(
            "SELECT r.* FROM control.run r JOIN control.streamline s ON s.id=r.streamline_id "
            "WHERE r.id=%s AND r.status='running' AND r.tenant_id IS NULL AND s.enabled AND s.layer='gold'",
            (run_id,),
        )
        if not run or not run.get("resolved_config", {}).get("llm_step"):
            raise typer.BadParameter(
                "Recording needs an admitted, running global gold run with a frozen Jev step. "
                "Start one: uv run --project functions mdp run <source_key> --target pg_local, "
                "then pass its run id. See docs/jev.md."
            )
        client = Jev(
            SimpleNamespace(settings=settings, db=db),
            run,
            run["resolved_config"]["llm_step"],
        )
        saved = Cassette(cassette)
        if (
            cassette.exists()
            and json.loads(cassette.read_text()).get("kind") != "recorded"
        ):
            raise typer.BadParameter(
                "Use a new cassette path for real vendor responses"
            )

        async def capture():
            for row in rows:
                response = await client.ask(row, questions)
                saved.write(questions, questions.state(row), response)

        asyncio.run(capture())
    finally:
        db.close()
    typer.echo(f"Recorded {len(rows)} responses to {cassette}")


@app.command("install")
def install_config(
    question_set: str,
    source: Annotated[str, typer.Option()],
    params: Path | None = None,
    key_alias: str | None = None,
):
    """Store a disabled immutable step using the configured control writer."""
    from mdp_functions.control_db import ControlDB
    from mdp_functions.jev_config import install
    from mdp_functions.registry import discover
    from mdp_functions.settings import Settings

    settings = Settings()
    manifest = discover().get(source)
    if not manifest or manifest.provider != "typesafe" or not manifest.llm_step:
        raise typer.BadParameter(
            "Source must declare @gold(provider='typesafe', llm_step=...)"
        )
    if not settings.control_rt_url:
        raise typer.BadParameter(
            "Set MDP_CONTROL_RT_URL to the intended control database"
        )
    questions = load_set(question_set)
    db = ControlDB(settings.control_rt_url)
    try:
        with db.transaction() as conn:
            version = install(
                conn,
                questions,
                manifest.llm_step,
                params=json.loads(params.read_text()) if params else {},
                key_alias=key_alias,
            )
    finally:
        db.close()
    typer.echo(f"Stored disabled step {version}; question version {questions.version}")
