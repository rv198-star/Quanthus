from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, GlobalScan
from quanthus.pipelines.securities_warehouse.base231_domain_pack import schemas
from quanthus.pipelines.securities_warehouse.base231_image import (
    merge_holder_batches,
    partition_files,
)
from tests.unit.test_base231_domain_pack import LIMITS
from tests.unit.test_base231_native_delta import HOLDING, row


def inputs(budget, old, closed, revised, added):
    for name, records in [
        ("before", old),
        ("closed", closed),
        ("revised", revised),
        ("added", added),
    ]:
        path = budget.root / (name + ".parquet")
        pq.write_table(pa.Table.from_pylist(records, schema=schemas()[HOLDING]), path)
    return {
        name: [budget.root / (name + ".parquet")]
        for name in ("before", "closed", "revised", "added")
    }


def run(tmp_path, old, closed, revised, added):
    with Budget(tmp_path / "job", [], LIMITS) as budget:
        paths = inputs(budget, old, closed, revised, added)
        with GlobalScan(budget) as scan:
            for name, members in paths.items():
                scan.add(name, members)
            return list(merge_holder_batches(scan, budget))


def test_exact_authorized_interval_close_preserves_old_values_and_keeps_new_version(tmp_path):
    old = dict(row(), share_nature=None, holding_quantity=Decimal("1.2345678910"))
    clock = datetime(2026, 10, 12, tzinfo=timezone.utc)
    closed = dict(old, knowledge_to=clock)
    new = dict(old, share_nature="流通A股", knowledge_from=clock)
    before = deepcopy(old)
    batches = run(tmp_path, [old], [closed], [new], [])
    result = [r for batch in batches for r in batch.to_pylist()]
    assert sorted(result, key=lambda r: r["knowledge_from"]) == [closed, new] and old == before


@pytest.mark.parametrize("damage", ["value", "missing", "duplicate", "clock"])
def test_unregistered_or_changed_old_record_cannot_be_closed(tmp_path, damage):
    old = row()
    clock = datetime(2026, 10, 12, tzinfo=timezone.utc)
    closed = dict(old, knowledge_to=clock)
    new = dict(old, share_nature="流通A股", knowledge_from=clock)
    original = [old]
    ends = [closed]
    if damage == "value":
        closed["holding_quantity"] += 1
    if damage == "missing":
        original = []
    if damage == "duplicate":
        ends.append(dict(closed))
    if damage == "clock":
        closed["knowledge_to"] = old["knowledge_from"]
    with pytest.raises(BoundedError):
        run(tmp_path, original, ends, [new], [])


def test_partitioning_uses_shanghai_day_and_preserves_all_arrays(tmp_path):
    old = row()
    old["observation_start"] = datetime(2025, 12, 31, 16, tzinfo=timezone.utc)
    table = pa.Table.from_pylist([old], schema=schemas()[HOLDING])
    with Budget(tmp_path / "job", [], LIMITS) as budget:
        files = partition_files(
            table.to_batches(),
            table=HOLDING,
            rule={"partition_by": ["observation_start_year"]},
            schema=table.schema,
            budget=budget,
        )
        assert files[0]["partition"] == "observation_start_year=2026"
        assert (
            pq.read_table(budget.root / files[0]["path"])
            .drop(["observation_start_year"])
            .equals(table, check_metadata=False)
        )


@pytest.mark.parametrize("damage", ["unrelated", "binding", "missing", "bytes"])
def test_image_binding_rejects_history_changes_and_faults_before_expensive_replay(
    tmp_path,
    monkeypatch,
    damage,
):
    import json

    from quanthus.pipelines.securities_warehouse import base231_image as n
    from quanthus.pipelines.securities_warehouse.base22 import file_sha

    root = tmp_path / "image"
    baseline = root / n.PREFIX / "inputs/before"
    baseline.mkdir(parents=True)
    payload = root / "payload/example/all/part-000000.parquet"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"synthetic-only")
    member = dict(
        path="old.parquet", partition="all", rows=1, size_bytes=14, sha256=file_sha(payload)
    )
    layout = {"example": {"files": [member]}}
    (baseline / "MATERIALIZATION.json").write_text(json.dumps({"layout": layout}))
    scope = dict(identity="sha256:scope", outputs={})
    monkeypatch.setattr(n, "validate_scope", lambda _: scope)
    binding = dict(
        kind=n.KIND,
        local_prefix=n.PREFIX,
        scope_identity=scope["identity"],
        base_manifest_sha256=n.DWD,
    )
    entry = {**member, "source_id": "before23"}
    plan = dict(patch_acceptance=binding, tables={"example": {"files": [entry]}})
    (root / "evidence").mkdir(exist_ok=True)
    if damage == "unrelated":
        entry["sha256"] = "sha256:unknown"
    if damage == "binding":
        binding["scope_identity"] = "sha256:unknown"
    if damage == "missing":
        payload.unlink()
    if damage == "bytes":
        entry["source_id"] = "patch"
        scope["outputs"]["example"] = [member]
        layout["example"]["files"] = []
        (baseline / "MATERIALIZATION.json").write_text(json.dumps({"layout": layout}))
        payload.write_bytes(b"changed-bytes")
    (root / "evidence/input-plan.json").write_text(json.dumps(plan))
    with pytest.raises(BoundedError):
        n.validate_image_binding(root)
