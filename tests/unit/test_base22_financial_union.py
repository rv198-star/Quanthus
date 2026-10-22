"""Disposable typed financial years: exact atoms and cold local-only closure."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_financial_union as u
from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    Budget,
    Limits,
    Member,
    semantic_scan,
)
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError
from quanthus.pipelines.securities_warehouse.financial_reporting import financial_reporting_schemas

TABLE = "dwd_financial_fact"
SCHEMA = financial_reporting_schemas()[TABLE]
RULE = json.loads(Path("config/pipelines/base22-independent-image.v1.json").read_bytes())["tables"][
    TABLE
]


@pytest.fixture(autouse=True)
def disposable_reader_mount_fixture(monkeypatch, tmp_path):
    # Synthetic SQL/atom tests only. Real cold jobs separately use kernel mounts.
    real = os.statvfs
    roots = {tmp_path / "source", tmp_path / "job"}

    def snapshot(p):
        info = real(p)
        if Path(p) not in roots:
            return info
        return SimpleNamespace(
            **(
                {k: getattr(info, k) for k in dir(info) if k.startswith("f_")}
                | {"f_flag": info.f_flag | os.ST_RDONLY}
            )
        )

    monkeypatch.setattr(os, "statvfs", snapshot)


def clock(day):
    return datetime(2026, 8, day, 2, 3, 4, 123456, tzinfo=timezone.utc)


def row(day=1, key="a", **changes):
    return (
        dict(
            record_source="fixture",
            source_record_id=f"raw-{day}-{key}",
            source_revision="fixture-v1",
            source_artifact_sha256="sha256:" + "a" * 64,
            knowledge_from=clock(day),
            knowledge_to=None,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            published_at=None,
            financial_fact_id=key,
            financial_report_id=key,
            financial_concept_id="concept",
            unit_code="CNY",
            value_decimal=Decimal("1.0000000001"),
            value_text=None,
            dimensions_json="{}",
            context_hash="hash",
            decimals=10,
            is_nil=False,
        )
        | changes
    )


def member(path, rows, schema=SCHEMA):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return Member(path, file_sha(path), path.stat().st_size, len(rows))


def limits(batch=2):
    return Limits(
        batch_rows=batch,
        free_floor_bytes=1,
        memory_bytes=128 * 1024**2,
        temp_bytes=64 * 1024**2,
        allocated_bytes=128 * 1024**2,
    )


def build(tmp_path, old=None, new=None):
    native = [member(tmp_path / "source/old.parquet", old or [row(1), row(1, "b")])]
    incoming = [member(tmp_path / "source/new.parquet", new or [row(3), row(5)])]
    with Budget(tmp_path / "job", [tmp_path / "source"], limits()) as budget:
        output, patches = budget.root / "output.parquet", budget.root / "patches.parquet"
        receipt = u.materialize_year(
            native,
            incoming,
            table=TABLE,
            year=2025,
            schema=SCHEMA,
            rule=RULE,
            output=output,
            patches=patches,
            budget=budget,
        )
    return output, patches, receipt


def audit(tmp_path, output, patches, receipt):
    with Budget(tmp_path / "audit", [output.parent], limits(3)) as budget:
        return u.audit_year(output, patches, receipt, schema=SCHEMA, rule=RULE, budget=budget)


@pytest.mark.parametrize("batch", [1, 2, 3, 7])
def test_column_digest_oracle_across_slices_null_decimal_and_clocks(tmp_path, batch):
    rows = [
        row(1),
        row(2, value_decimal=None, is_nil=True, value_text="非数字"),
        row(3, knowledge_to=clock(4), published_at=clock(1), decimals=None),
    ]
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    p = tmp_path / "fixture.parquet"
    pq.write_table(table, p)
    summary = u._ColumnSummary(SCHEMA)
    for b in table.to_batches(max_chunksize=batch):
        summary.add(b)
    assert summary.finish() == semantic_scan([p], SCHEMA, batch)


def test_native_atoms_and_new_versions_survive_independent_cold_without_sources(tmp_path):
    output, patches, receipt = build(tmp_path)
    assert (receipt["old_rows"], receipt["new_rows"], receipt["closed_versions"]) == (2, 2, 2)
    actual = pq.read_table(output).to_pylist()
    assert actual == [
        row(1, knowledge_to=clock(3)),
        row(1, "b"),
        row(3, knowledge_to=clock(5)),
        row(5),
    ]
    (tmp_path / "source").rename(tmp_path / "unreachable-original")
    assert audit(tmp_path, output, patches, receipt)["original_source_runtime_reads"] == 0
    assert not (output.parent / "READY").exists()


def test_finite_gap_is_preserved_and_only_exact_earlier_end_is_closed(tmp_path):
    output, patches, receipt = build(tmp_path, [row(1, knowledge_to=clock(2))], [row(3)])
    assert receipt["closed_versions"] == 0
    assert pq.read_table(output).to_pylist()[0]["knowledge_to"] == clock(2)
    assert audit(tmp_path, output, patches, receipt)["rows"] == 2


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("equal-clock", "EQUAL_CLOCK_VERSION_CONFLICT"),
        ("provider", "CROSS_PROVIDER_PRIORITY_UNQUALIFIED"),
        ("wrong-year", "DATE_OR_INTERVAL_INVALID"),
        ("interval", "DATE_OR_INTERVAL_INVALID"),
        ("null-key", "INPUT_SCHEMA_OR_CENSUS_CHANGED"),
    ],
)
def test_unqualified_input_fails_before_output_without_changing_sources(tmp_path, fault, reason):
    new = row(3)
    if fault == "equal-clock":
        new = row(1)
    if fault == "provider":
        new["record_source"] = "other-fixture"
    if fault == "wrong-year":
        new["period_end"] = date(2024, 12, 31)
    if fault == "interval":
        new["knowledge_to"] = clock(2)
    if fault == "null-key":
        new["financial_concept_id"] = None
    nullable_key = pa.schema([f.with_nullable(True) for f in SCHEMA])
    old_m, new_m = (
        member(tmp_path / "source/old.parquet", [row()]),
        member(
            tmp_path / "source/new.parquet", [new], nullable_key if fault == "null-key" else SCHEMA
        ),
    )
    before = [m.path.read_bytes() for m in [old_m, new_m]]
    with Budget(tmp_path / "job", [tmp_path / "source"], limits()) as budget:
        with pytest.raises(u.BoundedError, match=reason):
            u.materialize_year(
                [old_m],
                [new_m],
                table=TABLE,
                year=2025,
                schema=SCHEMA,
                rule=RULE,
                output=budget.root / "output.parquet",
                patches=budget.root / "patches.parquet",
                budget=budget,
            )
        assert not (budget.root / "output.parquet").exists()
        assert not (budget.root / "patches.parquet").exists()
    assert [m.path.read_bytes() for m in [old_m, new_m]] == before


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("value", "UNAPPROVED_ORIGINAL_ATOM_CHANGE"),
        ("null", "LOCAL_SCHEMA_OR_CENSUS_CHANGED"),
        ("duplicate", "GLOBAL_DUPLICATE_VERSION"),
        ("end", "COLD_OVERLAPPING_INTERVAL"),
        ("patch", "COLD_PATCH_NOT_EXACT_NEXT_CLOCK"),
        ("census", "LOCAL_SEGMENT_SET_INVALID"),
    ],
)
def test_fully_rehashed_cold_fault_is_semantically_rejected(tmp_path, fault, reason):
    output, patches, receipt = build(tmp_path)
    rows = pq.read_table(output).to_pylist()
    if fault == "value":
        rows[0]["value_decimal"] = Decimal("2.0000000001")
    if fault == "null":
        rows[0]["record_source"] = None
    if fault == "duplicate":
        rows[2]["knowledge_from"] = rows[0]["knowledge_from"]
    if fault == "end":
        rows[0]["knowledge_to"] = None
    if fault == "patch":
        edit = pq.read_table(patches).to_pylist()
        edit[0]["after_to"] = clock(2)
        rows[0]["knowledge_to"] = clock(2)
        pq.write_table(pa.Table.from_pylist(edit, schema=u._patch_schema()), patches)
    if fault == "census":
        receipt["old_rows"] += 1
    schema = pa.schema([f.with_nullable(True) for f in SCHEMA]) if fault == "null" else SCHEMA
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), output)
    receipt["output_sha256"], receipt["patches_sha256"] = file_sha(output), file_sha(patches)
    receipt["identity"] = digest({k: v for k, v in receipt.items() if k != "identity"})
    with pytest.raises(u.BoundedError, match=reason):
        audit(tmp_path, output, patches, receipt)


def test_no_overwrite_or_unpinned_source(tmp_path):
    m = member(tmp_path / "source/new.parquet", [row()])
    with Budget(tmp_path / "job", [m.path.parent], limits()) as budget:
        output, patches = budget.root / "output.parquet", budget.root / "patches.parquet"
        output.write_bytes(b"retained")
        with pytest.raises(u.BoundedError, match="OUTPUT_EXISTS"):
            u.materialize_year(
                [],
                [m],
                table=TABLE,
                year=2025,
                schema=SCHEMA,
                rule=RULE,
                output=output,
                patches=patches,
                budget=budget,
            )
        assert output.read_bytes() == b"retained"
        bad = Member(m.path, "sha256:" + "0" * 64, m.size_bytes, m.rows)
        with pytest.raises(u.BoundedError, match="FROZEN_MEMBER_MISMATCH"):
            u.materialize_year(
                [],
                [bad],
                table=TABLE,
                year=2025,
                schema=SCHEMA,
                rule=RULE,
                output=budget.root / "other.parquet",
                patches=patches,
                budget=budget,
            )


def concept(day, effective):
    return {
        "financial_concept_id": "id",
        "knowledge_from": clock(day),
        "knowledge_to": None,
        "effective_from": clock(effective),
        "effective_to": None,
        "record_source": "fixture",
        "source_record_id": f"raw-{day}",
        "source_revision": "v1",
        "source_artifact_sha256": "sha256:" + "a" * 64,
        **dict.fromkeys(
            [
                "taxonomy_namespace",
                "taxonomy_version",
                "concept_code",
                "concept_name",
                "statement_type",
                "data_type",
                "period_type",
                "balance_type",
            ],
            "fixture",
        ),
    }


def test_concept_earliest_prefix_preserves_native_and_archives_nonextending_capture():
    old, early, stable = concept(1, 3), concept(3, 1), concept(5, 2)
    rows, proof = u.merge_concepts([old], [early, stable])
    assert rows == [old | {"knowledge_to": clock(3)}, early]
    assert proof["nonextending_captures_archived"] == 1
    assert old["knowledge_to"] is None


def test_concept_semantic_conflict_does_not_create_taxonomy_priority():
    with pytest.raises(SourceAssessmentError, match="SEMANTICS_CONFLICT"):
        u.merge_concepts([concept(1, 3)], [concept(3, 1) | {"concept_name": "other"}])


def test_native_concept_prefix_is_not_silently_dropped():
    with pytest.raises(u.BoundedError, match="NATIVE_CONCEPT_ATOM_CHANGED"):
        u.merge_concepts([concept(1, 1), concept(2, 2)], [concept(3, 3)])
