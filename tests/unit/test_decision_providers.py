"""Offline contract tests for the multi-vendor provider chain (no network)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_decision_api import GA_RESPONSE_BODY, sample_request

from quanthus.decision_api import (
    ChoiceAnswer,
    DecisionHttpError,
    DecisionProviderExhaustedError,
    DecisionSchemaError,
    DecisionTimeoutError,
    DecisionTransportError,
    PredicateAnswer,
    RecordingBackend,
    ReplayBackend,
    build_priority_backend,
    build_provider_backends,
    dialect_parser,
    load_provider_chain,
    parse_provider_chain,
)
from quanthus.decision_api.backends import DECISIONS_DIALECT_OPENAI_GA
from quanthus.decision_api.providers import (
    PROVIDERS_SCHEMA_VERSION,
    DecisionProviderSpec,
    ProviderChain,
)


def spec(**overrides) -> DecisionProviderSpec:
    payload = {
        "id": "p1",
        "type": "openai-ga",
        "base_url": "https://one.example.test",
        "endpoint_path": "/v1/decisions",
        "model": "model-one",
        "api_key_env": "KEY_ONE",
    }
    payload.update(overrides)
    return DecisionProviderSpec(**payload)


def document(*entries, **overrides) -> dict:
    doc = {
        "schema_version": PROVIDERS_SCHEMA_VERSION,
        "providers": list(entries),
    }
    doc.update(overrides)
    return doc


def entry(id_: str = "p1", **overrides) -> dict:
    base = {
        "id": id_,
        "type": "openai-ga",
        "base_url": "https://one.example.test",
        "endpoint_path": "/v1/decisions",
        "model": "model-one",
        "api_key_env": "KEY_ONE",
    }
    base.update(overrides)
    return base


def scripted_transport(script):
    """Transport dispatching on (url, payload-model) with a call log."""

    def post(url, payload, headers, timeout):
        key = (url, payload.get("model"))
        post.calls.append({"url": url, "payload": payload, "timeout": timeout})
        behavior = script[key]
        if isinstance(behavior, BaseException):
            raise behavior
        status, body = behavior
        return status, body

    post.calls = []
    return post


TWO_PROVIDER_CHAIN = ProviderChain(
    providers=(
        spec(id="first", base_url="https://first.example.test", model="model-first"),
        spec(
            id="second",
            base_url="https://second.example.test",
            model="model-second",
            api_key_env="KEY_TWO",
        ),
    )
)

FIRST_URL = "https://first.example.test/v1/decisions"
SECOND_URL = "https://second.example.test/v1/decisions"
ENV = {"KEY_ONE": "secret-one", "KEY_TWO": "secret-two"}


class ProviderConfigValidationTests(unittest.TestCase):
    def test_repository_default_config_loads(self) -> None:
        chain = load_provider_chain(Path("config/decision-providers.v1.json"))
        self.assertEqual(len(chain.providers), 4)
        enabled = chain.enabled()
        self.assertEqual([s.id for s in enabled], [s.id for s in chain.providers[:3]])
        self.assertEqual(enabled[0].url, "https://gw.72live.com/v1/decisions")
        self.assertEqual(chain.max_attempts, 3)

    def test_schema_version_enforced(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "SCHEMA_VERSION"):
            parse_provider_chain(document(entry(), schema_version="other"))

    def test_unknown_key_rejected(self) -> None:
        bad = entry()
        bad["notes"] = "no comments in json"
        with self.assertRaisesRegex(DecisionSchemaError, "SPEC_KEYS"):
            parse_provider_chain(document(bad))

    def test_missing_required_key_rejected(self) -> None:
        bad = entry()
        del bad["api_key_env"]
        with self.assertRaisesRegex(DecisionSchemaError, "SPEC_MISSING"):
            parse_provider_chain(document(bad))

    def test_duplicate_id_rejected(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "SPEC_DUPLICATE"):
            parse_provider_chain(document(entry("dup"), entry("dup")))

    def test_type_url_path_timeout_enabled_validation(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "SPEC_TYPE"):
            parse_provider_chain(document(entry(type="bogus")))
        with self.assertRaisesRegex(DecisionSchemaError, "SPEC_URL"):
            parse_provider_chain(document(entry(base_url="ftp://x")))
        with self.assertRaisesRegex(DecisionSchemaError, "SPEC_PATH"):
            parse_provider_chain(document(entry(endpoint_path="v1/decisions")))
        with self.assertRaisesRegex(DecisionSchemaError, "SPEC_TIMEOUT"):
            parse_provider_chain(document(entry(timeout_seconds=900.0)))
        with self.assertRaisesRegex(DecisionSchemaError, "SPEC_ENABLED"):
            parse_provider_chain(document(entry(enabled="yes")))
        with self.assertRaisesRegex(DecisionSchemaError, "MAX_ATTEMPTS"):
            parse_provider_chain(document(entry(), max_attempts=99))

    def test_empty_or_shape_invalid(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "EMPTY"):
            parse_provider_chain(document())
        with self.assertRaisesRegex(DecisionSchemaError, "SHAPE"):
            parse_provider_chain([1, 2])


class ProviderBuildTests(unittest.TestCase):
    def test_missing_key_env_fails_closed_at_build(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, r"KEY_MISSING\[first\]:KEY_ONE"):
            build_provider_backends(TWO_PROVIDER_CHAIN, environ={})

    def test_mixed_dialect_chain_rejected_at_build(self) -> None:
        chain = ProviderChain(
            providers=(
                spec(id="ga", type="openai-ga"),
                spec(
                    id="alpha",
                    type="openrouter-alpha",
                    base_url="https://two.example.test",
                    endpoint_path="/api/alpha/decisions",
                    api_key_env="KEY_TWO",
                ),
            )
        )
        with self.assertRaisesRegex(DecisionSchemaError, "DIALECT_MIXED"):
            build_provider_backends(chain, environ=ENV)

    def test_disabled_providers_excluded_and_mixed_check_ignores_them(self) -> None:
        chain = ProviderChain(
            providers=(
                spec(id="ga", type="openai-ga"),
                spec(
                    id="alpha",
                    type="openrouter-alpha",
                    base_url="https://two.example.test",
                    endpoint_path="/api/alpha/decisions",
                    api_key_env="KEY_TWO",
                    enabled=False,
                ),
            )
        )
        built = build_provider_backends(chain, environ=ENV)
        self.assertEqual([item[0].id for item in built], ["ga"])

    def test_all_disabled_is_configuration_error(self) -> None:
        chain = ProviderChain(providers=(spec(enabled=False),))
        with self.assertRaisesRegex(DecisionSchemaError, "CHAIN_EMPTY"):
            build_provider_backends(chain, environ=ENV)


class PriorityDispatchTests(unittest.TestCase):
    def test_first_provider_answers_without_failover(self) -> None:
        script = {(FIRST_URL, "model-first"): (200, GA_RESPONSE_BODY)}
        backend = build_priority_backend(
            TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
        )
        response = backend.decide(sample_request(model="model-first"))
        self.assertIsInstance(response.answers[0], PredicateAnswer)
        self.assertEqual(len(backend.last_attempts), 1)
        self.assertEqual(backend.last_attempts[0]["provider_id"], "first")
        self.assertEqual(backend.last_attempts[0]["outcome"], "answer")

    def test_model_substitution_hits_wire(self) -> None:
        script = {(FIRST_URL, "model-first"): (200, GA_RESPONSE_BODY)}
        transport = scripted_transport(script)
        backend = build_priority_backend(TWO_PROVIDER_CHAIN, transport=transport, environ=ENV)
        backend.decide(sample_request(model="unrelated-canonical-model"))
        self.assertEqual(transport.calls[0]["payload"]["model"], "model-first")  # type: ignore[attr-defined]

    def test_transport_error_fails_over_to_second_provider(self) -> None:
        script = {
            (FIRST_URL, "model-first"): DecisionTransportError("DECISION_TRANSPORT: reset"),
            (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
        }
        backend = build_priority_backend(
            TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
        )
        response = backend.decide(sample_request(model="model-first"))
        self.assertEqual(len(backend.last_attempts), 2)
        self.assertEqual(backend.last_attempts[0]["outcome"], "transport")
        self.assertEqual(backend.last_attempts[1]["outcome"], "answer")
        self.assertEqual(backend.last_attempts[1]["provider_id"], "second")
        self.assertIsInstance(response.answers[1], ChoiceAnswer)

    def test_timeout_and_transient_http_failover(self) -> None:
        for failure in (
            DecisionTimeoutError("DECISION_TIMEOUT"),
            DecisionHttpError(503, "upstream"),
            DecisionHttpError(429, "rate limited"),
        ):
            script = {
                (FIRST_URL, "model-first"): failure,
                (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
            }
            backend = build_priority_backend(
                TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
            )
            response = backend.decide(sample_request(model="model-first"))
            self.assertEqual(len(response.answers), 3)
            self.assertEqual(backend.last_attempts[-1]["outcome"], "answer")

    def test_deterministic_http_error_never_fails_over(self) -> None:
        for status in (400, 401, 403, 404, 422):
            script = {
                (FIRST_URL, "model-first"): DecisionHttpError(status, "bad"),
                (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
            }
            backend = build_priority_backend(
                TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
            )
            with self.assertRaises(DecisionHttpError) as ctx:
                backend.decide(sample_request(model="model-first"))
            self.assertEqual(ctx.exception.status, status)
            self.assertEqual(len(backend.last_attempts), 1)

    def test_exhaustion_raises_with_full_attempt_history(self) -> None:
        script = {
            (FIRST_URL, "model-first"): DecisionTimeoutError("t"),
            (SECOND_URL, "model-second"): DecisionHttpError(502, "bad gateway"),
        }
        backend = build_priority_backend(
            TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
        )
        with self.assertRaises(DecisionProviderExhaustedError) as ctx:
            backend.decide(sample_request(model="model-first"))
        self.assertEqual(len(ctx.exception.attempts), 2)
        self.assertIn("first=timeout", str(ctx.exception))
        self.assertIn("second=http_502", str(ctx.exception))

    def test_max_attempts_caps_chain_depth(self) -> None:
        script = {
            (FIRST_URL, "model-first"): DecisionTimeoutError("t"),
            (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
        }
        chain = ProviderChain(providers=TWO_PROVIDER_CHAIN.providers, max_attempts=1)
        backend = build_priority_backend(chain, transport=scripted_transport(script), environ=ENV)
        with self.assertRaises(DecisionProviderExhaustedError):
            backend.decide(sample_request(model="model-first"))
        self.assertEqual(len(backend.last_attempts), 1)

    def test_invalid_request_rejected_before_any_transport(self) -> None:
        transport = scripted_transport({})
        backend = build_priority_backend(TWO_PROVIDER_CHAIN, transport=transport, environ=ENV)
        with self.assertRaises(DecisionSchemaError):
            backend.decide(sample_request(questions=()))
        self.assertEqual(transport.calls, [])  # type: ignore[attr-defined]


class PriorityRecordingReplayTests(unittest.TestCase):
    def test_failover_answer_records_and_replays_roundtrip(self) -> None:
        script = {
            (FIRST_URL, "model-first"): DecisionTimeoutError("t"),
            (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
        }
        backend = build_priority_backend(
            TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
        )
        parser = dialect_parser(DECISIONS_DIALECT_OPENAI_GA)
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "decision-replay.json"
            recording = RecordingBackend(backend, fixture, parser)
            recording.decide(sample_request(model="model-first"))
            recorded = json.loads(fixture.read_text())
            self.assertEqual(len(recorded["responses"]), 1)
            entry = next(iter(recorded["responses"].values()))
            self.assertEqual(entry["dialect"], DECISIONS_DIALECT_OPENAI_GA)
            replay = ReplayBackend(fixture, parser)
            replayed = replay.decide(sample_request(model="model-first"))
            self.assertIsInstance(replayed.answers[0], PredicateAnswer)
            self.assertAlmostEqual(replayed.answers[0].probability, 0.93)


if __name__ == "__main__":
    unittest.main()
