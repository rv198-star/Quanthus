from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, GlobalScan, Limits
from quanthus.pipelines.securities_warehouse.base231_disclosure_body import project_body
from quanthus.pipelines.securities_warehouse.base231_financial_integration import native_batches
from quanthus.pipelines.securities_warehouse.base231_observation_integration import (
    observation_views,
)
from quanthus.pipelines.securities_warehouse.disclosure_event import disclosure_event_schemas
from tests.unit.test_base231_disclosure_body import sample


def body():
    selection, receipt, args = sample()
    return project_body(selection, receipt, **args)[0]["dwd_disclosure"][0]


def run(tmp_path, old, incoming, table="dwd_disclosure"):
    schema = disclosure_event_schemas()[table]
    rule = load_contract(Path("config/pipelines"))[0]["tables"][table]
    with (
        Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as budget,
        GlobalScan(budget) as scan,
    ):
        for name, rows in [("before", old), ("incoming_raw", incoming)]:
            p = budget.root / (name + ".parquet")
            pq.write_table(pa.Table.from_pylist(rows, schema=schema), p)
            scan.add(name, [p])
        proof = observation_views(scan, table, schema, rule)
        rows = [
            r
            for batch in native_batches(scan, "integrated", schema, rule["version_key"])
            for r in batch.to_pylist()
        ]
        return rows, proof


def test_exact_old_version_reuses_without_rewriting(tmp_path):
    a = body()
    rows, proof = run(tmp_path, [a], [a, a])
    assert rows == [a]
    assert proof["exact_native_versions_reused"] == 1
    assert proof["incoming_exact_duplicates"] == 1


def test_body_closes_only_open_end_at_real_new_observation(tmp_path):
    new = body()
    old = dict(
        new,
        knowledge_from=new["knowledge_from"] - timedelta(days=1),
        record_source="cninfo.old_index",
        source_revision="original-index",
        title="旧标题",
    )
    rows, proof = run(tmp_path, [old], [new])
    assert rows == [dict(old, knowledge_to=new["knowledge_from"]), new]
    assert proof["closed_native_intervals"] == 1
    assert old["knowledge_to"] is None


def test_finite_end_and_gap_are_immutable(tmp_path):
    new = body()
    old = dict(
        new,
        knowledge_from=new["knowledge_from"] - timedelta(days=2),
        knowledge_to=new["knowledge_from"] - timedelta(days=1),
    )
    rows, proof = run(tmp_path, [old], [new])
    assert rows == [old, new]
    assert proof["closed_native_intervals"] == 0


@pytest.mark.parametrize(
    "fault", ["company", "document", "contract", "provider", "finite", "retro"]
)
def test_unqualified_or_conflicting_disclosure_successor_fails(tmp_path, fault):
    new = body()
    old = dict(new, knowledge_from=new["knowledge_from"] - timedelta(days=1))
    if fault == "company":
        new["company_id"] = "other"
    elif fault == "document":
        new["source_document_id"] = "other"
    elif fault == "contract":
        new["source_revision"] = "unqualified"
    elif fault == "provider":
        new["record_source"] = "unqualified"
    elif fault == "finite":
        old["knowledge_to"] = new["knowledge_from"] + timedelta(days=1)
    else:
        new["knowledge_from"] = old["knowledge_from"] - timedelta(days=1)
    with pytest.raises(RuntimeError, match="OBSERVATION_"):
        run(tmp_path, [old], [new])


def test_same_version_different_value_is_not_silently_deduplicated(tmp_path):
    a = body()
    with pytest.raises(RuntimeError, match="VERSION_VALUE_CONFLICT"):
        run(tmp_path / "old-conflict", [a], [dict(a, title="conflict")])
    with pytest.raises(RuntimeError, match="INCOMING_VERSION_CONFLICT"):
        run(tmp_path / "incoming-conflict", [], [a, dict(a, title="conflict")])


def test_disclosure_subject_does_not_inherit_body_revision_permission(tmp_path):
    selection, receipt, args = sample()
    new = project_body(selection, receipt, **args)[0]["dwd_disclosure_subject"][0]
    old = dict(new, knowledge_from=new["knowledge_from"] - timedelta(days=1))
    with pytest.raises(RuntimeError, match="UNAUTHORIZED_INTERVAL_CLOSURE"):
        run(tmp_path, [old], [new], "dwd_disclosure_subject")
