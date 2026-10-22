from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base22_native_keys import NativeGlobalScan


def row(key, start, end):
    return {"key": key, "knowledge_from": start, "knowledge_to": end}


@pytest.mark.parametrize("case", ["valid", "duplicate", "overlap", "previous_open", "null"])
def test_native_key_copy_checks_global_across_files_and_preserves_failed_tmp(tmp_path, case):
    a = datetime(2026, 8, 1, tzinfo=timezone.utc)
    b = a + timedelta(days=1)
    first = row("exact", a, b)
    second = row("exact", b, None)
    if case == "duplicate":
        second = dict(first)
    elif case == "overlap":
        second["knowledge_from"] = a + timedelta(hours=1)
    elif case == "previous_open":
        first["knowledge_to"] = None
    elif case == "null":
        second["key"] = None
    schema = pa.schema(
        [
            pa.field("key", pa.string()),
            pa.field("knowledge_from", pa.timestamp("us", tz="UTC")),
            pa.field("knowledge_to", pa.timestamp("us", tz="UTC")),
        ]
    )
    with Budget(
        tmp_path / "job",
        [],
        Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2),
    ) as budget:
        paths = []
        for n, value in enumerate([first, second]):
            p = budget.root / (str(n) + ".parquet")
            pq.write_table(pa.Table.from_pylist([value], schema=schema), p)
            paths.append(p)
        with NativeGlobalScan(budget) as scan:
            scan.add("table_rows", paths)
            if case == "valid":
                scan.keys("table_rows", ["key"], ["key", "knowledge_from"])
                assert scan.key_metrics["table_rows"]["rows"] == 2
                assert not list(scan.temp.glob("native-keys-*"))
            else:
                reason = {
                    "duplicate": "GLOBAL_DUPLICATE_VERSION_KEY",
                    "overlap": "GLOBAL_OVERLAPPING_KNOWLEDGE_VERSIONS",
                    "previous_open": "GLOBAL_OVERLAPPING_KNOWLEDGE_VERSIONS",
                    "null": "GLOBAL_KEY_NULL",
                }[case]
                with pytest.raises(BoundedError, match=reason):
                    scan.keys("table_rows", ["key"], ["key", "knowledge_from"])
                if case != "null":
                    assert list(scan.temp.glob("native-keys-*"))
