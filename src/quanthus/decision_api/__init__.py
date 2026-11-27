"""Unified System One decision-model adapter for Quanthus (issue #755).

Consumers program against one canonical schema -- the OpenAI Decisions API
GA dialect -- and the adapter translates it onto whatever backend is
reachable:

* OpenAIDecisionsBackend serves gateways implementing the GA dialect
  natively (verified live against google/gemini-decision-1 on gw.72live.com
  on 2026-11-27).
* OpenRouterDecisionsBackend translates to and from the OpenRouter alpha
  dialect (state field, question dictionaries, noul, criteria) used by
  typesafe/jev-* models on OpenRouter.
* ReplayBackend answers from sealed fixtures so offline replay, contract
  tests and regression harnesses perform zero network calls at zero cost;
  RecordingBackend produces those fixtures during authorized probes.

Hard rules honoured by this package:

* no automatic retries -- the caller owns budgeting and re-dispatch;
* one strict wall-clock timeout per request, fail-closed on transport or
  schema violations;
* consumers must strip account positions, order intents and monetary
  details before building inputs -- the adapter never inspects or adds
  account data itself.
"""

from quanthus.decision_api.backends import (
    DECISIONS_DIALECT_OPENAI_GA,
    DECISIONS_DIALECT_OPENROUTER_ALPHA,
    DecisionBackend,
    HttpBackendConfig,
    OpenAIDecisionsBackend,
    OpenRouterDecisionsBackend,
    TransportBudget,
    UrllibJsonTransport,
)
from quanthus.decision_api.providers import (
    PROVIDERS_SCHEMA_VERSION,
    DecisionProviderExhaustedError,
    DecisionProviderSpec,
    PriorityBackend,
    ProviderChain,
    build_priority_backend,
    build_provider_backends,
    chain_worst_case_usd,
    load_provider_chain,
    parse_provider_chain,
    reserve_worst_case_usd,
)
from quanthus.decision_api.replay import (
    REPLAY_SCHEMA_VERSION,
    RecordingBackend,
    ReplayBackend,
    load_replay_fixture,
)
from quanthus.decision_api.types import (
    ChoiceAnswer,
    ChoiceOption,
    ChoiceQuestion,
    DecisionAnswer,
    DecisionDeadline,
    DecisionError,
    DecisionHttpError,
    DecisionLateAnswerError,
    DecisionQuestion,
    DecisionRefusal,
    DecisionRequest,
    DecisionResponse,
    DecisionSchemaError,
    DecisionTimeoutError,
    DecisionTransportError,
    PredicateAnswer,
    PredicateQuestion,
    ScoreAnswer,
    ScoreLevel,
    ScoreQuestion,
    TokenUsage,
    request_digest,
)

__all__ = [
    "ChoiceAnswer",
    "ChoiceOption",
    "ChoiceQuestion",
    "DECISIONS_DIALECT_OPENAI_GA",
    "DECISIONS_DIALECT_OPENROUTER_ALPHA",
    "PROVIDERS_SCHEMA_VERSION",
    "DecisionAnswer",
    "DecisionBackend",
    "DecisionDeadline",
    "DecisionError",
    "DecisionHttpError",
    "DecisionLateAnswerError",
    "DecisionProviderExhaustedError",
    "DecisionProviderSpec",
    "DecisionQuestion",
    "DecisionRefusal",
    "DecisionRequest",
    "DecisionResponse",
    "DecisionSchemaError",
    "DecisionTimeoutError",
    "DecisionTransportError",
    "HttpBackendConfig",
    "OpenAIDecisionsBackend",
    "OpenRouterDecisionsBackend",
    "PredicateAnswer",
    "PredicateQuestion",
    "PriorityBackend",
    "ProviderChain",
    "REPLAY_SCHEMA_VERSION",
    "RecordingBackend",
    "ReplayBackend",
    "ScoreAnswer",
    "ScoreLevel",
    "ScoreQuestion",
    "TokenUsage",
    "TransportBudget",
    "UrllibJsonTransport",
    "build_priority_backend",
    "build_provider_backends",
    "chain_worst_case_usd",
    "load_provider_chain",
    "load_replay_fixture",
    "parse_provider_chain",
    "reserve_worst_case_usd",
    "request_digest",
]
