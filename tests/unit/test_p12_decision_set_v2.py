'''p12.decision-set.v2 reader, schema and pin (issue #723 first step).

Covers the frozen comment-7121082946 rules: the inner decision_set_schema
marker, the v2-required exit_kind on exit_plan, v1 fallback with the original
bytes/digest, the leaked-v2-field breach, and the consumer pin that rejects a
non-admitted schema outright.
'''

from __future__ import annotations

import copy
import datetime
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
from quanthus.p12_daily.package_store import DailyPackageStore
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


# --- issue #723 second step: audit R-returns -------------------------------


def test_frozen_signature_accepts_p11_wire() -> None:
    """P11's real serialized v2 wire (f36fa95e) loads under the frozen
    signature - no mode argument - and returns the reduce decision.  The
    account-mode binding is NOT asserted at this entry; it is enforced at the
    published-package boundary."""

    raw = _raw("p11-f36fa95e-reduce.json")
    value, d = load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V2)
    item = value.decisions[0]
    assert item.exit_kind == "reduce"
    assert item.target_position_fraction == Decimal("0.5")
    assert d == digest(raw)
    # Wire carries no mode field and no account binding was asserted, so the
    # set adopts the default (non-synthetic) mode.
    assert value.mode == "simulation"


def test_p11_wire_audit_block_is_accepted() -> None:
    """The v2 schema admits the P11 producer ``audit`` member; the loader
    ignores it."""

    raw = _raw("p11-f36fa95e-reduce.json")
    assert "audit" in raw
    value, _ = load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V2)
    assert value.decisions[0].exit_kind == "reduce"


def test_wire_mode_field_adopted_when_mode_none() -> None:
    raw = _raw("v1-baseline.json")
    raw["mode"] = "real"
    value, _ = load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V1)
    assert value.mode == "real"
    assert value.synthetic is False


def test_explicit_mode_still_enforced_against_wire() -> None:
    raw = _raw("v1-baseline.json")
    raw["mode"] = "simulation"
    with pytest.raises(FactError):
        load_decision_set(
            raw, expected_schema=DECISION_SET_SCHEMA_V1, mode="synthetic"
        )


def test_null_v2_fields_without_marker_are_refused() -> None:
    """A bare set carrying an explicit-null exit_kind or
    target_position_fraction is a v2 version breach - key presence decides,
    not the value."""

    for field in ("exit_kind", "target_position_fraction"):
        raw = _raw("v1-baseline.json")
        raw["decisions"][0][field] = None
        with pytest.raises(FactError):
            load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V1)


def test_reduce_without_target_is_refused() -> None:
    raw = _raw("reduce.json")
    del raw["decisions"][0]["target_position_fraction"]
    with pytest.raises(FactError) as exc:
        load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V2)
    assert "target_position_fraction" in str(exc.value)


@pytest.mark.parametrize(
    "target",
    ["0", "1", "1.5", "-0.2", 0.5, "5e-1", "abc", ""],
)
def test_reduce_target_must_be_strict_decimal_in_open_range(target) -> None:
    """JSON numbers, exponent forms, out-of-range and non-string targets are
    refused on a reduce."""

    raw = _raw("reduce.json")
    raw["decisions"][0]["target_position_fraction"] = target
    with pytest.raises(FactError):
        load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V2)


def test_full_exit_must_not_carry_target() -> None:
    raw = _raw("full.json")
    raw["decisions"][0]["target_position_fraction"] = "0.4"
    with pytest.raises(FactError):
        load_decision_set(raw, expected_schema=DECISION_SET_SCHEMA_V2)


def test_legacy_reader_refuses_v1_set_with_null_v2_field() -> None:
    raw = _raw("v1-baseline.json")
    raw["decisions"][0]["exit_kind"] = None
    with pytest.raises(FactError):
        load_decision_set_dict(raw, mode="synthetic")


def test_legacy_reader_refuses_v1_set_with_value_v2_field() -> None:
    raw = _raw("v1-baseline.json")
    raw["decisions"][0]["target_position_fraction"] = "0.5"
    with pytest.raises(FactError):
        load_decision_set_dict(raw, mode="synthetic")


# --- issue #723 second step: envelope <-> inner consistency ----------------


def _write_envelope(state_dir, *, envelope_schema: str, decision_set: dict) -> None:
    pkg_dir = Path(state_dir) / "daily-packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    body = {
        "schema_version": envelope_schema,
        "package_id": "p12-daily-2026-11-24",
        "market_date": "2026-11-24",
        "published_at": "2026-11-24T22:35:00+08:00",
        "runner": {},
        "input_evidence": {},
        "decision_set": decision_set,
    }
    (pkg_dir / "2026-11-24.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2)
    )


def test_v1_envelope_carrying_v2_inner_is_refused(tmp_path) -> None:
    """A v1 envelope must never wrap a v2 decision_set; the legacy reader
    would silently drop the new exit fields."""

    _write_envelope(
        tmp_path,
        envelope_schema="quanthus.p12.daily-package.v1",
        decision_set=_raw("reduce.json"),
    )
    with pytest.raises(FactError) as exc:
        DailyPackageStore(tmp_path).package_for(
            datetime.date(2026, 11, 24)
        )
    assert "v1" in str(exc.value) or "envelope" in str(exc.value).lower()


def test_v2_envelope_carrying_v1_inner_reads(tmp_path) -> None:
    """A v2 envelope may carry a bare v1 inner set for back-compat."""

    _write_envelope(
        tmp_path,
        envelope_schema="quanthus.p12.daily-package.v2",
        decision_set=_raw("v1-baseline.json"),
    )
    stored = DailyPackageStore(tmp_path).package_for(
        datetime.date(2026, 11, 24)
    )
    assert stored.decision_set["source_id"] == "p11-g1-synthetic"


def test_v2_envelope_carrying_v2_inner_reads(tmp_path) -> None:
    _write_envelope(
        tmp_path,
        envelope_schema="quanthus.p12.daily-package.v2",
        decision_set=_raw("reduce.json"),
    )
    stored = DailyPackageStore(tmp_path).package_for(
        datetime.date(2026, 11, 24)
    )
    assert stored.decision_set["decision_set_schema"] == DECISION_SET_SCHEMA_V2


def test_v1_envelope_bare_inner_with_v2_keys_is_refused(tmp_path) -> None:
    """A v1 envelope must refuse a bare inner set (no decision_set_schema)
    that smuggles a v2-only key, whether the value is a real value or null."""

    for field, value in (
        ("exit_kind", "reduce"),
        ("exit_kind", None),
        ("target_position_fraction", "0.5"),
        ("target_position_fraction", None),
    ):
        inner = _raw("v1-baseline.json")  # no decision_set_schema marker
        inner["decisions"][0][field] = value
        _write_envelope(
            tmp_path,
            envelope_schema="quanthus.p12.daily-package.v1",
            decision_set=inner,
        )
        with pytest.raises(FactError) as exc:
            DailyPackageStore(tmp_path).package_for(
                datetime.date(2026, 11, 24)
            )
        assert "v1" in str(exc.value) or "envelope" in str(exc.value).lower()
        # clean the package so the next field writes a fresh file
        (Path(tmp_path) / "daily-packages" / "2026-11-24.json").unlink()


def test_v1_envelope_bare_inner_reads_through_stored_for(tmp_path) -> None:
    """Regression: a clean v1 envelope with a bare v1 inner set still reads
    via package_for/stored_for with bytes and digest unchanged."""

    inner = _raw("v1-baseline.json")
    _write_envelope(
        tmp_path,
        envelope_schema="quanthus.p12.daily-package.v1",
        decision_set=inner,
    )
    stored = DailyPackageStore(tmp_path).package_for(
        datetime.date(2026, 11, 24)
    )
    assert stored.decision_set["source_id"] == "p11-g1-synthetic"
    assert "exit_kind" not in stored.decision_set["decisions"][0]
