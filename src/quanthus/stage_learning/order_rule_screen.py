"""One frozen descriptive rule over supplied reference packets; no data or execution route.

The upstream caller must verify the complete evidence package. Local digest/value checks do not
prove source authenticity or semantic correctness. Unmatched predicates mean NO_ASSERTION, not
an externally established NEITHER reference. Recent facts are preserved without a hidden veto.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.order_reference_protocol import PROTOCOL_DIGEST, load_protocol
from quanthus.structure_v2_inputs import canonical, digest, exact_sha

RULE_PATH = "config/stage-learning/order-rule-screen.v1.json"
RULE_DIGEST = "sha256:74d257c75e57b0fa9d09fd183496ee63a3d9cbed21492c36acecc4f7e71bf7f0"
PRIMARY_FIELDS = ("directional_efficiency", "range_width", "width_change", "center_shift")


class OrderRuleError(ValueError):
    """The supplied case or frozen rule is inconsistent."""


def load_rule(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    load_protocol(root)
    try:
        rule = json.loads((root / RULE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OrderRuleError("rule_unreadable") from exc
    if digest(rule) != RULE_DIGEST:
        raise OrderRuleError("rule_drift_requires_new_version")
    return rule


def _number(measurement: Any, field: str) -> Decimal:
    if (not isinstance(measurement, dict)
            or set(measurement) != {"value", "revision_id", "reason"}
            or measurement["reason"] != "available"
            or not exact_sha(measurement["revision_id"])
            or not isinstance(measurement["value"], str)):
        raise OrderRuleError(f"invalid_measurement:{field}")
    try:
        value = Decimal(measurement["value"])
    except InvalidOperation as exc:
        raise OrderRuleError(f"invalid_number:{field}") from exc
    if not value.is_finite():
        raise OrderRuleError(f"nonfinite_number:{field}")
    return value


def stage2_predicate(values: Mapping[str, Decimal], rule: Mapping[str, Any]) -> bool:
    """The exact frozen R0 predicate, reusable without fabricating a legacy packet.

    A false predicate is NO_ASSERTION, not a semantic negative reference. The caller
    still owns packet/source/scale admission. No thresholds are fitted here.
    """
    if digest(rule) != RULE_DIGEST:
        raise OrderRuleError("rule_drift_requires_new_version")
    if set(values) != set(PRIMARY_FIELDS) or any(
        not isinstance(v, Decimal) or not v.is_finite() for v in values.values()
    ):
        raise OrderRuleError("invalid_stage2_values")
    e = values["directional_efficiency"].copy_abs()
    w, d = values["range_width"], values["width_change"]
    c = values["center_shift"].copy_abs()
    if e > 1 or not 0 <= w < 2 or d < -1 or values["center_shift"] <= -1:
        raise OrderRuleError("formal_measurement_range_invalid")
    two = {key: Decimal(value) for key, value in rule["rules"]["stage_2"].items()}
    return (e <= two["efficiency20_abs_max"] and w <= two["range_width20_max"]
            and d <= two["width_change20_max"] and c <= two["center_shift20_abs_max"])


def evaluate_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one already-built packet without fetching data or modifying the packet."""
    rule = load_rule()
    if not isinstance(case, dict):
        raise OrderRuleError("case_object_required")
    case_id = case.get("case_id")
    try:
        if not exact_sha(case_id) or digest({
            key: value for key, value in case.items() if key != "case_id"
        }) != case_id:
            raise OrderRuleError("case_digest_mismatch")
        if (case["protocol_digest"] != PROTOCOL_DIGEST
                or type(case["primary_window_sessions"]) is not int
                or case["primary_window_sessions"] != 20
                or type(case["recent_window_sessions"]) is not int
                or case["recent_window_sessions"] != 10
                or case["knowledge_view"] != "retrospective"):
            raise OrderRuleError("case_profile_mismatch")
        day = date.fromisoformat(case["market_date"])
        if not date(2020, 1, 1) <= day <= date(2021, 12, 31):
            raise OrderRuleError("case_outside_development")
        if type(case["primary_available"]) is not bool:
            raise OrderRuleError("availability_boolean_required")
        if set(case["primary"]) != set(PRIMARY_FIELDS):
            raise OrderRuleError("primary_fields_mismatch")
        result = {
            "schema_version": "quanthus.stage-order-rule-screen-result.v1",
            "rule_id": rule["rule_id"], "rule_digest": RULE_DIGEST, "case_id": case_id,
            "decision": "INPUT_UNAVAILABLE",
            "assertions": {"stage_0": None, "stage_2": None},
            "reason_codes": ["primary_input_unavailable"],
            "fact_revision_ids": {
                name: case["primary"][name]["revision_id"] for name in PRIMARY_FIELDS
            },
            "recent": json.loads(canonical(case["recent"])),
            "primary_stage": None, "official_label_authorized": False,
            "independent_semantic_validation": False,
        }
        if not case["primary_available"]:
            return result
        values = {name: _number(case["primary"][name], name) for name in PRIMARY_FIELDS}
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise OrderRuleError("case_fields_invalid") from exc
    # copy_abs is exact even when a caller changes the ambient Decimal precision.
    e = values["directional_efficiency"].copy_abs()
    w, d = values["range_width"], values["width_change"]
    c = values["center_shift"].copy_abs()
    if e > 1 or not 0 <= w < 2 or d < -1 or values["center_shift"] <= -1:
        raise OrderRuleError("formal_measurement_range_invalid")
    zero = {key: Decimal(value) for key, value in rule["rules"]["stage_0"].items()}
    expansion = d >= zero["width_change20_min"]
    migration = c >= zero["center_shift20_abs_min"]
    stage_0 = e <= zero["efficiency20_abs_max"] and (expansion or migration)
    stage_2 = stage2_predicate(values, rule)
    if stage_0 and stage_2:
        decision, reasons = "RULE_CONFLICT", ["same_scale_assertion_conflict"]
    elif stage_0:
        decision, reasons = "STAGE_0", ["low_directional_efficiency"]
        if expansion:
            reasons.append("main_window_expansion")
        if migration:
            reasons.append("main_window_center_migration")
    elif stage_2:
        decision = "STAGE_2"
        reasons = ["low_directional_efficiency", "bounded_contracting_stable_center"]
    else:
        decision, reasons = "NO_ASSERTION", ["no_positive_predicate_not_a_neither_reference"]
    result.update(decision=decision, assertions={"stage_0": stage_0, "stage_2": stage_2},
                  reason_codes=reasons)
    return result
