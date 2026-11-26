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

R3 hardening after the second independent review of PR #756:

* the transport no longer delegates framing to http.client.  It is a
  minimal HTTP/1.x client over raw sockets so every blocking operation
  -- connect, TLS, send, response headers, every body read including
  close-delimited EOF -- is re-armed to the remaining budget before it
  starts and re-checked after it returns.  A slow-header server, a
  trickling body and an HTTP/1.0 close-delimited body are all bounded
  by the same strict deadline;
* the absolute deadline covers the logical call from dispatch to
  return: a complete response assembled only after the deadline is
  rejected as DecisionLateAnswerError, never delivered, while its
  parsed usage is preserved because the vendor did process the request;
* a non-2xx status is locked for failover classification the moment the
  response headers are complete.  The error body and its usage evidence
  are then read best-effort inside the remaining budget only; a delayed,
  absent or unparseable error body degrades to an empty body and can
  never reclassify the attempt as a timeout or a schema failure;
* malformed numerics -- giant integers that overflow float conversion,
  NaN or infinite probabilities, boolean masquerading, negative token
  counts or negative cost -- are typed schema errors, never bare
  OverflowError/ValueError escapes and never silently accepted.
"""

from __future__ import annotations

import contextlib
import json
import math
import socket
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
    DecisionLateAnswerError,
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

_MAX_HEAD_BYTES = 65536
_MAX_BODY_BYTES = 4 * 1024 * 1024
_TLS_CONTEXT: ssl.SSLContext | None = None


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


def _tls_context() -> ssl.SSLContext:
    global _TLS_CONTEXT
    if _TLS_CONTEXT is None:
        _TLS_CONTEXT = ssl.create_default_context()
    return _TLS_CONTEXT


class UrllibJsonTransport:
    """Synchronous JSON-over-HTTP transport with one strict wall-clock budget.

    post(url, payload, headers, timeout_seconds) returns the HTTP status
    and the raw response body text.  The implementation is a minimal
    HTTP/1.x client over raw sockets: framing (Content-Length, chunked,
    close-delimited EOF) is parsed explicitly so every blocking socket
    operation is re-armed to the remaining budget before it starts and
    the deadline is re-checked after every read and at each phase
    boundary.  Nothing in the response path can outlive the budget:
    slow headers, a trickling body and a close-delimited body all hit
    the same hard wall.

    Non-2xx handling: the status is fixed as soon as the response
    headers are complete.  The error body is then read best-effort
    inside the remaining budget; a delayed, absent or unparseable error
    body degrades to an empty string and the preserved status governs
    every downstream decision.  Success responses are strict: the full
    body must complete inside the budget, and the post-completion
    deadline check applies, so a response finished only after the
    deadline surfaces as DecisionTimeoutError, never as data.

    Network, TLS and framing failures raise DecisionTransportError;
    budget expiry raises DecisionTimeoutError.  Exceptions raised before
    the first request byte was sent carry wire_dispatched=False.
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
        port = parts.port or (443 if parts.scheme == "https" else 80)
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        deadline = time.monotonic() + timeout_seconds
        sent_any = False
        sock: socket.socket | None = None
        try:
            body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_bytes = self._request_bytes(parts, target, body_bytes, headers)
            self._require_budget(deadline, timeout_seconds, "CONNECT")
            sock = socket.create_connection(
                (parts.hostname, port), timeout=max(self._remaining(deadline), 0.001)
            )
            self._require_budget(deadline, timeout_seconds, "CONNECT")
            if parts.scheme == "https":
                sock = _tls_context().wrap_socket(sock, server_hostname=parts.hostname)
                self._require_budget(deadline, timeout_seconds, "CONNECT")
            self._send_all(sock, request_bytes, deadline, timeout_seconds)
            sent_any = True
            status, head_rest, response_headers = self._read_head(sock, deadline, timeout_seconds)
            self._require_budget(deadline, timeout_seconds, "HEADERS")
            if 200 <= status < 300:
                body_raw = self._read_body(
                    sock, head_rest, status, response_headers, deadline, timeout_seconds
                )
                # strict: the completed body must exist inside the budget
                self._require_budget(deadline, timeout_seconds, "BODY")
                return status, body_raw.decode("utf-8", errors="replace")
            # non-2xx: the status is already locked for classification.
            # the error body is evidence only, read inside the remaining
            # budget; anything that does not complete cleanly degrades to
            # an empty body instead of reclassifying the attempt.
            try:
                body_raw = self._read_body(
                    sock, head_rest, status, response_headers, deadline, timeout_seconds
                )
                self._require_budget(deadline, timeout_seconds, "BODY")
            except (DecisionTimeoutError, DecisionTransportError, OSError):
                body_raw = b""
            return status, body_raw.decode("utf-8", errors="replace")
        except DecisionTimeoutError as error:
            error.wire_dispatched = error.wire_dispatched and sent_any
            raise
        except DecisionTransportError as error:
            error.wire_dispatched = error.wire_dispatched and sent_any
            raise
        except TimeoutError as error:
            raise DecisionTimeoutError(f"DECISION_TIMEOUT after {timeout_seconds}s") from error
        except ssl.SSLError as error:
            if "timed out" in str(error).lower():
                raise DecisionTimeoutError(f"DECISION_TIMEOUT after {timeout_seconds}s") from error
            raise DecisionTransportError(f"DECISION_TRANSPORT_TLS: {error}") from error
        except OSError as error:
            raise DecisionTransportError(f"DECISION_TRANSPORT: {error}") from error
        finally:
            if sock is not None:
                with contextlib.suppress(Exception):
                    sock.close()

    @staticmethod
    def _remaining(deadline: float) -> float:
        return deadline - time.monotonic()

    @classmethod
    def _require_budget(cls, deadline: float, budget: float, phase: str) -> None:
        if cls._remaining(deadline) <= 0.0:
            raise DecisionTimeoutError(f"DECISION_TIMEOUT_{phase} after {budget}s")

    @classmethod
    def _recv_some(cls, sock: socket.socket, deadline: float, budget: float) -> bytes:
        remaining = cls._remaining(deadline)
        if remaining <= 0.0:
            raise DecisionTimeoutError(f"DECISION_TIMEOUT_BODY after {budget}s")
        sock.settimeout(remaining)
        try:
            return sock.recv(65536)
        except TimeoutError as error:
            raise DecisionTimeoutError(f"DECISION_TIMEOUT_BODY after {budget}s") from error

    @staticmethod
    def _request_bytes(
        parts: urllib.parse.SplitResult,
        target: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> bytes:
        merged: dict[str, str] = {}
        for key, value in headers.items():
            merged[key.lower()] = str(value)
        merged.setdefault("host", parts.netloc)
        merged.setdefault("content-type", "application/json")
        merged["content-length"] = str(len(body))
        merged["connection"] = "close"
        head = [f"POST {target} HTTP/1.1"]
        head.extend(f"{key}: {value}" for key, value in merged.items())
        return ("\r\n".join(head) + "\r\n\r\n").encode("utf-8") + body

    def _send_all(self, sock: socket.socket, data: bytes, deadline: float, budget: float) -> None:
        view = memoryview(data)
        while view:
            remaining = self._remaining(deadline)
            if remaining <= 0.0:
                raise DecisionTimeoutError(f"DECISION_TIMEOUT_SEND after {budget}s")
            sock.settimeout(remaining)
            try:
                written = sock.send(view)
            except TimeoutError as error:
                raise DecisionTimeoutError(f"DECISION_TIMEOUT_SEND after {budget}s") from error
            if written <= 0:
                raise DecisionTransportError("DECISION_TRANSPORT_SEND_ZERO")
            view = view[written:]

    def _read_head(
        self, sock: socket.socket, deadline: float, budget: float
    ) -> tuple[int, bytes, dict[str, str]]:
        """Read response headers, skipping interim 1xx responses."""
        buffered = b""
        while True:
            while b"\r\n\r\n" not in buffered:
                if len(buffered) > _MAX_HEAD_BYTES:
                    raise DecisionTransportError("DECISION_RESPONSE_HEAD_TOO_LARGE")
                chunk = self._recv_some(sock, deadline, budget)
                if not chunk:
                    raise DecisionTransportError("DECISION_RESPONSE_EOF_IN_HEADERS")
                buffered += chunk
            marker = buffered.index(b"\r\n\r\n")
            head_bytes, buffered = buffered[:marker], buffered[marker + 4 :]
            lines = head_bytes.split(b"\r\n")
            try:
                status_line = lines[0].decode("latin-1")
                pieces = status_line.split(" ", 2)
                if len(pieces) < 2 or not pieces[0].startswith("HTTP/"):
                    raise ValueError("bad status line")
                status = int(pieces[1])
            except (ValueError, IndexError) as error:
                raise DecisionTransportError("DECISION_RESPONSE_STATUS_LINE") from error
            headers: dict[str, str] = {}
            for raw_line in lines[1:]:
                if b":" not in raw_line:
                    raise DecisionTransportError("DECISION_RESPONSE_HEADER_LINE")
                key, _, value = raw_line.partition(b":")
                name = key.decode("latin-1").strip().lower()
                text = value.decode("latin-1").strip()
                headers[name] = text if name not in headers else headers[name] + "," + text
            if 100 <= status < 200:
                # interim response (for example 100 Continue): keep reading
                continue
            return status, buffered, headers

    def _read_line(
        self,
        sock: socket.socket,
        buffered: bytes,
        deadline: float,
        budget: float,
    ) -> tuple[bytes, bytes]:
        while b"\r\n" not in buffered:
            if len(buffered) > _MAX_HEAD_BYTES:
                raise DecisionTransportError("DECISION_RESPONSE_HEAD_TOO_LARGE")
            chunk = self._recv_some(sock, deadline, budget)
            if not chunk:
                raise DecisionTransportError("DECISION_RESPONSE_EOF_IN_BODY")
            buffered += chunk
        marker = buffered.index(b"\r\n")
        return buffered[:marker], buffered[marker + 2 :]

    def _fill(
        self,
        sock: socket.socket,
        buffered: bytes,
        want: int,
        deadline: float,
        budget: float,
    ) -> tuple[bytes, bytes]:
        """Return exactly want bytes, bounded by the strict deadline."""
        while len(buffered) < want:
            chunk = self._recv_some(sock, deadline, budget)
            if not chunk:
                raise DecisionTransportError("DECISION_RESPONSE_EOF_IN_BODY")
            buffered += chunk
        return buffered[:want], buffered[want:]

    def _read_body(
        self,
        sock: socket.socket,
        buffered: bytes,
        status: int,
        headers: dict[str, str],
        deadline: float,
        budget: float,
    ) -> bytes:
        """Read the response body per its framing, deadline-checked throughout."""
        if status in (204, 304) or 100 <= status < 200:
            return b""
        transfer = headers.get("transfer-encoding", "").lower()
        if "chunked" in transfer:
            return self._read_chunked(sock, buffered, deadline, budget)
        lengths = [
            piece.strip() for piece in headers.get("content-length", "").split(",") if piece.strip()
        ]
        if len(lengths) > 1 and len(set(lengths)) != 1:
            raise DecisionTransportError("DECISION_RESPONSE_LENGTH_CONFLICT")
        if lengths:
            try:
                declared = int(lengths[0])
            except ValueError as error:
                raise DecisionTransportError("DECISION_RESPONSE_LENGTH_VALUE") from error
            if declared < 0 or declared > _MAX_BODY_BYTES:
                raise DecisionTransportError("DECISION_RESPONSE_TOO_LARGE")
            body, _rest = self._fill(sock, buffered, declared, deadline, budget)
            return body
        # close-delimited: the body ends at EOF, and the EOF read itself is
        # deadline-bounded, so an HTTP/1.0-style server cannot stretch the
        # attempt by delaying the connection close.
        chunks: list[bytes] = []
        total = len(buffered)
        if total > _MAX_BODY_BYTES:
            raise DecisionTransportError("DECISION_RESPONSE_TOO_LARGE")
        if buffered:
            chunks.append(buffered)
        while True:
            chunk = self._recv_some(sock, deadline, budget)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_BODY_BYTES:
                raise DecisionTransportError("DECISION_RESPONSE_TOO_LARGE")
            chunks.append(chunk)
        return b"".join(chunks)

    def _read_chunked(
        self, sock: socket.socket, buffered: bytes, deadline: float, budget: float
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            line, buffered = self._read_line(sock, buffered, deadline, budget)
            size_text = line.split(b";", 1)[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError as error:
                raise DecisionTransportError("DECISION_RESPONSE_CHUNK_SIZE") from error
            if size < 0:
                raise DecisionTransportError("DECISION_RESPONSE_CHUNK_SIZE")
            if size == 0:
                # trailers terminate with one empty line
                while True:
                    trailer, buffered = self._read_line(sock, buffered, deadline, budget)
                    if trailer == b"":
                        return b"".join(chunks)
                    if len(trailer) > _MAX_HEAD_BYTES:
                        raise DecisionTransportError("DECISION_RESPONSE_HEAD_TOO_LARGE")
            total += size
            if total > _MAX_BODY_BYTES:
                raise DecisionTransportError("DECISION_RESPONSE_TOO_LARGE")
            piece, buffered = self._fill(sock, buffered, size + 2, deadline, budget)
            if not piece.endswith(b"\r\n"):
                raise DecisionTransportError("DECISION_RESPONSE_CHUNK_FRAME")
            chunks.append(piece[:-2])


def _decode_json_or_raise(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as error:
        raise DecisionTransportError("DECISION_RESPONSE_NOT_JSON") from error


def _as_finite_float(value: Any, reason: str) -> float:
    """Convert one wire numeric strictly, rejecting overflow, NaN and infinity.

    json can surface arbitrarily large integers whose float conversion
    raises a bare OverflowError; every numeric the adapter accepts passes
    through this gate so malformed values are typed schema errors.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DecisionSchemaError(reason)
    try:
        converted = float(value)
    except (OverflowError, ValueError) as error:
        raise DecisionSchemaError(reason) from error
    if not math.isfinite(converted):
        raise DecisionSchemaError(reason)
    return converted


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
        raises DecisionTimeoutError before any network use.  A response
        assembled only after the deadline is rejected as
        DecisionLateAnswerError even when every byte is well-formed.
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
        for key, value in headers.items():
            headers[key] = value
        return headers

    def _effective_timeout(self, deadline: DecisionDeadline | None) -> float:
        timeout = self._config.timeout_seconds
        if deadline is None:
            return timeout
        remaining = deadline.remaining()
        if remaining <= 0.0:
            error = DecisionTimeoutError("DECISION_DEADLINE_EXCEEDED")
            error.wire_dispatched = False
            raise error
        # no floor: the shared deadline is a hard wall, so a tiny remainder
        # must yield a tiny budget rather than an overrun allowance
        return min(timeout, remaining)

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

    def _reject_if_late(self, deadline: DecisionDeadline | None, decoded: JsonObject) -> None:
        """Refuse to deliver a complete response assembled past the deadline.

        The deadline covers dispatch-to-return, so a late answer is never
        usable evidence.  The vendor did process the request, so the
        rejection carries the leniently parsed usage as billing evidence.
        """
        if deadline is None or not deadline.exceeded():
            return
        usage = None
        try:
            usage = _parse_usage(decoded.get("usage"))
        except DecisionSchemaError:
            usage = None
        raise DecisionLateAnswerError("DECISION_LATE_ANSWER", usage=usage)


def _lenient_usage(raw: str) -> TokenUsage | None:
    """Extract billing evidence from an error body without ever failing.

    A malformed or absent usage object yields None, which the chain
    records as unknown usage billed at worst case; a well-formed one is
    kept verbatim so billed failures carry their cost evidence.  An
    object that parses but is incomplete (missing token counters) is
    returned with complete=False so the chain still accounts worst case
    while preserving what the wire did report.
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
    if not usage.present:
        return None
    return usage


class OpenAIDecisionsBackend(_BaseHttpBackend):
    """Backend speaking the OpenAI Decisions API GA dialect verbatim."""

    dialect = DECISIONS_DIALECT_OPENAI_GA

    def decide(
        self, request: DecisionRequest, deadline: DecisionDeadline | None = None
    ) -> DecisionResponse:
        try:
            request.validate()
            payload = request_to_openai_ga(request)
        except DecisionSchemaError as error:
            error.wire_dispatched = False
            raise
        timeout = self._effective_timeout(deadline)
        body = self._send(payload, timeout)
        self._reject_if_late(deadline, body)
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
    score_value = _as_finite_float(entry.get("score"), f"DECISION_SCORE_ANSWER_TYPE[{index}]")
    if not 0.0 <= score_value <= float(len(question.levels) - 1):
        raise DecisionSchemaError(f"DECISION_SCORE_RANGE[{index}]")
    confidence = _as_probability(entry.get("confidence"), index)
    wire = entry.get("probabilities")
    if wire is None:
        return ScoreAnswer(score=score_value, confidence=confidence, probabilities=(), name=name)
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
        score=score_value,
        confidence=confidence,
        probabilities=tuple(triples),
        name=name,
    )


def _as_probability(value: Any, index: int) -> float:
    converted = _as_finite_float(value, f"DECISION_PROBABILITY_TYPE[{index}]")
    if not 0.0 <= converted <= 1.0:
        raise DecisionSchemaError(f"DECISION_PROBABILITY_RANGE[{index}]")
    return converted


def _usage_integer(field: str, source: JsonObject) -> tuple[int, bool]:
    """Strict non-negative integer counter; reports whether the key existed."""
    if field not in source:
        return 0, False
    value = source[field]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DecisionSchemaError(f"DECISION_USAGE_FIELD:{field}")
    return value, True


def _parse_usage(raw: Any) -> TokenUsage:
    """Strict usage parsing: malformed usage is a schema error, never zeroed.

    present records whether a usage object existed on the wire at all;
    complete is true only when every counter needed for accounting
    (input and output tokens) is present.  Negative counts or cost,
    boolean masquerading and non-finite numerics are schema errors: a
    malformed negative cost must never enter the ledger as a small or
    negative bill, and a partial object must never masquerade as a
    complete one.
    """
    if raw is None:
        return TokenUsage()
    if not isinstance(raw, dict):
        raise DecisionSchemaError("DECISION_USAGE_TYPE")
    details_raw = raw.get("input_tokens_details")
    if details_raw is not None and not isinstance(details_raw, dict):
        raise DecisionSchemaError("DECISION_USAGE_DETAILS_TYPE")
    details: JsonObject = details_raw if isinstance(details_raw, dict) else {}

    input_tokens, has_input = _usage_integer("input_tokens", raw)
    output_tokens, has_output = _usage_integer("output_tokens", raw)
    total_tokens, _has_total = _usage_integer("total_tokens", raw)
    cached_tokens, _has_cached = _usage_integer("cached_tokens", details)

    cost: float | None = None
    if "cost" in raw:
        cost = _as_finite_float(raw["cost"], "DECISION_USAGE_FIELD:cost")
        if cost < 0:
            raise DecisionSchemaError("DECISION_USAGE_FIELD:cost")
    # any dict at the usage key means the wire carried a usage object,
    # even an empty one: absent (None) is the only not-present case.
    present = True
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        cost=cost,
        present=present,
        complete=has_input and has_output,
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
        try:
            request.validate()
            payload = request_to_openrouter_alpha(request)
        except DecisionSchemaError as error:
            error.wire_dispatched = False
            raise
        timeout = self._effective_timeout(deadline)
        body = self._send(payload, timeout)
        self._reject_if_late(deadline, body)
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
        score_value = _as_finite_float(entry.get("score"), "DECISION_SCORE_ANSWER_TYPE[alpha]")
        if not 0.0 <= score_value <= float(len(question.levels) - 1):
            raise DecisionSchemaError("DECISION_SCORE_RANGE[alpha]")
        probabilities_raw = entry.get("probabilities")
        if probabilities_raw is not None and not isinstance(probabilities_raw, dict):
            raise DecisionSchemaError("DECISION_SCORE_PROBABILITIES_TYPE[alpha]")
        confidence = _as_probability(entry.get("confidence"), 0)
        if probabilities_raw is None:
            return ScoreAnswer(
                score=score_value, confidence=confidence, probabilities=(), name=name
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
            score=score_value,
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
