"""HTTP backends for the Decision API adapter.

Two live dialects are supported:

* openai-ga -- the OpenAI Decisions API GA schema (input plus a list of
  predicate/choice/score questions).  Verified live against
  google/gemini-decision-1 behind gw.72live.com on 2026-11-27.
* openrouter-alpha -- the OpenRouter alpha schema (state field, question
  dictionary, noul primitive, criteria dict/list) used by typesafe/jev-*
  models on OpenRouter.  The same dialect is served natively at
  /v1/systemone on magpie gateways.

Translation between the two dialects is explicit and lossy only where the
OpenRouter dialect structurally cannot carry information: boolean choice
values (its criteria keys are always strings) and score labels (its
responses echo numeric indices only).  Both cases fail closed at request
construction instead of degrading silently.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from quanthus.decision_api.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionHttpError,
    DecisionRefusal,
    DecisionRequest,
    DecisionResponse,
    DecisionSchemaError,
    DecisionTimeoutError,
    DecisionTransportError,
    PredicateAnswer,
    PredicateQuestion,
    ScoreAnswer,
    ScoreQuestion,
    TokenUsage,
)

DECISIONS_DIALECT_OPENAI_GA = "openai-ga"
DECISIONS_DIALECT_OPENROUTER_ALPHA = "openrouter-alpha"

JsonObject = dict[str, Any]
TransportCallable = Callable[[str, JsonObject, Mapping[str, str], float], "tuple[int, JsonObject]"]


class UrllibJsonTransport:
    """Minimal synchronous JSON-over-HTTP transport with a strict timeout.

    post(url, payload, headers, timeout_seconds) returns the HTTP status and
    decoded JSON body.  Network, TLS and decoding failures raise
    DecisionTransportError; timeouts raise DecisionTimeoutError.
    """

    def __init__(self, opener: Any | None = None) -> None:
        self._opener = opener or urllib.request.build_opener()

    def post(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> tuple[int, JsonObject]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            return error.code, _decode_json_or_raise(raw)
        except TimeoutError as error:
            raise DecisionTimeoutError(f"DECISION_TIMEOUT after {timeout_seconds}s") from error
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise DecisionTimeoutError(f"DECISION_TIMEOUT after {timeout_seconds}s") from error
            raise DecisionTransportError(f"DECISION_TRANSPORT: {reason}") from error
        except OSError as error:
            raise DecisionTransportError(f"DECISION_TRANSPORT: {error}") from error
        return status, _decode_json_or_raise(raw)


def _decode_json_or_raise(raw: str) -> JsonObject:
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as error:
        raise DecisionTransportError("DECISION_RESPONSE_NOT_JSON") from error
    if not isinstance(decoded, dict):
        raise DecisionSchemaError("DECISION_RESPONSE_NOT_OBJECT")
    return decoded


class DecisionBackend(ABC):
    """One decision-model endpoint speaking one wire dialect."""

    dialect: str

    @abstractmethod
    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Evaluate every question in one batch and return typed answers."""


@dataclass(frozen=True)
class HttpBackendConfig:
    url: str
    api_key: str
    timeout_seconds: float = 30.0
    extra_headers: tuple = ()
    user_agent: str = "quanthus-decision-adapter/1.0"


class _BaseHttpBackend(DecisionBackend):
    def __init__(
        self,
        config: HttpBackendConfig,
        transport: TransportCallable | UrllibJsonTransport | None = None,
    ) -> None:
        if not config.url.startswith("https://") and not config.url.startswith("http://"):
            raise DecisionSchemaError("DECISION_BACKEND_URL_SCHEME")
        if not config.api_key:
            raise DecisionSchemaError("DECISION_BACKEND_KEY_EMPTY")
        if not 0.5 <= config.timeout_seconds <= 300.0:
            raise DecisionSchemaError("DECISION_BACKEND_TIMEOUT_RANGE")
        self._config = config
        if transport is None:
            self._transport: TransportCallable = UrllibJsonTransport().post
        elif isinstance(transport, UrllibJsonTransport):
            self._transport = transport.post
        else:
            self._transport = transport

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "User-Agent": self._config.user_agent,
        }
        for key, value in self._config.extra_headers:
            headers[key] = value
        return headers

    def _send(self, payload: JsonObject) -> JsonObject:
        status, body = self._transport(
            self._config.url,
            payload,
            self._headers(),
            self._config.timeout_seconds,
        )
        if not 200 <= status < 300:
            raise DecisionHttpError(status, json.dumps(body, ensure_ascii=False)[:400])
        return body


class OpenAIDecisionsBackend(_BaseHttpBackend):
    """Backend speaking the OpenAI Decisions API GA dialect verbatim."""

    dialect = DECISIONS_DIALECT_OPENAI_GA

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        request.validate()
        payload = request_to_openai_ga(request)
        body = self._send(payload)
        return parse_openai_ga_response(body)


def request_to_openai_ga(request: DecisionRequest) -> JsonObject:
    """Serialize the canonical request into the GA wire shape."""
    request.validate()
    questions: list[JsonObject] = []
    for question in request.questions:
        entry: JsonObject = {"type": question.type, "instructions": question.instructions}
        if question.name is not None:
            entry["name"] = question.name
        if isinstance(question, ChoiceQuestion):
            entry["choices"] = [
                {"value": option.value, "description": option.description}
                for option in question.choices
            ]
        elif isinstance(question, ScoreQuestion):
            entry["levels"] = [
                {"label": level.label, "description": level.description}
                for level in question.levels
            ]
        questions.append(entry)
    return {
        "model": request.model,
        "input": list(request.input) if isinstance(request.input, tuple) else request.input,
        "questions": questions,
    }


def parse_openai_ga_response(body: JsonObject) -> DecisionResponse:
    """Parse one GA wire response, failing closed on malformed envelopes."""
    answers_raw = body.get("answers")
    if not isinstance(answers_raw, list):
        raise DecisionSchemaError("DECISION_RESPONSE_ANSWERS_MISSING")
    answers: list = []
    for index, entry in enumerate(answers_raw):
        if not isinstance(entry, dict):
            raise DecisionSchemaError("DECISION_ANSWER_NOT_OBJECT")
        answers.append(_parse_ga_answer(entry, index))
    usage = _parse_usage(body.get("usage"))
    model = body.get("model")
    return DecisionResponse(
        answers=tuple(answers),
        model=str(model) if isinstance(model, str) else "",
        usage=usage,
        raw=body,
    )


def _parse_ga_answer(entry: JsonObject, index: int) -> Any:
    atype = entry.get("type")
    name = entry.get("name") if isinstance(entry.get("name"), str) else None
    if atype == "refusal":
        return DecisionRefusal(name=name)
    if atype == "predicate":
        probability = _as_probability(entry.get("probability"), index)
        return PredicateAnswer(probability=probability, name=name)
    if atype == "choice":
        choice = entry.get("choice")
        if not isinstance(choice, (str, bool)):
            raise DecisionSchemaError(f"DECISION_CHOICE_ANSWER_TYPE[{index}]")
        probabilities = entry.get("probabilities") or []
        pairs = tuple(
            (
                item["value"],
                _as_probability(item.get("probability"), index),
            )
            for item in probabilities
            if isinstance(item, dict) and isinstance(item.get("value"), (str, bool))
        )
        confidence = _as_probability(entry.get("confidence"), index)
        return ChoiceAnswer(choice=choice, confidence=confidence, probabilities=pairs, name=name)
    if atype == "score":
        score_raw = entry.get("score")
        if not isinstance(score_raw, (int, float)) or isinstance(score_raw, bool):
            raise DecisionSchemaError(f"DECISION_SCORE_ANSWER_TYPE[{index}]")
        probabilities = entry.get("probabilities") or []
        triples = tuple(
            (
                str(item.get("label", "")),
                int(item.get("value", 0)),
                _as_probability(item.get("probability"), index),
            )
            for item in probabilities
            if isinstance(item, dict)
        )
        confidence = _as_probability(entry.get("confidence"), index)
        return ScoreAnswer(
            score=float(score_raw),
            confidence=confidence,
            probabilities=triples,
            name=name,
        )
    raise DecisionSchemaError(f"DECISION_ANSWER_TYPE_UNKNOWN[{index}]")


def _as_probability(value: Any, index: int) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DecisionSchemaError(f"DECISION_PROBABILITY_TYPE[{index}]")
    if not 0.0 <= float(value) <= 1.0:
        raise DecisionSchemaError(f"DECISION_PROBABILITY_RANGE[{index}]")
    return float(value)


def _parse_usage(raw: Any) -> TokenUsage:
    if not isinstance(raw, dict):
        return TokenUsage()
    details = (
        raw.get("input_tokens_details") if isinstance(raw.get("input_tokens_details"), dict) else {}
    )
    cost = raw.get("cost")
    return TokenUsage(
        input_tokens=int(raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        total_tokens=int(raw.get("total_tokens") or 0),
        cached_tokens=int(details.get("cached_tokens") or 0),
        cost=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
    )


class OpenRouterDecisionsBackend(_BaseHttpBackend):
    """Backend translating the canonical schema onto the OpenRouter alpha dialect.

    Structural limits of the alpha dialect (verified against the official
    documentation on 2026-11-27):

    * criteria keys are strings, so boolean choice values are rejected;
    * score answers echo numeric indices only, so level labels are
      reconstructed from the request rather than the wire;
    * every question must be named because answers arrive as a dictionary
      keyed by question name -- unnamed questions are rejected.
    """

    dialect = DECISIONS_DIALECT_OPENROUTER_ALPHA

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        request.validate()
        payload = request_to_openrouter_alpha(request)
        body = self._send(payload)
        return parse_openrouter_alpha_response(body, request)


def request_to_openrouter_alpha(request: DecisionRequest) -> JsonObject:
    """Translate the canonical request into the OpenRouter alpha wire shape."""
    request.validate()
    questions: dict[str, JsonObject] = {}
    for index, question in enumerate(request.questions):
        if question.name is None:
            raise DecisionSchemaError(f"DECISION_OPENROUTER_REQUIRES_NAME[{index}]")
        if question.name in questions:
            raise DecisionSchemaError(f"DECISION_OPENROUTER_DUPLICATE_NAME[{index}]")
        if isinstance(question, PredicateQuestion):
            entry: JsonObject = {"type": "noul", "instructions": question.instructions}
        elif isinstance(question, ChoiceQuestion):
            criteria: dict[str, str] = {}
            for option in question.choices:
                if isinstance(option.value, bool):
                    raise DecisionSchemaError(f"DECISION_OPENROUTER_BOOL_CHOICE[{index}]")
                criteria[option.value] = option.description
            entry = {"type": "choice", "instructions": question.instructions, "criteria": criteria}
        elif isinstance(question, ScoreQuestion):
            criteria_list = [
                level.description if level.description else level.label for level in question.levels
            ]
            entry = {
                "type": "score",
                "instructions": question.instructions,
                "criteria": criteria_list,
            }
        else:
            raise DecisionSchemaError("DECISION_QUESTION_TYPE")
        questions[question.name] = entry
    if isinstance(request.input, tuple):
        state: Any = _messages_to_openrouter_state(request.input)
    else:
        state = request.input
    return {"model": request.model, "state": state, "questions": questions}


def _messages_to_openrouter_state(messages: tuple) -> list:
    """Flatten canonical message arrays into alpha state parts.

    The alpha dialect accepts a string or a mixed list of strings and image
    parts.  Text-bearing messages are concatenated in order; image parts are
    translated where present.  Anything else fails closed.
    """
    parts: list = []
    for index, message in enumerate(messages):
        content = message.get("content")
        if isinstance(content, str):
            if content:
                parts.append(content)
            continue
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    raise DecisionSchemaError(f"DECISION_OPENROUTER_INPUT_PART[{index}]")
                ptype = part.get("type")
                if ptype == "input_text" and part.get("text"):
                    parts.append(str(part["text"]))
                elif ptype == "input_image" and isinstance(part.get("image_url"), str):
                    parts.append({"type": "image_url", "image_url": {"url": part["image_url"]}})
                else:
                    raise DecisionSchemaError(f"DECISION_OPENROUTER_INPUT_PART_TYPE[{index}]")
            continue
        raise DecisionSchemaError(f"DECISION_OPENROUTER_INPUT_TYPE[{index}]")
    return parts


def parse_openrouter_alpha_response(body: JsonObject, request: DecisionRequest) -> DecisionResponse:
    """Translate one alpha wire response back into canonical answers.

    Positional alignment is impossible because alpha answers form a name-keyed
    dictionary; the canonical ordering is restored by matching question names,
    and questions missing from the response degrade to explicit refusals.
    """
    answers_raw = body.get("answers")
    if not isinstance(answers_raw, dict):
        raise DecisionSchemaError("DECISION_RESPONSE_ANSWERS_MISSING")
    ordered: list = []
    for question in request.questions:
        entry = answers_raw.get(question.name) if question.name is not None else None
        if not isinstance(entry, dict):
            ordered.append(DecisionRefusal(name=question.name))
            continue
        ordered.append(_parse_alpha_answer(entry, question))
    usage = _parse_usage(body.get("usage"))
    model = body.get("model")
    return DecisionResponse(
        answers=tuple(ordered),
        model=str(model) if isinstance(model, str) else "",
        usage=usage,
        raw=body,
    )


def _parse_alpha_answer(entry: JsonObject, question: Any) -> Any:
    name = question.name
    atype = entry.get("type")
    if atype == "refusal":
        return DecisionRefusal(name=name)
    if atype == "noul" and isinstance(question, PredicateQuestion):
        probability = _as_probability(entry.get("noul"), 0)
        return PredicateAnswer(probability=probability, name=name)
    if atype == "choice" and isinstance(question, ChoiceQuestion):
        choice = entry.get("choice")
        probabilities_raw = (
            entry.get("probabilities") if isinstance(entry.get("probabilities"), dict) else {}
        )
        pairs = tuple(
            (key, _as_probability(probability, 0)) for key, probability in probabilities_raw.items()
        )
        confidence = _as_probability(entry.get("confidence"), 0)
        if not isinstance(choice, str):
            raise DecisionSchemaError("DECISION_CHOICE_ANSWER_TYPE")
        return ChoiceAnswer(choice=choice, confidence=confidence, probabilities=pairs, name=name)
    if atype == "score" and isinstance(question, ScoreQuestion):
        score_raw = entry.get("score")
        if not isinstance(score_raw, (int, float)) or isinstance(score_raw, bool):
            raise DecisionSchemaError("DECISION_SCORE_ANSWER_TYPE")
        probabilities_raw = (
            entry.get("probabilities") if isinstance(entry.get("probabilities"), dict) else {}
        )
        triples = []
        for key, probability in probabilities_raw.items():
            try:
                idx = int(key)
            except (TypeError, ValueError) as error:
                raise DecisionSchemaError("DECISION_SCORE_INDEX_TYPE") from error
            label = question.levels[idx].label if 0 <= idx < len(question.levels) else ""
            triples.append((label, idx, _as_probability(probability, 0)))
        confidence = _as_probability(entry.get("confidence"), 0)
        return ScoreAnswer(
            score=float(score_raw),
            confidence=confidence,
            probabilities=tuple(triples),
            name=name,
        )
    raise DecisionSchemaError(f"DECISION_ALPHA_ANSWER_TYPE:{atype}")


__all__ = [
    "DECISIONS_DIALECT_OPENAI_GA",
    "DECISIONS_DIALECT_OPENROUTER_ALPHA",
    "DecisionBackend",
    "HttpBackendConfig",
    "OpenAIDecisionsBackend",
    "OpenRouterDecisionsBackend",
    "UrllibJsonTransport",
    "parse_openai_ga_response",
    "parse_openrouter_alpha_response",
    "request_to_openai_ga",
    "request_to_openrouter_alpha",
]
