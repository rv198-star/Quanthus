"""HTTP backends for the Decision API adapter.

Two live dialects are supported:

* openai-ga -- the OpenAI Decisions API GA schema (input plus a list of
  predicate/choice/score questions).  Verified live against
  google/gemini-decision-1 and typesafe/jev-latest on gw.72live.com and
  against gpt-6-luna on api.openai.com on 2026-11-27.
* openrouter-alpha -- the OpenRouter alpha schema (state field, question
  dictionary, noul primitive, criteria dict/list) used by typesafe/jev-*
  models on OpenRouter.  The same dialect is served natively at
  /v1/systemone on magpie gateways.

R2 hardening after the independent cold review of PR #756:

* failover classification is status-first: the HTTP status is preserved
  for every non-2xx response regardless of whether the error body parses
  as JSON, so an empty or HTML body can no longer masquerade as a
  transport failure, and a JSON error body cannot extend the approved
  failover set beyond its fixed statuses;
* response parsing binds every answer to the batch that asked for it:
  answer count, position, primitive type, name, choice values and score
  indices are all validated against the original questions, while
  legitimate per-question refusals stay isolated;
* probability distributions are emitted in declared option/level order,
  never in wire order, so live and replayed answers compare equal;
* the transport enforces one strict wall-clock budget per attempt
  covering connect, headers and body: read1 returns at most one socket
  read per call, so the loop can re-arm the socket timeout to the
  remaining budget before every read, and a slow-trickle server cannot
  stretch the attempt beyond its budget;
* non-2xx responses carry their usage object into the raised
  DecisionHttpError when the wire offered a well-formed one, so billed
  failures keep their cost evidence instead of degrading to unknown.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import ssl
import time
import urllib.parse
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from quanthus.decision_api.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionDeadline,
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
TransportCallable = Callable[[str, JsonObject, Mapping[str, str], float], "tuple[int, str]"]


def primitive_of(question: Any) -> str:
    """Canonical wire primitive of one question, derived from its class.

    The wire primitive follows the question class, never the mutable
    type attribute, and request validation rejects any mismatch, so a
    runtime override cannot desynchronize payload and replay digest.
    """
    if isinstance(question, PredicateQuestion):
        return "predicate"
    if isinstance(question, ChoiceQuestion):
        return "choice"
    if isinstance(question, ScoreQuestion):
        return "score"
    raise DecisionSchemaError("DECISION_QUESTION_TYPE")


class UrllibJsonTransport:
    """Synchronous JSON-over-HTTP transport with one strict wall-clock budget.

    post(url, payload, headers, timeout_seconds) returns the HTTP status
    and the raw response body text.  The budget covers connect, request
    send, response headers and the full body read: the socket timeout is
    re-armed to the remaining budget before every read1 call, and read1
    performs at most one underlying socket read, so the deadline is
    re-checked between chunks and a trickling server cannot stretch the
    attempt beyond its budget.  Network, TLS and decoding failures raise
    DecisionTransportError; timeouts raise DecisionTimeoutError.

    Non-2xx responses return their raw body without any JSON decoding,
    so the caller classifies failover purely on the preserved HTTP
    status.
    """

    def post(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> tuple[int, str]:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise DecisionTransportError(f"DECISION_TRANSPORT_URL: {url[:80]}")
        connection_cls = (
            http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        )
        port = parts.port or (443 if parts.scheme == "https" else 80)
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        body = json.dumps(payload).encode("utf-8")
        deadline = time.monotonic() + timeout_seconds
        connection = connection_cls(parts.hostname, port, timeout=timeout_seconds)
        try:
            connection.request("POST", target, body=body, headers=dict(headers))
            self._rearm(connection, deadline)
            response = connection.getresponse()
            chunks: list[bytes] = []
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise DecisionTimeoutError(f"DECISION_TIMEOUT_BODY after {timeout_seconds}s")
                self._rearm(connection, deadline)
                chunk = response.read1(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks).decode("utf-8", errors="replace")
            return response.status, raw
        except DecisionTimeoutError:
            raise
        except TimeoutError as error:
            raise DecisionTimeoutError(f"DECISION_TIMEOUT after {timeout_seconds}s") from error
        except ssl.SSLError as error:
            if "timed out" in str(error).lower():
                raise DecisionTimeoutError(f"DECISION_TIMEOUT after {timeout_seconds}s") from error
            raise DecisionTransportError(f"DECISION_TRANSPORT_TLS: {error}") from error
        except (OSError, http.client.HTTPException) as error:
            raise DecisionTransportError(f"DECISION_TRANSPORT: {error}") from error
        finally:
            with contextlib.suppress(Exception):
                connection.close()

    @staticmethod
    def _rearm(connection: http.client.HTTPConnection, deadline: float) -> None:
        sock = connection.sock
        if sock is not None:
            sock.settimeout(max(0.05, deadline - time.monotonic()))


def _decode_json_or_raise(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as error:
        raise DecisionTransportError("DECISION_RESPONSE_NOT_JSON") from error


class DecisionBackend(ABC):
    """One decision-model endpoint speaking one wire dialect."""

    dialect: str

    @abstractmethod
    def decide(
        self, request: DecisionRequest, deadline: DecisionDeadline | None = None
    ) -> DecisionResponse:
        """Evaluate every question in one batch and return typed answers.

        When an absolute deadline is supplied, the per-attempt timeout is
        truncated to the remaining budget; an already-exceeded deadline
        raises DecisionTimeoutError before any network use.
        """


@dataclass(frozen=True)
class HttpBackendConfig:
    url: str
    api_key: str
    timeout_seconds: float = 30.0
    extra_headers: tuple[tuple[str, str], ...] = ()
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

    def _effective_timeout(self, deadline: DecisionDeadline | None) -> float:
        timeout = self._config.timeout_seconds
        if deadline is None:
            return timeout
        remaining = deadline.remaining()
        if remaining <= 0.0:
            raise DecisionTimeoutError("DECISION_DEADLINE_EXCEEDED")
        return max(0.1, min(timeout, remaining))

    def _send(self, payload: JsonObject, timeout_seconds: float) -> JsonObject:
        status, raw = self._transport(
            self._config.url,
            payload,
            self._headers(),
            timeout_seconds,
        )
        # Status-first classification: the HTTP status alone decides
        # failover semantics, whatever the error body looks like.  The
        # body is preserved verbatim for audit; a well-formed usage
        # object inside it is kept as billing evidence for the failed
        # attempt.
        if not 200 <= status < 300:
            raise DecisionHttpError(status, raw[:400], usage=_lenient_usage(raw))
        decoded = _decode_json_or_raise(raw)
        if not isinstance(decoded, dict):
            raise DecisionSchemaError("DECISION_RESPONSE_NOT_OBJECT")
        return decoded


def _lenient_usage(raw: str) -> TokenUsage | None:
    """Extract billing evidence from an error body without ever failing.

    A malformed or absent usage object yields None, which the chain
    records as unknown usage billed at worst case; a well-formed one is
    kept verbatim so billed failures carry their cost evidence.
    """
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    try:
        usage = _parse_usage(decoded.get("usage"))
    except (DecisionSchemaError, TypeError, AttributeError):
        return None
    if usage.input_tokens == 0 and usage.output_tokens == 0 and usage.cost is None:
        return None
    return usage


class OpenAIDecisionsBackend(_BaseHttpBackend):
    """Backend speaking the OpenAI Decisions API GA dialect verbatim."""

    dialect = DECISIONS_DIALECT_OPENAI_GA

    def decide(
        self, request: DecisionRequest, deadline: DecisionDeadline | None = None
    ) -> DecisionResponse:
        request.validate()
        payload = request_to_openai_ga(request)
        timeout = self._effective_timeout(deadline)
        body = self._send(payload, timeout)
        return parse_openai_ga_response(body, request)


def request_to_openai_ga(request: DecisionRequest) -> JsonObject:
    """Serialize the canonical request into the GA wire shape."""
    request.validate()
    questions: list[JsonObject] = []
    for question in request.questions:
        entry: JsonObject = {"type": primitive_of(question), "instructions": question.instructions}
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


def parse_openai_ga_response(body: JsonObject, request: DecisionRequest) -> DecisionResponse:
    """Parse one GA wire response, binding every answer to its question.

    The answer list must match the batch exactly: same count, same
    positional order, the primitive type of each answer must equal the
    primitive of its question (or be a refusal), names must match
    positionally, choice values must be declared options and score
    probability indices must be in range with matching labels.
    Probability distributions are emitted in declared option/level order
    so live and replayed answers compare equal regardless of wire
    ordering.  A bare refusal carrying no name is accepted at its
    position because some vendors strip the name from refusals; the
    position binding still ties it to its question.
    """
    answers_raw = body.get("answers")
    if not isinstance(answers_raw, list):
        raise DecisionSchemaError("DECISION_RESPONSE_ANSWERS_MISSING")
    if len(answers_raw) != len(request.questions):
        raise DecisionSchemaError(
            f"DECISION_ANSWER_COUNT[{len(answers_raw)}!={len(request.questions)}]"
        )
    answers: list[PredicateAnswer | ChoiceAnswer | ScoreAnswer | DecisionRefusal] = []
    # lengths are already proven equal above, so strict=False never truncates
    for index, (entry, question) in enumerate(zip(answers_raw, request.questions, strict=False)):
        if not isinstance(entry, dict):
            raise DecisionSchemaError(f"DECISION_ANSWER_NOT_OBJECT[{index}]")
        answers.append(_parse_ga_answer(entry, index, question))
    usage = _parse_usage(body.get("usage"))
    model = body.get("model")
    return DecisionResponse(
        answers=tuple(answers),
        model=str(model) if isinstance(model, str) else "",
        usage=usage,
        raw=body,
    )


def _bind_name(entry: JsonObject, index: int, question: Any, *, bare_refusal: bool) -> None:
    wire_name = entry.get("name")
    if wire_name is None and question.name is None:
        return
    if wire_name is None and bare_refusal:
        return
    if wire_name != question.name:
        raise DecisionSchemaError(f"DECISION_ANSWER_NAME_MISMATCH[{index}]")


def _parse_ga_answer(
    entry: JsonObject, index: int, question: Any
) -> PredicateAnswer | ChoiceAnswer | ScoreAnswer | DecisionRefusal:
    atype = entry.get("type")
    if atype == "refusal":
        _bind_name(entry, index, question, bare_refusal=True)
        return DecisionRefusal(name=question.name)
    expected = primitive_of(question)
    if atype != expected:
        raise DecisionSchemaError(f"DECISION_ANSWER_PRIMITIVE_MISMATCH[{index}]:{atype}")
    _bind_name(entry, index, question, bare_refusal=False)
    name = question.name
    if atype == "predicate":
        probability = _as_probability(entry.get("probability"), index)
        return PredicateAnswer(probability=probability, name=name)
    if atype == "choice" and isinstance(question, ChoiceQuestion):
        return _parse_ga_choice(entry, index, question, name)
    if atype == "score" and isinstance(question, ScoreQuestion):
        return _parse_ga_score(entry, index, question, name)
    raise DecisionSchemaError(f"DECISION_ANSWER_TYPE_UNKNOWN[{index}]")


def _same_choice_value(left: Any, right: Any) -> bool:
    return bool(left == right) and type(left) is type(right)


def _parse_ga_choice(
    entry: JsonObject, index: int, question: ChoiceQuestion, name: str | None
) -> ChoiceAnswer:
    choice = entry.get("choice")
    declared = [option.value for option in question.choices]
    if not isinstance(choice, (str, bool)):
        raise DecisionSchemaError(f"DECISION_CHOICE_UNDECLARED[{index}]")
    if not any(_same_choice_value(choice, value) for value in declared):
        raise DecisionSchemaError(f"DECISION_CHOICE_UNDECLARED[{index}]")
    confidence = _as_probability(entry.get("confidence"), index)
    wire = entry.get("probabilities")
    if wire is None:
        return ChoiceAnswer(choice=choice, confidence=confidence, probabilities=(), name=name)
    if not isinstance(wire, list):
        raise DecisionSchemaError(f"DECISION_CHOICE_PROBABILITIES_TYPE[{index}]")
    by_value: dict[tuple[str, str], float] = {}
    for item in wire:
        if not isinstance(item, dict):
            raise DecisionSchemaError(f"DECISION_CHOICE_PROBABILITY_ENTRY[{index}]")
        value = item.get("value")
        if not any(_same_choice_value(value, option) for option in declared):
            raise DecisionSchemaError(f"DECISION_CHOICE_PROBABILITY_UNDECLARED[{index}]")
        key = (str(type(value)), json.dumps(value))
        if key in by_value:
            raise DecisionSchemaError(f"DECISION_CHOICE_PROBABILITY_DUPLICATE[{index}]")
        by_value[key] = _as_probability(item.get("probability"), index)
    pairs: list[tuple[Any, float]] = []
    for value in declared:
        key = (str(type(value)), json.dumps(value))
        if key not in by_value:
            raise DecisionSchemaError(f"DECISION_CHOICE_PROBABILITY_INCOMPLETE[{index}]")
        pairs.append((value, by_value[key]))
    return ChoiceAnswer(choice=choice, confidence=confidence, probabilities=tuple(pairs), name=name)


def _parse_ga_score(
    entry: JsonObject, index: int, question: ScoreQuestion, name: str | None
) -> ScoreAnswer:
    score_raw = entry.get("score")
    if not isinstance(score_raw, (int, float)) or isinstance(score_raw, bool):
        raise DecisionSchemaError(f"DECISION_SCORE_ANSWER_TYPE[{index}]")
    if not 0.0 <= float(score_raw) <= float(len(question.levels) - 1):
        raise DecisionSchemaError(f"DECISION_SCORE_RANGE[{index}]")
    confidence = _as_probability(entry.get("confidence"), index)
    wire = entry.get("probabilities")
    if wire is None:
        return ScoreAnswer(
            score=float(score_raw), confidence=confidence, probabilities=(), name=name
        )
    if not isinstance(wire, list):
        raise DecisionSchemaError(f"DECISION_SCORE_PROBABILITIES_TYPE[{index}]")
    by_index: dict[int, float] = {}
    for item in wire:
        if not isinstance(item, dict):
            raise DecisionSchemaError(f"DECISION_SCORE_PROBABILITY_ENTRY[{index}]")
        value = item.get("value")
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value < len(question.levels)
        ):
            raise DecisionSchemaError(f"DECISION_SCORE_PROBABILITY_INDEX[{index}]")
        if value in by_index:
            raise DecisionSchemaError(f"DECISION_SCORE_PROBABILITY_DUPLICATE[{index}]")
        label = item.get("label")
        if label != question.levels[value].label:
            raise DecisionSchemaError(f"DECISION_SCORE_PROBABILITY_LABEL[{index}]")
        by_index[value] = _as_probability(item.get("probability"), index)
    triples: list[tuple[str, int, float]] = []
    for position, level in enumerate(question.levels):
        if position not in by_index:
            raise DecisionSchemaError(f"DECISION_SCORE_PROBABILITY_INCOMPLETE[{index}]")
        triples.append((level.label, position, by_index[position]))
    return ScoreAnswer(
        score=float(score_raw),
        confidence=confidence,
        probabilities=tuple(triples),
        name=name,
    )


def _as_probability(value: Any, index: int) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DecisionSchemaError(f"DECISION_PROBABILITY_TYPE[{index}]")
    if not 0.0 <= float(value) <= 1.0:
        raise DecisionSchemaError(f"DECISION_PROBABILITY_RANGE[{index}]")
    return float(value)


def _parse_usage(raw: Any) -> TokenUsage:
    """Strict usage parsing: malformed usage is a schema error, never zeroed.

    Silently zeroing a malformed usage object would understate cost, so
    every present field must carry its documented type.  A missing usage
    object still yields an empty TokenUsage because some backends omit it
    on legitimate responses; consumers treat zero-usage answers as
    unknown-usage for budget purposes.
    """
    if raw is None:
        return TokenUsage()
    if not isinstance(raw, dict):
        raise DecisionSchemaError("DECISION_USAGE_TYPE")
    details_raw = raw.get("input_tokens_details")
    if details_raw is not None and not isinstance(details_raw, dict):
        raise DecisionSchemaError("DECISION_USAGE_DETAILS_TYPE")
    details: JsonObject = details_raw if isinstance(details_raw, dict) else {}

    def integer(field: str, source: JsonObject = raw) -> int:
        value = source.get(field, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DecisionSchemaError(f"DECISION_USAGE_FIELD:{field}")
        return value

    cost = raw.get("cost")
    if cost is not None and (not isinstance(cost, (int, float)) or isinstance(cost, bool)):
        raise DecisionSchemaError("DECISION_USAGE_FIELD:cost")
    return TokenUsage(
        input_tokens=integer("input_tokens"),
        output_tokens=integer("output_tokens"),
        total_tokens=integer("total_tokens"),
        cached_tokens=integer("cached_tokens", details),
        cost=float(cost) if cost is not None else None,
    )


class OpenRouterDecisionsBackend(_BaseHttpBackend):
    """Backend translating the canonical schema onto the OpenRouter alpha dialect.

    Structural limits of the alpha dialect (verified against the official
    documentation and the live systemone endpoint on 2026-11-27):

    * criteria keys are strings, so boolean choice values are rejected;
    * score answers echo numeric indices only, so level labels are
      reconstructed from the request rather than the wire;
    * every question must be named because answers arrive as a dictionary
      keyed by question name -- unnamed questions are rejected;
    * the flattened-state subset is versioned (v1): only user-role
      messages, text and image parts, no image detail tiers.  Anything
      outside the subset fails closed instead of being silently dropped.
    """

    dialect = DECISIONS_DIALECT_OPENROUTER_ALPHA

    def decide(
        self, request: DecisionRequest, deadline: DecisionDeadline | None = None
    ) -> DecisionResponse:
        request.validate()
        payload = request_to_openrouter_alpha(request)
        timeout = self._effective_timeout(deadline)
        body = self._send(payload, timeout)
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
        entry: JsonObject
        if isinstance(question, PredicateQuestion):
            entry = {"type": "noul", "instructions": question.instructions}
        elif isinstance(question, ChoiceQuestion):
            criteria: dict[str, str] = {}
            for option in question.choices:
                if isinstance(option.value, bool):
                    raise DecisionSchemaError(f"DECISION_OPENROUTER_BOOL_CHOICE[{index}]")
                criteria[str(option.value)] = option.description
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


# Versioned flattened-state subset v1.  The alpha dialect carries a bare
# string or a mixed list of strings and image_url parts; anything richer in
# the canonical message array cannot be represented and must fail closed
# rather than silently lose caller-specified semantics.
OPENROUTER_STATE_SUBSET_VERSION = "openrouter-state-subset.v1"


def _messages_to_openrouter_state(messages: tuple[dict[str, Any], ...]) -> list[Any]:
    """Flatten canonical message arrays into alpha state parts, subset v1.

    Accepted: user-role messages whose content is a string or a list of
    input_text parts and input_image parts without detail tiers.  Roles
    other than user, image detail tiers, and every other part type fail
    closed with an indexed reason.
    """
    parts: list[Any] = []
    for index, message in enumerate(messages):
        role = message.get("role")
        if role != "user":
            raise DecisionSchemaError(f"DECISION_OPENROUTER_ROLE_SUBSET[{index}]:{role}")
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
                    if "detail" in part:
                        raise DecisionSchemaError(f"DECISION_OPENROUTER_IMAGE_DETAIL[{index}]")
                    parts.append({"type": "image_url", "image_url": {"url": part["image_url"]}})
                else:
                    raise DecisionSchemaError(f"DECISION_OPENROUTER_INPUT_PART_TYPE[{index}]")
            continue
        else:
            raise DecisionSchemaError(f"DECISION_OPENROUTER_INPUT_TYPE[{index}]")
    return parts


def parse_openrouter_alpha_response(body: JsonObject, request: DecisionRequest) -> DecisionResponse:
    """Translate one alpha wire response back into canonical answers.

    Answers are matched by question name and validated against the batch:
    choice values must be declared options, score indices must be canonical
    decimal strings inside the level range with exact coverage, and
    probability distributions are emitted in declared order.  Questions
    missing from the response degrade to explicit refusals.
    """
    answers_raw = body.get("answers")
    if not isinstance(answers_raw, dict):
        raise DecisionSchemaError("DECISION_RESPONSE_ANSWERS_MISSING")
    ordered: list[PredicateAnswer | ChoiceAnswer | ScoreAnswer | DecisionRefusal] = []
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


def _canonical_index_key(key: Any) -> int:
    """Strict canonical index: decimal digits only, no zero-padding."""
    if not isinstance(key, str) or not key or not key.isascii() or not key.isdigit():
        raise DecisionSchemaError(f"DECISION_SCORE_INDEX_TYPE:{key}")
    if len(key) > 1 and key.startswith("0"):
        raise DecisionSchemaError(f"DECISION_SCORE_INDEX_PADDING:{key}")
    return int(key)


def _parse_alpha_answer(
    entry: JsonObject, question: Any
) -> PredicateAnswer | ChoiceAnswer | ScoreAnswer | DecisionRefusal:
    name = question.name
    atype = entry.get("type")
    if atype == "refusal":
        return DecisionRefusal(name=name)
    if atype == "noul" and isinstance(question, PredicateQuestion):
        probability = _as_probability(entry.get("noul"), 0)
        return PredicateAnswer(probability=probability, name=name)
    if atype == "choice" and isinstance(question, ChoiceQuestion):
        declared = [str(option.value) for option in question.choices]
        choice = entry.get("choice")
        if not isinstance(choice, str) or choice not in declared:
            raise DecisionSchemaError("DECISION_CHOICE_UNDECLARED[alpha]")
        probabilities_raw = entry.get("probabilities")
        if probabilities_raw is not None and not isinstance(probabilities_raw, dict):
            raise DecisionSchemaError("DECISION_CHOICE_PROBABILITIES_TYPE[alpha]")
        confidence = _as_probability(entry.get("confidence"), 0)
        if probabilities_raw is None:
            return ChoiceAnswer(choice=choice, confidence=confidence, probabilities=(), name=name)
        for key in probabilities_raw:
            if key not in declared:
                raise DecisionSchemaError(f"DECISION_CHOICE_PROBABILITY_UNDECLARED[alpha]:{key}")
        if len(probabilities_raw) != len(declared):
            raise DecisionSchemaError("DECISION_CHOICE_PROBABILITY_INCOMPLETE[alpha]")
        # declared order, never wire order
        original = [option.value for option in question.choices]
        pairs = tuple(
            (original[position], _as_probability(probabilities_raw[declared[position]], 0))
            for position in range(len(declared))
        )
        return ChoiceAnswer(choice=choice, confidence=confidence, probabilities=pairs, name=name)
    if atype == "score" and isinstance(question, ScoreQuestion):
        score_raw = entry.get("score")
        if not isinstance(score_raw, (int, float)) or isinstance(score_raw, bool):
            raise DecisionSchemaError("DECISION_SCORE_ANSWER_TYPE[alpha]")
        if not 0.0 <= float(score_raw) <= float(len(question.levels) - 1):
            raise DecisionSchemaError("DECISION_SCORE_RANGE[alpha]")
        probabilities_raw = entry.get("probabilities")
        if probabilities_raw is not None and not isinstance(probabilities_raw, dict):
            raise DecisionSchemaError("DECISION_SCORE_PROBABILITIES_TYPE[alpha]")
        confidence = _as_probability(entry.get("confidence"), 0)
        if probabilities_raw is None:
            return ScoreAnswer(
                score=float(score_raw), confidence=confidence, probabilities=(), name=name
            )
        indices: dict[int, float] = {}
        for key, probability in probabilities_raw.items():
            position = _canonical_index_key(key)
            if position in indices:
                raise DecisionSchemaError(f"DECISION_SCORE_INDEX_DUPLICATE:{key}")
            if not 0 <= position < len(question.levels):
                raise DecisionSchemaError(f"DECISION_SCORE_INDEX_RANGE:{key}")
            indices[position] = _as_probability(probability, 0)
        if len(indices) != len(question.levels):
            raise DecisionSchemaError("DECISION_SCORE_PROBABILITY_INCOMPLETE[alpha]")
        # declared order, never wire order
        triples = tuple(
            (level.label, position, indices[position])
            for position, level in enumerate(question.levels)
        )
        return ScoreAnswer(
            score=float(score_raw),
            confidence=confidence,
            probabilities=triples,
            name=name,
        )
    raise DecisionSchemaError(f"DECISION_ALPHA_ANSWER_TYPE:{atype}")


__all__ = [
    "DECISIONS_DIALECT_OPENAI_GA",
    "DECISIONS_DIALECT_OPENROUTER_ALPHA",
    "OPENROUTER_STATE_SUBSET_VERSION",
    "DecisionBackend",
    "HttpBackendConfig",
    "OpenAIDecisionsBackend",
    "OpenRouterDecisionsBackend",
    "TransportCallable",
    "UrllibJsonTransport",
    "parse_openai_ga_response",
    "parse_openrouter_alpha_response",
    "primitive_of",
    "request_to_openai_ga",
    "request_to_openrouter_alpha",
]
