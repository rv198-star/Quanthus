from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.daily_reference_views import reference_relation


def test_closed_and_future_parent_versions_do_not_multiply_daily_rows(tmp_path: Path):
    early = datetime(2026, 10, 13, tzinfo=timezone.utc)
    changed = datetime(2026, 10, 14, tzinfo=timezone.utc)
    future = datetime(2026, 11, 12, tzinfo=timezone.utc)
    path = tmp_path / "listing.parquet"
    rows = [
        {
            "listing_id": "id1",
            "code": "301707",
            "knowledge_from": early,
            "knowledge_to": changed,
            "currency": "CNY",
        },
        {
            "listing_id": "id1",
            "code": "301707",
            "knowledge_from": changed,
            "knowledge_to": None,
            "currency": "CNY",
        },
        {
            "listing_id": "id2",
            "code": "301799",
            "knowledge_from": future,
            "knowledge_to": None,
            "currency": "CNY",
        },
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)
    before = path.read_bytes()
    with duckdb.connect() as c:
        rel = reference_relation([path], datetime(2026, 11, 2, tzinfo=timezone.utc))
        assert c.execute(f"SELECT listing_id,currency,knowledge_from FROM {rel}").fetchall() == [
            ("id1", "CNY", changed)
        ]
        old = reference_relation([path], early)
        assert c.execute(f"SELECT knowledge_from FROM {old}").fetchall() == [(early,)]
        boundary = reference_relation([path], changed)
        assert c.execute(f"SELECT knowledge_from FROM {boundary}").fetchall() == [(changed,)]
    assert path.read_bytes() == before


def test_reference_cutoff_requires_real_timezone(tmp_path: Path):
    with pytest.raises(ValueError, match="TIMEZONE_REQUIRED"):
        reference_relation([tmp_path / "x.parquet"], datetime(2026, 11, 2))
