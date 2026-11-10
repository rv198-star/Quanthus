"""Disposable Parquet only: bounded storage and genuinely global constraints."""

from __future__ import annotations

import errno
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_bounded as b
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_capacity import guarded_child
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError


def limits(**changes):
    return b.Limits(
        **(
            {
                "free_floor_bytes": 1,
                "rss_bytes": 4 * 1024**3,
                "allocated_bytes": 64 * 1024**2,
                "temp_bytes": 32 * 1024**2,
                "memory_bytes": 64 * 1024**2,
            }
            | changes
        )
    )


def member(path: Path, table: pa.Table):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return b.Member(path, file_sha(path), path.stat().st_size, table.num_rows)


def records(keys=(1, 2)):
    return pa.table(
        {
            "key": list(keys),
            "knowledge_from": pa.array(
                [datetime(2026, 8, 3, tzinfo=timezone.utc)] * len(keys),
                pa.timestamp("us", tz="UTC"),
            ),
            "knowledge_to": pa.array([None] * len(keys), pa.timestamp("us", tz="UTC")),
            "value": pa.array([Decimal("1.0000000001")] * len(keys), pa.decimal128(38, 10)),
            "record_source": ["fixture"] * len(keys),
            "source_revision": ["v1"] * len(keys),
            "source_artifact_sha256": ["hash"] * len(keys),
            "source_record_id": ["r"] * len(keys),
        }
    )


@pytest.mark.parametrize("kind", ["directory-symlink", "dangling-symlink", "fifo"])
def test_budget_census_rejects_nonregular_members_inside_nested_job(tmp_path, kind):
    budget = b.Budget(tmp_path / "job", [], limits())
    nested = budget.root / "nested"
    nested.mkdir(parents=True)
    if kind == "fifo":
        os.mkfifo(nested / "wire")
        reason = "RESOURCE_SPECIAL_FILE_FORBIDDEN"
    else:
        target = tmp_path / "elsewhere"
        if kind == "directory-symlink":
            target.mkdir()
        (nested / "link").symlink_to(target, target_is_directory=target.is_dir())
        reason = "RESOURCE_SYMLINK_FORBIDDEN"
    with pytest.raises(b.BoundedError, match=reason):
        budget.check()


def test_budget_census_counts_nested_partial_spill_bytes(tmp_path):
    budget = b.Budget(tmp_path / "job", [], limits(allocated_bytes=4096))
    nested = budget.root / "temp" / "sort"
    nested.mkdir(parents=True)
    (nested / "part.parquet.partial").write_bytes(b"x" * 8192)
    with pytest.raises(b.BoundedError, match="ALLOCATED_WRITE_BUDGET_EXCEEDED"):
        budget.check()


def test_stage_clock_reset_preserves_peaks_and_rejects_an_expired_stage(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(b.time, "monotonic", lambda: clock[0])
    budget = b.Budget(tmp_path / "job", [], limits(wall_seconds=10))
    clock[0] = 9
    budget.begin_stage("second")
    peak = dict(budget.peak)
    clock[0] = 18
    budget.check()
    assert all(budget.peak[k] >= v for k, v in peak.items())
    clock[0] = 20
    with pytest.raises(b.BoundedError, match="STAGE_WALL_LIMIT_EXCEEDED"):
        budget.begin_stage("must-not-reset-breach")
    assert budget.stage == "second" and budget.started == 9


def test_silent_owned_child_stopped_at_budget_breach(tmp_path, monkeypatch):
    from quanthus.pipelines.securities_warehouse import base22_capacity as capacity

    children = []
    original_popen = capacity.subprocess.Popen

    def tracked_popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(capacity.subprocess, "Popen", tracked_popen)
    start = time.monotonic()
    with (
        pytest.raises(b.BoundedError, match="STAGE_WALL_LIMIT_EXCEEDED"),
        b.Budget(tmp_path / "job", [], limits(wall_seconds=1)) as budget,
    ):
        guarded_child([sys.executable, "-c", "import time; time.sleep(60)"], budget.root, budget)
    assert time.monotonic() - start < 5
    assert len(children) == 1 and children[0].poll() is not None
    # The polling guard may detect the same breach before the monitor thread.
    assert budget.error in {None, "STAGE_WALL_LIMIT_EXCEEDED"}


def test_date_reference_coordinate_is_shanghai_midnight(tmp_path):
    from datetime import date

    parent = (
        records((1,))
        .append_column("effective_from", pa.array([datetime(2026, 7, 30, 16, tzinfo=timezone.utc)]))
        .append_column("effective_to", pa.array([datetime(2026, 7, 30, 20, tzinfo=timezone.utc)]))
    )
    child = records((1,)).append_column("business_day", pa.array([date(2026, 7, 31)]))
    model = {
        "tables": [
            {"name": "parent", "foreign_keys": []},
            {
                "name": "child",
                "time_profile": "effective_state",
                "foreign_keys": [
                    {
                        "column": "key",
                        "table": "parent",
                        "reference_column": "key",
                        "nullable": False,
                    }
                ],
            },
        ]
    }
    contract = {
        "tables": {
            "parent": {"cutoff_field": "effective_from"},
            "child": {"cutoff_field": "business_day"},
        }
    }
    with b.Budget(tmp_path / "job", [], limits()) as budget:
        p = member(budget.root / "parent.parquet", parent)
        c = member(budget.root / "child.parquet", child)
        with b.GlobalScan(budget) as scan:
            scan.add("parent", [p.path])
            scan.add("child", [c.path])
            scan.references(model, contract)


@pytest.mark.parametrize("failure", [errno.EXDEV, errno.EOPNOTSUPP, None, errno.EPERM])
def test_copy_fallback_independent_inode_and_cow(tmp_path, monkeypatch, failure):
    source = member(tmp_path / "source/part.parquet", records())
    before = source.path.read_bytes()
    if failure is not None:

        def fail(*args):
            raise OSError(failure, "disposable probe")

        monkeypatch.setattr(b.fcntl, "ioctl", fail)
    with b.Budget(tmp_path / "job", [tmp_path / "source"], limits()) as budget:
        path = budget.root / "copy/part.parquet"
        if failure == errno.EPERM:
            with pytest.raises(b.BoundedError, match="REFLINK_UNEXPECTED_FAILURE"):
                b.independent_copy(source, path, budget)
            return
        result = b.independent_copy(source, path, budget)
        assert result["mode"] in {"reflink", "independent_copy"}
        if failure:
            assert result["fallback_errno"] == failure
        assert path.stat().st_nlink == 1
        assert path.stat().st_ino != source.path.stat().st_ino
        with path.open("r+b") as stream:
            stream.write(b"damage only copy")
        assert source.path.read_bytes() == before


@pytest.mark.parametrize(
    "fault", ["symlink", "overlap", "source-changed", "existing-output", "budget"]
)
def test_fail_closed_before_input_mutation(tmp_path, fault):
    source = member(tmp_path / "source/part.parquet", records())
    before = source.path.read_bytes()
    root = tmp_path / "job"
    if fault == "overlap":
        root = source.path.parent / "job"
    if fault == "symlink":
        root.symlink_to(source.path.parent, target_is_directory=True)
    if fault in {"overlap", "symlink"}:
        with pytest.raises(SourceAssessmentError):
            b.Budget(root, [source.path.parent], limits())
    else:
        with b.Budget(
            root,
            [source.path.parent],
            limits(allocated_bytes=1 if fault == "budget" else 64 * 1024**2),
        ) as budget:
            output = root / "part.parquet"
            if fault == "existing-output":
                output.write_bytes(b"retained")
            if fault == "source-changed":
                source = b.Member(source.path, "sha256:" + "0" * 64, source.size_bytes, source.rows)
            with pytest.raises((b.BoundedError, FileExistsError)):
                b.independent_copy(source, output, budget)
            if fault == "existing-output":
                assert output.read_bytes() == b"retained"
    assert source.path.read_bytes() == before


def test_partition_stream_serialization_decimal_time_null_and_boundaries(tmp_path):
    first = member(tmp_path / "source/first.parquet", records((1, 2)))
    second = member(tmp_path / "source/second.parquet", records((3, 4)))
    with b.Budget(tmp_path / "job", [tmp_path / "source"], limits(batch_rows=1)) as budget:
        output = budget.root / "part.parquet"
        result = b.rewrite_partition([first, second], output, budget)
        expected = b.semantic_scan([first.path, second.path], records().schema, 1)
        assert result["rows"] == 4
        assert b.semantic_scan([output], records().schema, 3) == expected
        assert pq.read_table(output).column("value").to_pylist() == [Decimal("1.0000000001")] * 4
        with pytest.raises(b.BoundedError, match="PARTITION_OUTPUT_EXISTS"):
            b.rewrite_partition([first, second], output, budget)
        assert not (budget.root / "READY").exists()


@pytest.mark.parametrize("fault", [None, "closure", "bytes", "extra", "false-count"])
def test_one_history_tmp_shared_by_consumers_without_source_or_image_fallback(tmp_path, fault):
    from quanthus.pipelines.securities_warehouse import base22_history as history
    from quanthus.pipelines.securities_warehouse.base22 import digest

    closure = {key: "sha256:" + "a" * 64 for key in history.CLOSURE_FIELDS}
    with b.Budget(tmp_path / "job", [], limits()) as budget:
        data = member(budget.root / "history/part.parquet", records())
        census = {
            "part.parquet": {
                "sha256": data.sha256,
                "rows": data.rows,
                "schema_sha256": digest(str(records().schema.remove_metadata())),
            }
        }
        if fault == "false-count":
            census["part.parquet"]["rows"] += 1
            with pytest.raises(b.BoundedError, match="HISTORY_SCHEMA_ROW_CENSUS_MISMATCH"):
                history.seal_history(budget, closure, census)
            assert not (budget.root / "history/HISTORY.json").exists()
            return
        sealed = history.seal_history(budget, closure, census)
        assert sealed["disposition"] == "SEAL_ONCE"
        marker = (budget.root / "history/HISTORY.json").read_bytes()
        with pytest.raises(b.BoundedError, match="HISTORY_MISSING_OR_ALREADY_SEALED"):
            history.seal_history(budget, closure, census)
        assert (budget.root / "history/HISTORY.json").read_bytes() == marker
        if fault == "closure":
            closure["scope"] = "sha256:" + "b" * 64
        if fault == "bytes":
            data.path.write_bytes(b"disposable corruption")
        if fault == "extra":
            (budget.root / "history/extra").write_bytes(b"disposable")
        if fault:
            with pytest.raises(b.BoundedError):
                history.consume_history(budget, closure, sealed["descriptor_sha256"])
        else:
            for _ in range(2):
                consumed = history.consume_history(budget, closure, sealed["descriptor_sha256"])
                assert consumed["identity"] == sealed["identity"]
                assert consumed["rows"] == data.rows
                assert consumed["disposition"] == "REUSE_IMMUTABLE_TMP"
        assert not (budget.root / "READY").exists()


@pytest.mark.parametrize("fault", ["duplicate", "overlap", "unknown-source", "null-key", None])
def test_global_cross_partition_keys_versions_and_table_lineage(tmp_path, fault):
    one = records((1,))
    two = records((2,))
    if fault == "duplicate":
        two = records((1,))
    if fault == "overlap":
        two = records((1,)).set_column(
            1,
            "knowledge_from",
            pa.array([datetime(2026, 8, 4, tzinfo=timezone.utc)], pa.timestamp("us", tz="UTC")),
        )
    if fault == "unknown-source":
        two = two.set_column(4, "record_source", pa.array(["other-table-only"]))
    if fault == "null-key":
        two = two.set_column(0, "key", pa.array([None], pa.int64()))
    with b.Budget(tmp_path / "job", [], limits()) as budget:
        first = member(budget.root / "a.parquet", one)
        second = member(budget.root / "b.parquet", two)
        with b.GlobalScan(budget) as scan:
            scan.add("fixture", [first.path, second.path])

            def check():
                scan.keys("fixture", ["key"], ["key", "knowledge_from"])
                scan.lineage("fixture", [("fixture", "v1", "hash")])

            if fault:
                with pytest.raises(b.BoundedError):
                    check()
            else:
                check()


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "calendar-bar-date",
        "calendar-state-date",
        "calendar-venue",
        "unknown-lineage",
        "extra-empty",
        "renamed-empty",
        "missing-table",
    ],
)
def test_bounded_exact_33_table_offline_model_closure(tmp_path, fault):
    from quanthus.pipelines.securities_warehouse.base22 import partition
    from tests.unit.test_base22_independent_image import Fixture

    fixture = Fixture(tmp_path / "synthetic-inputs")
    rows = fixture.history
    rows["dwd_security_bar"][0]["price_basis"] = "UNADJUSTED"
    if fault in {"calendar-bar-date", "calendar-state-date"}:
        calendar = dict(rows["dwd_trading_calendar"][0])
        calendar.update(
            calendar_session_id="future", trade_date=__import__("datetime").date(2026, 8, 3)
        )
        calendar["observation_start"] = datetime(2026, 8, 3, 1, tzinfo=timezone.utc)
        calendar["observation_end"] = datetime(2026, 8, 3, 7, tzinfo=timezone.utc)
        rows["dwd_trading_calendar"].append(calendar)
        target = "dwd_tradability_state" if fault == "calendar-state-date" else "dwd_security_bar"
        rows[target][0]["calendar_session_id"] = "future"
    if fault == "calendar-venue":
        rows["dwd_trading_calendar"][0]["venue_id"] = "other"
    rows["dwd_quality_exception"] = []  # legal unique typed empty table
    with b.Budget(tmp_path / "job", [fixture.root], limits()) as budget:
        layout = {}
        admitted = {}
        for name, schema in fixture.schemas.items():
            admitted[name] = [
                (r["record_source"], r["source_revision"], r["source_artifact_sha256"])
                for r in fixture.history[name]
            ] or [("fixture", "empty", "hash")]
            if fault == "unknown-lineage" and name == "dwd_security_bar":
                admitted[name] = [("other", "revision", "hash")]
            groups = {}
            for row in rows[name]:
                groups.setdefault(partition(row, fixture.contract["tables"][name]), []).append(row)
            if not groups:
                groups = {"empty": []}
            parts = {}
            expected = {}
            for part, items in groups.items():
                path = budget.root / "payload" / name / part / "part.parquet"
                f = member(path, pa.Table.from_pylist(items, schema=schema))
                parts[part] = [
                    {
                        "path": str(path.relative_to(budget.root)),
                        "sha256": f.sha256,
                        "size_bytes": f.size_bytes,
                        "rows": f.rows,
                    }
                ]
                expected[part] = f.rows
            layout[name] = {"partitions": parts, "expected_partition_rows": expected}
        if fault == "extra-empty":
            path = budget.root / "payload/dwd_security_bar/extra/part.parquet"
            f = member(path, pa.Table.from_pylist([], schema=fixture.schemas["dwd_security_bar"]))
            layout["dwd_security_bar"]["partitions"]["extra"] = [
                {
                    "path": str(path.relative_to(budget.root)),
                    "sha256": f.sha256,
                    "size_bytes": f.size_bytes,
                    "rows": 0,
                }
            ]
            layout["dwd_security_bar"]["expected_partition_rows"]["extra"] = 0
        if fault == "renamed-empty":
            item = layout["dwd_quality_exception"]["partitions"].pop("empty")
            old = budget.root / item[0]["path"]
            new = budget.root / "payload/dwd_quality_exception/other/part.parquet"
            new.parent.mkdir()
            old.rename(new)
            old.parent.rmdir()
            item[0]["path"] = str(new.relative_to(budget.root))
            layout["dwd_quality_exception"]["partitions"]["other"] = item
            layout["dwd_quality_exception"]["expected_partition_rows"] = {"other": 0}
        if fault == "missing-table":
            layout.pop("dwd_security_bar")

        def run():
            return b.audit_local_partitions(
                budget, layout, fixture.schemas, fixture.contract, fixture.model, admitted
            )

        if fault:
            with pytest.raises(b.BoundedError):
                run()
        else:
            assert set(run()) == set(fixture.schemas)


def test_failed_global_key_evidence_does_not_block_a_new_scan_attempt(tmp_path):
    root = tmp_path / "job"
    with b.Budget(root, [], limits()) as budget:
        bad = member(root / "bad.parquet", records((1, 1)))
        good = member(root / "good.parquet", records())
        with b.GlobalScan(budget) as scan:
            failed_temp = scan.temp
            scan.add("t", [bad.path])
            with pytest.raises(b.BoundedError, match="DUPLICATE"):
                scan.keys("t", ["key"], ["key", "knowledge_from"])
        retained = {str(p): file_sha(p) for p in failed_temp.rglob("*.parquet")}
        assert retained
        with b.GlobalScan(budget) as scan:
            assert scan.temp != failed_temp
            scan.add("t", [good.path])
            scan.keys("t", ["key"], ["key", "knowledge_from"])
        assert retained == {str(p): file_sha(p) for p in failed_temp.rglob("*.parquet")}


@pytest.mark.parametrize(
    "variant", ["stable_versions", "mixed_venues", "future_clock", "future_effective", "company"]
)
def test_streaming_references_preserve_existential_temporal_and_venue_rules(tmp_path, variant):
    from copy import deepcopy
    from datetime import timedelta

    from tests.unit.test_base22_independent_image import Fixture

    fixture = Fixture(tmp_path / "synthetic")
    rows = deepcopy(fixture.history)
    listing = rows["dwd_listing"][0]
    if variant in {"stable_versions", "mixed_venues"}:
        another = dict(
            listing, knowledge_from=listing["knowledge_from"] + timedelta(microseconds=1)
        )
        if variant == "mixed_venues":
            another["venue_id"] = "other-venue"
            rows["dwd_trading_venue"].append(
                dict(rows["dwd_trading_venue"][0], venue_id="other-venue")
            )
        rows["dwd_listing"].append(another)
        for name in ("dwd_security_bar", "dwd_tradability_state"):
            rows[name][0]["knowledge_from"] += timedelta(microseconds=2)
    elif variant == "future_clock":
        listing["knowledge_from"] += timedelta(days=1)
    elif variant == "future_effective":
        listing["effective_from"] = rows["dwd_security_bar"][0]["observation_start"] + timedelta(
            days=1
        )
    else:
        rows["dwd_listed_company"][0]["knowledge_from"] += timedelta(days=1)
    with (
        b.Budget(tmp_path / "job", [fixture.root], limits()) as budget,
        b.GlobalScan(budget) as scan,
    ):
        for table, schema in fixture.schemas.items():
            path = budget.root / (table + ".parquet")
            member(path, pa.Table.from_pylist(rows[table], schema=schema))
            scan.add(table, [path])
        queries = []
        original = scan.reject

        def observe(sql, reason):
            queries.append(sql)
            return original(sql, reason)

        scan.reject = observe
        if variant in {"stable_versions", "mixed_venues"}:
            scan.references(fixture.model, fixture.contract)
            assert queries  # Both stable and mixed venue histories keep existential semantics.
        else:
            with pytest.raises(b.BoundedError, match="GLOBAL_FOREIGN_KEY_DATE_VENUE"):
                scan.references(fixture.model, fixture.contract)


def test_global_hash_index_coalesces_small_batches_into_bounded_row_groups(tmp_path):
    """Physical footer growth must not scale with every bucket/batch intersection."""
    with b.Budget(tmp_path / "job", [], limits(batch_rows=32)) as budget:
        first = member(budget.root / "a.parquet", records(tuple(range(1024))))
        with b.GlobalScan(budget) as scan:
            scan.add("t", [first.path])
            physical = []
            reject = scan.reject

            def inspect(sql, reason):
                if reason == "GLOBAL_DUPLICATE_VERSION_KEY" and not physical:
                    for path in (scan.temp / "keys-t").glob("*.parquet"):
                        p = pq.ParquetFile(path)
                        physical.append((p.metadata.num_rows, p.metadata.num_row_groups))
                return reject(sql, reason)

            scan.reject = inspect
            scan.keys("t", ["key"], ["key", "knowledge_from"])
            assert sum(rows for rows, groups in physical) == 1024
            assert sum(groups for rows, groups in physical) <= 2 * (1024 // 32) + 64
            assert scan.key_metrics["t"]["materializations"] == 1


def test_native_large_string_binary_and_null_streams_are_batch_independent(tmp_path):
    schema = pa.schema(
        [
            ("large", pa.large_string()),
            ("binary", pa.binary()),
            ("large_binary", pa.large_binary()),
            ("nil", pa.null()),
        ]
    )
    table = pa.Table.from_arrays(
        [
            pa.array(["中文", None, "tail"], pa.large_string()),
            pa.array([b"\x00\x01", None, b"z"], pa.binary()),
            pa.array([b"a", None, b"\x00"], pa.large_binary()),
            pa.nulls(3),
        ],
        schema=schema,
    )
    path = tmp_path / "native.parquet"
    pq.write_table(table, path)
    assert b.semantic_scan([path], schema, 1) == b.semantic_scan([path], schema, 3)


@pytest.mark.parametrize(
    "variant", ["valid_version", "missing_version", "nullable_reference", "reuse"]
)
def test_reference_parent_lists_force_key_join_and_keep_complete_versions(tmp_path, variant):
    from datetime import timedelta

    first = datetime(2026, 8, 3, tzinfo=timezone.utc)
    parent = (
        records((1, 2))
        .append_column("effective_from", pa.array([first - timedelta(days=1)] * 2))
        .append_column(
            "effective_to",
            pa.array([None, first + timedelta(days=1)], pa.timestamp("us", tz="UTC")),
        )
    )
    if variant in {"valid_version", "missing_version"}:
        another = parent.slice(0, 1).set_column(
            parent.schema.get_field_index("knowledge_from"),
            "knowledge_from",
            pa.array([first - timedelta(days=2)], pa.timestamp("us", tz="UTC")),
        )
        if variant == "missing_version":
            another = another.set_column(
                another.schema.get_field_index("effective_to"),
                "effective_to",
                pa.array([first - timedelta(days=2)], pa.timestamp("us", tz="UTC")),
            )
        parent = parent.set_column(
            parent.schema.get_field_index("knowledge_from"),
            "knowledge_from",
            pa.array([first + timedelta(days=1)] * 2, pa.timestamp("us", tz="UTC")),
        )
        parent = pa.concat_tables([parent, another])
    child = records((1,)).append_column("observation_start", pa.array([first]))
    if variant == "nullable_reference":
        child = child.set_column(
            child.schema.get_field_index("key"), "key", pa.array([None], pa.int64())
        )
    model = {
        "tables": [
            {"name": "parent", "foreign_keys": []},
            {
                "name": "child",
                "time_profile": "market_observation",
                "foreign_keys": [
                    {
                        "column": "key",
                        "table": "parent",
                        "reference_column": "key",
                        "nullable": variant == "nullable_reference",
                    }
                ],
            },
        ]
    }
    contract = {
        "tables": {
            "parent": {"cutoff_field": "effective_from"},
            "child": {"cutoff_field": "observation_start"},
        }
    }
    with b.Budget(tmp_path / "job", [], limits()) as budget, b.GlobalScan(budget) as scan:
        for name, table in [("parent", parent), ("child", child)]:
            m = member(budget.root / (name + ".parquet"), table)
            scan.add(name, [m.path])
        plans = []
        original = scan.reject

        def prove_plan(sql, reason):
            plans.append(scan.connection.execute("EXPLAIN " + sql).fetchone()[1])
            return original(sql, reason)

        scan.reject = prove_plan
        if variant == "missing_version":
            with pytest.raises(b.BoundedError, match="GLOBAL_FOREIGN_KEY_DATE_VENUE"):
                scan.references(model, contract)
        else:
            scan.references(model, contract)
        assert plans and all("BLOCKWISE_NL_JOIN" not in p and "DELIM_JOIN" not in p for p in plans)
        assert any("HASH_JOIN" in p for p in plans)
        if variant == "reuse":
            first_maps = dict(scan._reference_maps)
            first_storage = b.storage_census(budget.root)
            scan.references(model, contract)
            assert (
                scan._reference_maps == first_maps
                and b.storage_census(budget.root) == first_storage
            )


@pytest.mark.parametrize("defect", [None, "late-batch-missing", "oversized"])
def test_reference_batches_keep_all_global_parents_and_original_global_table(tmp_path, defect):
    from datetime import timedelta

    n = 66
    first = datetime(2026, 8, 3, tzinfo=timezone.utc)
    parent = (
        records(tuple(range(n)))
        .append_column("effective_from", pa.array([first - timedelta(days=1)] * n))
        .append_column("effective_to", pa.array([None] * n, pa.timestamp("us", tz="UTC")))
    )
    child = records(tuple(reversed(range(n)))).append_column(
        "observation_start", pa.array([first] * n)
    )
    if defect == "late-batch-missing":
        child = child.set_column(
            child.schema.get_field_index("key"),
            "key",
            pa.array(list(reversed(range(1, n))) + [999]),
        )
    model = {
        "tables": [
            {"name": "parent", "foreign_keys": []},
            {
                "name": "child",
                "time_profile": "market_observation",
                "foreign_keys": [
                    {
                        "column": "key",
                        "table": "parent",
                        "reference_column": "key",
                        "nullable": False,
                    }
                ],
            },
        ]
    }
    contract = {
        "tables": {
            "parent": {"cutoff_field": "effective_from"},
            "child": {"cutoff_field": "observation_start"},
        }
    }
    with (
        b.Budget(tmp_path / "job", [], limits(batch_rows=1)) as budget,
        b.GlobalScan(budget) as scan,
    ):
        p = member(budget.root / "parent.parquet", parent)
        scan.add("parent", [p.path])
        pieces = [child] if defect == "oversized" else [child.slice(0, 33), child.slice(33)]
        paths = [member(budget.root / f"child-{i}.parquet", t).path for i, t in enumerate(pieces)]
        scan.add("child", paths)
        queried = []
        original = scan.reject

        def observe(sql, reason):
            queried.append(sql)
            return original(sql, reason)

        scan.reject = observe
        if defect == "late-batch-missing":
            with pytest.raises(b.BoundedError, match="GLOBAL_FOREIGN_KEY"):
                scan.references(model, contract)
        else:
            scan.references(model, contract)
        assert scan.connection.execute("SELECT count(*) FROM child").fetchone()[0] == n
        assert len(queried) == (1 if defect == "oversized" else 2)
        if defect != "oversized":
            assert len(scan._reference_maps) == 1
            assert scan.connection.execute("SELECT count(*) FROM parent").fetchone()[0] == n


@pytest.mark.parametrize("change", [None, "bytes", "paths", "symlink"])
def test_reference_batch_preparation_is_shared_only_for_unchanged_job_inputs(
    tmp_path, monkeypatch, change
):
    with (
        b.Budget(tmp_path / "job", [], limits()) as budget,
        b.GlobalScan(budget) as scan,
    ):
        paths = [member(budget.root / f"child-{i}.parquet", records()).path for i in range(2)]
        scan.add("child", paths)
        opened = []
        original = pq.ParquetFile

        def observe(path, *args, **kwargs):
            opened.append(path)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(pq, "ParquetFile", observe)
        prepared = scan._reference_batches("child")
        assert opened == paths
        if change == "bytes":
            member(paths[0], records((8, 9)))
        elif change == "paths":
            scan.source_paths["child"] = list(reversed(paths))
        elif change == "symlink":
            replacement = member(budget.root / "replacement.parquet", records()).path
            paths[0].unlink()
            paths[0].symlink_to(replacement)
        if change:
            with pytest.raises(b.BoundedError, match="REFERENCE_BATCH_INPUT_CHANGED"):
                scan._reference_batches("child")
            if change == "symlink":
                paths[0].unlink()
        else:
            reused = scan._reference_batches("child")
            assert reused == prepared
            reused[0].clear()
            assert scan._reference_batches("child") == prepared
        assert opened == paths


@pytest.mark.parametrize("defect", [None, "missing", "nullable"])
def test_structural_relocation_reference_uses_all_global_parent_keys_and_no_delimiter(
    tmp_path, defect
):
    parent = records(tuple(range(66)))
    keys = list(reversed(range(66)))
    if defect == "missing":
        keys[-1] = 999
    child = records(tuple(keys))
    if defect == "nullable":
        child = child.set_column(
            child.schema.get_field_index("key"), "key", pa.array(keys[:-1] + [None], pa.int64())
        )
    with (
        b.Budget(tmp_path / "job", [], limits(batch_rows=1)) as budget,
        b.GlobalScan(budget) as scan,
    ):
        p = member(budget.root / "parent.parquet", parent)
        scan.add("parent", [p.path])
        paths = [
            member(budget.root / f"child-{i}.parquet", child.slice(i * 33, 33)).path
            for i in range(2)
        ]
        scan.add("child", paths)
        plans = []
        original = scan.reject

        def observe(sql, reason):
            plans.append(scan.connection.execute("EXPLAIN " + sql).fetchone()[1])
            return original(sql, reason)

        scan.reject = observe
        if defect == "missing":
            with pytest.raises(b.BoundedError, match="RELOCATION_MODEL_FOREIGN_KEY_MISSING"):
                scan.structural_reference("child", "key", "parent", "key")
        else:
            scan.structural_reference("child", "key", "parent", "key")
        assert len(plans) == 2
        assert all("HASH_JOIN" in p and "DELIM_JOIN" not in p for p in plans)
        assert scan.connection.execute("SELECT count(*) FROM child").fetchone()[0] == 66
