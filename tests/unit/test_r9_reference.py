import json
from copy import deepcopy

import pytest

from quanthus.r9_reference import collect_role, strict_json, validate_opinion, validate_trace


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
        {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
        {"type": "turn.completed", "usage": {}},
    ]
    validate_trace("\n".join(map(json.dumps, events)))
    events[1]["item"]["type"] = "command_execution"
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
