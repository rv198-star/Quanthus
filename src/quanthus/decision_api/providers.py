"""Multi-vendor provider configuration and priority failover for Decision API.

Operators declare an ordered provider list in a versioned JSON document
(schema quanthus.decision-providers.v1).  Each entry binds one vendor
endpoint: base URL, dialect type, endpoint path, model, the environment
variable holding its API key, enablement, timeout and optional published
per-million-token prices.  Keys never enter the versioned document.

R2 hardening after the independent cold review of PR #756:

* the dialect type and endpoint path binding is validated: openai-ga
  entries must target the GA decisions path, alpha entries an alpha
  decisions path, so retargeting an entry at a foreign dialect fails at
  configuration time; provider base URLs must be HTTPS because every
  request carries a bearer credential;
* failover is status-first and restricted to the approved transient set
  {408, 409, 425, 429, 500, 502, 503, 504}: deterministic 4xx and schema
  errors never switch providers, whatever the response body looks like;
* callers may pass one absolute DecisionDeadline reused across batches;
  each attempt truncates its socket budget to the remaining time and the
  chain stops dispatching once the remainder is below the useful sliver;
* every dispatched attempt leaves evidence: provider, model, dialect,
  outcome, elapsed seconds, a usage snapshot when the wire offered one
  (including billed non-2xx failures), and a usage_status of metered,
  unknown or none.  Failed attempts without wire usage count as unknown
  usage and must be reserved at worst case by the consumer;
* last_attempts is reset at the start of every decide() call, and
  post-2xx schema failures are recorded before propagating, so a parse
  failure can no longer erase the attempt history or leak stale records
  across calls.
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
    TransportCallable,
    UrllibJsonTransport,
)
from quanthus.decision_api.types import (
    DecisionDeadline,
    DecisionError,
    DecisionHttpError,
    DecisionRequest,
    DecisionResponse,
    DecisionSchemaError,
    DecisionTimeoutError,
    DecisionTransportError,
    TokenUsage,
)

PROVIDERS_SCHEMA_VERSION = "quanthus.decision-providers.v1"

PROVIDER_TYPE_OPENAI_GA = "openai-ga"
PROVIDER_TYPE_SYSTEMONE_ALPHA = "systemone-alpha"
PROVIDER_TYPE_OPENROUTER_ALPHA = "openrouter-alpha"

_PROVIDER_TYPES = frozenset(
    {PROVIDER_TYPE_OPENAI_GA, PROVIDER_TYPE_SYSTEMONE_ALPHA, PROVIDER_TYPE_OPENROUTER_ALPHA}
)

# Approved transient statuses: only these HTTP failures may switch provider.
# Everything else in the 4xx range is deterministic for every vendor and
# fails closed immediately; 5xx statuses outside this set are unusual enough
# to surface rather than multiply cost across the chain.
FAILOVER_HTTP_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# Versioned binding between the declared dialect type and the endpoint path
# shape.  Retargeting a GA entry at an alpha path (or vice versa) is a
# configuration error, not a runtime surprise.
TYPE_ENDPOINT_PATHS = {
    PROVIDER_TYPE_OPENAI_GA: ("/v1/decisions",),
    PROVIDER_TYPE_SYSTEMONE_ALPHA: ("/v1/systemone",),
    PROVIDER_TYPE_OPENROUTER_ALPHA: ("/api/alpha/decisions",),
}

# Minimum remaining budget worth dispatching a new attempt with.
MIN_ATTEMPT_SLIVER_SECONDS = 0.25

_SPEC_REQUIRED_KEYS = frozenset({"id", "type", "base_url", "endpoint_path", "model", "api_key_env"})
_SPEC_ALLOWED_KEYS = _SPEC_REQUIRED_KEYS | frozenset(
    {"enabled", "timeout_seconds", "input_price_usd_per_mtok", "output_price_usd_per_mtok"}
)

AttemptRecord = dict[str, Any]


class DecisionProviderExhaustedError(DecisionError):
    """Every dispatched provider in the chain failed with a failover-class error.

    deadline_stopped is true when the shared absolute deadline prevented
    dispatching further providers; those skipped providers never went on
    the wire and therefore have no attempt record.
    """

    def __init__(self, attempts: tuple[AttemptRecord, ...], deadline_stopped: bool = False) -> None:
        parts = []
        for attempt in attempts:
            parts.append(str(attempt.get("provider_id")) + "=" + str(attempt.get("outcome")))
        suffix = "; deadline_stopped" if deadline_stopped else ""
        super().__init__(
            "DECISION_PROVIDER_EXHAUSTED[" + str(len(attempts)) + "]: " + "; ".join(parts) + suffix
        )
        self.attempts = attempts
        self.deadline_stopped = deadline_stopped


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
    input_price_usd_per_mtok: float | None = None
    output_price_usd_per_mtok: float | None = None

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
    max_attempts = None
    if max_attempts_raw is not None:
        if (
            not isinstance(max_attempts_raw, int)
            or isinstance(max_attempts_raw, bool)
            or not 1 <= max_attempts_raw <= len(entries)
        ):
            raise DecisionSchemaError("DECISION_PROVIDER_MAX_ATTEMPTS")
        max_attempts = max_attempts_raw
    specs = []
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
    spec_type = str(entry["type"])
    spec_id = str(entry["id"])
    if spec_type not in _PROVIDER_TYPES:
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_TYPE{where}:{spec_type}")
    if not entry["base_url"].startswith("https://"):
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_URL{where}")
    if not entry["endpoint_path"].startswith("/"):
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_PATH{where}")
    allowed_paths = TYPE_ENDPOINT_PATHS[spec_type]
    if entry["endpoint_path"] not in allowed_paths:
        raise DecisionSchemaError(f"DECISION_PROVIDER_TYPE_PATH_MISMATCH{where}")
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
    prices: dict[str, float | None] = {}
    for price_key in ("input_price_usd_per_mtok", "output_price_usd_per_mtok"):
        value = entry.get(price_key)
        if value is None:
            prices[price_key] = None
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_PRICE{where}:{price_key}")
        prices[price_key] = float(value)
    if spec_id in seen_ids:
        raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_DUPLICATE{where}:{spec_id}")
    seen_ids.add(spec_id)
    return DecisionProviderSpec(
        id=spec_id,
        type=spec_type,
        base_url=str(entry["base_url"]),
        endpoint_path=str(entry["endpoint_path"]),
        model=str(entry["model"]),
        api_key_env=str(entry["api_key_env"]),
        enabled=enabled,
        timeout_seconds=float(timeout),
        input_price_usd_per_mtok=prices["input_price_usd_per_mtok"],
        output_price_usd_per_mtok=prices["output_price_usd_per_mtok"],
    )


def build_provider_backends(
    chain: ProviderChain,
    *,
    transport: TransportCallable | UrllibJsonTransport | None = None,
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
    built = []
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
    transport: TransportCallable | UrllibJsonTransport | None = None,
    environ: dict[str, str] | None = None,
) -> PriorityBackend:
    """Construct the failover backend for one validated chain."""
    backends = build_provider_backends(chain, transport=transport, environ=environ)
    return PriorityBackend(backends, max_attempts=chain.max_attempts)


def _usage_snapshot(usage: TokenUsage | None) -> dict[str, Any]:
    """Attempt-level billing evidence.

    metered means the wire carried a positive token count or a cost;
    none means the wire carried a well-formed empty usage object (no
    billing event); unknown means no trustworthy usage existed -- a
    failure before or during the wire exchange -- and the consumer must
    reserve worst case.
    """
    if usage is not None and (
        usage.input_tokens > 0 or usage.output_tokens > 0 or usage.cost is not None
    ):
        return {
            "status": "metered",
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "cached_tokens": usage.cached_tokens,
            "cost": usage.cost,
        }
    if usage is not None:
        return {
            "status": "none",
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "cost": None,
        }
    return {
        "status": "unknown",
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "cost": None,
    }


def reserve_worst_case_usd(
    spec: DecisionProviderSpec, max_input_tokens: int, max_output_tokens: int
) -> float | None:
    """Worst-case reservation for one attempt at declared volumes.

    Returns None when either price is undeclared: the consumer must treat
    an unpriced vendor as unreservable and block online dispatch until the
    price is pinned in configuration.  Zero output tokens is the common
    case for decision endpoints; output volume is still parameterized
    because jev emits nonzero output tokens.
    """
    if spec.input_price_usd_per_mtok is None or spec.output_price_usd_per_mtok is None:
        return None
    return (
        max_input_tokens * spec.input_price_usd_per_mtok
        + max_output_tokens * spec.output_price_usd_per_mtok
    ) / 1_000_000.0


def chain_worst_case_usd(
    chain: ProviderChain, max_input_tokens: int, max_output_tokens: int
) -> dict[str, Any]:
    """Per-provider and total worst-case reservation for one logical batch.

    The total assumes every enabled provider up to max_attempts fails over
    and each attempt bills the full declared volume.  Entries with
    undeclared prices report None; the consumer must treat them as
    unbounded and refuse online dispatch for that chain configuration.
    """
    enabled = chain.enabled()
    budget = len(enabled) if chain.max_attempts is None else min(chain.max_attempts, len(enabled))
    rows = []
    total = 0.0
    complete = True
    for spec in enabled[:budget]:
        value = reserve_worst_case_usd(spec, max_input_tokens, max_output_tokens)
        rows.append({"provider_id": spec.id, "worst_case_usd": value})
        if value is None:
            complete = False
        else:
            total += value
    return {
        "per_provider": rows,
        "total_worst_case_usd": total if complete else None,
        "complete": complete,
        "attempts_budgeted": budget,
    }


def _outcome_of(error: DecisionError) -> str:
    if isinstance(error, DecisionTimeoutError):
        return "timeout"
    if isinstance(error, DecisionTransportError):
        return "transport"
    if isinstance(error, DecisionHttpError):
        return f"http_{error.status}"
    return type(error).__name__


def _is_failover_class(error: DecisionError) -> bool:
    if isinstance(error, DecisionTransportError):
        return True
    return isinstance(error, DecisionHttpError) and error.status in FAILOVER_HTTP_STATUSES


class PriorityBackend(DecisionBackend):
    """Dispatch one canonical request across an ordered provider chain.

    The chain shares one dialect (enforced at build time), so the static
    dialect attribute remains exact for replay recording.  Failover
    substitutes the provider, never the question content: the canonical
    model field is overridden per attempt with the provider model, and
    the answering model is visible verbatim in the response and in
    last_attempts.
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
        self.last_attempts: tuple[AttemptRecord, ...] = ()

    def decide(
        self, request: DecisionRequest, deadline: DecisionDeadline | None = None
    ) -> DecisionResponse:
        request.validate()
        self.last_attempts = ()
        attempts: list[AttemptRecord] = []
        budget = len(self._backends) if self._max_attempts is None else self._max_attempts
        last_error: DecisionError | None = None
        deadline_stop = False
        for spec, backend in self._backends[:budget]:
            if deadline is not None and deadline.remaining() < MIN_ATTEMPT_SLIVER_SECONDS:
                # No dispatch happens here, so no attempt record is made:
                # only actual wire calls leave evidence, and skipped
                # providers are visible through deadline_stopped.
                deadline_stop = True
                break
            attempt_request = (
                request if request.model == spec.model else _remodel(request, spec.model)
            )
            started = time.monotonic()
            try:
                response = backend.decide(attempt_request, deadline=deadline)
            except DecisionSchemaError:
                # post-2xx parse failures are deterministic; record the
                # attempt (usage unknown: the wire shape was not trusted)
                # before failing closed, and never switch providers.
                attempts.append(
                    self._record(spec, backend, "schema_error", time.monotonic() - started, None)
                )
                self.last_attempts = tuple(attempts)
                raise
            except (DecisionTransportError, DecisionHttpError) as error:
                elapsed = time.monotonic() - started
                usage = error.usage if isinstance(error, DecisionHttpError) else None
                attempts.append(self._record(spec, backend, _outcome_of(error), elapsed, usage))
                last_error = error
                if not _is_failover_class(error):
                    self.last_attempts = tuple(attempts)
                    raise
                continue
            elapsed = time.monotonic() - started
            attempts.append(self._record(spec, backend, "answer", elapsed, response.usage))
            self.last_attempts = tuple(attempts)
            return response
        self.last_attempts = tuple(attempts)
        raise DecisionProviderExhaustedError(
            tuple(attempts), deadline_stopped=deadline_stop
        ) from last_error

    @staticmethod
    def _record(
        spec: DecisionProviderSpec,
        backend: DecisionBackend,
        outcome: str,
        elapsed: float,
        usage: TokenUsage | None,
    ) -> AttemptRecord:
        return {
            "provider_id": spec.id,
            "model": spec.model,
            "dialect": backend.dialect,
            "outcome": outcome,
            "elapsed_seconds": round(elapsed, 3),
            "usage": _usage_snapshot(usage),
        }


def _remodel(request: DecisionRequest, model: str) -> DecisionRequest:
    """Copy one canonical request with a substituted model field."""
    return DecisionRequest(model=model, input=request.input, questions=request.questions)


__all__ = [
    "FAILOVER_HTTP_STATUSES",
    "MIN_ATTEMPT_SLIVER_SECONDS",
    "PROVIDERS_SCHEMA_VERSION",
    "PROVIDER_TYPE_OPENAI_GA",
    "PROVIDER_TYPE_OPENROUTER_ALPHA",
    "PROVIDER_TYPE_SYSTEMONE_ALPHA",
    "TYPE_ENDPOINT_PATHS",
    "AttemptRecord",
    "DecisionProviderExhaustedError",
    "DecisionProviderSpec",
    "PriorityBackend",
    "ProviderChain",
    "build_priority_backend",
    "build_provider_backends",
    "chain_worst_case_usd",
    "load_provider_chain",
    "parse_provider_chain",
    "reserve_worst_case_usd",
]
