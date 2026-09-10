from __future__ import annotations

import copy
import json
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

import pytest

from quanthus.stage_learning import order_rule_screen as api
from quanthus.structure_v2_inputs import canonical, digest

from .test_stage_order_reference_protocol import make_evidence
from .test_structure_v2_inputs import row

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def template():
    return make_evidence()["cases"][0]


def bind(case):
    case["case_id"] = digest({key: value for key, value in case.items() if key != "case_id"})
    return case


def vector(template, e="0.20", w="0.12", d="-0.25", c="0.01"):
    """Predicate unit inputs, not claims of independently verified price-path evidence."""
    case = copy.deepcopy(template)
    for key, value in zip(api.PRIMARY_FIELDS, (e, w, d, c), strict=True):
        case["primary"][key]["value"] = value
        case["primary"][key]["reason"] = "available"
    case["primary_available"] = True
    return bind(case)


def test_parameters_are_the_old_preregistered_seed_not_newly_fitted():
    rule = api.load_rule()
    old = json.loads((ROOT / rule["parameter_origin"]["path"]).read_text())
    assert rule["rules"] == old["r0"]
    assert digest(rule) == api.RULE_DIGEST
    assert rule["candidate_count"] == 1
    assert not rule["authority"]["runtime_job_submission"]
    assert not rule["authority"]["parameter_search"]


@pytest.mark.parametrize("e,w,d,c,expected", [
    ("0.33", "0.18", "-0.20", "0.04", "STAGE_2"),
    ("-0.33", "0.18", "-0.20", "-0.04", "STAGE_2"),
    ("0.330000000000000000000000001", "0.18", "-0.20", "0.04", "NO_ASSERTION"),
    ("0.33", "0.180000000000000000000000001", "-0.20", "0.04", "NO_ASSERTION"),
    ("0.33", "0.18", "-0.199999999999999999999999999", "0.04", "NO_ASSERTION"),
    ("0.33", "0.18", "-0.20", "0.040000000000000000000000001", "NO_ASSERTION"),
    ("0.40", "0.12", "0.20", "0.00", "STAGE_0"),
    ("-0.40", "0.12", "0.20", "0.00", "STAGE_0"),
    ("0.40", "0.12", "-0.20", "0.07", "STAGE_0"),
    ("0.40", "0.12", "-0.20", "-0.07", "STAGE_0"),
    ("0.400000000000000000000000001", "0.12", "1.00", "0.20", "NO_ASSERTION"),
    ("0.40", "0.12", "0.199999999999999999999999999", "0.00", "NO_ASSERTION"),
    ("0.40", "0.12", "0.00", "0.069999999999999999999999999", "NO_ASSERTION"),
    ("0.75", "0.50", "1.00", "0.40", "NO_ASSERTION"),
    ("0.00", "0.12", "0.00", "0.00", "NO_ASSERTION"),
    ("0.00", "0.12", "-0.50", "0.05", "NO_ASSERTION"),
])
def test_exact_thresholds_and_non_residual_behavior(template, e, w, d, c, expected):
    result = api.evaluate_case(vector(template, e, w, d, c))
    assert result["decision"] == expected
    assert result["primary_stage"] is None
    assert not result["official_label_authorized"]
    assert not result["independent_semantic_validation"]


def test_expansion_and_migration_reasons_are_not_conflated(template):
    result = api.evaluate_case(vector(template, d="0.20", c="0.10"))
    assert result["reason_codes"] == [
        "low_directional_efficiency", "main_window_expansion", "main_window_center_migration",
    ]


def test_actual_admitted_paths_keep_main_scale_when_recent_changes():
    results = []
    for contracting in (True, False):
        rows = []
        for i in range(80):
            high, low = ("108", "92") if i < 60 else (
                ("106", "94") if (i < 70) == contracting else ("104", "96")
            )
            rows.append(row(i, str(99 if i % 2 == 0 else 101),
                            high=Decimal(high), low=Decimal(low)))
        case = make_evidence(rows)["cases"][0]
        assert case["primary_available"] is True
        results.append(api.evaluate_case(case))
    assert [r["decision"] for r in results] == ["STAGE_2", "STAGE_2"]
    assert results[0]["recent"] != results[1]["recent"]
    assert results[0]["fact_revision_ids"] != results[1]["fact_revision_ids"]


def test_missing_input_is_not_no_assertion_or_stage_zero():
    rows = [row(i, "100", high=Decimal(106), low=Decimal(94)) for i in range(80)]
    rows[-1] = row(79, None, quality_reason="missing_bar")
    case = make_evidence(rows)["cases"][0]
    result = api.evaluate_case(case)
    assert result["decision"] == "INPUT_UNAVAILABLE"
    assert result["assertions"] == {"stage_0": None, "stage_2": None}


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "oops", None, True, 0.2])
def test_invalid_values_never_become_a_market_judgement(template, value):
    case = vector(template)
    case["primary"]["directional_efficiency"]["value"] = value
    with pytest.raises(api.OrderRuleError):
        api.evaluate_case(bind(case))


@pytest.mark.parametrize("changes", [
    {"primary_window_sessions": 10}, {"recent_window_sessions": 20},
    {"primary_window_sessions": True}, {"primary_available": 1},
    {"knowledge_view": "public_as_known"}, {"market_date": "2025-01-01"},
    {"protocol_digest": "sha256:" + "0" * 64},
])
def test_case_identity_scale_and_partition_boundaries(template, changes):
    case = vector(template)
    case.update(changes)
    with pytest.raises(api.OrderRuleError):
        api.evaluate_case(bind(case))


def test_tampering_is_rejected_and_call_does_not_mutate_evidence(template):
    case = vector(template)
    original = canonical(case)
    result = api.evaluate_case(case)
    result["recent"]["width_change"]["value"] = "999"
    assert canonical(case) == original
    case["primary"]["width_change"]["value"] = "999"
    with pytest.raises(api.OrderRuleError, match="digest"):
        api.evaluate_case(case)


@pytest.mark.parametrize("e,c", [("0.334", "0.01"), ("0.20", "-0.0404")])
def test_caller_decimal_precision_does_not_change_the_decision(template, e, c):
    case = vector(template, e=e, c=c)
    expected = api.evaluate_case(case)
    assert expected["decision"] == "NO_ASSERTION"
    with localcontext() as context:
        context.prec = 2
        assert api.evaluate_case(case) == expected


def test_rule_changes_require_a_new_version():
    raw = api.load_rule()
    raw["rules"]["stage_2"]["range_width20_max"] = "0.99"
    with patch.object(api, "load_protocol"), patch.object(
        Path, "read_text", return_value=json.dumps(raw),
    ), pytest.raises(api.OrderRuleError, match="rule_drift"):
        api.load_rule(ROOT)
