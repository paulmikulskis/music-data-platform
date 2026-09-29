"""OpenAI-compatible chat completions, pinned configuration and reserved usage."""

import asyncio
import hashlib
import json
from decimal import ROUND_CEILING, Decimal
from uuid import uuid4

import httpx

from mdp_functions import budget
from mdp_functions.control_db import event
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import RefusingClient


def step_version(model, prompt_version, params_hash):
    return hashlib.sha256(
        json.dumps([model, prompt_version, params_hash], separators=(",", ":")).encode()
    ).hexdigest()


def params_hash(params):
    return hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def step_for(db, source, version=None):
    row = db.one(
        """SELECT s.*, p.body FROM control.llm_step s JOIN control.prompt p ON p.id=s.prompt_id
        AND p.version=s.prompt_version WHERE s.source_key=%s AND s.enabled
        AND (%s::text IS NULL OR s.step_version=%s) ORDER BY s.created_at DESC,s.step_version DESC LIMIT 1""",
        (source, version, version),
    )
    if not row:
        raise ServiceError(
            "llm_step_missing", "An enabled immutable prompt configuration is required"
        )
    if row["params_hash"] != params_hash(row["params"]) or row[
        "step_version"
    ] != step_version(row["model"], row["prompt_version"], row["params_hash"]):
        raise ServiceError(
            "llm_config_invalid",
            "Configuration hash does not match its model, prompt and params",
        )
    return row


class LLM:
    def __init__(self, rt, run, step, ctx):
        self.rt, self.run, self.step, self.ctx = rt, run, step, ctx
        self.overage = None
        self.overage_receipt = None

    async def classify(self, row, prompt):
        self.ctx.request_id = None
        if self.overage:
            raise ServiceError("cost_cap_hit", self.overage)
        settings, step = self.rt.settings, self.step
        fixture = settings.fixture and step["model"] == "local-stub"
        transport = None
        if fixture:
            from mdp_functions.llm_scaffold import LocalModel
            from mdp_functions.settings import PACKAGE
            if not hasattr(self, "fixture_transport"):
                self.fixture_transport = LocalModel(PACKAGE / "sources" / self.ctx.manifest.source_key / "fixtures/llm.json")
            transport = self.fixture_transport
        if not fixture and not settings.litellm_base_url:
            raise ServiceError(
                "litellm_unavailable", "the model proxy input is not configured", 503
            )
        alias = step["litellm_key_alias"]
        token = settings.litellm_keys.get(alias or "")
        if not token and not fixture:
            raise ServiceError(
                "litellm_unavailable", "Proxy key alias is not configured", 503
            )
        request_id = "mdp-" + uuid4().hex
        parameters = dict(step["params"])
        ceiling = int(parameters.pop("max_cost_cents", 0))
        if ceiling <= 0:
            raise ServiceError(
                "llm_config_invalid", "A positive per-call max_cost_cents is required"
            )
        model = step["model"]
        try:
            await asyncio.to_thread(
                budget.draw, self.rt.db, self.run, "litellm", request_id, ceiling
            )
        except ServiceError as exc:
            policy = self.rt.db.one(
                "SELECT b.hard_action FROM control.budget_reservation r JOIN control.budget b ON b.id=r.budget_id WHERE r.run_id=%s AND b.hard_action='degrade' LIMIT 1",
                (self.run["id"],),
            )
            fallback = parameters.pop("fallback_max_cost_cents", None)
            if (
                exc.error_class != "cost_cap_hit"
                or not policy
                or not step["fallback_model"]
                or not fallback
            ):
                raise
            model, ceiling = step["fallback_model"], int(fallback)
            await asyncio.to_thread(
                budget.draw, self.rt.db, self.run, "litellm", request_id, ceiling
            )
        parameters.pop("fallback_max_cost_cents", None)
        try:
            async with RefusingClient(timeout=30, layer="gold", trust_env=False,
                                **({"transport": transport} if fixture else {})) as client:
                response = await client.post(
                    ("https://local-model.invalid" if fixture else settings.litellm_base_url.rstrip("/")) + "/chat/completions",
                    headers={
                        **({"Authorization": "Bearer " + token} if token and not fixture else {}),
                        "x-litellm-call-id": request_id,
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": json.dumps(row, default=str)},
                        ],
                        "metadata": {
                            "mdp_run_id": str(self.run["id"]),
                            "mdp_request_id": request_id,
                        },
                        **parameters,
                    },
                )
                response.raise_for_status()
                body = response.json()
                label = body["choices"][0]["message"]["content"].strip()
                cost = int(
                    (
                        Decimal(
                            response.headers.get(
                                "x-litellm-response-cost", str(Decimal(ceiling) / 100)
                            )
                        )
                        * 100
                    ).to_integral_value(rounding=ROUND_CEILING)
                )
                usage = int(body["usage"]["total_tokens"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ServiceError(
                "litellm_unavailable",
                "Proxy completion unavailable; reservation retained",
                503,
            ) from exc
        provider_id = body.get("id", request_id)
        # Detect the overrun before recording, but retain the actual spend even on failure.
        if cost > ceiling:
            self.overage = f"reserved={ceiling} spent={cost} overage={cost - ceiling}"
            self.overage_receipt = {
                "reserved": ceiling,
                "spent": cost,
                "overage": cost - ceiling,
            }
        await asyncio.to_thread(self.record, request_id, provider_id, cost, usage)
        self.ctx.request_id = provider_id
        if cost > ceiling:
            raise ServiceError("cost_cap_hit", self.overage)
        return label

    def record(self, request_id, provider_id, cost, usage):
        with self.rt.db.transaction() as conn:
            if self.overage:
                event(
                    conn,
                    self.run["id"],
                    "cost_cap_hit",
                    self.overage,
                    self.overage_receipt,
                )
            old = conn.execute(
                "SELECT cost_cents FROM control.cost_ledger WHERE vendor='litellm' AND provider_request_id=%s FOR UPDATE",
                (request_id,),
            ).fetchone()["cost_cents"]
            conn.execute(
                "UPDATE control.cost_ledger SET provider_request_id=%s,cost_cents=%s,quantity=%s,unit='token',llm_step_id=%s WHERE vendor='litellm' AND provider_request_id=%s AND origin='estimate'",
                (provider_id, cost, usage, self.step["id"], request_id),
            )
            conn.execute(
                "UPDATE control.run SET cost_cents=cost_cents+%s WHERE id=%s",
                (cost - old, self.run["id"]),
            )
            conn.execute(
                "UPDATE control.budget_reservation SET consumed_cents=consumed_cents+%s WHERE run_id=%s",
                (cost - old, self.run["id"]),
            )
