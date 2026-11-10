from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_native_keys import NativeGlobalScan
from quanthus.pipelines.securities_warehouse.base24_image import PROFILE as LEGACY_PROFILE
from quanthus.pipelines.securities_warehouse.base_build_profile import (
    HARD_CHECKS,
    StandardGlobalScan,
    delivery_contract,
    profile,
    validate_contract,
)


def contract():
    legacy = json.loads(Path("config/pipelines/base24-delivery.v1.json").read_text())
    return delivery_contract(legacy, version="2.5", predecessor_version="2.4",
                             through="2026-10-31")


@pytest.mark.parametrize("value,forbidden", [
    ("2026-10-01 00:00:00+08", False),
    ("2026-10-31 23:59:59.999999+08", False),
    ("2026-11-01 00:00:00+08", True),
])
def test_actual_target_cutoff_uses_shanghai_boundary(value, forbidden):
    scan = object.__new__(StandardGlobalScan)
    scan.through = date(2026, 10, 31)
    predicate = scan._cutoff_predicate("event_at", pa.field("event_at", pa.timestamp("us", "UTC")))
    with duckdb.connect() as connection:
        result = connection.execute(
            "SELECT " + predicate + " FROM (SELECT ?::TIMESTAMPTZ AS event_at)", [value],
        ).fetchone()[0]
    assert result is forbidden


def test_legacy_september_profile_and_scan_are_unchanged():
    old = object.__new__(NativeGlobalScan)
    field = pa.field("event_at", pa.timestamp("us", "UTC"))
    predicate = old._cutoff_predicate("event_at", field)
    with duckdb.connect() as connection:
        assert connection.execute(
            "SELECT " + predicate + " FROM (SELECT TIMESTAMPTZ '2026-10-01 00:00:00+08' event_at)",
        ).fetchone()[0] is True
    assert LEGACY_PROFILE.business_through == "2026-09-30"
    assert LEGACY_PROFILE.scan_factory is None


def test_standard_profile_has_full_structural_gates_and_versioned_cutoff():
    c = contract()
    p = profile(c)
    assert p.business_through == "2026-10-31"
    assert p.version == "2.5"
    assert p.hard_checks == HARD_CHECKS
    assert p.scan_factory is not None
    assert p.kind == "quanthus_dwd_base_bundle_v1"
    later = copy.deepcopy(c)
    later.update(base_version="2.6", predecessor_version="2.5", business_through="2026-11-30")
    assert profile(later).business_through == "2026-11-30"
    # Profile construction alone never produces a materialization or READY.


@pytest.mark.parametrize("field,value", [
    ("derive_5m_from_1m", True),
    ("required_structural_checks", []),
    ("required_intervals", ["1d"]),
    ("permanent_protections", {}),
    ("model_sha256", "sha256:" + "0" * 64),
])
def test_version_neutral_contract_cannot_weaken_inherited_protections(field, value):
    c = contract()
    c[field] = value
    with pytest.raises(BoundedError):
        validate_contract(c)


def test_table_policy_cannot_allow_extra_held_or_infer_refresh():
    for field, value in [("new_quality_held", 1), ("other_tables", "ALL_REFRESHED")]:
        c = contract()
        c["table_policy"][field] = value
        with pytest.raises(BoundedError, match="TABLE_POLICY_CHANGED"):
            validate_contract(c)


def test_layer_and_version_metadata_must_match_profile():
    with pytest.raises(BoundedError, match="CONTENT_SCOPE_INVALID"):
        profile(contract(), content={"role": "ods", "base_version": "2.5",
                                     "through_date": "2026-10-31"})


@pytest.mark.parametrize("version,through", [("2.4", "2026-10-31"), ("2.5", "Oct 31")])
def test_invalid_target_scope_is_rejected(version, through):
    c = contract()
    c.update(base_version=version, business_through=through)
    with pytest.raises(BoundedError, match="VERSION_OR_CUTOFF_INVALID"):
        validate_contract(c)
