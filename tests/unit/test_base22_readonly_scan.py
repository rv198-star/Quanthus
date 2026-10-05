import os
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    GlobalScan,
    Limits,
)
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError


@pytest.mark.parametrize(
    "fault", [None, "rw", "overlap", "duplicate", "symlink", "outside", "hardlink"]
)
def test_explicit_readonly_inputs_keep_all_write_and_file_guards(tmp_path, monkeypatch, fault):
    image = tmp_path / "image"
    image.mkdir()
    p = image / "keys.parquet"
    schema = pa.schema(
        [
            ("id", pa.string()),
            ("knowledge_from", pa.timestamp("us", tz="UTC")),
            ("knowledge_to", pa.timestamp("us", tz="UTC")),
        ]
    )
    from datetime import datetime, timezone

    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "id": "synthetic",
                    "knowledge_from": datetime(2026, 8, 3, tzinfo=timezone.utc),
                    "knowledge_to": None,
                }
            ],
            schema=schema,
        ),
        p,
    )
    real_statvfs = os.statvfs
    # Only simulate the readonly flag. Actual private RO bind acceptance is
    # independently exercised by the real cold-run harness, not this unit case.
    if fault != "rw":
        monkeypatch.setattr(
            os,
            "statvfs",
            lambda path: (
                SimpleNamespace(f_flag=os.ST_RDONLY) if Path(path) == image else real_statvfs(path)
            ),
        )
    with Budget(tmp_path / "scratch", [], Limits(free_floor_bytes=1)) as budget:
        roots = [image]
        if fault == "overlap":
            roots = [budget.root]
        if fault == "duplicate":
            roots = [image, image]
        if fault == "symlink":
            link = tmp_path / "link"
            link.symlink_to(image, target_is_directory=True)
            roots = [link]
        if fault == "hardlink":
            os.link(p, image / "alias.parquet")
        if fault in ["rw", "overlap", "duplicate", "symlink"]:
            with pytest.raises((BoundedError, SourceAssessmentError)):
                GlobalScan(budget, read_only_roots=roots)
            assert not (budget.root / "temp").exists()
            return
        with GlobalScan(budget, read_only_roots=roots) as scan:
            if fault == "outside":
                extra = tmp_path / "other.parquet"
                pq.write_table(pa.Table.from_pylist([], schema=schema), extra)
                with pytest.raises(BoundedError, match="DESTINATION_OUTSIDE_JOB"):
                    scan.add("synthetic", [extra])
            elif fault == "hardlink":
                with pytest.raises(BoundedError, match="SCAN_LOCAL_INDEPENDENT_FILE_REQUIRED"):
                    scan.add("synthetic", [p])
            else:
                scan.add("synthetic", [p])
                scan.keys("synthetic", ["id"], ["id", "knowledge_from"])
                assert scan.key_metrics["synthetic"]["rows"] == 1
                assert scan.key_metrics["synthetic"]["lifecycle"] == "TMP"
            with pytest.raises(BoundedError, match="DESTINATION_OUTSIDE_JOB"):
                budget.destination(image / "write.parquet")
