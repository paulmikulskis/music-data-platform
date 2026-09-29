"""A local gold starter with a pinned prompt, a budget, and key-free model fixtures."""

import json
import os
import re
from pathlib import Path

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from mdp_functions.errors import ServiceError
from mdp_functions.llm import params_hash, step_version
from mdp_functions.settings import REPO, Settings


def local_url(url: str, settings: Settings) -> str:
    opts = conninfo_to_dict(url) if url else {}
    if (
        settings.dbt_cloud_verify
        or opts.get("host") not in {"127.0.0.1", "localhost", "::1"}
        or opts.get("hostaddr")
    ):
        raise ValueError(
            "Local setup is refused on this connection; source ops/local/env.sh for the disposable stack (docs/operating.md#add-a-local-llm-step)."
        )
    return url


def scaffold(source_key: str, settings: Settings, root: Path = REPO):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", source_key):
        raise ValueError(
            "Use a source key with lowercase letters, digits and underscores"
        )
    url = local_url(os.environ.get("MDP_CONTROL_DATABASE_URL", ""), settings)
    directory = root / "functions/src/mdp_functions/sources" / source_key
    prompt = root / "control/prompts" / source_key / "1.md"
    if directory.exists() or prompt.exists():
        raise ValueError("Source or prompt already exists; choose a new source key")
    body = "Classify the supplied chart observations. Return one label: emerging or established.\n"
    params = {"temperature": 0, "max_tokens": 16, "max_cost_cents": 1}
    digest = params_hash(params)
    version = step_version("local-stub", 1, digest)
    with psycopg.connect(url) as conn:
        prompt_id = conn.execute(
            "INSERT INTO control.prompt(name,version,body) VALUES (%s,1,%s) RETURNING id",
            (source_key, body),
        ).fetchone()[0]
        step_id = conn.execute(
            """INSERT INTO control.llm_step(source_key,model,prompt_id,prompt_version,params,params_hash,step_version)
            VALUES (%s,'local-stub',%s,1,%s,%s,%s) RETURNING id""",
            (source_key, prompt_id, Jsonb(params), digest, version),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO control.budget(scope,scope_id,period,cap_cents,soft_pct,hard_action)
            VALUES ('llm_step',%s,'daily',100,80,'pause')""",
            (step_id,),
        )
        (directory / "fixtures").mkdir(parents=True)
        prompt.parent.mkdir(parents=True)
        prompt.write_text(body)
        (
            directory / "function.py"
        ).write_text(f'''"""Enrich chart observations with a budgeted model step."""
from mdp_functions.layers import gold


@gold(
    source_key="{source_key}",
    reads=["marts.mart_chart_history"],
    writes=["raw.enrich_{source_key}"],
    cadence="daily",
    llm_step="{source_key}",
    input_key=["chart_name", "chart_week", "chart_position"],
    input_version=["track_title", "artist_name"],
)
async def enrich(ctx, rows):
    for row in rows:
        yield {{"label": await ctx.llm.classify(row, prompt=ctx.prompt), **ctx.input_identity(row)}}
''')
        (directory / "fixtures/llm.json").write_text(
            json.dumps(
                {
                    "responses": ["emerging", "established"],
                    "total_tokens": 8,
                },
                indent=2,
            )
            + "\n"
        )
    return version


class LocalModel(httpx.AsyncBaseTransport):
    """Replay source-owned responses through the same refusal and cost accounting path."""

    def __init__(self, path: Path):
        self.fixture = json.loads(path.read_text())
        self.index = 0
        if not self.fixture.get("responses"):
            raise ServiceError("fixture_miss", "Add responses to fixtures/llm.json")

    async def handle_async_request(self, request):
        answers = self.fixture["responses"]
        answer = answers[self.index % len(answers)]
        self.index += 1
        return httpx.Response(
            200,
            headers={"x-litellm-response-cost": "0"},
            json={
                "id": request.headers["x-litellm-call-id"],
                "choices": [{"message": {"content": answer}}],
                "usage": {"total_tokens": self.fixture.get("total_tokens", 0)},
            },
        )
