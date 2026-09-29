"""Jev's wire types and content-addressed question sets."""

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from mdp_functions.errors import ServiceError
from mdp_functions.owners import PUBLIC_OWNERS

Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Content = str | dict[str, JsonValue] | list[JsonValue]


def canonical(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Typed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Choice(Typed):
    type: Literal["choice"] = "choice"
    instructions: Content
    criteria: Annotated[dict[str, Content | None], Field(min_length=2, max_length=255)]


class Score(Typed):
    type: Literal["score"] = "score"
    instructions: Content
    criteria: Annotated[list[Content], Field(min_length=2, max_length=10)]


class Noul(Typed):
    type: Literal["noul"] = "noul"
    instructions: Content
    criteria: dict[Literal["true", "false"], Content] | None = None


Question = Annotated[Choice | Score | Noul, Field(discriminator="type")]


class ChoiceAnswer(Typed):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, Probability]
    confidence: Probability


class ScoreAnswer(Typed):
    type: Literal["score"]
    score: Annotated[float, Field(allow_inf_nan=False)]
    legend: dict[str, Content]
    probabilities: dict[str, Probability]
    confidence: Probability


class NoulAnswer(Typed):
    type: Literal["noul"]
    noul: Probability


Answer = Annotated[ChoiceAnswer | ScoreAnswer | NoulAnswer, Field(discriminator="type")]


class Usage(Typed):
    input_tokens: Annotated[int, Field(ge=0, strict=True)]
    output_tokens: Annotated[int, Field(ge=0, strict=True)] = 0


class Response(Typed):
    model: str
    answers: dict[str, Answer]
    usage: Usage

    def check(self, questions: "QuestionSet") -> "Response":
        if self.model != questions.model or set(self.answers) != set(
            questions.questions
        ):
            raise ValueError(
                "Jev response model or question ids differ from the pinned request"
            )
        for key, answer in self.answers.items():
            question = questions.questions[key]
            if question.type != answer.type:
                raise ValueError("Jev answer type differs from the question")
            if isinstance(answer, NoulAnswer):
                continue
            expected = (
                set(question.criteria)
                if isinstance(question, Choice)
                else {str(i) for i in range(len(question.criteria))}
            )
            if (
                set(answer.probabilities) != expected
                or abs(sum(answer.probabilities.values()) - 1) > 0.001
            ):
                raise ValueError(
                    "Jev probabilities must cover every option and sum to one"
                )
            if isinstance(answer, ChoiceAnswer):
                if answer.choice not in expected or answer.probabilities[
                    answer.choice
                ] < max(answer.probabilities.values()):
                    raise ValueError("Jev choice must have the highest probability")
            else:
                weighted = sum(int(k) * p for k, p in answer.probabilities.items())
                if (
                    set(answer.legend) != expected
                    or abs(answer.score - weighted) > 0.01
                ):
                    raise ValueError(
                        "Jev score must match the weighted level probabilities"
                    )
        return self


class QuestionSet(Typed):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    model: Annotated[str, Field(pattern=r"^jev-\d+\.\d+\.\d+$")]
    fields: Annotated[list[str], Field(min_length=1)]
    data_class: Literal["public", "tenant", "personal"]
    questions: Annotated[dict[str, Question], Field(min_length=1)]
    thresholds: dict[str, Probability] = Field(default_factory=dict)
    # Metric names are validated by evaluation; missing floors never fail a run.
    floors: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def thresholds_match(self):
        if set(self.thresholds) - set(self.questions):
            raise ValueError("Confidence thresholds must name a question")
        if any(isinstance(self.questions[k], Noul) for k in self.thresholds):
            raise ValueError("Noul has a probability, not a separate confidence")
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("State fields must be unique")
        return self

    @property
    def version(self) -> str:
        return digest(self.model_dump(mode="json", exclude_none=True))

    def state(self, row: dict) -> dict:
        """Select declared public fields. Metadata never becomes part of the vendor state."""
        if self.data_class != "public" or row.get("tenant_id") or row.get("_tenant_id"):
            raise ServiceError(
                "jev_egress_refused",
                "Jev has no tenant or personal-data opt-in; use public fields",
            )
        owner = row.get("owner_class_observed", row.get("owner_class"))
        if {
            "playlist_id",
            "owner_class",
            "owner_class_observed",
        } & row.keys() and owner not in PUBLIC_OWNERS:
            raise ServiceError(
                "jev_egress_refused", "Playlist state needs a platform-owned input"
            )
        # Structured state is deliberately flat: nested objects can hide identifiers.
        forbidden = re.compile(
            r"(?:^id$|name|tenant|email|phone|handle|user|owner|account|address|person|comment|url|uri|_id)",
            re.IGNORECASE,
        )
        if any(forbidden.search(k) for k in self.fields):
            raise ServiceError(
                "jev_egress_refused",
                "State fields must not contain personal identifiers or tenant material",
            )
        if any(
            k not in row or not isinstance(row[k], (str, int, float, bool))
            for k in self.fields
        ):
            raise ServiceError(
                "jev_state_invalid", "State needs each declared field as a scalar value"
            )
        state = {k: row[k] for k in self.fields}
        if re.search(
            r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|https?://|(?<!\w)@\w+", canonical(state)
        ):
            raise ServiceError(
                "jev_egress_refused", "State contains a contact identifier or URL"
            )
        return state
