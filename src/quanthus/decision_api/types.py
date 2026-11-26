"""Canonical decision-model types for the Quanthus Decision API adapter.

The canonical dialect is the OpenAI Decisions API GA schema.  Question and
answer objects are immutable, hashable value types so they can travel through
digest computation, replay lookup and audit serialization without mutation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal


class DecisionError(Exception):
    """Base error for all decision-adapter failures."""


class DecisionTransportError(DecisionError):
    """Transport-level failure (DNS, TCP, TLS, reset, malformed JSON)."""


class DecisionTimeoutError(DecisionTransportError):
    """The strict wall-clock request timeout expired."""


class DecisionHttpError(DecisionError):
    """The backend answered with a non-2xx HTTP status."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"DECISION_HTTP_{status}: {body[:400]}")
        self.status = status
        self.body = body


class DecisionSchemaError(DecisionError):
    """Request or response violates the canonical or backend dialect schema."""


ChoiceValue = str | bool


@dataclass(frozen=True)
class ChoiceOption:
    """One candidate value of a choice question."""

    value: ChoiceValue
    description: str = ""

    def validate(self) -> None:
        if not isinstance(self.value, (str, bool)):
            raise DecisionSchemaError("DECISION_CHOICE_VALUE_TYPE")


@dataclass(frozen=True)
class ScoreLevel:
    """One ordered level of a score question (index zero is the minimum)."""

    label: str
    description: str = ""

    def validate(self) -> None:
        if not self.label:
            raise DecisionSchemaError("DECISION_SCORE_LABEL_EMPTY")


@dataclass(frozen=True)
class PredicateQuestion:
    """Estimate how likely it is that a statement about the input is true."""

    instructions: str
    name: str | None = None
    type: Literal["predicate"] = "predicate"

    def validate(self) -> None:
        if not self.instructions.strip():
            raise DecisionSchemaError("DECISION_INSTRUCTIONS_EMPTY")


@dataclass(frozen=True)
class ChoiceQuestion:
    """Choose exactly one option from two to 255 unique candidates."""

    instructions: str
    choices: tuple[ChoiceOption, ...]
    name: str | None = None
    type: Literal["choice"] = "choice"

    def validate(self) -> None:
        if not self.instructions.strip():
            raise DecisionSchemaError("DECISION_INSTRUCTIONS_EMPTY")
        if not 2 <= len(self.choices) <= 255:
            raise DecisionSchemaError("DECISION_CHOICE_ARITY")
        seen: list[ChoiceValue] = []
        for option in self.choices:
            option.validate()
            if any(option.value == other and type(option.value) is type(other) for other in seen):
                raise DecisionSchemaError("DECISION_CHOICE_DUPLICATE")
            seen.append(option.value)


@dataclass(frozen=True)
class ScoreQuestion:
    """Rate the input against two to 255 ordered levels."""

    instructions: str
    levels: tuple[ScoreLevel, ...]
    name: str | None = None
    type: Literal["score"] = "score"

    def validate(self) -> None:
        if not self.instructions.strip():
            raise DecisionSchemaError("DECISION_INSTRUCTIONS_EMPTY")
        if not 2 <= len(self.levels) <= 255:
            raise DecisionSchemaError("DECISION_SCORE_ARITY")
        labels = [level.label for level in self.levels]
        if len(set(labels)) != len(labels):
            raise DecisionSchemaError("DECISION_SCORE_LABEL_DUPLICATE")
        for level in self.levels:
            level.validate()


DecisionQuestion = PredicateQuestion | ChoiceQuestion | ScoreQuestion


@dataclass(frozen=True)
class DecisionRequest:
    """One decision evaluation over shared input evidence."""

    model: str
    input: str | tuple[dict[str, Any], ...]
    questions: tuple[DecisionQuestion, ...]

    def validate(self) -> None:
        if not self.model.strip():
            raise DecisionSchemaError("DECISION_MODEL_EMPTY")
        if (
            isinstance(self.input, str)
            or isinstance(self.input, tuple)
            and all(isinstance(item, dict) for item in self.input)
        ):
            pass
        else:
            raise DecisionSchemaError("DECISION_INPUT_TYPE")
        if not 1 <= len(self.questions) <= 400:
            raise DecisionSchemaError("DECISION_QUESTION_ARITY")
        for question in self.questions:
            question.validate()

    def canonical_json(self) -> dict[str, Any]:
        """Dialect-neutral canonical serialization used for digests and replay."""
        return {
            "model": self.model,
            "input": list(self.input) if isinstance(self.input, tuple) else self.input,
            "questions": [question_to_canonical(q) for q in self.questions],
        }


def question_to_canonical(question: DecisionQuestion) -> dict[str, Any]:
    if isinstance(question, PredicateQuestion):
        return {"type": "predicate", "name": question.name, "instructions": question.instructions}
    if isinstance(question, ChoiceQuestion):
        return {
            "type": "choice",
            "name": question.name,
            "instructions": question.instructions,
            "choices": [
                {"value": option.value, "description": option.description}
                for option in question.choices
            ],
        }
    if isinstance(question, ScoreQuestion):
        return {
            "type": "score",
            "name": question.name,
            "instructions": question.instructions,
            "levels": [
                {"label": level.label, "description": level.description}
                for level in question.levels
            ],
        }
    raise DecisionSchemaError("DECISION_QUESTION_TYPE")


def question_from_canonical(payload: dict[str, Any]) -> DecisionQuestion:
    qtype = payload.get("type")
    name = payload.get("name")
    instructions = str(payload.get("instructions") or "")
    if qtype == "predicate":
        return PredicateQuestion(instructions=instructions, name=name)
    if qtype == "choice":
        choices = tuple(
            ChoiceOption(value=entry["value"], description=str(entry.get("description") or ""))
            for entry in payload.get("choices") or ()
        )
        return ChoiceQuestion(instructions=instructions, choices=choices, name=name)
    if qtype == "score":
        levels = tuple(
            ScoreLevel(label=str(entry["label"]), description=str(entry.get("description") or ""))
            for entry in payload.get("levels") or ()
        )
        return ScoreQuestion(instructions=instructions, levels=levels, name=name)
    raise DecisionSchemaError("DECISION_QUESTION_TYPE")


def request_digest(request: DecisionRequest) -> str:
    """Stable SHA256 over the canonical request, used for replay keys and audit."""
    blob = json.dumps(
        request.canonical_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PredicateAnswer:
    probability: float
    name: str | None = None
    type: Literal["predicate"] = "predicate"


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: ChoiceValue
    confidence: float
    probabilities: tuple[tuple[ChoiceValue, float], ...]
    name: str | None = None
    type: Literal["choice"] = "choice"


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    confidence: float
    probabilities: tuple[tuple[str, int, float], ...]
    name: str | None = None
    type: Literal["score"] = "score"


@dataclass(frozen=True)
class DecisionRefusal:
    """The model declined one question; sibling answers may still be valid."""

    name: str | None = None
    type: Literal["refusal"] = "refusal"


DecisionAnswer = PredicateAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    cost: float | None = None


@dataclass(frozen=True)
class DecisionResponse:
    """Answers in question order plus the model and token accounting."""

    answers: tuple[DecisionAnswer | DecisionRefusal, ...]
    model: str
    usage: TokenUsage
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def named(self) -> dict[str, DecisionAnswer | DecisionRefusal]:
        """Answers keyed by question name; unnamed answers are excluded."""
        return {answer.name: answer for answer in self.answers if answer.name is not None}

    def require_named(self, name: str) -> DecisionAnswer:
        """Fetch one named answer, failing closed on absence or refusal."""
        answer = self.named().get(name)
        if answer is None:
            raise DecisionSchemaError(f"DECISION_ANSWER_MISSING:{name}")
        if isinstance(answer, DecisionRefusal):
            raise DecisionSchemaError(f"DECISION_ANSWER_REFUSED:{name}")
        return answer
