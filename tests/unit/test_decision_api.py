from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from quanthus.decision_api import (
    ChoiceAnswer,
    ChoiceOption,
    ChoiceQuestion,
    DecisionDeadline,
    DecisionHttpError,
    DecisionRefusal,
    DecisionRequest,
    DecisionSchemaError,
    DecisionTimeoutError,
    PredicateAnswer,
    PredicateQuestion,
    RecordingBackend,
    ReplayBackend,
    ScoreAnswer,
    ScoreLevel,
    ScoreQuestion,
    request_digest,
)
from quanthus.decision_api.backends import (
    DECISIONS_DIALECT_OPENAI_GA,
    DECISIONS_DIALECT_OPENROUTER_ALPHA,
    HttpBackendConfig,
    OpenAIDecisionsBackend,
    OpenRouterDecisionsBackend,
    parse_openai_ga_response,
    parse_openrouter_alpha_response,
    request_to_openai_ga,
    request_to_openrouter_alpha,
)


def sample_questions() -> tuple:
    return (
        PredicateQuestion(
            instructions="判断输入是否描述宏观级政策事件。",
            name="is_macro",
        ),
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

OPENROUTER_RESPONSE_BODY = {
    "id": "gen-dec-test",
    "model": "typesafe/jev-1.14",
    "provider": "TypeSafe",
    "answers": {
        "is_macro": {"type": "noul", "noul": 0.93, "confidence": 0.07},
        "event_tier": {
            "type": "choice",
            "choice": "DOMESTIC_POLICY",
            "probabilities": {
                "GLOBAL_MACRO": 0.02,
                "DOMESTIC_POLICY": 0.91,
                "INDUSTRY_REGIME": 0.05,
                "NOISE": 0.02,
            },
            "confidence": 0.91,
        },
        "magnitude": {
            "type": "score",
            "score": 1.3,
            "probabilities": {"0": 0.1, "1": 0.5, "2": 0.4},
            "confidence": 0.5,
        },
    },
    "usage": {
        "input_tokens": 300,
        "output_tokens": 0,
        "cost": 0.0001,
        "input_tokens_details": {"cached_tokens": 0},
    },
}


def spy_transport(status: int, body: object) -> object:
    """Scripted transport returning raw body text, as the real transport does.

    dict/list bodies are JSON-serialized; string bodies pass through
    verbatim so error-body shapes (HTML, empty) reproduce faithfully.
    """
    calls: list = []

    def transport(url, payload, headers, timeout):
        calls.append({"url": url, "payload": payload, "headers": dict(headers), "timeout": timeout})
        raw = body if isinstance(body, str) else json.dumps(body)
        return status, raw

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def ga_body_with(answers) -> dict:
    body = copy.deepcopy(GA_RESPONSE_BODY)
    body["answers"] = answers
    return body


def clone_answer(index: int, **overrides) -> dict:
    entry = copy.deepcopy(GA_RESPONSE_BODY["answers"][index])
    entry.update(overrides)
    return entry


class QuestionValidationTests(unittest.TestCase):
    def test_empty_instructions_rejected(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_INSTRUCTIONS_EMPTY"):
            sample_request(questions=(PredicateQuestion(instructions="  ", name="q"),)).validate()

    def test_choice_arity_and_duplicates(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_CHOICE_ARITY"):
            sample_request(
                questions=(
                    ChoiceQuestion(
                        instructions="x",
                        name="q",
                        choices=(ChoiceOption("ONLY"),),
                    ),
                )
            ).validate()
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_CHOICE_DUPLICATE"):
            sample_request(
                questions=(
                    ChoiceQuestion(
                        instructions="x",
                        name="q",
                        choices=(ChoiceOption("A"), ChoiceOption("A")),
                    ),
                )
            ).validate()

    def test_choice_string_true_distinct_from_boolean_true(self) -> None:
        question = ChoiceQuestion(
            instructions="x",
            name="q",
            choices=(ChoiceOption(True, "bool true"), ChoiceOption("True", "string true")),
        )
        question.validate()

    def test_score_arity_and_duplicate_labels(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_SCORE_ARITY"):
            sample_request(
                questions=(ScoreQuestion(instructions="x", name="q", levels=(ScoreLevel("a"),)),)
            ).validate()
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_SCORE_LABEL_DUPLICATE"):
            sample_request(
                questions=(
                    ScoreQuestion(
                        instructions="x",
                        name="q",
                        levels=(ScoreLevel("a"), ScoreLevel("a")),
                    ),
                )
            ).validate()

    def test_request_rejects_bad_input_and_zero_questions(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_INPUT_TYPE"):
            sample_request(input=42).validate()
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_QUESTION_ARITY"):
            sample_request(questions=()).validate()

    def test_duplicate_question_names_rejected(self) -> None:
        questions = (
            PredicateQuestion(instructions="一", name="dup"),
            PredicateQuestion(instructions="二", name="dup"),
        )
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_QUESTION_NAME_DUPLICATE"):
            sample_request(questions=questions).validate()

    def test_primitive_type_override_rejected(self) -> None:
        # a runtime override of the wire primitive must not desynchronize
        # payload and digest (independent-review probe C06)
        question = PredicateQuestion(instructions="x", name="q")
        object.__setattr__(question, "type", "score")
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_PRIMITIVE_INCONSISTENT"):
            sample_request(questions=(question,)).validate()

    def test_deadline_construction(self) -> None:
        deadline = DecisionDeadline.after(5.0)
        self.assertGreater(deadline.remaining(), 0.0)
        self.assertFalse(deadline.exceeded())
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_DEADLINE_RANGE"):
            DecisionDeadline.after(0)

    def test_digest_stable_and_sensitive_to_content(self) -> None:
        first = request_digest(sample_request())
        second = request_digest(sample_request())
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))
        altered = request_digest(sample_request(input="different evidence"))
        self.assertNotEqual(first, altered)


class OpenAIGaDialectTests(unittest.TestCase):
    def test_wire_shape_matches_ga_schema(self) -> None:
        payload = request_to_openai_ga(sample_request())
        self.assertEqual(payload["model"], "google/gemini-decision-1")
        self.assertIsInstance(payload["input"], str)
        self.assertEqual(len(payload["questions"]), 3)
        predicate, choice, score = payload["questions"]
        self.assertEqual(
            predicate,
            {
                "type": "predicate",
                "instructions": "判断输入是否描述宏观级政策事件。",
                "name": "is_macro",
            },
        )
        self.assertEqual(
            choice["choices"][0], {"value": "GLOBAL_MACRO", "description": "全球宏观系统性事件"}
        )
        self.assertEqual(score["levels"][1], {"label": "moderate", "description": "中等行业影响"})

    def test_message_array_input_passes_through(self) -> None:
        request = sample_request(input=({"role": "user", "content": "x"},))
        payload = request_to_openai_ga(request)
        self.assertEqual(payload["input"], [{"role": "user", "content": "x"}])

    def test_unnamed_question_omits_name_key(self) -> None:
        request = sample_request(questions=(PredicateQuestion(instructions="x"),))
        payload = request_to_openai_ga(request)
        self.assertNotIn("name", payload["questions"][0])

    def test_parse_full_response(self) -> None:
        response = parse_openai_ga_response(GA_RESPONSE_BODY, sample_request())
        self.assertEqual(response.model, "google/gemini-decision-1")
        self.assertEqual(len(response.answers), 3)
        predicate, choice, score = response.answers
        self.assertIsInstance(predicate, PredicateAnswer)
        self.assertAlmostEqual(predicate.probability, 0.93)
        self.assertIsInstance(choice, ChoiceAnswer)
        self.assertEqual(choice.choice, "DOMESTIC_POLICY")
        self.assertEqual(dict(choice.probabilities)["DOMESTIC_POLICY"], 0.91)
        self.assertIsInstance(score, ScoreAnswer)
        self.assertAlmostEqual(score.score, 1.3)
        self.assertEqual(score.probabilities[1], ("moderate", 1, 0.5))
        self.assertEqual(response.usage.input_tokens, 300)
        self.assertEqual(response.usage.output_tokens, 0)

    def test_refusal_per_question_does_not_block_siblings(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][1] = {"name": "event_tier", "type": "refusal"}
        response = parse_openai_ga_response(body, sample_request())
        self.assertIsInstance(response.answers[0], PredicateAnswer)
        self.assertIsInstance(response.answers[1], DecisionRefusal)
        self.assertIsInstance(response.answers[2], ScoreAnswer)
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_ANSWER_REFUSED"):
            response.require_named("event_tier")
        self.assertIsInstance(response.require_named("is_macro"), PredicateAnswer)

    def test_bare_refusal_binds_positionally(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][1] = {"type": "refusal"}
        response = parse_openai_ga_response(body, sample_request())
        refusal = response.answers[1]
        self.assertIsInstance(refusal, DecisionRefusal)
        self.assertEqual(refusal.name, "event_tier")

    def test_missing_answers_envelope_fails_closed(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_RESPONSE_ANSWERS_MISSING"):
            parse_openai_ga_response({"model": "m"}, sample_request())

    def test_probability_range_enforced(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][0]["probability"] = 1.7
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_PROBABILITY_RANGE"):
            parse_openai_ga_response(body, sample_request())

    def test_boolean_choice_answer_parsed(self) -> None:
        body = {
            "answers": [
                {
                    "choice": True,
                    "confidence": 0.99,
                    "name": "suspension_flag",
                    "probabilities": [
                        {"probability": 0.99, "value": True},
                        {"probability": 0.01, "value": False},
                    ],
                    "type": "choice",
                }
            ],
            "model": "m",
            "usage": {},
        }
        request = sample_request(
            questions=(
                ChoiceQuestion(
                    instructions="x",
                    name="suspension_flag",
                    choices=(ChoiceOption(True), ChoiceOption(False)),
                ),
            )
        )
        response = parse_openai_ga_response(body, request)
        answer = response.answers[0]
        self.assertIsInstance(answer, ChoiceAnswer)
        self.assertIs(answer.choice, True)
        self.assertEqual(dict(answer.probabilities)[False], 0.01)

    def test_mixed_content_parts_pass_through_verbatim(self) -> None:
        content = [
            {"type": "input_text", "text": "这是一张测试图。"},
            {"type": "input_image", "image_url": "data:image/png;base64,QUJD"},
            {"type": "input_json", "data": {"metrics": {"net_inflow": 2.1}}},
        ]
        request = sample_request(input=({"role": "user", "content": content},))
        payload = request_to_openai_ga(request)
        self.assertEqual(payload["input"][0]["content"], content)
        # canonical digest must see the same mixed parts so replay keys stay honest
        self.assertEqual(request.canonical_json()["input"][0]["content"], content)


class GaAnswerBindingTests(unittest.TestCase):
    """Every answer must be bound to the batch that asked for it (review S01)."""

    def test_fewer_answers_rejected(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"] = body["answers"][:2]
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_ANSWER_COUNT"):
            parse_openai_ga_response(body, sample_request())

    def test_more_answers_rejected(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"] = body["answers"] + [clone_answer(0)]
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_ANSWER_COUNT"):
            parse_openai_ga_response(body, sample_request())

    def test_swapped_order_rejected_by_primitive_and_name(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        answers = body["answers"]
        answers[0], answers[1] = answers[1], answers[0]
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_ANSWER_PRIMITIVE_MISMATCH"):
            parse_openai_ga_response(body, sample_request())

    def test_wrong_primitive_rejected(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][0] = {"name": "is_macro", "type": "score", "score": 1.0, "confidence": 0.5}
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_ANSWER_PRIMITIVE_MISMATCH"):
            parse_openai_ga_response(body, sample_request())

    def test_name_mismatch_rejected(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][0]["name"] = "other_name"
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_ANSWER_NAME_MISMATCH"):
            parse_openai_ga_response(body, sample_request())

    def test_absent_name_on_named_content_answer_rejected(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        del body["answers"][0]["name"]
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_ANSWER_NAME_MISMATCH"):
            parse_openai_ga_response(body, sample_request())

    def test_undeclared_choice_value_rejected(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][1]["choice"] = "UNDECLARED_TIER"
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_CHOICE_UNDECLARED"):
            parse_openai_ga_response(body, sample_request())

    def test_score_probability_out_of_range_index_rejected(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][2]["probabilities"][0]["value"] = 9
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_SCORE_PROBABILITY_INDEX"):
            parse_openai_ga_response(body, sample_request())

    def test_score_probability_wrong_label_rejected(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][2]["probabilities"][0]["label"] = "forged"
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_SCORE_PROBABILITY_LABEL"):
            parse_openai_ga_response(body, sample_request())

    def test_score_out_of_range_rejected(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][2]["score"] = 7.5
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_SCORE_RANGE"):
            parse_openai_ga_response(body, sample_request())

    def test_choice_probability_incomplete_rejected(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][1]["probabilities"] = body["answers"][1]["probabilities"][:2]
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_CHOICE_PROBABILITY_INCOMPLETE"):
            parse_openai_ga_response(body, sample_request())

    def test_choice_wire_order_normalized_to_declared_order(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][1]["probabilities"] = list(reversed(body["answers"][1]["probabilities"]))
        response = parse_openai_ga_response(body, sample_request())
        answer = response.answers[1]
        self.assertIsInstance(answer, ChoiceAnswer)
        values = [value for value, _ in answer.probabilities]
        self.assertEqual(values, ["GLOBAL_MACRO", "DOMESTIC_POLICY", "INDUSTRY_REGIME", "NOISE"])

    def test_score_wire_order_normalized_to_declared_order(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["answers"][2]["probabilities"] = list(reversed(body["answers"][2]["probabilities"]))
        response = parse_openai_ga_response(body, sample_request())
        answer = response.answers[2]
        self.assertIsInstance(answer, ScoreAnswer)
        indices = [index for _, index, _ in answer.probabilities]
        self.assertEqual(indices, [0, 1, 2])

    def test_malformed_usage_rejected_as_schema_error(self) -> None:
        body = copy.deepcopy(GA_RESPONSE_BODY)
        body["usage"]["input_tokens"] = "not-a-number"
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_USAGE_FIELD"):
            parse_openai_ga_response(body, sample_request())

    def test_require_named_cannot_cross_primitive(self) -> None:
        response = parse_openai_ga_response(GA_RESPONSE_BODY, sample_request())
        answer = response.require_named("magnitude")
        self.assertIsInstance(answer, ScoreAnswer)
        self.assertNotIsInstance(answer, PredicateAnswer)


class OpenRouterDialectTests(unittest.TestCase):
    def test_wire_shape_matches_alpha_schema(self) -> None:
        payload = request_to_openrouter_alpha(sample_request())
        self.assertIn("state", payload)
        self.assertNotIn("input", payload)
        questions = payload["questions"]
        self.assertIsInstance(questions, dict)
        self.assertEqual(
            questions["is_macro"],
            {"type": "noul", "instructions": "判断输入是否描述宏观级政策事件。"},
        )
        self.assertEqual(
            questions["event_tier"]["criteria"]["GLOBAL_MACRO"],
            "全球宏观系统性事件",
        )
        self.assertEqual(
            questions["magnitude"]["criteria"],
            ["无实质影响", "中等行业影响", "重大结构性冲击"],
        )

    def test_unnamed_question_rejected(self) -> None:
        request = sample_request(questions=(PredicateQuestion(instructions="x"),))
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_OPENROUTER_REQUIRES_NAME"):
            request_to_openrouter_alpha(request)

    def test_boolean_choice_rejected(self) -> None:
        request = sample_request(
            questions=(
                ChoiceQuestion(
                    instructions="x",
                    name="q",
                    choices=(ChoiceOption(True), ChoiceOption(False)),
                ),
            )
        )
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_OPENROUTER_BOOL_CHOICE"):
            request_to_openrouter_alpha(request)

    def test_message_array_input_flattened(self) -> None:
        request = sample_request(
            input=(
                {"role": "user", "content": "第一段。"},
                {"role": "user", "content": [{"type": "input_text", "text": "第二段。"}]},
            )
        )
        payload = request_to_openrouter_alpha(request)
        self.assertEqual(payload["state"], ["第一段。", "第二段。"])

    def test_non_user_role_fails_closed_on_alpha(self) -> None:
        request = sample_request(input=({"role": "assistant", "content": "x"},))
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_OPENROUTER_ROLE_SUBSET"):
            request_to_openrouter_alpha(request)

    def test_image_detail_tier_fails_closed_on_alpha(self) -> None:
        request = sample_request(
            input=(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,QUJD",
                            "detail": "high",
                        }
                    ],
                },
            )
        )
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_OPENROUTER_IMAGE_DETAIL"):
            request_to_openrouter_alpha(request)

    def test_image_part_translated_to_alpha_image_url(self) -> None:
        request = sample_request(
            input=(
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "看图。"},
                        {"type": "input_image", "image_url": "data:image/png;base64,QUJD"},
                    ],
                },
            )
        )
        payload = request_to_openrouter_alpha(request)
        self.assertEqual(
            payload["state"],
            ["看图。", {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}],
        )

    def test_input_json_part_fails_closed_on_alpha(self) -> None:
        request = sample_request(
            input=(
                {
                    "role": "user",
                    "content": [
                        {"type": "input_json", "data": {"metrics": {"x": 1}}},
                    ],
                },
            )
        )
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_OPENROUTER_INPUT_PART_TYPE"):
            request_to_openrouter_alpha(request)

    def test_parse_full_response_restores_labels_and_order(self) -> None:
        response = parse_openrouter_alpha_response(OPENROUTER_RESPONSE_BODY, sample_request())
        self.assertEqual(len(response.answers), 3)
        predicate, choice, score = response.answers
        self.assertIsInstance(predicate, PredicateAnswer)
        self.assertAlmostEqual(predicate.probability, 0.93)
        self.assertEqual(choice.choice, "DOMESTIC_POLICY")
        self.assertIsInstance(score, ScoreAnswer)
        self.assertAlmostEqual(score.score, 1.3)
        by_index = {idx: (label, prob) for label, idx, prob in score.probabilities}
        self.assertEqual(by_index[1], ("moderate", 0.5))
        self.assertAlmostEqual(response.usage.cost, 0.0001)

    def test_missing_answer_degrades_to_refusal(self) -> None:
        body = copy.deepcopy(OPENROUTER_RESPONSE_BODY)
        del body["answers"]["magnitude"]
        response = parse_openrouter_alpha_response(body, sample_request())
        self.assertIsInstance(response.answers[2], DecisionRefusal)
        self.assertIsInstance(response.answers[0], PredicateAnswer)

    def test_alpha_wire_order_normalized_to_declared_order(self) -> None:
        body = copy.deepcopy(OPENROUTER_RESPONSE_BODY)
        body["answers"]["event_tier"]["probabilities"] = {
            "NOISE": 0.02,
            "INDUSTRY_REGIME": 0.05,
            "DOMESTIC_POLICY": 0.91,
            "GLOBAL_MACRO": 0.02,
        }
        body["answers"]["magnitude"]["probabilities"] = {"2": 0.4, "1": 0.5, "0": 0.1}
        response = parse_openrouter_alpha_response(body, sample_request())
        choice = response.answers[1]
        score = response.answers[2]
        self.assertIsInstance(choice, ChoiceAnswer)
        self.assertIsInstance(score, ScoreAnswer)
        self.assertEqual(
            [value for value, _ in choice.probabilities],
            ["GLOBAL_MACRO", "DOMESTIC_POLICY", "INDUSTRY_REGIME", "NOISE"],
        )
        self.assertEqual([index for _, index, _ in score.probabilities], [0, 1, 2])

    def test_alpha_undeclared_choice_rejected(self) -> None:
        body = copy.deepcopy(OPENROUTER_RESPONSE_BODY)
        body["answers"]["event_tier"]["choice"] = "outside"
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_CHOICE_UNDECLARED"):
            parse_openrouter_alpha_response(body, sample_request())

    def test_alpha_score_padded_index_rejected(self) -> None:
        body = copy.deepcopy(OPENROUTER_RESPONSE_BODY)
        probs = body["answers"]["magnitude"]["probabilities"]
        probs["00"] = probs.pop("0")
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_SCORE_INDEX_PADDING"):
            parse_openrouter_alpha_response(body, sample_request())

    def test_alpha_score_out_of_range_index_rejected(self) -> None:
        body = copy.deepcopy(OPENROUTER_RESPONSE_BODY)
        probs = body["answers"]["magnitude"]["probabilities"]
        probs["9"] = probs.pop("0")
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_SCORE_INDEX_RANGE"):
            parse_openrouter_alpha_response(body, sample_request())

    def test_alpha_score_incomplete_distribution_rejected(self) -> None:
        body = copy.deepcopy(OPENROUTER_RESPONSE_BODY)
        del body["answers"]["magnitude"]["probabilities"]["0"]
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_SCORE_PROBABILITY_INCOMPLETE"):
            parse_openrouter_alpha_response(body, sample_request())

    def test_alpha_choice_incomplete_distribution_rejected(self) -> None:
        body = copy.deepcopy(OPENROUTER_RESPONSE_BODY)
        del body["answers"]["event_tier"]["probabilities"]["NOISE"]
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_CHOICE_PROBABILITY_INCOMPLETE"):
            parse_openrouter_alpha_response(body, sample_request())

    def test_alpha_primitive_mismatch_rejected(self) -> None:
        body = copy.deepcopy(OPENROUTER_RESPONSE_BODY)
        body["answers"]["is_macro"] = {
            "type": "score",
            "score": 1.0,
            "confidence": 0.5,
        }
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_ALPHA_ANSWER_TYPE"):
            parse_openrouter_alpha_response(body, sample_request())


class BackendTransportTests(unittest.TestCase):
    def test_openai_backend_end_to_end_with_spy_transport(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        backend = OpenAIDecisionsBackend(
            HttpBackendConfig(
                url="https://gw.example.test/v1/decisions", api_key="test-key", timeout_seconds=25.0
            ),
            transport=transport,
        )
        response = backend.decide(sample_request())
        self.assertEqual(len(response.answers), 3)
        call = transport.calls[0]  # type: ignore[attr-defined]
        self.assertEqual(call["url"], "https://gw.example.test/v1/decisions")
        self.assertEqual(call["timeout"], 25.0)
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertIn("questions", call["payload"])

    def test_openrouter_backend_end_to_end_with_spy_transport(self) -> None:
        transport = spy_transport(200, OPENROUTER_RESPONSE_BODY)
        backend = OpenRouterDecisionsBackend(
            HttpBackendConfig(url="https://openrouter.ai/api/alpha/decisions", api_key="test-key"),
            transport=transport,
        )
        response = backend.decide(sample_request())
        self.assertIsInstance(response.answers[0], PredicateAnswer)
        call = transport.calls[0]  # type: ignore[attr-defined]
        self.assertIn("state", call["payload"])
        self.assertNotIn("input", call["payload"])

    def test_http_error_preserves_status_whatever_the_body(self) -> None:
        for raw_body in (
            '{"error": {"message": "bad schema"}}',
            "",
            "<html>nginx 400</html>",
            "[1]",
        ):
            transport = spy_transport(400, raw_body)
            backend = OpenAIDecisionsBackend(
                HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
                transport=transport,
            )
            with self.assertRaises(DecisionHttpError) as ctx:
                backend.decide(sample_request())
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(len(transport.calls), 1)  # type: ignore[attr-defined]

    def test_http_error_body_json_preserved_for_audit(self) -> None:
        transport = spy_transport(400, {"error": {"message": "bad schema"}})
        backend = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with self.assertRaises(DecisionHttpError) as ctx:
            backend.decide(sample_request())
        self.assertIn("bad schema", ctx.exception.body)

    def test_http_error_carries_wellformed_usage_evidence(self) -> None:
        body = {
            "error": {"message": "throttled"},
            "usage": {"input_tokens": 120, "output_tokens": 0},
        }
        transport = spy_transport(503, body)
        backend = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with self.assertRaises(DecisionHttpError) as ctx:
            backend.decide(sample_request())
        self.assertIsNotNone(ctx.exception.usage)
        assert ctx.exception.usage is not None
        self.assertEqual(ctx.exception.usage.input_tokens, 120)

    def test_http_error_malformed_usage_degrades_to_none(self) -> None:
        body = {"error": {"message": "throttled"}, "usage": {"input_tokens": "garbage"}}
        transport = spy_transport(503, body)
        backend = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with self.assertRaises(DecisionHttpError) as ctx:
            backend.decide(sample_request())
        self.assertIsNone(ctx.exception.usage)

    def test_two_xx_non_object_body_fails_closed_without_failover_semantics(self) -> None:
        transport = spy_transport(200, "[1, 2]")
        backend = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_RESPONSE_NOT_OBJECT"):
            backend.decide(sample_request())

    def test_deadline_truncates_timeout(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        backend = OpenAIDecisionsBackend(
            HttpBackendConfig(
                url="https://gw.example.test/v1/decisions", api_key="test-key", timeout_seconds=25.0
            ),
            transport=transport,
        )
        backend.decide(sample_request(), deadline=DecisionDeadline.after(3.0))
        call = transport.calls[0]  # type: ignore[attr-defined]
        self.assertLessEqual(call["timeout"], 3.0)
        self.assertGreater(call["timeout"], 0.0)

    def test_expired_deadline_fails_before_transport(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        backend = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        deadline = DecisionDeadline.after(0.01)
        import time

        time.sleep(0.02)
        with self.assertRaises(DecisionTimeoutError):
            backend.decide(sample_request(), deadline=deadline)
        self.assertEqual(transport.calls, [])  # type: ignore[attr-defined]

    def test_invalid_request_rejected_before_transport(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        backend = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with self.assertRaises(DecisionSchemaError):
            backend.decide(sample_request(questions=()))
        self.assertEqual(transport.calls, [])  # type: ignore[attr-defined]

    def test_backend_config_validation(self) -> None:
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_BACKEND_URL_SCHEME"):
            OpenAIDecisionsBackend(HttpBackendConfig(url="ftp://x", api_key="k"))
        with self.assertRaisesRegex(DecisionSchemaError, "DECISION_BACKEND_KEY_EMPTY"):
            OpenAIDecisionsBackend(HttpBackendConfig(url="https://x", api_key=""))


class ReplayRecordingTests(unittest.TestCase):
    def test_record_then_replay_roundtrip(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        live = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "decision-replay.json"
            recording = RecordingBackend(live, fixture, DECISIONS_DIALECT_OPENAI_GA)
            response = recording.decide(sample_request())
            self.assertIsInstance(response.answers[0], PredicateAnswer)
            self.assertTrue(fixture.exists())

            replay = ReplayBackend(fixture, DECISIONS_DIALECT_OPENAI_GA)
            self.assertEqual(len(replay.fixture_digests), 1)
            replayed = replay.decide(sample_request())
            self.assertIsInstance(replayed.answers[1], ChoiceAnswer)
            self.assertAlmostEqual(replayed.answers[1].confidence, 0.91)

    def test_replay_binds_answers_to_batch_like_live_traffic(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        live = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "decision-replay.json"
            recording = RecordingBackend(live, fixture, DECISIONS_DIALECT_OPENAI_GA)
            recording.decide(sample_request())
            replayed = ReplayBackend(fixture, DECISIONS_DIALECT_OPENAI_GA).decide(sample_request())
            live_response = parse_openai_ga_response(GA_RESPONSE_BODY, sample_request())
            self.assertEqual(replayed.answers, live_response.answers)

    def test_replay_miss_fails_closed_on_different_request(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        live = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "decision-replay.json"
            RecordingBackend(live, fixture, DECISIONS_DIALECT_OPENAI_GA).decide(sample_request())
            replay = ReplayBackend(fixture, DECISIONS_DIALECT_OPENAI_GA)
            with self.assertRaisesRegex(DecisionSchemaError, "DECISION_REPLAY_MISS"):
                replay.decide(sample_request(input="unrecorded evidence"))
            self.assertEqual(len(replay.misses), 1)

    def test_replay_rejects_foreign_dialect_fixture_at_construction(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        live = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "decision-replay.json"
            RecordingBackend(live, fixture, DECISIONS_DIALECT_OPENAI_GA).decide(sample_request())
            with self.assertRaisesRegex(DecisionSchemaError, "DECISION_REPLAY_DIALECT_MISMATCH"):
                ReplayBackend(fixture, DECISIONS_DIALECT_OPENROUTER_ALPHA)

    def test_recording_rejects_mismatched_declared_dialect(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        live = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "decision-replay.json"
            with self.assertRaisesRegex(DecisionSchemaError, "DECISION_RECORD_DIALECT_MISMATCH"):
                RecordingBackend(live, fixture, DECISIONS_DIALECT_OPENROUTER_ALPHA)

    def test_recording_rejects_foreign_dialect_existing_fixture(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        live = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "decision-replay.json"
            RecordingBackend(live, fixture, DECISIONS_DIALECT_OPENAI_GA).decide(sample_request())
            # tamper the sealed dialect label and confirm re-opening fails closed
            payload = json.loads(fixture.read_text())
            for entry in payload["responses"].values():
                entry["dialect"] = DECISIONS_DIALECT_OPENROUTER_ALPHA
            fixture.write_text(json.dumps(payload))
            with self.assertRaisesRegex(
                DecisionSchemaError, "DECISION_RECORD_FIXTURE_DIALECT_MISMATCH"
            ):
                RecordingBackend(live, fixture, DECISIONS_DIALECT_OPENAI_GA)

    def test_replay_fixture_validation_rejects_foreign_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "bad.json"
            fixture.write_text(json.dumps({"schema_version": "other", "responses": {}}))
            with self.assertRaisesRegex(DecisionSchemaError, "DECISION_REPLAY"):
                ReplayBackend(fixture, DECISIONS_DIALECT_OPENAI_GA)

    def test_recording_preserves_existing_entries(self) -> None:
        transport = spy_transport(200, GA_RESPONSE_BODY)
        live = OpenAIDecisionsBackend(
            HttpBackendConfig(url="https://gw.example.test/v1/decisions", api_key="test-key"),
            transport=transport,
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "decision-replay.json"
            first = RecordingBackend(live, fixture, DECISIONS_DIALECT_OPENAI_GA)
            first.decide(sample_request())
            second = RecordingBackend(live, fixture, DECISIONS_DIALECT_OPENAI_GA)
            second.decide(sample_request(input="second batch evidence"))
            replay = ReplayBackend(fixture, DECISIONS_DIALECT_OPENAI_GA)
            self.assertEqual(len(replay.fixture_digests), 2)

    def test_unknown_dialect_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "decision-replay.json"
            with self.assertRaisesRegex(DecisionSchemaError, "DECISION_DIALECT_UNKNOWN"):
                ReplayBackend(fixture, "bogus")


if __name__ == "__main__":
    unittest.main()
