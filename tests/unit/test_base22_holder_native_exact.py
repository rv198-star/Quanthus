import json
import shutil
from decimal import Decimal, localcontext
from pathlib import Path

import pyarrow as pa
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_holder_native_exact import (
    CONTRACT_ID,
    carry_native_holder_batch,
    load_native_holder_contract,
)
from tests.unit.test_base22_independent_image import _row


@pytest.mark.parametrize(
    "raw",
    [
        "123.12345678901",
        "123.123456789012",
        "123.1234567890123",
        "9999999999999999999999999999.1234567890123",
        "1.1234567890123000",
        "-123.1234567890123",
        "0.0000000000000",
        "-0.0000000000000",
        None,
        "",
    ],
)
def test_native_exact_preserves_every_old_field_and_native_raw_precision(raw):
    contract, legacy, exact = load_native_holder_contract(Path("config/pipelines"))
    row = _row(legacy)
    row.update(
        raw_average_holding=raw,
        average_holding=Decimal("123.1234567890"),
        parser_version="legacy-original",
    )
    original = pa.Table.from_pylist([row], schema=legacy).to_batches()[0]
    with localcontext() as context:
        context.prec = 3
        result = carry_native_holder_batch(original, legacy_schema=legacy, contract_id=CONTRACT_ID)
    assert result.schema.equals(exact)
    assert result.select(legacy.names).equals(original)
    assert result.column("average_holding_exact")[0].as_py() == (
        Decimal(raw) if raw not in (None, "") else None
    )
    assert result.column("raw_average_holding")[0].as_py() == raw
    assert all(exact.field(f.name) == f for f in legacy)
    assert contract["whole_image_schema_admission"] == "pending_final_candidate_review"
    assert contract["consumer_admission"] == "NOT_GRANTED"
    assert exact.field("average_holding_exact").type == pa.decimal256(41, 13)


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "garbage", "1e-14", "1e28"])
def test_native_exact_cannot_round_or_infer_unsupported_values(raw):
    _, legacy, _ = load_native_holder_contract(Path("config/pipelines"))
    row = _row(legacy)
    row["raw_average_holding"] = raw
    with pytest.raises(BoundedError, match="NOT_REPRESENTABLE"):
        carry_native_holder_batch(
            pa.Table.from_pylist([row], schema=legacy).to_batches()[0],
            legacy_schema=legacy,
            contract_id=CONTRACT_ID,
        )


@pytest.mark.parametrize("fault", ["purpose", "old-contract", "wrong-input-schema", "oversized"])
def test_native_contract_or_batch_mismatch_closed(tmp_path, fault):
    cfg = tmp_path / "contract"
    shutil.copytree(Path("config/pipelines"), cfg)
    _, legacy, _ = load_native_holder_contract(cfg)
    if fault in ["purpose", "old-contract"]:
        path = cfg / "base22-holder-native-exact.v3.json"
        body = json.loads(path.read_text())
        body["consumer_admission"] = "ADMITTED" if fault == "purpose" else "NOT_GRANTED"
        if fault == "old-contract":
            body["parent_schema_contract_sha256"] = "sha256:" + "a" * 64
        path.write_text(json.dumps(body))
        with pytest.raises(BoundedError):
            load_native_holder_contract(cfg)
    else:
        row = _row(legacy)
        row["raw_average_holding"] = "1.1234567890123"
        batch = pa.Table.from_pylist(
            [row] * (16385 if fault == "oversized" else 1), schema=legacy
        ).to_batches(max_chunksize=20000)[0]
        if fault == "wrong-input-schema":
            batch = batch.select(["raw_average_holding"])
        with pytest.raises(BoundedError):
            carry_native_holder_batch(batch, legacy_schema=legacy, contract_id=CONTRACT_ID)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "old-value",
        "raw-text",
        "exact-value",
        "timestamp",
        "lost-row",
        "changed-input",
        "wrong-schema",
        "repeat",
    ],
)
def test_partition_serialization_and_cold_comparison_preserve_all_old_atoms(tmp_path, fault):
    from datetime import timedelta

    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits, Member
    from quanthus.pipelines.securities_warehouse.base22_holder_native_exact import (
        materialize_native_holder_partition,
        validate_native_holder_partition,
    )

    _, legacy, exact = load_native_holder_contract(Path("config/pipelines"))
    rows = []
    for i, raw in enumerate(
        ["123.1234567890123", "9999999999999999999999999999.1234567890123", None]
    ):
        row = _row(legacy)
        row.update(
            raw_average_holding=raw,
            average_holding=Decimal("123.1234567890"),
            source_record_id=str(i),
        )
        rows.append(row)
    source = tmp_path / "source"
    source.mkdir()
    native = []
    for i, group in enumerate([rows[:1], rows[1:]]):
        p = source / (str(i) + ".parquet")
        pq.write_table(pa.Table.from_pylist(group, schema=legacy), p)
        native.append(Member(p, file_sha(p), p.stat().st_size, len(group)))
    before = {m.path: m.path.read_bytes() for m in native}
    if fault == "changed-input":
        p = native[0].path
        pq.write_table(
            pa.Table.from_pylist(
                [dict(rows[0], source_revision="mutated-before-build")], schema=legacy
            ),
            p,
        )
        before = {m.path: m.path.read_bytes() for m in native}
    with Budget(tmp_path / "job", [source], Limits(free_floor_bytes=1)) as budget:
        out = budget.root / "out.parquet"
        if fault in ["changed-input", "wrong-schema"]:
            with pytest.raises(BoundedError):
                materialize_native_holder_partition(
                    native,
                    out,
                    legacy_schema=legacy,
                    exact_schema=legacy if fault == "wrong-schema" else exact,
                    budget=budget,
                )
            assert not out.exists()
        else:
            receipt = materialize_native_holder_partition(
                native, out, legacy_schema=legacy, exact_schema=exact, budget=budget
            )
            assert receipt["rows"] == 3
            assert out.stat().st_nlink == 1
            if fault in ["old-value", "raw-text", "exact-value", "timestamp", "lost-row"]:
                actual = pq.ParquetFile(out).read().to_pylist()
                if fault == "old-value":
                    actual[0]["average_holding"] = Decimal("0")
                if fault == "raw-text":
                    actual[0]["raw_average_holding"] = "different-original-token"
                if fault == "exact-value":
                    actual[0]["average_holding_exact"] = Decimal("123.1234567890120")
                if fault == "timestamp":
                    actual[0]["knowledge_from"] += timedelta(microseconds=1)
                if fault == "lost-row":
                    actual = actual[:-1]
                pq.write_table(pa.Table.from_pylist(actual, schema=exact), out)
                with pytest.raises(BoundedError):
                    validate_native_holder_partition(
                        native, out, legacy_schema=legacy, exact_schema=exact, budget=budget
                    )
            elif fault == "repeat":
                saved = out.read_bytes()
                with pytest.raises(BoundedError, match="OUTPUT_EXISTS"):
                    materialize_native_holder_partition(
                        native, out, legacy_schema=legacy, exact_schema=exact, budget=budget
                    )
                assert out.read_bytes() == saved
            else:
                assert (
                    validate_native_holder_partition(
                        native, out, legacy_schema=legacy, exact_schema=exact, budget=budget
                    )
                    == 3
                )
    assert before == {p: p.read_bytes() for p in before}
