"""A disabled gold example over public instrument facts; synthetic responses are offline only."""

from mdp_functions.layers import gold
from pydantic import BaseModel


class Decision(BaseModel):
    state_id: str
    question: str
    answer: str
    probabilities: dict[str, float]
    confidence: float
    question_set_version: str
    model_id: str


@gold(
    source_key="jev_instrument_family",
    reads=["intermediate.int_jev_instruments"],
    writes=["raw.jev_instrument_family"],
    cadence="daily",
    provider="typesafe",
    llm_step="jev_instrument_family",
    input_key=["state_id"],
    input_version=["instrument"],
    output_key=["question"],
    schema=Decision,
    hosts=["api.typesafe.ai"],
    knobs={"enabled": False, "timeout_s": 300},
)
async def instrument_family(ctx, rows):
    for row in rows:
        response = await ctx.jev.ask(row, ctx.question_set)
        for key, answer in response.answers.items():
            yield {
                "state_id": row["state_id"],
                "question": key,
                "answer": answer.choice,
                "probabilities": answer.probabilities,
                "confidence": answer.confidence,
                "question_set_version": ctx.question_set.version,
                "model_id": response.model,
            }
