from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base22_native_coordinates import (
    check_decoded,
    predicates,
)
from quanthus.pipelines.securities_warehouse.base22_native_repartition import repartition_table


@pytest.mark.parametrize(
    "instant,part,valid",
    [
        (datetime(2026, 7, 31, 15, 59, tzinfo=timezone.utc), "at_year=2026/at_month=7", True),
        (datetime(2026, 7, 31, 16, tzinfo=timezone.utc), "at_year=2026/at_month=7", False),
        (datetime(2026, 7, 31, 16, tzinfo=timezone.utc), "at_year=2026/at_month=8", True),
        (datetime(1990, 6, 30, 15, tzinfo=timezone.utc), "at_year=1990/at_month=7", True),
        (datetime(1990, 6, 30, 14, 59, tzinfo=timezone.utc), "at_year=1990/at_month=7", False),
    ],
)
def test_decoded_instant_bounds_include_historical_shanghai_dst(instant, part, valid):
    batch = pa.record_batch([pa.array([instant], type=pa.timestamp("us", tz="UTC"))], names=["at"])
    rule = {"partition_by": ["at_year", "at_month"]}
    assert all("timezone(" not in p for p in predicates(part, rule, batch.schema))
    if valid:
        check_decoded(batch, part, rule)
    else:
        with pytest.raises(BoundedError, match="PARTITION_COORDINATE_CHANGED"):
            check_decoded(batch, part, rule)


def test_mixed_month_repartition_preserves_decimal_null_and_every_original_instant(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    schema = pa.schema(
        [
            pa.field("at", pa.timestamp("us", tz="UTC")),
            pa.field("quantity", pa.decimal128(20, 10)),
            pa.field("acquired_at", pa.timestamp("us", tz="UTC")),
            pa.field("unknown", pa.string()),
        ]
    )
    rows = [
        {
            "at": datetime(2026, 7, 31, 15 + n, tzinfo=timezone.utc),
            "quantity": [Decimal("1.1234567890"), Decimal("0.0000000000")][n],
            "acquired_at": datetime(2026, 8, 9, 1, 2, 3, 4, tzinfo=timezone.utc),
            "unknown": None,
        }
        for n in range(2)
    ]
    original = pa.Table.from_pylist(rows, schema=schema)
    p = source / "mixed.parquet"
    pq.write_table(original, p)
    body = {"files": [{"path": p.name, "sha256": file_sha(p)}]}
    (source / "manifest.json").write_text(json.dumps(body))
    plan = {
        "sources": {
            "x": {
                "root": str(source),
                "descriptor": "manifest.json",
                "descriptor_sha256": file_sha(source / "manifest.json"),
            }
        },
        "tables": {
            "table": {
                "source_ids": ["x"],
                "rows": 2,
                "files": [
                    {
                        "source_id": "x",
                        "path": p.name,
                        "sha256": file_sha(p),
                        "size_bytes": p.stat().st_size,
                        "rows": 2,
                    }
                ],
            }
        },
    }
    before = p.read_bytes()
    with Budget(
        tmp_path / "job",
        [source],
        Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2),
    ) as budget:
        result = repartition_table(
            plan=plan,
            table="table",
            schema=schema,
            rule={"partition_by": ["at_year", "at_month"]},
            budget=budget,
            code_revision="a" * 40,
        )
        restored = pa.concat_tables(
            [pq.ParquetFile(budget.root / i["path"]).read() for i in result["files"]]
        )
        assert restored.equals(original)
        assert result["partition_rows"] == {
            "at_year=2026/at_month=7": 1,
            "at_year=2026/at_month=8": 1,
        }
        assert result["all_original_values_and_times_preserved"]
        assert not result["new_provider_observation_or_knowledge_or_original_normalization_claim"]
    assert p.read_bytes() == before
