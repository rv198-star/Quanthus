import json
from copy import deepcopy

import pytest

from quanthus.r9_reference import (
    AUTO_SOL_METADATA_WARNING,
    AUTO_SOL_MODEL,
    collect_role,
    strict_json,
    validate_opinion,
    validate_trace,
)


def valid():
    packet = {"case_id": "opaque-case", "HLC40": [["105.0000", "95.0000", "100.0000"]] * 40}
    value = {
        "case_id": "opaque-case",
        "reviewer": "fund",
        "label": "TRUE",
        "support": [
            {"offset": n, "field": "H", "value": "105.0000", "reason": "bounded"} for n in (1, 21)
        ],
        "counterevidence": [],
        "rationale": "comparison",
    }
    return value, packet


def test_exact_blind_identity_and_price_citations():
    value, packet = valid()
    assert validate_opinion(value, packet, "fund") == value
    altered = deepcopy(value)
    altered["support"][0]["value"] = "105"
    with pytest.raises(ValueError, match="CITATION_VALUE"):
        validate_opinion(altered, packet, "fund")


@pytest.mark.parametrize(
    "change", ["identity", "future", "field", "evidence", "window", "schema", "type"]
)
def test_unknown_or_invalid_outputs_cannot_become_definite_labels(change):
    value, packet = valid()
    if change == "identity":
        value["case_id"] = "other"
    elif change == "future":
        value["support"][0]["offset"] = 41
    elif change == "field":
        value["support"][0]["field"] = "future-return"
    elif change == "evidence":
        value["support"] = []
    elif change == "window":
        value["support"] = value["support"][1:]
    elif change == "schema":
        value["prediction"] = 0.9
    else:
        value["label"] = []
    with pytest.raises(ValueError):
        validate_opinion(value, packet, "fund")


def test_actual_tool_trace_and_incomplete_turn_fail_closed():
    events = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
        {"type": "turn.completed", "usage": {}},
    ]
    validate_trace("\n".join(map(json.dumps, events)))
    events[2]["item"]["type"] = "command_execution"
    with pytest.raises(ValueError, match="TOOL_OR_CONTEXT"):
        validate_trace("\n".join(map(json.dumps, events)))
    with pytest.raises(ValueError, match="INCOMPLETE"):
        validate_trace(json.dumps(events[0]))


@pytest.mark.parametrize(
    "event",
    [
        None,
        [],
        {"type": []},
        {"type": {}},
        {"type": "item.completed", "item": None},
        {"type": "item.completed", "item": []},
        {"type": "item.completed", "item": {"type": []}},
    ],
)
def test_malformed_trace_is_an_accounted_validation_failure(event):
    with pytest.raises(ValueError):
        validate_trace(json.dumps(event) + "\n" + json.dumps({"type": "turn.completed"}))


def test_duplicate_label_keys_are_rejected():
    with pytest.raises(ValueError, match="DUPLICATE_JSON_KEY"):
        strict_json('{"label":"FALSE","label":"TRUE"}')


def test_changed_qualified_input_stops_before_any_call_or_output(tmp_path):
    qualified = tmp_path / "input.json"
    qualified.write_text("{}")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"qualified_input": {"qualified_cases_sha256": "0" * 64}}))
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="QUALIFIED_HASH"):
        collect_role(qualified, "guard", output, tmp_path / "sandbox", request=request)
    assert not output.exists()


def test_prior_support_with_only_current_counterevidence_is_not_positive_support():
    value, packet = valid()
    value["counterevidence"] = [value["support"].pop()]
    with pytest.raises(ValueError, match="TWO_WINDOW_SUPPORT"):
        validate_opinion(value, packet, "fund")


def metadata_trace():
    return [
        {"type": "thread.started", "thread_id": "opaque"},
        {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "error", "message": AUTO_SOL_METADATA_WARNING},
        },
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "{}"}},
        {"type": "turn.completed", "usage": {}},
    ]


def test_declared_model_exact_preturn_diagnostic_is_recorded_not_an_opinion():
    validate_trace("\n".join(map(json.dumps, metadata_trace())), model_id=AUTO_SOL_MODEL)


@pytest.mark.parametrize("change", ["after_turn", "suffix", "twice", "tool_field", "other_model"])
def test_diagnostic_exception_does_not_accept_other_errors_or_tools(change):
    events = metadata_trace()
    model = AUTO_SOL_MODEL
    if change == "after_turn":
        events[1], events[2] = events[2], events[1]
    elif change == "suffix":
        events[1]["item"]["message"] += " another error"
    elif change == "twice":
        events.insert(2, deepcopy(events[1]))
    elif change == "tool_field":
        events[1]["item"]["tool"] = "read_file"
    else:
        model = "codex/gpt-6.1-sol"
    with pytest.raises(ValueError):
        validate_trace("\n".join(map(json.dumps, events)), model_id=model)


def test_metadata_warning_and_completion_require_exactly_one_real_turn():
    events = [e for e in metadata_trace() if e["type"] != "turn.started"]
    with pytest.raises(ValueError, match="TURN_BOUNDARY"):
        validate_trace("\n".join(map(json.dumps, events)), model_id=AUTO_SOL_MODEL)
    events = metadata_trace()
    events.insert(3, {"type": "turn.started"})
    with pytest.raises(ValueError, match="TURN_BOUNDARY"):
        validate_trace("\n".join(map(json.dumps, events)), model_id=AUTO_SOL_MODEL)


def test_rubric_version_cannot_silently_reinterpret_old_receipts():
    import hashlib

    from quanthus.r9_reference import (
        CANONICAL_RUBRIC,
        CANONICAL_RUBRIC_VERSION,
        CANONICAL_SCHEMA,
        RUBRIC,
        reference_rubric,
    )

    assert reference_rubric({}) == RUBRIC
    request = {
        "schema_version": CANONICAL_SCHEMA,
        "reference": {"rubric_version": CANONICAL_RUBRIC_VERSION},
    }
    with pytest.raises(ValueError, match="RUBRIC_BINDING"):
        reference_rubric(request)
    request["reference"]["rubric_sha256"] = hashlib.sha256(CANONICAL_RUBRIC.encode()).hexdigest()
    assert reference_rubric(request) == CANONICAL_RUBRIC
    request["reference"]["rubric_version"] = "undeclared-repair"
    with pytest.raises(ValueError, match="RUBRIC_VERSION"):
        reference_rubric(request)


@pytest.mark.parametrize("reference", [{}, {"rubric_version": None}, {"rubric_sha256": "0" * 64}])
def test_canonical_request_cannot_fall_back_to_legacy_rubric(reference):
    from quanthus.r9_reference import CANONICAL_SCHEMA, reference_rubric

    with pytest.raises(ValueError, match="RUBRIC_VERSION"):
        reference_rubric({"schema_version": CANONICAL_SCHEMA, "reference": reference})
