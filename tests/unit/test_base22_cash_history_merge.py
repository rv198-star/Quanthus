from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, partition
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_cash_history_merge import (
    TABLES,
    merge_cash_partition,
)
from tests.unit.test_base22_cash_restatement import fixture


def case(table):
    _, old, terms, _, _, _ = fixture()
    row = old if table == TABLES[0] else terms[0]
    new = dict(row, knowledge_from=row["knowledge_from"] + timedelta(days=1))
    closed = dict(row, knowledge_to=new["knowledge_from"])
    rule = load_contract(Path("config/pipelines"))[0]["tables"][table]
    return rule, partition(row, rule), row, closed, new


@pytest.mark.parametrize("table", TABLES)
def test_exact_existing_and_new_versions_preserve_all_atoms(table):
    rule, part, old, closed, new = case(table)
    args = ([old], [old], [closed, new])
    frozen = deepcopy(args)
    rows, proof = merge_cash_partition(table, part, rule, *args)
    assert rows == [closed, new]
    assert args == frozen
    assert proof["closed_versions"] == proof["new_versions"] == 1
    assert not proof["provider_priority_granted"]


@pytest.mark.parametrize("table", TABLES)
def test_healthy_partition_remains_exact(table):
    rule, part, old, _, _ = case(table)
    rows, proof = merge_cash_partition(table, part, rule, [old], [], [])
    assert rows == [old] and proof["closed_versions"] == 0


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize(
    "defect",
    [
        "changed_before", "missing_before", "duplicate", "old_clock", "other_atom",
        "extra_version", "wrong_month", "unbound",
    ],
)
def test_unapproved_change_or_incomplete_patch_fails_closed(table, defect):
    rule, part, old, closed, new = case(table)
    history, before, patch = [deepcopy(old)], [old], [closed, new]
    if defect == "changed_before":
        history[0]["source_record_id"] = "changed"
    elif defect == "missing_before":
        history = []
    elif defect == "duplicate":
        patch.append(deepcopy(new))
    elif defect == "old_clock":
        new["knowledge_from"] = old["knowledge_from"]
    elif defect == "other_atom":
        closed["source_record_id"] = "changed"
    elif defect == "extra_version":
        history.append(dict(old, knowledge_from=old["knowledge_from"] - timedelta(days=1)))
    elif defect == "wrong_month":
        part += "/not-the-business-partition"
    else:
        before = []
    with pytest.raises(BoundedError, match="CASH_HISTORY"):
        merge_cash_partition(table, part, rule, history, before, patch)


@pytest.mark.parametrize("defect", [None, "resealed_atom", "extra_partition"])
def test_cold_merge_reconstructs_allowed_changes_even_after_resealing(
    tmp_path, monkeypatch, defect
):
    import os
    import shutil
    from types import SimpleNamespace

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha, schemas
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits, semantic_scan
    from quanthus.pipelines.securities_warehouse.base22_cash_history_merge import (
        audit_cash_history_scope,
    )
    from quanthus.pipelines.securities_warehouse.base22_sources import _tree

    root = tmp_path / "image"
    root.mkdir()
    shutil.copytree("config/pipelines", root / "contract")
    _, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    body = {
        "purpose": "EXACT_QUALIFIED_CASH_HISTORY_MERGE_NOT_COMPLETE_BASE22",
        "READY": False, "complete_Base22": False, "consumer_admission": "NOT_GRANTED",
        "outputs": [], "partition_bindings": {}, "parents": {}, "output_rows": {},
        "selected_versions_each": {}, "admitted_lineage": {},
    }

    def write(table, name, rows):
        path = root / (table + "-" + name + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows, schema=ss[table]), path)
        return {"table": table, "path": path.name, "rows": len(rows)}

    for table in TABLES:
        rule, part, old, closed, new = case(table)
        output, proof = merge_cash_partition(table, part, rule, [old], [old], [closed, new])
        binding = {
            "history": write(table, "history", [old]),
            "before": write(table, "before", [old]),
            "patch": write(table, "patch", [closed, new]), "proof": proof,
        }
        if defect == "resealed_atom" and table == TABLES[1]:
            output[1]["amount_per_share"] += 1
        item = write(table, "output", output)
        item.update(partition=part, semantic_scan=semantic_scan([root / item["path"]], ss[table]))
        body["outputs"].append(item)
        body["partition_bindings"][table] = {part: binding}
        body["output_rows"][table] = 2
        body["selected_versions_each"][table] = 1
        body["admitted_lineage"][table] = [
            [old[f] for f in ("record_source", "source_revision", "source_artifact_sha256")]
        ]
    for table, column, value in [
        ("dwd_security", "security_id", "security"),
        ("dwd_listed_company", "company_id", "company"),
    ]:
        path = root / (table + ".parquet")
        pq.write_table(pa.table({column: [value]}), path)
        body["parents"][table] = [{"path": path.name}]
    if defect == "extra_partition":
        body["outputs"].append(dict(body["outputs"][0]))
    body["physical_members"] = {
        p: {"sha256": file_sha(root / p), "size_bytes": (root / p).stat().st_size}
        for p in _tree(root)
    }
    body["identity"] = digest(body)
    (root / "QUALIFICATION.json").write_bytes(canonical(body))
    real_statvfs = os.statvfs
    monkeypatch.setattr(
        os, "statvfs", lambda p: SimpleNamespace(f_flag=os.ST_RDONLY)
        if Path(p) == root else real_statvfs(p),
    )
    with Budget(tmp_path / "audit-TMP", [root], Limits(free_floor_bytes=1)) as budget:
        if defect is None:
            result = audit_cash_history_scope(root, body, budget)
            assert result["output_rows"] == dict.fromkeys(TABLES, 2)
        else:
            with pytest.raises(BoundedError, match="UNAPPROVED_OUTPUT_CHANGE|PARTITION_DUPLICATE"):
                audit_cash_history_scope(root, body, budget)
