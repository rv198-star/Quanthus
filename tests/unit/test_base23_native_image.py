from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest

from quanthus.pipelines.securities_warehouse import base23_native_image as n
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base22_native_keys import (
    NativeGlobalScan as AugustScan,
)
from quanthus.pipelines.securities_warehouse.base23_delivery import validate_contract
from quanthus.pipelines.securities_warehouse.base23_native_keys import NativeGlobalScan
from tests.unit.test_base22_native_image import _native_fixture, selection


def test_explicit_successor_contract_and_august_producer_stays_frozen():
    config = Path("config/pipelines")
    old = json.loads((config / "base22-delivery.v2.json").read_text())
    new = json.loads((config / "base23-delivery.v1.json").read_text())
    validate_contract(new)
    with pytest.raises(BoundedError):
        validate_contract(old)
    plan, rules, descriptors = selection()
    with pytest.raises(BoundedError, match="SELECTION_SCOPE"):
        n.validate_selection(plan, rules, descriptors)
    plan["through_date"] = "2026-09-30"
    n.validate_selection(plan, rules, descriptors)
    assert old["permanent_protections"] == new["permanent_protections"]
    assert old["required_structural_checks"] == new["required_structural_checks"]


@pytest.mark.parametrize("damage", ["purpose", "source", "quality", "extra_empty"])
def test_successor_repacked_offline_contract_does_not_relax_source_or_physical_closure(damage):
    plan, rules, descriptors = copy.deepcopy(selection())
    plan["through_date"] = "2026-09-30"
    entry = plan["tables"]["table"]
    if damage == "purpose":
        plan["purpose"] = "trading"
    elif damage == "source":
        entry["source_ids"] = []
    elif damage == "quality":
        plan["sources"]["native"]["quality_gate"] = "unqualified"
    else:
        item = {**entry["files"][0], "rows": 0, "partition": "extra",
                "path": "payload/table/empty.parquet"}
        descriptors["native"]["files"].append({"path": item["path"], "sha256": item["sha256"]})
        entry["files"].append(item)
        entry["expected_partition_rows"]["extra"] = 0
    with pytest.raises(BoundedError):
        n.validate_selection(plan, rules, descriptors)


def test_shanghai_september_boundary_and_original_august_date_predicate():
    field = pa.field("clock", pa.timestamp("us", tz="UTC"))
    new = NativeGlobalScan._cutoff_predicate(None, "clock", field)
    old = AugustScan._cutoff_predicate(None, "clock", field)
    assert "2026-09-30T16:00:00+00:00" in new
    assert "2026-08-31T16:00:00+00:00" in old
    predicate = NativeGlobalScan._cutoff_predicate(None, "day", pa.field("day", pa.date32()))
    assert "2026-09-30" in predicate


def test_complete_33_table_successor_materialization_and_offline_native_scan(tmp_path, monkeypatch):
    f, config, _, plan = _native_fixture(tmp_path)
    plan["through_date"] = "2026-09-30"
    output = tmp_path / "september-image"
    n.materialize_native_image(
        plan=plan, rules=f.contract,
        contract=json.loads((config / "base23-delivery.v1.json").read_bytes()),
        model=f.model, extension=f.extension, limitations=[], local_evidence={},
        output=output, allocated_limit=64*1024**2, free_floor=1,
        rss_limit=4*1024**3, wall_seconds=30, config_root=config,
    )
    real = os.statvfs

    def readonly(path):
        value = real(path)
        if Path(path) == output or output in Path(path).parents:
            fields = {k: getattr(value, k) for k in dir(value) if k.startswith("f_")}
            return SimpleNamespace(**fields | {"f_flag": value.f_flag | os.ST_RDONLY})
        return value

    monkeypatch.setattr(n.os, "statvfs", readonly)
    with Budget(tmp_path / "scratch", [output], Limits(
        free_floor_bytes=1, allocated_bytes=64*1024**2, temp_bytes=32*1024**2,
    )) as budget:
        receipt = n.audit_native_image(output, budget)
    assert len(receipt["summaries"]) == 33
    assert receipt["kind"] == n.KIND
    assert receipt["assessment"]["build_eligible"]
    assert not receipt["assessment"]["independently_verified"]
    assert not (output / "READY").exists()
    assert receipt["assessment"]["business_through"] == "2026-09-30"
