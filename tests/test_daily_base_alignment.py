from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.daily_base_alignment import compare_bars


def write(root: Path, *, close: int):
    (root / "payload").mkdir(parents=True)
    for interval in ("1d", "5m"):
        pq.write_table(
            pa.table(
                {
                    "listing_id": ["id1"],
                    "close": [close],
                    "volume": [100],
                    "knowledge_from": ["actual-source-time"],
                }
            ),
            root / "payload" / (interval + ".parquet"),
        )


def test_rebase_business_values_are_independently_compared(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    write(old, close=10)
    write(new, close=10)
    result = compare_bars(old, new)
    assert result["1d"] == {"business_rows": 1, "differences": 0}
    assert result["5m"] == {"business_rows": 1, "differences": 0}


def test_rebase_audit_refuses_changed_business_values(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    write(old, close=10)
    write(new, close=11)
    with pytest.raises(ValueError, match="BUSINESS_VALUES_CHANGED"):
        compare_bars(old, new)
