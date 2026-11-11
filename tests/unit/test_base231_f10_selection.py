import hashlib

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base231_f10_selection import (
    select_metadata,
    selected_bodies,
)
from tests.unit.test_base23_ownership_reconfirmation import fixture


def write(tmp_path, name, rows):
    path = tmp_path / name
    pq.write_table(pa.Table.from_pylist(rows), path)
    return name, path


def test_one_snapshot_selection_is_shared_and_unrelated_body_not_decoded(tmp_path):
    old = fixture()
    new = dict(old, acquired_at="2026-09-05T01:00:00+08:00", market_date="2026-09-05")
    finance = dict(new, category="财务分析")
    unrelated = dict(new, category="机构研究", content="protected unrelated category")
    files = [write(tmp_path, "source.parquet", [old, new, finance, unrelated])]
    selection = select_metadata(files)
    assert selection["metadata_rows_scanned"] == 4
    assert len(selection["selected"]) == 2
    rows = list(selected_bodies(files, selection))
    assert {r[1]["category"] for r in rows} == {"财务分析", "股东研究"}
    assert all(r[1]["acquired_at"] == new["acquired_at"] for r in rows)


def test_same_capture_different_body_held_and_not_first_wins(tmp_path):
    a = fixture()
    b = dict(a, content="different",
             content_sha256="sha256:" + hashlib.sha256(b"different").hexdigest())
    files = [write(tmp_path, "a.parquet", [a]), write(tmp_path, "b.parquet", [b])]
    selection = select_metadata(files)
    assert not selection["selected"] and len(selection["held"]) == 1
    assert list(selected_bodies(files, selection)) == []


def test_identical_clock_atom_tie_deterministic_and_october_excluded(tmp_path):
    a = fixture()
    files = [write(tmp_path, "b.parquet", [a]), write(tmp_path, "a.parquet", [a])]
    selected = select_metadata(files)
    assert selected["selected"][0]["member"] == "a.parquet"
    future = [write(tmp_path, "oct.parquet", [dict(a, market_date="2026-10-01")])]
    assert not select_metadata(future)["selected"]


def test_changed_selected_metadata_fails_closed(tmp_path):
    files = [write(tmp_path, "source.parquet", [fixture()])]
    selection = select_metadata(files)
    selection["selected"][0]["stock_code"] = "600001"
    with pytest.raises(RuntimeError, match="METADATA_CHANGED"):
        list(selected_bodies(files, selection))
