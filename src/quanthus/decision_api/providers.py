"""Multi-vendor provider configuration and priority failover for Decision API.

Operators declare an ordered provider list in a versioned JSON document
(schema quanthus.decision-providers.v1).  Each entry binds one vendor
endpoint: base URL, dialect type, endpoint path, model, the environment
variable holding its API key, enablement, timeout and optional published
per-million-token prices.  Keys never enter the versioned document.

R3 hardening after the second independent review of PR #756:

* every logical decide() call -- success or failure -- discharges exactly
  one independent attempts tuple: the success response carries it in
  DecisionResponse.attempts, and every error raised out of the chain
  carries it in error.attempts.  The tuple is built locally inside the
  call, so two concurrent batches sharing one chain instance can never
  overwrite each other's billing evidence; last_attempts remains as a
  best-effort diagnostic mirror only;
* every provider inside the attempt budget leaves a ledger record:
  dispatched providers record their outcome (answer, timeout,
  late_answer, transport, http_<status>, schema_error), and providers
  that never went on the wire record outcome not_dispatched with a
  skip_reason (deadline, deterministic_stop or local_validation), which
  is positive proof of zero dispatch and zero cost for the ledger;
* usage evidence is four-state: metered (well-formed usage with
  positive tokens or cost, input and output counters present), zero
  (well-formed usage explicitly reporting zero tokens), unknown (usage
  absent, incomplete or untrustworthy -- the consumer must account
  worst case), plus certain-zero evidence when the wire was never
  reached (wire_dispatched=false) or the provider was never dispatched;
* reservation entry points validate their volume arguments: negative or
  non-integer token volumes are typed schema errors, never negative
  quotes.

R4 hardening after the third independent review of PR #756:

* a usage object whose input and output counters are both zero but
  whose declared total or cached count is positive is contradictory
  evidence, never certain-zero proof: it keeps its reported counters
  verbatim and is accounted as unknown at worst case;
* the chain applies the final delivery gate: an answer that completes
  its bookkeeping past the shared absolute deadline is rejected as
  late_answer with its usage evidence attached, never delivered;
* prices and reservation arithmetic are finite-checked end to end:
  NaN, infinite or overflowing prices, volumes and products are typed
  schema errors, never NaN/Infinity quotes or bare OverflowError
  escapes, so every computed quote is usable in a budget comparison.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, replace
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
    DecisionLateAnswerError,
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
    dispatching further providers.  Skipped providers are still visible
    in attempts as outcome not_dispatched with skip_reason deadline:
    positive ledger proof that they never went on the wire.
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
        try:
            converted = float(value)
        except (OverflowError, ValueError) as error:
            raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_PRICE{where}:{price_key}") from error
        # NaN and infinity can never participate in a budget comparison:
        # a non-finite published price is a configuration error, not a
        # quote the consumer must defend against downstream.
        if not math.isfinite(converted):
            raise DecisionSchemaError(f"DECISION_PROVIDER_SPEC_PRICE{where}:{price_key}")
        prices[price_key] = converted
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


def _usage_snapshot(usage: TokenUsage | None, *, wire_reached: bool) -> dict[str, Any]:
    """Attempt-level billing evidence in four states plus certainty detail.

    metered: the wire carried a complete usage object with positive
      tokens or a cost -- bill as reported.
    zero: the wire carried a complete usage object explicitly reporting
      zero input and output tokens and no cost -- certain zero bill.
    unknown: usage evidence is absent, incomplete or untrustworthy --
      the consumer must reserve worst case.  The detail field says why
      (no_wire_response, usage_object_absent, usage_object_empty or
      usage_incomplete).
    A failure proven to happen before any request byte was sent is
      certain zero cost (detail wire_not_reached), and a provider that
      was never dispatched is recorded through the not_dispatched
      outcome instead of this snapshot.
    """
    base = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "cost": None,
    }
    if not wire_reached:
        return {"status": "zero", "detail": "wire_not_reached", **base}
    if usage is None or not usage.present:
        detail = "no_wire_response" if usage is None else "usage_object_absent"
        return {"status": "unknown", "detail": detail, **base}
    if not usage.complete:
        detail = (
            "usage_object_empty"
            if (
                usage.input_tokens == 0
                and usage.output_tokens == 0
                and usage.total_tokens == 0
                and usage.cost is None
            )
            else "usage_incomplete"
        )
        return {
            "status": "unknown",
            "detail": detail,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "cached_tokens": usage.cached_tokens,
            "cost": usage.cost,
        }
    positive = (
        usage.input_tokens > 0
        or usage.output_tokens > 0
        or (usage.cost is not None and usage.cost > 0)
    )
    if not positive and (usage.total_tokens > 0 or usage.cached_tokens > 0):
        # both billable counters are zero yet the declared total or the
        # cached count is positive: the wire evidence contradicts itself,
        # so it is unverifiable rather than certain-zero proof.  the
        # reported counts stay verbatim for audit while the ledger
        # reserves worst case.
        return {
            "status": "unknown",
            "detail": "usage_contradictory",
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "cached_tokens": usage.cached_tokens,
            "cost": usage.cost,
        }
    return {
        "status": "metered" if positive else "zero",
        "detail": "wire_reported",
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "cached_tokens": usage.cached_tokens,
        "cost": usage.cost,
    }


def _zero_usage_snapshot(detail: str) -> dict[str, Any]:
    """Certain-zero billing evidence for providers that never dispatched."""
    return {
        "status": "zero",
        "detail": detail,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "cost": None,
    }


def _validate_volume(max_input_tokens: int, max_output_tokens: int) -> None:
    """Reservation volumes must be non-negative integers.

    A negative or fractional volume must never produce a negative or
    misleading quote: the budget gate fails closed instead.
    """
    for label, value in (
        ("max_input_tokens", max_input_tokens),
        ("max_output_tokens", max_output_tokens),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DecisionSchemaError(f"DECISION_RESERVATION_VOLUME:{label}")


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
    _validate_volume(max_input_tokens, max_output_tokens)
    if spec.input_price_usd_per_mtok is None or spec.output_price_usd_per_mtok is None:
        return None
    # the versioned JSON loader already rejects negative, boolean,
    # non-finite and overflowing prices, but DecisionProviderSpec is a
    # public constructor: code-built specs reach this quote path without
    # that validation.  a negative or non-finite price must never form a
    # quote, so the reservation fails closed here as well.  zero is a
    # legitimate declared-free price.
    for label, price in (
        ("input_price_usd_per_mtok", spec.input_price_usd_per_mtok),
        ("output_price_usd_per_mtok", spec.output_price_usd_per_mtok),
    ):
        converted = None
        if isinstance(price, (int, float)) and not isinstance(price, bool):
            try:
                converted = float(price)
            except (OverflowError, ValueError):
                converted = None
        if converted is None or not math.isfinite(converted) or converted < 0:
            raise DecisionSchemaError(f"DECISION_RESERVATION_PRICE:{label}")
    try:
        total = (
            max_input_tokens * spec.input_price_usd_per_mtok
            + max_output_tokens * spec.output_price_usd_per_mtok
        ) / 1_000_000.0
    except (OverflowError, ValueError) as error:
        raise DecisionSchemaError("DECISION_RESERVATION_OVERFLOW") from error
    # a quote outside the finite range cannot participate in any budget
    # comparison, so the reservation fails closed instead of returning
    # NaN or Infinity as accounting evidence.
    if not math.isfinite(total):
        raise DecisionSchemaError("DECISION_RESERVATION_OVERFLOW")
    return total


def chain_worst_case_usd(
    chain: ProviderChain, max_input_tokens: int, max_output_tokens: int
) -> dict[str, Any]:
    """Per-provider and total worst-case reservation for one logical batch.

    The total assumes every enabled provider up to max_attempts fails over
    and each attempt bills the full declared volume.  Entries with
    undeclared prices report None; the consumer must treat them as
    unbounded and refuse online dispatch for that chain configuration.
    """
    _validate_volume(max_input_tokens, max_output_tokens)
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
    if isinstance(error, DecisionLateAnswerError):
        return "late_answer"
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
    the answering model is visible verbatim in the response and in the
    attempt records.

    Credential contract: each logical decide() call builds its attempt
    records in local state and discharges exactly one independent tuple
    -- attached to the returned response as response.attempts, or to any
    raised error as error.attempts.  Concurrent calls sharing this
    instance never observe each other's records.  last_attempts mirrors
    the most recent completed call for diagnostics only; the ledger must
    read the per-call tuple, never the mirror.
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

    def _fill_not_dispatched(
        self,
        attempts: list[AttemptRecord],
        fill_from: int,
        candidates: tuple[tuple[DecisionProviderSpec, DecisionBackend], ...],
        skip_reason: str,
    ) -> None:
        """Complete the ledger for in-budget providers that never dispatched.

        outcome not_dispatched with usage detail never_dispatched is
        positive proof of zero wire use and zero cost for every provider
        the chain considered but did not call.
        """
        for spec, backend in candidates[fill_from:]:
            attempts.append(
                {
                    "provider_id": spec.id,
                    "model": spec.model,
                    "dialect": backend.dialect,
                    "outcome": "not_dispatched",
                    "skip_reason": skip_reason,
                    "elapsed_seconds": 0.0,
                    "usage": _zero_usage_snapshot("never_dispatched"),
                }
            )

    def decide(
        self, request: DecisionRequest, deadline: DecisionDeadline | None = None
    ) -> DecisionResponse:
        attempts: list[AttemptRecord] = []
        self.last_attempts = ()
        depth = len(self._backends) if self._max_attempts is None else self._max_attempts
        candidates = self._backends[:depth]
        try:
            request.validate()
        except DecisionSchemaError as error:
            error.wire_dispatched = False
            # nothing was validated onto the wire at all: every in-budget
            # provider is certain zero cost, and the error itself carries
            # the completed credential tuple.
            self._fill_not_dispatched(attempts, 0, candidates, "local_validation")
            error.attempts = tuple(attempts)
            self.last_attempts = tuple(attempts)
            raise
        last_error: DecisionError | None = None
        deadline_stop = False
        fill_from = 0
        skip_reason = "deadline"
        for position, (spec, backend) in enumerate(candidates):
            fill_from = position
            if deadline is not None and deadline.remaining() < MIN_ATTEMPT_SLIVER_SECONDS:
                deadline_stop = True
                skip_reason = "deadline"
                break
            attempt_request = (
                request if request.model == spec.model else _remodel(request, spec.model)
            )
            started = time.monotonic()
            try:
                response = backend.decide(attempt_request, deadline=deadline)
            except DecisionSchemaError as error:
                elapsed = time.monotonic() - started
                if error.wire_dispatched:
                    # post-2xx parse failures are deterministic; the wire
                    # was reached but the response is untrusted, so usage
                    # evidence is taken from whatever the wire offered
                    # (the backend attaches leniently parsed usage to the
                    # error) and no failover happens.
                    attempts.append(
                        self._record(spec, backend, "schema_error", elapsed, error.usage, True)
                    )
                else:
                    # local validation rejected the request before any
                    # byte was sent: zero-cost proof for this provider.
                    attempts.append(
                        {
                            "provider_id": spec.id,
                            "model": spec.model,
                            "dialect": backend.dialect,
                            "outcome": "not_dispatched",
                            "skip_reason": "local_validation",
                            "elapsed_seconds": round(elapsed, 3),
                            "usage": _zero_usage_snapshot("never_dispatched"),
                        }
                    )
                self._fill_not_dispatched(attempts, position + 1, candidates, "deterministic_stop")
                error.attempts = tuple(attempts)
                self.last_attempts = tuple(attempts)
                raise
            except (DecisionTransportError, DecisionHttpError) as error:
                elapsed = time.monotonic() - started
                usage = getattr(error, "usage", None)
                attempts.append(
                    self._record(
                        spec, backend, _outcome_of(error), elapsed, usage, error.wire_dispatched
                    )
                )
                last_error = error
                if not _is_failover_class(error):
                    self._fill_not_dispatched(
                        attempts, position + 1, candidates, "deterministic_stop"
                    )
                    error.attempts = tuple(attempts)
                    self.last_attempts = tuple(attempts)
                    raise
                fill_from = position + 1
                continue
            elapsed = time.monotonic() - started
            attempts.append(
                self._record(
                    spec,
                    backend,
                    "answer",
                    elapsed,
                    response.usage,
                    True,
                )
            )
            self._fill_not_dispatched(attempts, position + 1, candidates, "answered_upstream")
            final = tuple(attempts)
            self.last_attempts = final
            # final delivery gate re-checks the shared clock after the
            # answer and every per-attempt credential are assembled: a
            # bookkeeping stall that spans the deadline must reject the
            # answer here, exactly as a backend-side late parse does.
            # the vendor did process the request, so the rejection
            # carries the wire usage and the full credential tuple, and
            # the answering attempt is recorded as late_answer.
            late = deadline is not None and deadline.exceeded()
            if late:
                # the answering record sits exactly at this position; the
                # trailing entries are never-dispatched fills, which stay
                # as they are.
                final = tuple(
                    {**record, "outcome": "late_answer"} if index == position else record
                    for index, record in enumerate(final)
                )
                self.last_attempts = final
                late_error = DecisionLateAnswerError("DECISION_LATE_ANSWER", usage=response.usage)
                late_error.attempts = final
                raise late_error
            return replace(response, attempts=final)
        self._fill_not_dispatched(attempts, fill_from, candidates, skip_reason)
        final = tuple(attempts)
        self.last_attempts = final
        raise DecisionProviderExhaustedError(final, deadline_stopped=deadline_stop) from last_error

    @staticmethod
    def _record(
        spec: DecisionProviderSpec,
        backend: DecisionBackend,
        outcome: str,
        elapsed: float,
        usage: TokenUsage | None,
        wire_reached: bool,
    ) -> AttemptRecord:
        snapshot = _usage_snapshot(usage, wire_reached=wire_reached)
        if usage is None and wire_reached and outcome not in ("timeout", "transport"):
            # some response bytes did assemble -- an http_<status> answer,
            # a post-2xx schema failure or a late answer -- but the wire
            # carried no usable usage object (absent, delayed past budget
            # or unparseable).  distinguish that from a timeout or reset
            # where no response ever assembled: the accounting is unknown
            # worst-case either way, but the ledger detail must describe
            # the wire truthfully.
            snapshot["detail"] = "usage_object_absent"
        return {
            "provider_id": spec.id,
            "model": spec.model,
            "dialect": backend.dialect,
            "outcome": outcome,
            "elapsed_seconds": round(elapsed, 3),
            "usage": snapshot,
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
