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


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "purpose",
        "wrong-schema",
        "rounded-exact",
        "old-time",
        "partition",
        "empty-extra",
        "dated-parent",
        "unknown-lineage",
    ],
)
def test_native_cold_scope_exact_physical_set_and_real_column_comparison(tmp_path, fault):
    from datetime import datetime, timedelta, timezone

    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import (
        digest,
        file_sha,
        load_contract,
        schemas,
    )
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits, semantic_scan
    from quanthus.pipelines.securities_warehouse.base22_holder_native_exact import (
        audit_native_holder_scope,
    )

    _, legacy, exact = load_native_holder_contract(Path("config/pipelines"))
    _, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    ss["dwd_shareholder_count_observation"] = exact
    table = "dwd_shareholder_count_observation"
    with Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as budget:
        root = budget.root / "image"
        stage = root / "observations/native-precision"
        cfg = stage / "contract"
        cfg.mkdir(parents=True)
        names = [
            "base22-holder-native-exact.v3.json",
            "base22-independent-image.v1.json",
            "dwd-domain-model.v1.json",
            "dwd-domain-model.v1.1-extension.json",
        ]
        cfgsha = {}
        for name in names:
            shutil.copyfile(Path("config/pipelines") / name, cfg / name)
            cfgsha[name] = file_sha(cfg / name)
        row = _row(legacy)
        row.update(
            raw_average_holding="123.1234567890123",
            average_holding=Decimal("123.1234567890"),
            company_id="company",
            security_id="security",
        )
        native = stage / "archives/native/period_end_year=2026/original.parquet"
        native.parent.mkdir(parents=True)
        old = pa.Table.from_pylist([row], schema=legacy)
        pq.write_table(old, native)
        archive = {
            "path": str(native.relative_to(root)),
            "sha256": file_sha(native),
            "size_bytes": native.stat().st_size,
            "rows": 1,
            "role": "original_native_holder",
            "partition": "period_end_year=2026",
            "original_source_root": "/unreachable-archived-source",
        }
        actual = carry_native_holder_batch(
            old.to_batches()[0], legacy_schema=legacy, contract_id=CONTRACT_ID
        )
        outs = actual.to_pylist()
        if fault == "rounded-exact":
            outs[0]["average_holding_exact"] = Decimal("123.1234567890120")
        if fault == "old-time":
            outs[0]["knowledge_from"] += timedelta(microseconds=1)
        groups = {table: outs}
        for t, key, value in [
            ("dwd_listed_company", "company_id", "company"),
            ("dwd_security", "security_id", "security"),
        ]:
            parent = _row(ss[t])
            parent.update(
                {
                    key: value,
                    "effective_from": datetime(2011, 1, 1, tzinfo=timezone.utc),
                    "knowledge_from": datetime(2011, 1, 1, tzinfo=timezone.utc),
                }
            )
            if fault == "dated-parent" and t == "dwd_security":
                parent["effective_from"] = datetime(2026, 8, 5, tzinfo=timezone.utc)
            groups[t] = [parent]
        outputs = []
        registry = {}
        for t, rows in groups.items():
            p = stage / "payload" / t / "period_end_year=2026" / "part.parquet"
            p.parent.mkdir(parents=True)
            schema = legacy if fault == "wrong-schema" and t == table else ss[t]
            data = pa.Table.from_pylist(rows, schema=schema)
            pq.write_table(data, p)
            outputs.append(
                {
                    "table": t,
                    "path": str(p.relative_to(root)),
                    "partition": "period_end_year=2026",
                    "rows": len(rows),
                    "sha256": file_sha(p),
                    "size_bytes": p.stat().st_size,
                    "semantic_scan": semantic_scan([p], schema),
                }
            )
            registry[t] = sorted(
                {
                    (r["record_source"], r["source_revision"], r["source_artifact_sha256"])
                    for r in rows
                }
            )
        if fault == "empty-extra":
            pq.write_table(pa.Table.from_pylist([], schema=exact), stage / "payload/extra.parquet")
        proof = {
            "partition": "period_end_year=2025" if fault == "partition" else "period_end_year=2026",
            "native_local_members": [archive["path"]],
            "output_local_member": outputs[0]["path"],
            "old_columns_unchanged": True,
            "rows": 1,
            "new_exact_rule": "exact_retained_native_decimal_text_else_null_only_for_source_null",
        }
        desc = {
            "kind": "isolated_native_holder_exact_preparation_not_complete_base22",
            "label": "native-precision",
            "schema_contract_id": CONTRACT_ID,
            "schema_contract_sha256": cfgsha[names[0]],
            "contract_members": cfgsha,
            "consumer_admission": "ADMITTED" if fault == "purpose" else "NOT_GRANTED",
            "whole_image_schema_admission": "pending_final_candidate_review",
            "whole33_acceptance": False,
            "READY": False,
            "outputs": outputs,
            "output_rows": {t: len(r) for t, r in groups.items()},
            "original_native_archives": [archive],
            "exact_native_precision_proofs": [proof],
            "source_registries": registry,
            "precision_counts": {"rows": 1, "exact_finite": 1, "raw_source_null": 0},
        }
        if fault == "unknown-lineage":
            desc["source_registries"][table] = [("unknown", "unknown", "sha256:" + "d" * 64)]
        desc["identity"] = digest(desc)
        if fault:
            with pytest.raises(BoundedError):
                audit_native_holder_scope(root, desc, budget)
        else:
            result = audit_native_holder_scope(root, desc, budget)
            assert result["all_original_native_columns_and_exact13_values"] == "PASS"
            assert result["complete_Base22"] is False
