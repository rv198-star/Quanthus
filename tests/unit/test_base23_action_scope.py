from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base23_action_scope import (
    check_output_geometry,
    copy_member,
    metadata_rows,
)
from quanthus.pipelines.securities_warehouse.base23_dividend import TABLES


def budget(root, sources):
    return Budget(root, sources, Limits(free_floor_bytes=1, allocated_bytes=2**26))


def test_copy_is_independent_and_preserves_source_bytes(tmp_path):
    source = tmp_path/"original"
    source.write_bytes(b"synthetic original")
    item = {"sha256": file_sha(source), "size_bytes": source.stat().st_size}
    with budget(tmp_path/"job", [source]) as b:
        dest = b.root/"native.parquet"
        copy_member(source, dest, item, b)
        assert dest.read_bytes() == source.read_bytes()
        assert dest.stat().st_ino != source.stat().st_ino
        assert dest.stat().st_nlink == 1
        dest.write_bytes(b"disposable change")
        assert source.read_bytes() == b"synthetic original"


def test_copy_rejects_changed_source_and_never_writes(tmp_path):
    source = tmp_path/"original"
    source.write_bytes(b"before")
    item = {"sha256": file_sha(source), "size_bytes": source.stat().st_size}
    source.write_bytes(b"after")
    with budget(tmp_path/"job", [source]) as b:
        with pytest.raises(BoundedError, match="INPUT_HASH_CHANGED"):
            copy_member(source, b.root/"new", item, b)
        assert not (b.root/"new").exists()


def test_existing_output_never_overwritten(tmp_path):
    source = tmp_path/"original"
    source.write_bytes(b"original")
    item = {"sha256": file_sha(source), "size_bytes": source.stat().st_size}
    with budget(tmp_path/"job", [source]) as b:
        dest = b.root/"existing"
        dest.write_bytes(b"retained attempt")
        with pytest.raises(FileExistsError):
            copy_member(source, dest, item, b)
        assert dest.read_bytes() == b"retained attempt"


def test_metadata_reader_projects_no_amount_and_binds_row_positions(tmp_path):
    root = tmp_path/"source"
    root.mkdir()
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    rows = [{"corporate_action_id": f"a{i}", "effective_at": now, "knowledge_from": now,
             "knowledge_to": None, "record_source": "synthetic", "security_id": "s",
             "action_type": "cash", "amount": 123+i} for i in range(3)]
    p = root/"input.parquet"
    pq.write_table(pa.Table.from_pylist(rows), p)
    item = {"path": p.name, "sha256": file_sha(p), "rows": 3,
            "size_bytes": p.stat().st_size}
    with budget(tmp_path/"job", [root]) as b:
        actual = list(metadata_rows(root, [item], actions=True, budget=b))
        assert [r["ordinal"] for r in actual] == [0, 1, 2]
        assert all("amount" not in r for r in actual)
        with pytest.raises(BoundedError, match="ROW_CENSUS_CHANGED"):
            list(metadata_rows(root, [{**item, "rows": 2}], actions=True, budget=b))


def geometry(root):
    outputs = {}
    expected = set()
    for table in TABLES:
        part = "effective_at_year=2026/effective_at_month=8"
        path = f"payload/{table}/{part}/part-000000.parquet"
        p = root/path
        p.parent.mkdir(parents=True)
        p.write_bytes(b"synthetic physical member")
        expected.add(path)
        outputs[table] = [{"partition": part, "path": path}]
    return {"outputs": outputs}, expected


def test_geometry_requires_both_physical_tables_and_exact_partitions(tmp_path):
    body, expected = geometry(tmp_path)
    check_output_geometry(tmp_path, body, expected)


def test_resealed_wrong_partition_path_rejected(tmp_path):
    body, expected = geometry(tmp_path)
    item = body["outputs"][TABLES[0]][0]
    old = item["path"]
    item["path"] = old.replace("month=8", "month=9")
    p = tmp_path/item["path"]
    p.parent.mkdir(parents=True)
    (tmp_path/old).rename(p)
    expected.remove(old)
    expected.add(item["path"])
    with pytest.raises(BoundedError, match="PHYSICAL_PARTITION_COORDINATE_CHANGED"):
        check_output_geometry(tmp_path, body, expected)


def test_extra_empty_partition_directory_fails(tmp_path):
    body, expected = geometry(tmp_path)
    (tmp_path/f"payload/{TABLES[0]}/effective_at_year=2026/effective_at_month=9").mkdir()
    with pytest.raises(BoundedError, match="UNDECLARED_PHYSICAL_DIRECTORY"):
        check_output_geometry(tmp_path, body, expected)


def test_unknown_table_fails_even_with_matching_file_inventory(tmp_path):
    body, expected = geometry(tmp_path)
    body["outputs"]["dwd_unknown"] = body["outputs"].pop(TABLES[0])
    with pytest.raises(BoundedError, match="TABLE_SET_CHANGED"):
        check_output_geometry(tmp_path, body, expected)
