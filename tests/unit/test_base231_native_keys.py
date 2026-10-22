from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base23_native_keys import NativeGlobalScan
from quanthus.pipelines.securities_warehouse.base231_native_keys import UniqueBusinessKeyScan


@pytest.mark.parametrize(
    "case,reason",
    [
        ("unique", None),
        ("adjacent", None),
        ("gap", None),
        ("duplicate", "GLOBAL_DUPLICATE_VERSION_KEY"),
        ("overlap", "GLOBAL_OVERLAPPING_KNOWLEDGE_VERSIONS"),
        ("open", "GLOBAL_OVERLAPPING_KNOWLEDGE_VERSIONS"),
        ("null-key", "GLOBAL_KEY_NULL"),
        ("null-version", "GLOBAL_KEY_NULL"),
        ("extra-version-overlap", "GLOBAL_OVERLAPPING_KNOWLEDGE_VERSIONS"),
    ],
)
def test_complete_legacy_equivalence_across_separate_files(tmp_path, case, reason):
    a = datetime(2026, 8, 1, tzinfo=timezone.utc)
    b = a + timedelta(days=1)
    first = dict(key="same", knowledge_from=a, knowledge_to=b, revision=1)
    second = dict(key="same", knowledge_from=b, knowledge_to=None, revision=1)
    if case == "unique":
        second["key"] = "other"
    elif case == "gap":
        second["knowledge_from"] = b + timedelta(days=1)
    elif case == "duplicate":
        second = dict(first)
    elif case == "overlap":
        second["knowledge_from"] = a + timedelta(hours=1)
    elif case == "open":
        first["knowledge_to"] = None
    elif case == "null-key":
        second["key"] = None
    elif case == "null-version":
        second["knowledge_from"] = None
    elif case == "extra-version-overlap":
        second.update(knowledge_from=a, revision=2)
    schema = pa.schema(
        [
            pa.field("key", pa.string()),
            pa.field("knowledge_from", pa.timestamp("us", tz="UTC")),
            pa.field("knowledge_to", pa.timestamp("us", tz="UTC")),
            pa.field("revision", pa.int64()),
        ]
    )
    version = ["key", "knowledge_from"] + (["revision"] if case == "extra-version-overlap" else [])
    for cls in [NativeGlobalScan, UniqueBusinessKeyScan]:
        with Budget(
            tmp_path / cls.__name__,
            [],
            Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2),
        ) as budget:
            paths = []
            for n, row in enumerate([first, second]):
                p = budget.root / (str(n) + ".parquet")
                pq.write_table(pa.Table.from_pylist([row], schema=schema), p)
                paths.append(p)
            with cls(budget) as scan:
                scan.add("observations", paths)
                if reason:
                    with pytest.raises(BoundedError, match=reason):
                        scan.keys("observations", ["key"], version)
                    if reason != "GLOBAL_KEY_NULL":
                        assert list(scan.temp.glob("native-keys-*"))
                else:
                    scan.keys("observations", ["key"], version)
                    assert scan.key_metrics["observations"]["rows"] == 2
                    assert not list(scan.temp.glob("native-keys-*"))
                    if cls is UniqueBusinessKeyScan:
                        metrics = scan.key_metrics["observations"]
                        assert (
                            metrics["exact_unique_business_buckets"] > 0
                            if case == "unique"
                            else metrics["legacy_version_checks_buckets"] == 1
                        )


def test_hash_collision_does_not_mean_same_business_key(tmp_path):
    import duckdb

    c = duckdb.connect()
    keys = c.execute(
        "SELECT list(k) FROM (SELECT 'synthetic-'||i::VARCHAR AS k FROM range(256) t(i)) "
        "GROUP BY hash(k)%64 HAVING count(*)>1 LIMIT 1"
    ).fetchone()[0]
    c.close()
    a = datetime(2026, 8, 1, tzinfo=timezone.utc)
    rows = [dict(key=k, knowledge_from=a, knowledge_to=None) for k in keys]
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
        p = budget.root / "synthetic.parquet"
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), p)
        with UniqueBusinessKeyScan(budget) as scan:
            scan.add("observations", [p])
            scan.keys("observations", ["key"], ["key", "knowledge_from"])
            assert scan.key_metrics["observations"]["buckets"] == 1
            assert scan.key_metrics["observations"]["exact_unique_business_buckets"] == 1
            assert scan.key_metrics["observations"]["rows"] == len(keys)


@pytest.mark.parametrize("version", [["unrelated", "knowledge_from"], ["business"]])
def test_unproven_version_key_relationship_uses_unchanged_legacy(monkeypatch, version):
    calls = []
    monkeypatch.setattr(NativeGlobalScan, "keys", lambda self, *args: calls.append(args))
    scan = object.__new__(UniqueBusinessKeyScan)
    scan.keys("observations", ["business"], version)
    assert calls == [("observations", ["business"], version)]


def test_full_33_table_receipt_identical_under_both_algorithms(tmp_path, monkeypatch):
    import json
    import os
    from pathlib import Path
    from types import SimpleNamespace

    from quanthus.pipelines.securities_warehouse import base23_native_image as n
    from tests.unit.test_base22_native_image import _native_fixture

    f, config, _, plan = _native_fixture(tmp_path)
    plan["through_date"] = "2026-09-30"
    output = tmp_path / "image"
    n.materialize_native_image(
        plan=plan,
        rules=f.contract,
        contract=json.loads((config / "base23-delivery.v1.json").read_bytes()),
        model=f.model,
        extension=f.extension,
        limitations=[],
        local_evidence={},
        output=output,
        allocated_limit=64 * 1024**2,
        free_floor=1,
        rss_limit=4 * 1024**3,
        wall_seconds=30,
        config_root=config,
    )
    real = os.statvfs

    def readonly(path):
        value = real(path)
        if Path(path) == output or output in Path(path).parents:
            fields = {k: getattr(value, k) for k in dir(value) if k.startswith("f_")}
            return SimpleNamespace(**fields | {"f_flag": value.f_flag | os.ST_RDONLY})
        return value

    monkeypatch.setattr(n.os, "statvfs", readonly)
    receipts = []
    for enabled in [False, True]:
        with Budget(
            tmp_path / ("audit-" + str(enabled)),
            [output],
            Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2),
        ) as budget:
            receipts.append(
                n.audit_native_image(output, budget, unique_business_key_shortcut=enabled)
            )
    assert receipts[0] == receipts[1]
    assert len(receipts[0]["summaries"]) == 33


@pytest.mark.parametrize("invalid", ["true", 1])
def test_algorithm_selector_cannot_accept_non_boolean(tmp_path, invalid):
    from quanthus.pipelines.securities_warehouse.base23_native_image import audit_native_image

    with (
        Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as budget,
        pytest.raises(BoundedError, match="KEY_ALGORITHM_OPTION_INVALID"),
    ):
        audit_native_image(tmp_path / "absent", budget, unique_business_key_shortcut=invalid)


@pytest.mark.parametrize("case", ["invalid_interval", "business_cutoff"])
def test_unique_keys_do_not_skip_original_time_row_rules(tmp_path, case):
    a = datetime(2026, 10, 14, tzinfo=timezone.utc)
    observation = datetime(2026, 9, 30, 15, 59, 59, tzinfo=timezone.utc)
    record = dict(
        key="synthetic", knowledge_from=a, knowledge_to=None, observation_start=observation
    )
    reason = "INVALID_TIME_INTERVAL"
    if case == "invalid_interval":
        record["knowledge_to"] = a
    else:
        record["observation_start"] = observation + timedelta(seconds=1)
        reason = "BUSINESS_DATE_AFTER_CUTOFF"
    schema = pa.schema(
        [
            pa.field("key", pa.string()),
            pa.field("knowledge_from", pa.timestamp("us", tz="UTC")),
            pa.field("knowledge_to", pa.timestamp("us", tz="UTC")),
            pa.field("observation_start", pa.timestamp("us", tz="UTC")),
        ]
    )
    for cls in [NativeGlobalScan, UniqueBusinessKeyScan]:
        with Budget(
            tmp_path / cls.__name__,
            [],
            Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2),
        ) as budget:
            path = budget.root / "synthetic.parquet"
            pq.write_table(pa.Table.from_pylist([record], schema=schema), path)
            with cls(budget) as scan:
                scan.add("observations", [path])
                scan.keys("observations", ["key"], ["key", "knowledge_from"])
                with pytest.raises(BoundedError, match=reason):
                    scan.row_rules("observations", schema, {"cutoff_field": "observation_start"})
