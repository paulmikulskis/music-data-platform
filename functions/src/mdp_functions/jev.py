"""Budgeted typed gold decisions through the shared refusing transport."""

import asyncio
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from uuid import uuid4

import httpx

from mdp_functions import budget
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import RefusingClient
from mdp_functions.fetch.logging import protect_transport_logs
from mdp_functions.http import no_cookies
from mdp_functions.jev_cassette import EXAMPLES, Cassette
from mdp_functions.jev_types import (  # noqa: F401 - the author-facing types live here too
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    QuestionSet,
    Response,
    Score,
    ScoreAnswer,
)


def question_set(step) -> QuestionSet:
    questions = QuestionSet.model_validate_json(step["body"])
    if (
        step["params"].get("question_set_version") != questions.version
        or step["model"] != questions.model
    ):
        raise ServiceError(
            "llm_config_invalid",
            "Jev question-set hash or model differs from the immutable step",
        )
    return questions


def connection(settings, step) -> tuple[str, str]:
    if settings.litellm_base_url:
        token = settings.litellm_keys.get(step.get("litellm_key_alias") or "")
        endpoint = (
            settings.litellm_base_url.rstrip("/").removesuffix("/v1")
            + "/typesafe/v1/systemone"
        )
    else:
        endpoint, token = (
            "https://api.typesafe.ai/v1/systemone",
            settings.typesafe_api_key.get_secret_value(),
        )
    if not token:
        raise ServiceError(
            "jev_unavailable",
            "Configure the Jev proxy key alias or TYPESAFE_API_KEY after owner setup",
        )
    return endpoint, token


def retry_delay(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("retry-after", "")
    try:
        seconds = float(value)
        if math.isfinite(seconds):
            return max(0, seconds)
    except ValueError:
        try:
            return max(
                0,
                (
                    parsedate_to_datetime(value) - datetime.now(timezone.utc)
                ).total_seconds(),
            )
        except (ValueError, TypeError, OverflowError):
            pass
    return min(2**attempt, 8)


class Jev:
    def __init__(
        self,
        rt=None,
        run=None,
        step=None,
        ctx=None,
        *,
        cassette: Cassette | None = None,
        transport=None,
    ):
        self.rt, self.run, self.step, self.ctx = rt, run, step, ctx
        self.cassette, self.transport = cassette, transport
        self.overage = False

    async def ask(self, state: dict, questions: QuestionSet) -> Response:
        if self.overage:
            raise ServiceError(
                "cost_cap_hit", "Jev caller stopped after a spend overrun"
            )
        if self.ctx is not None:
            if (
                self.ctx.manifest.layer != "gold"
                or self.ctx.manifest.provider != "typesafe"
            ):
                raise ServiceError(
                    "jev_layer_refused",
                    "Use @gold(provider='typesafe', llm_step=...) and ctx.jev.ask; bronze only fetches",
                )
            if (
                self.ctx.manifest.tenant_bound
                or self.run.get("tenant_id")
                or any(
                    r.split(".")[0].startswith("tenant_")
                    for r in self.ctx.manifest.reads
                )
            ):
                raise ServiceError(
                    "jev_egress_refused",
                    "Jev tenant opt-ins are absent; use global public inputs",
                )
            pinned = question_set(self.step)
            if pinned.version != questions.version:
                raise ServiceError(
                    "llm_config_invalid", "Use the question set frozen in this run"
                )
            # The function cannot replace a declared input with unrelated state.
            if questions.state(self.ctx.input_row) != questions.state(state):
                raise ServiceError(
                    "jev_state_invalid",
                    "Jev state must match this row's declared public fields",
                )
        state = questions.state(state)
        if self.cassette is not None:
            result = self.cassette.read(questions, state)
            if self.ctx is not None:
                self.ctx.input_metadata.update(
                    question_set_version=questions.version, model_id=result.model
                )
            return result
        if self.rt is None or self.run is None or self.step is None:
            raise ServiceError(
                "cost_cap_hit",
                "Live Jev needs a run, immutable step and provider budget; use fixture mode",
            )
        if (
            self.run.get("tenant_id")
            or question_set(self.step).version != questions.version
        ):
            raise ServiceError(
                "jev_egress_refused",
                "Live state must match the global run's frozen question set",
            )
        endpoint, token = connection(self.rt.settings, self.step)
        body = {
            "state": state,
            "model": questions.model,
            "questions": {
                k: q.model_dump(mode="json", exclude_none=True)
                for k, q in questions.questions.items()
            },
        }
        # A conservative byte bound also bounds text tokens; the server enforces its tokenizer's limit.
        from mdp_functions.jev_types import canonical

        if (
            len(canonical(state).encode())
            + max(len(canonical(q).encode()) for q in body["questions"].values())
            > 32000
        ):
            raise ServiceError(
                "jev_state_invalid",
                "State plus the longest question exceeds the local size limit",
            )
        protect_transport_logs()
        async with RefusingClient(
            timeout=30,
            transport=self.transport,
            follow_redirects=False,
            cookies=no_cookies(),
        ) as client:
            for attempt in range(3):
                request_id = "mdp-jev-" + uuid4().hex
                await asyncio.to_thread(self.reserve, request_id)
                try:
                    response = await client.post(
                        endpoint,
                        headers={"Authorization": "Bearer " + token},
                        json=body,
                    )
                    if (
                        response.status_code == 429 or response.status_code >= 500
                    ) and attempt < 2:
                        await asyncio.sleep(retry_delay(response, attempt))
                        continue
                    response.raise_for_status()
                    payload = response.json()
                    # Account for successful vendor work even if the answer fails validation.
                    from mdp_functions.jev_types import Usage

                    usage = Usage.model_validate(payload["usage"])
                    await asyncio.to_thread(self.record, request_id, usage.input_tokens)
                    result = Response.model_validate(payload).check(questions)
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    raise ServiceError(
                        "jev_unavailable",
                        "Jev response is unavailable or invalid; reservation retained",
                        503,
                    ) from exc
                if self.ctx is not None:
                    self.ctx.request_id = request_id
                    self.ctx.input_metadata.update(
                        question_set_version=questions.version, model_id=result.model
                    )
                return result
        raise ServiceError("jev_unavailable", "Jev retry limit reached", 503)

    def reserve(self, request_id):
        ceiling = self.step["params"].get("max_cost_cents", 0)
        rate = self.step["params"].get("input_token_microcents", 0)
        if (
            not isinstance(ceiling, int)
            or ceiling <= 0
            or not isinstance(rate, (int, float))
            or not math.isfinite(rate)
            or rate <= 0
        ):
            raise ServiceError(
                "llm_config_invalid",
                "Live Jev needs max_cost_cents and input_token_microcents from the reviewed account",
            )
        # Serialize cap check plus draw across workers. The reservation persists before network I/O.
        with self.rt.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('jev:typesafe'))")
            budget.provider_cap(self.rt.db, self.run, "typesafe")
            for row in conn.execute(
                "SELECT * FROM control.budget WHERE scope='provider' AND scope_id=%s",
                (budget.vendor_scope_id("typesafe"),),
            ).fetchall():
                used = conn.execute(
                    "SELECT coalesce(sum(cost_cents),0) AS n FROM control.cost_ledger "
                    "WHERE vendor='typesafe' AND is_current AND occurred_at>=date_trunc(%s,now())",
                    (budget.PERIODS[row["period"]],),
                ).fetchone()["n"]
                if used + ceiling > row["cap_cents"]:
                    budget.stop(
                        self.rt.db,
                        self.run,
                        "typesafe",
                        "Jev provider spending cap reached",
                    )
            budget.draw(self.rt.db, self.run, "typesafe", request_id, ceiling)

    def record(self, request_id, tokens):
        microcents = math.ceil(tokens * self.step["params"]["input_token_microcents"])
        cost = math.ceil(microcents / 1_000_000)
        with self.rt.db.transaction() as conn:
            old = conn.execute(
                "SELECT cost_cents FROM control.cost_ledger WHERE vendor='typesafe' "
                "AND provider_request_id=%s AND unit='request' FOR UPDATE",
                (request_id,),
            ).fetchone()["cost_cents"]
            conn.execute(
                "UPDATE control.cost_ledger SET cost_cents=%s,cost_microcents=%s,llm_step_id=%s "
                "WHERE vendor='typesafe' AND provider_request_id=%s",
                (cost, microcents, self.step["id"], request_id),
            )
            # Keep the request row for the provider cap; the token row has no extra cost.
            conn.execute(
                "INSERT INTO control.cost_ledger "
                "(run_id,streamline_id,tenant_id,llm_step_id,vendor,provider_request_id,unit,quantity,cost_cents,origin) "
                "VALUES (%s,%s,%s,%s,'typesafe',%s,'input_token',%s,0,'estimate')",
                (
                    self.run["id"],
                    self.run["streamline_id"],
                    self.run["tenant_id"],
                    self.step["id"],
                    request_id + ":usage",
                    tokens,
                ),
            )
            conn.execute(
                "UPDATE control.run SET cost_cents=cost_cents+%s WHERE id=%s",
                (cost - old, self.run["id"]),
            )
            conn.execute(
                "UPDATE control.budget_reservation SET consumed_cents=consumed_cents+%s WHERE run_id=%s",
                (cost - old, self.run["id"]),
            )
        if cost > old:
            self.overage = True
            raise ServiceError(
                "cost_cap_hit",
                "Jev spend exceeded the per-call ceiling; actual usage is retained",
            )


def for_run(rt, run, step, ctx):
    questions = question_set(step)
    cassette = None
    if rt.settings.fixture:
        from pathlib import Path

        cassette = Cassette(
            Path(rt.settings.jev_cassette)
            if rt.settings.jev_cassette
            else EXAMPLES / questions.name / "cassette.json"
        )
    return Jev(rt, run, step, ctx, cassette=cassette)
