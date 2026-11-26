"""Offline contract tests for the multi-vendor provider chain (no network).

Self-contained by design: the standard pytest collection entry must not
depend on sys.path tricks between sibling test modules.
"""

from __future__ import annotations

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from quanthus.decision_api import (
    ChoiceAnswer,
    ChoiceOption,
    ChoiceQuestion,
    DecisionDeadline,
    DecisionHttpError,
    DecisionProviderExhaustedError,
    DecisionRequest,
    DecisionSchemaError,
    DecisionTimeoutError,
    DecisionTransportError,
    PredicateAnswer,
    PredicateQuestion,
    RecordingBackend,
    ReplayBackend,
    ScoreLevel,
    ScoreQuestion,
    build_priority_backend,
    build_provider_backends,
    chain_worst_case_usd,
    load_provider_chain,
    parse_provider_chain,
    reserve_worst_case_usd,
)
from quanthus.decision_api.backends import DECISIONS_DIALECT_OPENAI_GA
from quanthus.decision_api.providers import (
    FAILOVER_HTTP_STATUSES,
    PROVIDERS_SCHEMA_VERSION,
    DecisionProviderSpec,
    ProviderChain,
)


def sample_questions() -> tuple:
    return (
        PredicateQuestion(instructions="判断输入是否描述宏观级政策事件。", name="is_macro"),
        ChoiceQuestion(
            instructions="该快讯属于哪个影响层级？",
            name="event_tier",
            choices=(
                ChoiceOption("GLOBAL_MACRO", "全球宏观系统性事件"),
                ChoiceOption("DOMESTIC_POLICY", "国内监管货币政策事件"),
                ChoiceOption("INDUSTRY_REGIME", "产业链重大事件"),
                ChoiceOption("NOISE", "无实质增量噪音"),
            ),
        ),
        ScoreQuestion(
            instructions="评估冲击量级。",
            name="magnitude",
            levels=(
                ScoreLevel("none", "无实质影响"),
                ScoreLevel("moderate", "中等行业影响"),
                ScoreLevel("major", "重大结构性冲击"),
            ),
        ),
    )


def sample_request(**overrides) -> DecisionRequest:
    payload = {
        "model": "google/gemini-decision-1",
        "input": "快讯：证监会就程序化交易新规公开征求意见。",
        "questions": sample_questions(),
    }
    payload.update(overrides)
    return DecisionRequest(**payload)


GA_RESPONSE_BODY = {
    "answers": [
        {"name": "is_macro", "probability": 0.93, "type": "predicate"},
        {
            "choice": "DOMESTIC_POLICY",
            "confidence": 0.91,
            "name": "event_tier",
            "probabilities": [
                {"probability": 0.02, "value": "GLOBAL_MACRO"},
                {"probability": 0.91, "value": "DOMESTIC_POLICY"},
                {"probability": 0.05, "value": "INDUSTRY_REGIME"},
                {"probability": 0.02, "value": "NOISE"},
            ],
            "type": "choice",
        },
        {
            "confidence": 0.5,
            "name": "magnitude",
            "probabilities": [
                {"label": "none", "probability": 0.1, "value": 0},
                {"label": "moderate", "probability": 0.5, "value": 1},
                {"label": "major", "probability": 0.4, "value": 2},
            ],
            "score": 1.3,
            "type": "score",
        },
    ],
    "model": "google/gemini-decision-1",
    "usage": {
        "input_tokens": 300,
        "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
        "output_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 300,
    },
}


def scripted_transport(script):
    """Transport dispatching on (url, payload-model) with a call log.

    Behaviors are exceptions to raise or (status, body) pairs; bodies are
    JSON-serialized unless already a string, mirroring the raw text the
    real transport returns.
    """

    def post(url, payload, headers, timeout):
        key = (url, payload.get("model"))
        post.calls.append({"url": url, "payload": payload, "timeout": timeout})
        behavior = script[key]
        if isinstance(behavior, BaseException):
            raise behavior
        if callable(behavior):
            return behavior()
        status, body = behavior
        raw = body if isinstance(body, str) else json.dumps(body)
        return status, raw

    post.calls = []
    return post


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

    def test_bearer_configs_require_https(self) -> None:
        # every request carries a bearer credential, so plain http is a
        # configuration error in versioned provider chains
        with self.assertRaisesRegex(DecisionSchemaError, "SPEC_URL"):
            parse_provider_chain(document(entry(base_url="http://insecure.example.test")))

    def test_type_endpoint_binding_enforced(self) -> None:
        # a GA entry retargeted at an alpha path (or vice versa) is a
        # configuration error, not a runtime surprise (review B03/B04)
        with self.assertRaisesRegex(DecisionSchemaError, "TYPE_PATH_MISMATCH"):
            parse_provider_chain(document(entry(endpoint_path="/api/alpha/decisions")))
        with self.assertRaisesRegex(DecisionSchemaError, "TYPE_PATH_MISMATCH"):
            parse_provider_chain(
                document(
                    entry(
                        type="openrouter-alpha",
                        endpoint_path="/v1/decisions",
                    )
                )
            )

    def test_alpha_systemone_path_accepted(self) -> None:
        chain = parse_provider_chain(
            document(entry(type="systemone-alpha", endpoint_path="/v1/systemone"))
        )
        self.assertEqual(chain.providers[0].type, "systemone-alpha")

    def test_price_fields_validated(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "SPEC_PRICE"):
            parse_provider_chain(document(entry(input_price_usd_per_mtok=-1.0)))
        chain = parse_provider_chain(
            document(entry(input_price_usd_per_mtok=0.1, output_price_usd_per_mtok=0.0))
        )
        self.assertEqual(chain.providers[0].input_price_usd_per_mtok, 0.1)

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
        self.assertEqual(backend.last_attempts[0]["usage"]["status"], "metered")
        self.assertEqual(backend.last_attempts[0]["usage"]["input_tokens"], 300)

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
        self.assertEqual(backend.last_attempts[0]["usage"]["status"], "unknown")
        self.assertEqual(backend.last_attempts[1]["outcome"], "answer")
        self.assertEqual(backend.last_attempts[1]["provider_id"], "second")
        self.assertIsInstance(response.answers[1], ChoiceAnswer)

    def test_failover_set_is_exactly_the_approved_statuses(self) -> None:
        self.assertEqual(
            FAILOVER_HTTP_STATUSES, frozenset({408, 409, 425, 429, 500, 502, 503, 504})
        )

    def test_full_5xx_sweep_only_approved_statuses_fail_over(self) -> None:
        # review reproduction: across every 5xx status only the four
        # approved ones may switch provider; everything else fails closed
        # on the first attempt regardless of the error body shape.
        switched = []
        stopped = []
        for status in range(500, 600):
            script = {
                (FIRST_URL, "model-first"): DecisionHttpError(status, "err"),
                (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
            }
            backend = build_priority_backend(
                TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
            )
            try:
                backend.decide(sample_request(model="model-first"))
                switched.append(status)
            except DecisionHttpError:
                stopped.append(status)
        self.assertEqual(switched, [500, 502, 503, 504])
        self.assertEqual(len(stopped), 96)

    def test_approved_transient_http_statuses_fail_over(self) -> None:
        for status in sorted(FAILOVER_HTTP_STATUSES):
            script = {
                (FIRST_URL, "model-first"): DecisionHttpError(status, "transient"),
                (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
            }
            backend = build_priority_backend(
                TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
            )
            response = backend.decide(sample_request(model="model-first"))
            self.assertEqual(len(response.answers), 3)
            self.assertEqual(backend.last_attempts[0]["outcome"], f"http_{status}")
            self.assertEqual(backend.last_attempts[-1]["outcome"], "answer")

    def test_timeout_fails_over(self) -> None:
        script = {
            (FIRST_URL, "model-first"): DecisionTimeoutError("DECISION_TIMEOUT"),
            (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
        }
        backend = build_priority_backend(
            TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
        )
        backend.decide(sample_request(model="model-first"))
        self.assertEqual(backend.last_attempts[0]["outcome"], "timeout")
        self.assertEqual(backend.last_attempts[-1]["outcome"], "answer")

    def test_deterministic_http_error_never_fails_over_whatever_the_body(self) -> None:
        for status in (400, 401, 403, 404, 418, 422, 520, 524, 529):
            for raw in ('{"error": "fixture"}', "", "<html>error</html>", "[1]"):
                script = {
                    (FIRST_URL, "model-first"): (status, raw),
                    (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
                }
                backend = build_priority_backend(
                    TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
                )
                with self.assertRaises(DecisionHttpError) as ctx:
                    backend.decide(sample_request(model="model-first"))
                self.assertEqual(ctx.exception.status, status)
                self.assertEqual(len(backend.last_attempts), 1, f"status={status} body={raw!r}")
                self.assertEqual(backend.last_attempts[0]["outcome"], f"http_{status}")

    def test_schema_error_after_2xx_is_recorded_and_never_fails_over(self) -> None:
        bad = copy.deepcopy(GA_RESPONSE_BODY)
        bad["answers"] = bad["answers"][:2]  # count mismatch -> schema error after a 200
        script = {
            (FIRST_URL, "model-first"): (200, bad),
            (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
        }
        backend = build_priority_backend(
            TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
        )
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_ANSWER_COUNT"):
            backend.decide(sample_request(model="model-first"))
        self.assertEqual(len(backend.last_attempts), 1)
        self.assertEqual(backend.last_attempts[0]["outcome"], "schema_error")
        self.assertEqual(backend.last_attempts[0]["usage"]["status"], "unknown")

    def test_last_attempts_reset_between_calls(self) -> None:
        bad = copy.deepcopy(GA_RESPONSE_BODY)
        bad["answers"] = bad["answers"][:2]
        script = {
            (FIRST_URL, "model-first"): (200, bad),
        }
        backend = build_priority_backend(
            TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
        )
        with self.assertRaises(DecisionSchemaError):
            backend.decide(sample_request(model="model-first"))
        self.assertEqual(len(backend.last_attempts), 1)
        script[(FIRST_URL, "model-first")] = (200, GA_RESPONSE_BODY)
        backend.decide(sample_request(model="model-first"))
        self.assertEqual(len(backend.last_attempts), 1)
        self.assertEqual(backend.last_attempts[0]["outcome"], "answer")

    def test_billed_http_failure_keeps_usage_evidence(self) -> None:
        error_body = {
            "error": {"message": "throttled"},
            "usage": {"input_tokens": 288, "output_tokens": 0},
        }
        script = {
            (FIRST_URL, "model-first"): (503, error_body),
            (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
        }
        backend = build_priority_backend(
            TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
        )
        backend.decide(sample_request(model="model-first"))
        failure = backend.last_attempts[0]
        self.assertEqual(failure["outcome"], "http_503")
        self.assertEqual(failure["usage"]["status"], "metered")
        self.assertEqual(failure["usage"]["input_tokens"], 288)

    def test_unbilled_failure_stays_unknown_usage(self) -> None:
        script = {
            (FIRST_URL, "model-first"): (503, ""),
            (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
        }
        backend = build_priority_backend(
            TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
        )
        backend.decide(sample_request(model="model-first"))
        self.assertEqual(backend.last_attempts[0]["usage"]["status"], "unknown")

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
        self.assertFalse(ctx.exception.deadline_stopped)

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


class PriorityDeadlineTests(unittest.TestCase):
    def test_expired_deadline_stops_before_first_dispatch(self) -> None:
        transport = scripted_transport({(FIRST_URL, "model-first"): (200, GA_RESPONSE_BODY)})
        backend = build_priority_backend(TWO_PROVIDER_CHAIN, transport=transport, environ=ENV)
        deadline = DecisionDeadline.after(0.01)
        time.sleep(0.02)
        with self.assertRaises(DecisionProviderExhaustedError) as ctx:
            backend.decide(sample_request(model="model-first"), deadline=deadline)
        self.assertTrue(ctx.exception.deadline_stopped)
        self.assertEqual(transport.calls, [])  # type: ignore[attr-defined]
        self.assertEqual(ctx.exception.attempts, ())

    def test_deadline_shared_across_attempts_truncates_each_timeout(self) -> None:
        script = {
            (FIRST_URL, "model-first"): DecisionTimeoutError("t"),
            (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
        }
        transport = scripted_transport(script)
        backend = build_priority_backend(TWO_PROVIDER_CHAIN, transport=transport, environ=ENV)
        backend.decide(sample_request(model="model-first"), deadline=DecisionDeadline.after(4.0))
        timeouts = [call["timeout"] for call in transport.calls]  # type: ignore[attr-defined]
        self.assertEqual(len(timeouts), 2)
        for value in timeouts:
            self.assertLessEqual(value, 4.0)
            self.assertGreater(value, 0.0)

    def test_exhausted_deadline_mid_chain_marks_deadline_stopped(self) -> None:
        def slow_timeout():
            time.sleep(0.4)
            raise DecisionTimeoutError("t")

        script = {
            (FIRST_URL, "model-first"): slow_timeout,
            (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
        }
        transport = scripted_transport(script)
        backend = build_priority_backend(TWO_PROVIDER_CHAIN, transport=transport, environ=ENV)
        # budget 0.6s: the first attempt consumes ~0.4s, so the second
        # provider falls below the minimum useful sliver and is skipped
        with self.assertRaises(DecisionProviderExhaustedError) as ctx:
            backend.decide(
                sample_request(model="model-first"), deadline=DecisionDeadline.after(0.6)
            )
        self.assertTrue(ctx.exception.deadline_stopped)
        # the second provider never went on the wire, so it leaves no record
        self.assertEqual(len(transport.calls), 1)  # type: ignore[attr-defined]
        self.assertEqual(len(ctx.exception.attempts), 1)
        self.assertEqual(ctx.exception.attempts[0]["outcome"], "timeout")


class ReservationTests(unittest.TestCase):
    def test_worst_case_reservation_with_declared_prices(self) -> None:
        priced = spec(
            input_price_usd_per_mtok=0.10,
            output_price_usd_per_mtok=0.0,
        )
        value = reserve_worst_case_usd(priced, max_input_tokens=1000, max_output_tokens=0)
        self.assertAlmostEqual(value, 0.0001)

    def test_unpriced_vendor_is_unreservable(self) -> None:
        self.assertIsNone(reserve_worst_case_usd(spec(), 1000, 0))

    def test_chain_worst_case_enumerates_max_attempts(self) -> None:
        chain = parse_provider_chain(
            document(
                entry("a", input_price_usd_per_mtok=0.1, output_price_usd_per_mtok=0.0),
                entry(
                    "b",
                    base_url="https://two.example.test",
                    model="model-two",
                    api_key_env="KEY_TWO",
                ),
                entry(
                    "c",
                    base_url="https://three.example.test",
                    model="model-three",
                    api_key_env="KEY_THREE",
                    input_price_usd_per_mtok=0.2,
                    output_price_usd_per_mtok=0.0,
                ),
                max_attempts=3,
            )
        )
        result = chain_worst_case_usd(chain, max_input_tokens=1000, max_output_tokens=0)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["total_worst_case_usd"])
        self.assertEqual(result["attempts_budgeted"], 3)
        by_id = {row["provider_id"]: row["worst_case_usd"] for row in result["per_provider"]}
        self.assertAlmostEqual(by_id["a"], 0.0001)
        self.assertIsNone(by_id["b"])
        self.assertAlmostEqual(by_id["c"], 0.0002)

    def test_chain_worst_case_complete_when_all_priced(self) -> None:
        chain = parse_provider_chain(
            document(
                entry("a", input_price_usd_per_mtok=0.1, output_price_usd_per_mtok=0.0),
                entry(
                    "b",
                    base_url="https://two.example.test",
                    model="model-two",
                    api_key_env="KEY_TWO",
                    input_price_usd_per_mtok=0.1,
                    output_price_usd_per_mtok=0.0,
                ),
                max_attempts=2,
            )
        )
        result = chain_worst_case_usd(chain, max_input_tokens=1000, max_output_tokens=0)
        self.assertTrue(result["complete"])
        self.assertAlmostEqual(result["total_worst_case_usd"], 0.0002)


class PriorityRecordingReplayTests(unittest.TestCase):
    def test_failover_answer_records_and_replays_roundtrip(self) -> None:
        script = {
            (FIRST_URL, "model-first"): DecisionTimeoutError("t"),
            (SECOND_URL, "model-second"): (200, GA_RESPONSE_BODY),
        }
        backend = build_priority_backend(
            TWO_PROVIDER_CHAIN, transport=scripted_transport(script), environ=ENV
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "decision-replay.json"
            recording = RecordingBackend(backend, fixture, DECISIONS_DIALECT_OPENAI_GA)
            recording.decide(sample_request(model="model-first"))
            recorded = json.loads(fixture.read_text())
            self.assertEqual(len(recorded["responses"]), 1)
            entry = next(iter(recorded["responses"].values()))
            self.assertEqual(entry["dialect"], DECISIONS_DIALECT_OPENAI_GA)
            replay = ReplayBackend(fixture, DECISIONS_DIALECT_OPENAI_GA)
            replayed = replay.decide(sample_request(model="model-first"))
            self.assertIsInstance(replayed.answers[0], PredicateAnswer)
            self.assertAlmostEqual(replayed.answers[0].probability, 0.93)


if __name__ == "__main__":
    unittest.main()
