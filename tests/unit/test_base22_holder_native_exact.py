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
