from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base23_native_image import image_schemas
from quanthus.pipelines.securities_warehouse.base_build_delta import build_delta, normalize_versions
from quanthus.pipelines.securities_warehouse.base_build_profile import (
    REFERENCE_TABLES,
    UPDATED_TABLES,
)

SCHEMA = pa.schema(
    [
        ("record_source", pa.string()),
        ("source_record_id", pa.string()),
        ("source_revision", pa.string()),
        ("source_artifact_sha256", pa.string()),
        ("knowledge_from", pa.timestamp("us", "UTC")),
        ("knowledge_to", pa.timestamp("us", "UTC")),
        ("disclosure_id", pa.string()),
        ("effective_at", pa.timestamp("us", "UTC")),
        ("title", pa.string()),
        ("exact_number", pa.decimal128(38, 10)),
    ]
)
KEY = ["disclosure_id", "effective_at"]


def row(known_day, *, title="original", source="original-source", number="123456789.1234567890"):
    return {
        "record_source": source,
        "source_record_id": "original-record",
        "source_revision": source + "@revision",
        "source_artifact_sha256": "sha256:" + "a" * 64,
        "knowledge_from": datetime(2026, 10, known_day, tzinfo=timezone.utc),
        "knowledge_to": None,
        "disclosure_id": "disclosure-1",
        "effective_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "title": title,
        "exact_number": Decimal(number),
    }


def normalize(records):
    connection = duckdb.connect()
    table = pa.Table.from_pylist(records, schema=SCHEMA)
    connection.register("raw_input", table)
    result = normalize_versions(connection, "raw_input", SCHEMA, KEY, [*KEY, "knowledge_from"])
    restored = connection.execute(
        "SELECT * FROM normalized ORDER BY knowledge_from",
    ).to_arrow_table()
    connection.close()
    return result, restored.cast(SCHEMA).to_pylist()


def test_identical_deliveries_retain_first_real_clock_and_exact_decimal():
    early, late = row(8), row(9, source="later-independent-source")
    result, observed = normalize([late, early])
    assert observed == [early]
    assert result == {"input_rows": 2, "output_rows": 1, "identical_repeats": 1}
    assert observed[0]["exact_number"] == Decimal("123456789.1234567890")


def test_input_order_does_not_change_retained_value_or_lineage():
    first = row(8, source="a-source")
    second = row(8, source="z-source")
    assert normalize([first, second]) == normalize([second, first])


def test_duplicate_version_from_same_source_revision_is_not_silently_dropped():
    first = row(8)
    with pytest.raises(BoundedError, match="SAME_SOURCE_DUPLICATE"):
        normalize([first, dict(first)])


def test_same_instant_disagreement_fails_instead_of_arbitrary_source_priority():
    with pytest.raises(BoundedError, match="SAME_INSTANT_BUSINESS_CONFLICT"):
        normalize([row(8), row(8, title="conflicting", source="other")])


def test_later_arrival_does_not_authorize_a_business_fact_change():
    with pytest.raises(BoundedError, match="UNAUTHORIZED_BUSINESS_REVISION"):
        normalize([row(8), row(9, title="changed", source="later")])


def test_closed_knowledge_versions_need_an_explicit_adapter():
    closed = {**row(8), "knowledge_to": datetime(2026, 10, 8, 12, tzinfo=timezone.utc)}
    with pytest.raises(BoundedError, match="CLOSED_KNOWLEDGE_VERSION_REQUIRES_ADAPTER"):
        normalize([closed, row(9)])


def test_distinct_business_keys_remain_distinct():
    other = {**row(9), "disclosure_id": "disclosure-2"}
    result, observed = normalize([row(8), other])
    assert len(observed) == 2
    assert result["identical_repeats"] == 0


def period_fixture(tmp_path, *, day=30):
    config = Path("config/pipelines")
    rules = json.loads((config / "base22-independent-image.v1.json").read_text())
    schemas = image_schemas(
        json.loads((config / "dwd-domain-model.v1.json").read_text()),
        json.loads((config / "dwd-domain-model.v1.1-extension.json").read_text()),
    )
    sources = tmp_path / "source"
    sources.mkdir()
    start = datetime(2026, 10, day, tzinfo=timezone.utc)
    bar = {
        "record_source": "independent-test-source",
        "source_record_id": "bar-1",
        "source_revision": "exact-test-v1",
        "source_artifact_sha256": "sha256:" + "1" * 64,
        "knowledge_from": datetime(2026, 11, 4, tzinfo=timezone.utc),
        "knowledge_to": None,
        "observation_start": start,
        "observation_end": start.replace(hour=23),
        "listing_id": "test-listing",
        "calendar_session_id": "test-session",
        "interval_size": "1d",
        "price_basis": "UNADJUSTED",
        "currency": "CNY",
        "open": Decimal("1.1234567890"),
        "high": Decimal("1.3"),
        "low": Decimal("1.1"),
        "close": Decimal("1.2"),
        "volume": Decimal("100"),
        "turnover": Decimal("120"),
        "trade_count": None,
    }
    tables = {}
    for table in sorted(UPDATED_TABLES):
        p = sources / (table + ".parquet")
        data = pa.Table.from_pylist(
            [bar] if table == "dwd_security_bar" else [], schema=schemas[table]
        )
        pq.write_table(data, p)
        tables[table] = [p]
    return rules, schemas, tables, bar


def run_period(tmp_path, name, rules, schemas, tables, *, through="2026-10-31"):
    limits = Limits(
        allocated_bytes=64 * 1024**2,
        temp_bytes=32 * 1024**2,
        free_floor_bytes=1024**2,
        rss_bytes=2 * 1024**3,
        wall_seconds=30,
    )
    with Budget(tmp_path / name, [p for paths in tables.values() for p in paths], limits) as budget:
        root = budget.root / "delta"
        try:
            result = build_delta(
                tables=tables,
                rules=rules,
                schemas=schemas,
                binding={"predecessor_through": "2026-09-30", "through_date": through},
                output=root,
                budget=budget,
            )
        finally:
            assert not budget._connections
    return root, result


def test_complete_delta_streams_exact_values_and_repeats_identically(tmp_path):
    rules, schemas, tables, bar = period_fixture(tmp_path)
    root, first = run_period(tmp_path, "first-job", rules, schemas, tables)
    other, second = run_period(tmp_path, "independent-job", rules, schemas, tables)
    assert first == second
    selected = next(row for row in first["files"] if row["table"] == "dwd_security_bar")
    assert pq.ParquetFile(root / selected["path"]).read().to_pylist() == [bar]
    assert file_sha(root / selected["path"]) == file_sha(other / selected["path"])
    assert len(first["files"]) == len(UPDATED_TABLES)
    assert first["READY"] is False
    assert not (root / "READY").exists()


def test_period_materialization_rejects_observation_after_cutoff(tmp_path):
    rules, schemas, tables, _ = period_fixture(tmp_path)
    with pytest.raises(BoundedError, match="BUSINESS_DATE_OUTSIDE_INTERVAL"):
        run_period(tmp_path, "outside-cutoff", rules, schemas, tables, through="2026-10-29")
    assert not (tmp_path / "outside-cutoff/delta/READY").exists()


@pytest.mark.parametrize("null_required", [False, True])
def test_legacy_nullable_headers_require_actual_nonnull_values(tmp_path, null_required):
    rules, schemas, tables, bar = period_fixture(tmp_path)
    table = "dwd_security_bar"
    nullable = pa.schema([pa.field(f.name, f.type, nullable=True) for f in schemas[table]])
    changed = {**bar, "close": None} if null_required else bar
    pq.write_table(pa.Table.from_pylist([changed], schema=nullable), tables[table][0])
    if null_required:
        with pytest.raises(BoundedError, match="REQUIRED_VALUE_NULL"):
            run_period(tmp_path, "nullable-invalid", rules, schemas, tables)
    else:
        root, result = run_period(tmp_path, "nullable-valid", rules, schemas, tables)
        item = next(r for r in result["files"] if r["table"] == table)
        restored = pq.ParquetFile(root / item["path"])
        assert restored.schema_arrow.equals(schemas[table], check_metadata=False)
        assert restored.read().to_pylist() == [bar]


def test_five_identity_extensions_keep_first_real_knowledge_and_flat_partitions(tmp_path):
    from tests.unit.test_base22_independent_image import _row

    rules, schemas, tables, _ = period_fixture(tmp_path)
    for table in REFERENCE_TABLES:
        early = _row(schemas[table])
        early.update(
            record_source="source",
            source_revision="overlay-v1",
            source_record_id="new-identity",
            source_artifact_sha256="sha256:" + "a" * 64,
            knowledge_from=datetime(2026, 10, 13, 10, tzinfo=timezone.utc),
            effective_from=datetime(2026, 10, 12, 16, tzinfo=timezone.utc),
        )
        later = {
            **early,
            "knowledge_from": datetime(2026, 10, 14, 10, tzinfo=timezone.utc),
            "source_artifact_sha256": "sha256:" + "b" * 64,
        }
        p = tmp_path / "source" / (table + ".parquet")
        pq.write_table(pa.Table.from_pylist([later, early], schema=schemas[table]), p)
        tables[table] = [p]
    root, result = run_period(tmp_path, "identity-job", rules, schemas, tables)
    for table in REFERENCE_TABLES:
        assert result["tables"][table] == {
            "input_rows": 2,
            "output_rows": 1,
            "identical_repeats": 1,
        }
        item = next(r for r in result["files"] if r["table"] == table)
        assert item["partition"] == "all"
        restored = pq.ParquetFile(root / item["path"]).read().to_pylist()
        assert restored[0]["knowledge_from"] == datetime(2026, 10, 13, 10, tzinfo=timezone.utc)
