"""Response-only cassettes. State is identified by hash and never copied into them."""

import json
from pathlib import Path

from mdp_functions.errors import ServiceError
from mdp_functions.jev_types import QuestionSet, Response, digest
from mdp_functions.settings import PACKAGE

EXAMPLES = PACKAGE / "jev_examples"


def load_set(value: str) -> QuestionSet:
    path = Path(value)
    if not path.is_file():
        path = EXAMPLES / value / "questions.json"
    return QuestionSet.model_validate_json(path.read_text())


class Cassette:
    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def key(questions: QuestionSet, state: dict) -> str:
        return questions.version + ":" + digest(state)

    def read(self, questions: QuestionSet, state: dict) -> Response:
        data = (
            json.loads(self.path.read_text()) if self.path.exists() else {"entries": {}}
        )
        entry = data["entries"].get(self.key(questions, state))
        if entry is None:
            raise ServiceError(
                "jev_fixture_miss",
                "No Jev cassette entry for this question version and state. "
                "After owner setup, run: uv run --project functions mdp jev record "
                "<question_set> --labels <csv> --run-id <run_uuid> --cassette <path>",
            )
        return Response.model_validate(entry).check(questions)

    def write(self, questions: QuestionSet, state: dict, response: Response) -> None:
        data = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {"kind": "recorded", "entries": {}}
        )
        if data.get("kind") != "recorded":
            raise ValueError(
                "Record into a new cassette; keep synthetic and vendor responses separate"
            )
        data["entries"][self.key(questions, state)] = response.model_dump(mode="json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n")
        temporary.replace(self.path)
