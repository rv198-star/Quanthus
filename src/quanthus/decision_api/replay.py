"""Sealed replay and recording backends for offline, zero-cost evaluation.

P11 v2 design section 3.2 mandates zero network calls and zero spend for
offline contracts, historical replay and regression harnesses.  A
RecordingBackend wraps any live backend during the small number of
explicitly authorized online probes and persists every raw wire response
under the canonical request digest.  A ReplayBackend later answers the
byte-identical request from that sealed fixture; any deviation --
different model, input, question set or dialect -- is a miss and fails
closed.

R2 hardening after the independent cold review of PR #756:

* both backends are constructed with their declared dialect, and the
  replay backend validates the sealed fixture dialect at construction:
  a fixture recorded for one dialect can no longer be answered through
  the parser of another;
* replayed responses are parsed against the exact incoming batch, so
  answer counts, primitives, names and option/index ranges are bound to
  the request the same way as live traffic.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from quanthus.decision_api.backends import (
    DECISIONS_DIALECT_OPENAI_GA,
    DECISIONS_DIALECT_OPENROUTER_ALPHA,
    DecisionBackend,
    parse_openai_ga_response,
    parse_openrouter_alpha_response,
)
from quanthus.decision_api.types import (
    DecisionRequest,
    DecisionResponse,
    DecisionSchemaError,
    request_digest,
)

REPLAY_SCHEMA_VERSION = "quanthus.decision-api-replay.v1"

_KNOWN_DIALECTS = frozenset({DECISIONS_DIALECT_OPENAI_GA, DECISIONS_DIALECT_OPENROUTER_ALPHA})


def load_replay_fixture(path: Path) -> dict[str, Any]:
    """Load and validate one sealed replay fixture."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise DecisionSchemaError(f"DECISION_REPLAY_UNREADABLE: {error}") from error
    try:
        fixture = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DecisionSchemaError("DECISION_REPLAY_NOT_JSON") from error
    if not isinstance(fixture, dict) or fixture.get("schema_version") != REPLAY_SCHEMA_VERSION:
        raise DecisionSchemaError("DECISION_REPLAY_SCHEMA_VERSION")
    responses = fixture.get("responses")
    if not isinstance(responses, dict) or not responses:
        raise DecisionSchemaError("DECISION_REPLAY_EMPTY")
    for digest, entry in responses.items():
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise DecisionSchemaError("DECISION_REPLAY_KEY_DIGEST")
        if not isinstance(entry, dict) or "dialect" not in entry or "body" not in entry:
            raise DecisionSchemaError("DECISION_REPLAY_ENTRY_SHAPE")
    return fixture


def _parse_for_dialect(
    dialect: str, body: dict[str, Any], request: DecisionRequest
) -> DecisionResponse:
    if dialect == DECISIONS_DIALECT_OPENAI_GA:
        return parse_openai_ga_response(body, request)
    if dialect == DECISIONS_DIALECT_OPENROUTER_ALPHA:
        return parse_openrouter_alpha_response(body, request)
    raise DecisionSchemaError(f"DECISION_DIALECT_UNKNOWN: {dialect}")


class ReplayBackend(DecisionBackend):
    """Answer canonical requests from a sealed fixture without any network use.

    Every lookup keys on the canonical request digest, so the replay cannot
    silently serve a response recorded for different evidence, questions or
    model.  The fixture dialect is validated at construction against the
    declared dialect, and each hit is re-validated: mixed-dialect fixtures
    fail closed entry by entry.  Replayed responses are parsed against the
    exact incoming batch through the same dialect parsers as live traffic,
    so schema drift surfaces identically offline and online.
    """

    def __init__(self, fixture_path: Path, dialect: str) -> None:
        if dialect not in _KNOWN_DIALECTS:
            raise DecisionSchemaError(f"DECISION_DIALECT_UNKNOWN: {dialect}")
        fixture = load_replay_fixture(Path(fixture_path))
        self._fixture_path = Path(fixture_path)
        self._responses: dict[str, dict[str, Any]] = fixture["responses"]
        self.dialect = dialect
        for entry in self._responses.values():
            entry_dialect = str(entry.get("dialect"))
            if entry_dialect != dialect:
                raise DecisionSchemaError(
                    f"DECISION_REPLAY_DIALECT_MISMATCH[{dialect}]:{entry_dialect}"
                )
        self.fixture_digests = tuple(sorted(self._responses))
        self.misses: list[str] = []

    @property
    def fixture_path(self) -> Path:
        return self._fixture_path

    def decide(self, request: DecisionRequest, deadline: Any = None) -> DecisionResponse:
        request.validate()
        digest = request_digest(request)
        entry = self._responses.get(digest)
        if entry is None:
            self.misses.append(digest)
            raise DecisionSchemaError(f"DECISION_REPLAY_MISS: {digest}")
        entry_dialect = str(entry.get("dialect"))
        if entry_dialect != self.dialect:
            raise DecisionSchemaError(
                f"DECISION_REPLAY_DIALECT_MISMATCH[{self.dialect}]:{entry_dialect}"
            )
        return _parse_for_dialect(self.dialect, entry["body"], request)


def recording_entry(dialect: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "dialect": dialect,
        "body": body,
        "recorded_at_epoch": int(time.time()),
    }


class RecordingBackend(DecisionBackend):
    """Wrap a live backend and persist raw responses into a replay fixture.

    The wrapped backend's own errors propagate unchanged; only successful
    responses are recorded.  Writes are atomic per process and the fixture
    is rewritten as a whole so a partial response set never replaces a
    complete earlier recording.  The declared dialect is validated against
    the wrapped backend at construction, so a recording can never seal
    bodies under a foreign dialect label.
    """

    def __init__(self, backend: DecisionBackend, fixture_path: Path, dialect: str) -> None:
        backend_dialect = getattr(backend, "dialect", None)
        if dialect not in _KNOWN_DIALECTS:
            raise DecisionSchemaError(f"DECISION_DIALECT_UNKNOWN: {dialect}")
        if backend_dialect is not None and backend_dialect != dialect:
            raise DecisionSchemaError(
                f"DECISION_RECORD_DIALECT_MISMATCH[{dialect}]:{backend_dialect}"
            )
        self._backend = backend
        self.dialect = dialect
        self._path = Path(fixture_path)
        self._entries: dict[str, dict[str, Any]] = {}
        if self._path.exists():
            existing = load_replay_fixture(self._path)
            for digest, entry in existing["responses"].items():
                existing_dialect = str(entry.get("dialect"))
                if existing_dialect != dialect:
                    raise DecisionSchemaError(
                        f"DECISION_RECORD_FIXTURE_DIALECT_MISMATCH[{dialect}]:{existing_dialect}"
                    )
                self._entries[digest] = entry

    @property
    def inner(self) -> DecisionBackend:
        return self._backend

    def decide(self, request: DecisionRequest, deadline: Any = None) -> DecisionResponse:
        response = self._backend.decide(request, deadline=deadline)
        digest = request_digest(request)
        self._entries[digest] = recording_entry(self.dialect, response.raw)
        self.flush()
        return response

    def flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "responses": dict(sorted(self._entries.items())),
        }
        temporary = self._path.with_name("." + self._path.name + "." + uuid.uuid4().hex)
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, ensure_ascii=False, indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._path)


__all__ = [
    "REPLAY_SCHEMA_VERSION",
    "RecordingBackend",
    "ReplayBackend",
    "load_replay_fixture",
]
