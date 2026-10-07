"""Actual typed global sort, bounded keys and precise successor intervals."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base22_f10_ownership import TABLES
from quanthus.pipelines.securities_warehouse.base22_ownership_versions import (
    ownership_versions,
    version_batches,
)
from quanthus.pipelines.securities_warehouse.ownership_capital import ownership_capital_schemas
from tests.unit.test_base22_f10_ownership import project, raw


def case():
    row = project(raw())[0][TABLES[0]][0]
    rule = load_contract(Path("config/pipelines"))[0]["tables"][TABLES[0]]
    return row, rule


def test_preserve_original_atoms_and_close_at_actual_changed_successor():
    old, rule = case()
    new = deepcopy(old)
    new["knowledge_from"] += timedelta(days=7)
    new["share_quantity"] += 1
    before = deepcopy([old, new])
    result = ownership_versions([new, old], rule)
    assert [r["share_quantity"] for r in result] == [old["share_quantity"], new["share_quantity"]]
    assert result[0]["knowledge_to"] == new["knowledge_from"]
    assert result[1]["knowledge_to"] is None and [old, new] == before


def test_identical_later_observation_is_not_a_fake_body_revision():
    old, rule = case()
    new = deepcopy(old)
    new["knowledge_from"] += timedelta(days=7)
    assert ownership_versions([new, old], rule) == [old]


@pytest.mark.parametrize(
    "defect", ["closed", "equal-clock-conflict", "other-key", "overflow", "empty"]
)
def test_source_or_interval_gaps_cannot_be_hidden_as_sorted_versions(defect):
    old, rule = case()
    new = deepcopy(old)
    rows = [old, new]
    if defect == "closed":
        old["knowledge_to"] = old["knowledge_from"] + timedelta(days=1)
    elif defect == "equal-clock-conflict":
        new["share_quantity"] += 1
    elif defect == "other-key":
        new["security_id"] = "other"
    elif defect == "overflow":
        rows *= 33
    else:
        rows = []
    with pytest.raises(RuntimeError, match="OWNERSHIP|EQUAL_CLOCK"):
        ownership_versions(rows, rule)


def test_cross_file_business_keys_and_actual_arrow_sort_have_identical_result(tmp_path):
    old, rule = case()
    schema = ownership_capital_schemas()[TABLES[0]]
    later = deepcopy(old)
    later["share_quantity"] += 1
    later["knowledge_from"] += timedelta(days=7)
    other = deepcopy(old)
    other["security_id"] = "other"
    limits = Limits(allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2, free_floor_bytes=1)
    with Budget(tmp_path / "job", [], limits) as budget:
        paths = []
        for n, rows in enumerate([[later, other], [old]]):
            p = budget.root / f"input-{n}.parquet"
            pq.write_table(pa.Table.from_pylist(rows, schema=schema), p)
            paths.append(p)
        result = [
            r for group in version_batches(TABLES[0], paths, rule, schema, budget) for r in group
        ]
        assert len(result) == 3
        same = [r for r in result if r["security_id"] == old["security_id"]]
        assert same == ownership_versions([old, later], rule)


def test_external_mutable_source_file_cannot_be_used_as_a_job_local_input(tmp_path):
    old, rule = case()
    schema = ownership_capital_schemas()[TABLES[0]]
    source = tmp_path / "source"
    source.mkdir()
    external = source / "external.parquet"
    pq.write_table(pa.Table.from_pylist([old], schema=schema), external)
    limits = Limits(allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2, free_floor_bytes=1)
    with Budget(tmp_path / "job", [source], limits) as budget, pytest.raises(BoundedError):
        list(version_batches(TABLES[0], [external], rule, schema, budget))
