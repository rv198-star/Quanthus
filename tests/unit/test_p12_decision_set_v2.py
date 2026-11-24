'''p12.decision-set.v2 reader, schema and pin (issue #723 first step).

Covers the frozen comment-7121082946 rules: the inner decision_set_schema
marker, the v2-required exit_kind on exit_plan, v1 fallback with the original
bytes/digest, the leaked-v2-field breach, and the consumer pin that rejects a
non-admitted schema outright.
'''

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.decision_access import OpportunityStage
from quanthus.p12_daily.decision_loader import (
    DECISION_SET_SCHEMA_V1,
    DECISION_SET_SCHEMA_V2,
    decision_set_to_dict,
    load_decision_set,
    load_decision_set_dict,
)
from quanthus.shared.fact_contracts import FactError, digest

FIXTURES = Path("tests/fixtures/p12/daily-advice-v2")


def _raw(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_v2_reduce_reads_exit_kind_and_target() -> None:
    raw = _raw("reduce.json")
    value, d = load_decision_set(
        raw, expected_schema=DECISION_SET_SCHEMA_V2, mode="synthetic"
    )
    item = value.decisions[0]
    assert item.stage is OpportunityStage.EXIT_PLAN
    assert item.exit_kind == "reduce"
    assert item.target_position_fraction == Decimal("0.50")
    assert d == digest(raw)


def test_v2_full_exit_target_is_null() -> None:
    raw = _raw("full.json")
    value, _ = load_decision_set(
        raw, expected_schema=DECISION_SET_SCHEMA_V2, mode="synthetic"
    )
    item = value.decisions[0]
    assert item.exit_kind == "full"
    assert item.target_position_fraction is None


def test_v2_missing_exit_kind_is_refused() -> None:
    raw = _raw("missing-exit-kind.json")
    with pytest.raises(FactError) as exc:
        load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V2, mode="synthetic")
    assert "exit_kind" in str(exc.value)


def test_tampered_payload_fails_pinned_digest() -> None:
    raw = _raw("reduce.json")
    _, good = load_decision_set(
        raw, expected_schema=DECISION_SET_SCHEMA_V2, mode="synthetic"
    )
    tampered = copy.deepcopy(raw)
    tampered["published_at"] = "2026-11-24T23:59:00+08:00"
    _, bad = load_decision_set(
        tampered, expected_schema=DECISION_SET_SCHEMA_V2, mode="synthetic"
    )
    assert bad != good


def test_v1_baseline_reads_with_original_digest() -> None:
    raw = _raw("v1-baseline.json")
    value, d = load_decision_set(
        raw, expected_schema=DECISION_SET_SCHEMA_V1, mode="synthetic"
    )
    item = value.decisions[0]
    assert item.exit_kind is None
    assert item.target_position_fraction is None
    assert d == digest(raw)


def test_v1_set_with_leaked_v2_field_is_refused() -> None:
    raw = _raw("v1-leaked-field.json")
    for schema in (DECISION_SET_SCHEMA_V1, DECISION_SET_SCHEMA_V2):
        with pytest.raises(FactError):
            load_decision_set(raw, expected_schema=schema, mode="synthetic")


def test_v2_set_refused_under_v1_pin() -> None:
    raw = _raw("reduce.json")
    with pytest.raises(FactError):
        load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V1, mode="synthetic")


def test_v1_set_refused_under_v2_pin() -> None:
    raw = _raw("v1-baseline.json")
    with pytest.raises(FactError):
        load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V2, mode="synthetic")


def test_unsupported_expected_schema_is_refused() -> None:
    with pytest.raises(FactError):
        load_decision_set(
            _raw("reduce.json"), expected_schema="p12.decision-set.v9", mode="synthetic"
        )


def test_legacy_reader_rejects_declared_v2() -> None:
    # The pre-v2 reader (load_decision_set_dict) must refuse a declared v2 set
    # outright rather than dropping the new fields silently.
    with pytest.raises(FactError):
        load_decision_set_dict(_raw("reduce.json"), mode="synthetic")


def test_legacy_reader_still_reads_bare_v1() -> None:
    value = load_decision_set_dict(_raw("v1-baseline.json"), mode="synthetic")
    assert value.decisions[0].exit_kind is None


def test_round_trip_emits_v2_marker_only_when_v2_fields_present() -> None:
    raw = _raw("reduce.json")
    value, _ = load_decision_set(
        raw, expected_schema=DECISION_SET_SCHEMA_V2, mode="synthetic"
    )
    wire = decision_set_to_dict(value)
    assert wire["decision_set_schema"] == DECISION_SET_SCHEMA_V2
    assert wire["decisions"][0]["exit_kind"] == "reduce"
    assert wire["decisions"][0]["target_position_fraction"] == "0.50"
    # And the emitted wire re-loads under the v2 pin.
    value2, _ = load_decision_set(
        wire, expected_schema=DECISION_SET_SCHEMA_V2, mode="synthetic"
    )
    assert value2.decisions[0].exit_kind == "reduce"

    v1 = load_decision_set_dict(_raw("v1-baseline.json"), mode="synthetic")
    wire_v1 = decision_set_to_dict(v1)
    assert "decision_set_schema" not in wire_v1
    assert "exit_kind" not in wire_v1["decisions"][0]


def test_target_fraction_only_on_reduce() -> None:
    raw = _raw("full.json")
    raw["decisions"][0]["target_position_fraction"] = "0.30"
    with pytest.raises(FactError):
        load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V2, mode="synthetic")


def test_exit_kind_only_on_exit_plan() -> None:
    raw = _raw("v1-baseline.json")
    raw["decision_set_schema"] = DECISION_SET_SCHEMA_V2
    raw["decisions"][0]["exit_kind"] = "full"  # stage is holding, not exit_plan
    with pytest.raises(FactError):
        load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V2, mode="synthetic")
