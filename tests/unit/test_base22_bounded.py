"""Disposable Parquet only: bounded storage and genuinely global constraints."""

from __future__ import annotations

import errno
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
