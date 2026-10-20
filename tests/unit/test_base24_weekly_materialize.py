"""Bounded real-format synthetic weekly matrix, typed output and closed failures."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
from quanthus.pipelines.securities_warehouse.base24_weekly_materialize import materialize_statements
from tests.unit.test_base24_weekly_financial import OBSERVED, THROUGH, inputs


def fixture(root):
    import json

    rows, receipts = [], []
    for provider, statement in [
        ("fzb", "balance_sheet"),
        ("lrb", "income_statement"),
        ("llb", "cash_flow"),
    ]:
        row, receipt, kw = inputs("1.00000000001", binary=True)
        row.update(provider_source=provider, statement_type=statement)
        receipt["request_json"] = json.dumps({"params": {"source": provider}})
        rows.append(row)
        receipts.append(receipt)
    source, receipt_path = root / "source.parquet", root / "receipts.parquet"
    pq.write_table(pa.Table.from_pylist(rows), source)
    pq.write_table(pa.Table.from_pylist(receipts), receipt_path)
    return dict(
        source=source,
        receipts=receipt_path,
        source_sha256=file_sha(source),
        receipt_sha256=file_sha(receipt_path),
        expected_envelopes=3,
        resolver=kw["resolver"],
        observed_at=OBSERVED,
        through=THROUGH,
    )


def budget(root, kw):
    return Budget(
        root,
        [kw["source"], kw["receipts"]],
        Limits(memory_bytes=128 * 1024**2, allocated_bytes=128 * 1024**2, free_floor_bytes=1),
    )


def test_three_statement_typed_output_global_closure_and_sources_unchanged(tmp_path):
    kw = fixture(tmp_path)
    with budget(tmp_path / "job", kw) as b:
        result = materialize_statements(**kw, output=b.root / "tables", budget=b)
    assert result["source_envelopes"] == 3 and result["source_securities"] == 1
    assert result["global_keys_and_financial_references"] == "PASS"
    assert result["row_counts"] == dict(
        dwd_financial_report=3, dwd_financial_concept=3, dwd_financial_fact=3
    )
    facts = pq.read_table(b.root / "tables/dwd_financial_fact.parquet").to_pylist()
    assert all(r["value_decimal"] is None and r["value_text"] == "1.00000000001" for r in facts)
    assert all(r["knowledge_from"] == OBSERVED and r["published_at"] is None for r in facts)
    assert file_sha(kw["source"]) == kw["source_sha256"]
    assert file_sha(kw["receipts"]) == kw["receipt_sha256"]
    assert not (b.root / "tables/READY").exists()


@pytest.mark.parametrize(
    "mutation",
    ["missing-provider", "duplicate", "unknown-provider", "hash", "census", "receipt-hash"],
)
def test_frozen_matrix_errors_never_create_typed_output(tmp_path, mutation):
    kw = fixture(tmp_path)
    receipts = pq.read_table(kw["receipts"]).to_pylist()
    if mutation == "missing-provider":
        receipts.pop()
    elif mutation == "duplicate":
        receipts[2] = receipts[0]
    elif mutation == "unknown-provider":
        receipts[2]["request_json"] = '{"params":{"source":"unknown"}}'
    elif mutation == "hash":
        kw["source_sha256"] = "sha256:" + "0" * 64
    elif mutation == "census":
        kw["expected_envelopes"] = 4
    elif mutation == "receipt-hash":
        kw["receipt_sha256"] = "sha256:" + "0" * 64
    if mutation in {"missing-provider", "duplicate", "unknown-provider"}:
        pq.write_table(pa.Table.from_pylist(receipts), kw["receipts"])
        kw["receipt_sha256"] = file_sha(kw["receipts"])
    with budget(tmp_path / "job", kw) as b:
        with pytest.raises(RuntimeError, match="WEEKLY_"):
            materialize_statements(**kw, output=b.root / "tables", budget=b)
        assert not (b.root / "tables").exists()


def test_existing_output_is_preserved(tmp_path):
    kw = fixture(tmp_path)
    with budget(tmp_path / "job", kw) as b:
        output = b.root / "tables"
        output.mkdir()
        (output / "old.txt").write_bytes(b"preserved")
        with pytest.raises(RuntimeError, match="OUTPUT_EXISTS_PRESERVED"):
            materialize_statements(**kw, output=output, budget=b)
        assert list(output.iterdir()) == [output / "old.txt"]
        assert (output / "old.txt").read_bytes() == b"preserved"
