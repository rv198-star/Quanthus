"""Multi-vendor provider configuration and priority failover for Decision API.

Operators declare an ordered provider list in a versioned JSON document
(schema quanthus.decision-providers.v1).  Each entry binds one vendor
endpoint: base URL, dialect type, endpoint path, model, the environment
variable holding its API key, enablement and timeout.  Keys never enter the
versioned document; only the names of the environment variables that carry
them.

PriorityBackend dispatches each canonical request to the providers in
configuration order.  Failover is deliberately narrower than a retry:

* failover triggers are transport failure (DNS/TCP/TLS/reset/malformed
  body), timeout, and transient HTTP statuses {408, 409, 425, 429, 500,
  502, 503, 504};
* deterministic HTTP 4xx (authentication failure, schema rejection) and
  schema errors fail closed immediately -- retrying them on another
  vendor would multiply cost without changing the outcome class;
* each provider is attempted at most once per call, so a chain of N
  providers costs at most N calls per logical request.  Consumers owning
  the <=60s/T and per-signal-day budgets must account for this worst case;
  max_attempts can cap the chain depth.

Every attempt is recorded in memory (last_attempts) with provider id,
model, dialect, outcome and elapsed seconds so audit and budget
attribution stay exact.  All enabled providers in one chain must share a
single wire dialect; mixing dialects in one chain is rejected at build
time because a replay fixture entry keys on one dialect.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.decision_api.backends import (
    DECISIONS_DIALECT_OPENAI_GA,
    DECISIONS_DIALECT_OPENROUTER_ALPHA,
    DecisionBackend,
    HttpBackendConfig,
    OpenAIDecisionsBackend,
    OpenRouterDecisionsBackend,
)
from quanthus.decision_api.types import (
    DecisionError,
    DecisionHttpError,
    DecisionRequest,
    DecisionResponse,
    DecisionSchemaError,
    DecisionTimeoutError,
    DecisionTransportError,
)

PROVIDERS_SCHEMA_VERSION = "quanthus.decision-providers.v1"

PROVIDER_TYPE_OPENAI_GA = "openai-ga"
PROVIDER_TYPE_SYSTEMONE_ALPHA = "systemone-alpha"
PROVIDER_TYPE_OPENROUTER_ALPHA = "openrouter-alpha"

_PROVIDER_TYPES = frozenset(
    {PROVIDER_TYPE_OPENAI_GA, PROVIDER_TYPE_SYSTEMONE_ALPHA, PROVIDER_TYPE_OPENROUTER_ALPHA}
)

# Transient HTTP statuses where a different vendor may legitimately succeed.
FAILOVER_HTTP_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

_SPEC_REQUIRED_KEYS = frozenset({"id", "type", "base_url", "endpoint_path", "model", "api_key_env"})
_SPEC_ALLOWED_KEYS = _SPEC_REQUIRED_KEYS | frozenset({"enabled", "timeout_seconds"})


class DecisionProviderExhaustedError(DecisionError):
    """Every enabled provider in the chain failed with a failover-class error."""

    def __init__(self, attempts: tuple[dict[str, Any], ...]) -> None:
        summary = "; ".join(
            f"{attempt['provider_id']}={attempt['outcome']}" for attempt in attempts
        )
        super().__init__(f"DECISION_PROVIDER_EXHAUSTED[{len(attempts)}]: {summary}")
        self.attempts = attempts


@dataclass(frozen=True)
class DecisionProviderSpec:
    """One vendor endpoint binding, in priority order within its chain."""

    id: str
    type: str
    base_url: str
    endpoint_path: str
    model: str
    api_key_env: str
    enabled: bool = True
    timeout_seconds: float = 20.0

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + self.endpoint_path


@dataclass(frozen=True)
class ProviderChain:
    """Ordered provider list plus dispatch policy."""

    providers: tuple[DecisionProviderSpec, ...]
    max_attempts: int | None = None

    def enabled(self) -> tuple[DecisionProviderSpec, ...]:
        return tuple(spec for spec in self.providers if spec.enabled)


def load_provider_chain(path: str | Path) -> ProviderChain:
    """Load and strictly validate one provider chain document."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise DecisionSchemaError(f"DECISION_PROVIDER_CONFIG_UNREADABLE: {error}") from error
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DecisionSchemaError("DECISION_PROVIDER_CONFIG_NOT_JSON") from error
    return parse_provider_chain(document)


def parse_provider_chain(document: Any) -> ProviderChain:
    """Validate one provider chain document already decoded from JSON."""
    if not isinstance(document, dict):
        raise DecisionSchemaError("DECISION_PROVIDER_CONFIG_SHAPE")
    if document.get("schema_version") != PROVIDERS_SCHEMA_VERSION:
        raise DecisionSchemaError("DECISION_PROVIDER_CONFIG_SCHEMA_VERSION")
    entries = document.get("providers")
    if not isinstance(entries, list) or not entries:
        raise DecisionSchemaError("DECISION_PROVIDER_CONFIG_EMPTY")
    max_attempts_raw = document.get("max_attempts")
    max_attempts: int | None = None
    if max_attempts_raw is not None:
        if (
            not isinstance(max_attempts_raw, int)
            or isinstance(max_attempts_raw, bool)
            or not 1 <= max_attempts_raw <= len(entries)
        ):
            raise DecisionSchemaError("DECISION_PROVIDER_MAX_ATTEMPTS")
        max_attempts = max_attempts_raw
    specs: list[DecisionProviderSpec] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        specs.append(_parse_provider_spec(entry, index, seen_ids))
    return ProviderChain(providers=tuple(specs), max_attempts=max_attempts)


def _parse_provider_spec(entry: Any, index: int, seen_ids: set[str]) -> DecisionProviderSpec:
    where = f"[{index}]"
    if not isinstance(entry, dict):
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_SHAPE{where}")
    unknown = set(entry) - _SPEC_ALLOWED_KEYS
    if unknown:
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_KEYS{where}:{sorted(unknown)[0]}")
    missing = _SPEC_REQUIRED_KEYS - set(entry)
    if missing:
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_MISSING{where}:{sorted(missing)[0]}")
    for key in ("id", "type", "base_url", "endpoint_path", "model", "api_key_env"):
        if not isinstance(entry[key], str) or not entry[key].strip():
            raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_FIELD{where}:{key}")
    if entry["type"] not in _PROVIDER_TYPES:
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_TYPE{where}:{entry['type']}")
    if not entry["base_url"].startswith(("https://", "http://")):
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_URL{where}")
    if not entry["endpoint_path"].startswith("/"):
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_PATH{where}")
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_ENABLED{where}")
    timeout = entry.get("timeout_seconds", 20.0)
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0.5 <= timeout <= 300.0
    ):
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_TIMEOUT{where}")
    if entry["id"] in seen_ids:
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_DUPLICATE{where}:{entry['id']}")
    seen_ids.add(entry["id"])
    return DecisionProviderSpec(
        id=entry["id"],
        type=entry["type"],
        base_url=entry["base_url"],
        endpoint_path=entry["endpoint_path"],
        model=entry["model"],
        api_key_env=entry["api_key_env"],
        enabled=enabled,
        timeout_seconds=float(timeout),
    )


def build_provider_backends(
    chain: ProviderChain,
    *,
    transport: Any = None,
    environ: dict[str, str] | None = None,
) -> tuple[tuple[DecisionProviderSpec, DecisionBackend], ...]:
    """Construct backends for every enabled provider, failing closed on gaps.

    A missing key environment variable or a mixed-dialect chain is a
    configuration error, not a runtime failover case: it is raised before
    any network use so the defect surfaces at construction time.
    """
    env = os.environ if environ is None else environ
    enabled = chain.enabled()
    if not enabled:
        raise DecisionSchemaError("DECISION_PROVIDER_CHAIN_EMPTY")
    dialects = {_dialect_for_type(spec.type) for spec in enabled}
    if len(dialects) != 1:
        raise DecisionSchemaError("DECISION_PROVIDER_DIALECT_MIXED")
    built: list[tuple[DecisionProviderSpec, DecisionBackend]] = []
    for spec in enabled:
        key = env.get(spec.api_key_env)
        if not key:
            raise DecisionSchemaError(
                f"DECISION_PROVIDER_KEY_MISSING[{spec.id}]:{spec.api_key_env}"
            )
        config = HttpBackendConfig(
            url=spec.url,
            api_key=key,
            timeout_seconds=spec.timeout_seconds,
        )
        backend_cls = (
            OpenAIDecisionsBackend
            if _dialect_for_type(spec.type) == DECISIONS_DIALECT_OPENAI_GA
            else OpenRouterDecisionsBackend
        )
        backend = (
            backend_cls(config, transport=transport)
            if transport is not None
            else backend_cls(config)
        )
        built.append((spec, backend))
    return tuple(built)


def _dialect_for_type(provider_type: str) -> str:
    if provider_type == PROVIDER_TYPE_OPENAI_GA:
        return DECISIONS_DIALECT_OPENAI_GA
    return DECISIONS_DIALECT_OPENROUTER_ALPHA


def build_priority_backend(
    chain: ProviderChain,
    *,
    transport: Any = None,
    environ: dict[str, str] | None = None,
) -> PriorityBackend:
    """Construct the failover backend for one validated chain."""
    backends = build_provider_backends(chain, transport=transport, environ=environ)
    return PriorityBackend(backends, max_attempts=chain.max_attempts)


def _outcome_of(error: BaseException) -> str:
    if isinstance(error, DecisionTimeoutError):
        return "timeout"
    if isinstance(error, DecisionTransportError):
        return "transport"
    if isinstance(error, DecisionHttpError):
        return f"http_{error.status}"
    return type(error).__name__


def _is_failover_class(error: BaseException) -> bool:
    if isinstance(error, DecisionTransportError):
        return True
    return isinstance(error, DecisionHttpError) and error.status in FAILOVER_HTTP_STATUSES


class PriorityBackend(DecisionBackend):
    """Dispatch one canonical request across an ordered provider chain.

    The chain shares one dialect (enforced at build time), so the static
    dialect attribute remains exact for replay recording.  Failover
    substitutes the provider, never the question content: the canonical
    request's model field is overridden per attempt with the provider's
    configured model, and the answering model is visible verbatim in the
    response's model field and in last_attempts.
    """

    def __init__(
        self,
        backends: tuple[tuple[DecisionProviderSpec, DecisionBackend], ...],
        *,
        max_attempts: int | None = None,
    ) -> None:
        if not backends:
            raise DecisionSchemaError("DECISION_PROVIDER_CHAIN_EMPTY")
        dialects = {backend.dialect for _, backend in backends}
        if len(dialects) != 1:
            raise DecisionSchemaError("DECISION_PROVIDER_DIALECT_MIXED")
        self._backends = backends
        self._max_attempts = max_attempts
        self.dialect = backends[0][1].dialect
        self.last_attempts: tuple[dict[str, Any], ...] = ()

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        request.validate()
        attempts: list[dict[str, Any]] = []
        budget = len(self._backends) if self._max_attempts is None else self._max_attempts
        last_error: BaseException | None = None
        for spec, backend in self._backends[:budget]:
            attempt_request = (
                request if request.model == spec.model else _remodel(request, spec.model)
            )
            started = time.monotonic()
            try:
                response = backend.decide(attempt_request)
            except DecisionSchemaError:
                self.last_attempts = tuple(attempts)
                raise
            except (DecisionTransportError, DecisionHttpError) as error:
                attempts.append(
                    {
                        "provider_id": spec.id,
                        "model": spec.model,
                        "dialect": backend.dialect,
                        "outcome": _outcome_of(error),
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    }
                )
                last_error = error
                if not _is_failover_class(error):
                    self.last_attempts = tuple(attempts)
                    raise
                continue
            attempts.append(
                {
                    "provider_id": spec.id,
                    "model": spec.model,
                    "dialect": backend.dialect,
                    "outcome": "answer",
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
            self.last_attempts = tuple(attempts)
            return response
        self.last_attempts = tuple(attempts)
        assert last_error is not None  # at least one backend always runs
        raise DecisionProviderExhaustedError(tuple(attempts)) from last_error


def _remodel(request: DecisionRequest, model: str) -> DecisionRequest:
    """Copy one canonical request with a substituted model field."""
    return DecisionRequest(model=model, input=request.input, questions=request.questions)


__all__ = [
    "FAILOVER_HTTP_STATUSES",
    "PROVIDERS_SCHEMA_VERSION",
    "PROVIDER_TYPE_OPENAI_GA",
    "PROVIDER_TYPE_OPENROUTER_ALPHA",
    "PROVIDER_TYPE_SYSTEMONE_ALPHA",
    "DecisionProviderExhaustedError",
    "DecisionProviderSpec",
    "PriorityBackend",
    "ProviderChain",
    "build_priority_backend",
    "build_provider_backends",
    "load_provider_chain",
    "parse_provider_chain",
]
