"""Disposable inputs only; real source integrity must not imply admission."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import CONTRACT_SHA, digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import (
    SourceAssessmentError,
    announcement_coverage,
    assess_package,
    canonical_daily_basis,
    disjoint_output,
    history_document_lower_bound,
    run_assessment,
    tail_raw_projection,
)


def _fixture(root: Path) -> Path:
    root.mkdir()
    (root / "artifacts").mkdir()
    data = root / "artifacts" / "dividends.parquet"
    pq.write_table(pa.table({"stock_code": ["synthetic"], "acquired_at": ["unknown"]}), data)
    manifest = {
        "schema_version": 1,
        "status": "ready",
        "release_id": "synthetic-source",
        "schedule": "daily",
        "market_date": "2026-08-01",
        "created_at": "2026-10-04T00:00:00+00:00",
        "producer": "synthetic",
        "artifacts": [
            {
                "name": "dividends_delta",
                "relative_path": "artifacts/dividends.parquet",
                "size_bytes": data.stat().st_size,
                "sha256": file_sha(data),
                "source_revision": "synthetic-v1",
                "knowledge_cutoff": "2026-10-04T00:00:00+00:00",
            }
        ],
    }
    (root / "release.json").write_text(json.dumps(manifest))
    (root / "READY").write_text(file_sha(root / "release.json") + "\n")
    return root


def test_release_census_does_not_admit_source_or_emit_values(tmp_path: Path) -> None:
    root = _fixture(tmp_path / "input")
    report = assess_package(root, manifest_name="release.json")
    assert report["status"] == "PASS"
    assert report["files"][0]["rows"] == 1
    assert report["identity"]["created_at"] == "2026-10-04T00:00:00+00:00"
    assert "unknown" not in json.dumps(report)
    assert "not_qualification" in report["assessment_kind"]
    assert "no source admission" in report["limitations"][-1]


@pytest.mark.parametrize("damage", ["missing", "byte", "extra"])
def test_payload_integrity_failure_retains_evidence(tmp_path: Path, damage: str) -> None:
    root = _fixture(tmp_path / "input")
    path = root / "artifacts/dividends.parquet"
    if damage == "missing":
        path.unlink()
    elif damage == "byte":
        with path.open("ab") as stream:
            stream.write(b"damaged")
    else:
        (root / "undeclared").write_bytes(b"extra")
    report = assess_package(root, manifest_name="release.json")
    assert report["status"] == "FAIL"
    assert report["failures"]
    assert report["files"]


@pytest.mark.parametrize("kind", ["member", "ancestor"])
def test_symlinks_fail_without_touching_source(tmp_path: Path, kind: str) -> None:
    root = _fixture(tmp_path / "input")
    manifest_bytes = (root / "release.json").read_bytes()
    data = root / "artifacts/dividends.parquet"
    original = data.read_bytes()
    if kind == "member":
        backup = root / "original"
        data.rename(backup)
        data.symlink_to(backup)
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    with pytest.raises(SourceAssessmentError, match="SOURCE_SYMLINK"):
        assess_package(root, manifest_name="release.json")
    assert (root / "release.json").read_bytes() == manifest_bytes
    assert data.read_bytes() == original


@pytest.mark.parametrize("relation", ["same", "inside", "parent"])
def test_preflight_overlap_before_any_output_mutation(tmp_path: Path, relation: str) -> None:
    root = _fixture(tmp_path / "input")
    output = {"same": root, "inside": root / "run", "parent": tmp_path}[relation]
    snapshot = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    with pytest.raises(SourceAssessmentError, match="SOURCE_OUTPUT_OVERLAP"):
        disjoint_output(output, [root])
    assert snapshot == {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def test_expected_manifest_and_ready_are_independent_gates(tmp_path: Path) -> None:
    root = _fixture(tmp_path / "input")
    with pytest.raises(SourceAssessmentError, match="FROZEN_MANIFEST_MISMATCH"):
        assess_package(
            root, manifest_name="release.json", expected_manifest_sha256="sha256:" + "0" * 64
        )
    (root / "READY").write_text("sha256:" + "0" * 64)
    with pytest.raises(SourceAssessmentError, match="READY_MISMATCH"):
        assess_package(root, manifest_name="release.json")


def test_ods_base_content_digest_and_physical_inventory(tmp_path: Path) -> None:
    root = tmp_path / "ods"
    root.mkdir()
    (root / "payload").mkdir()
    member = root / "payload/item.json"
    member.write_text("{}")
    content = {
        "through_date": "2026-07-31",
        "files": [{"path": "payload/item.json", "sha256": file_sha(member), "size_bytes": 2}],
    }
    manifest = {
        "kind": "quanthus_ods_base_bundle",
        "schema_version": 1,
        "bundle_id": "synthetic-ods",
        "content": content,
        "content_digest": digest(content),
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest))
    (root / "READY").write_text(file_sha(path))
    assert assess_package(root)["status"] == "PASS"
    manifest["content"]["through_date"] = "2026-08-31"
    path.write_text(json.dumps(manifest))
    (root / "READY").write_text(file_sha(path))
    with pytest.raises(SourceAssessmentError, match="CONTENT_DIGEST_MISMATCH"):
        assess_package(root)


def test_basis_adapter_preserves_decimal_precision_clocks_zero_and_null() -> None:
    batch = pa.record_batch(
        {
            "interval_size": ["1d", "1m", "1d"],
            "price_basis": ["none", "none", "UNADJUSTED"],
            "close": pa.array(
                [Decimal("12.1234567890"), Decimal("0"), None], type=pa.decimal128(38, 10)
            ),
            "knowledge_from": pa.array(
                [datetime(2026, 10, 4, tzinfo=timezone.utc)] * 3, type=pa.timestamp("us", tz="UTC")
            ),
        }
    )
    out, metrics = canonical_daily_basis(batch)
    assert out.column("price_basis").to_pylist() == ["UNADJUSTED", "none", "UNADJUSTED"]
    assert out.column("close").equals(batch.column("close"))
    assert out.column("knowledge_from").equals(batch.column("knowledge_from"))
    assert metrics == {
        "rows": 3,
        "daily_rows": 2,
        "changed_basis_rows": 1,
        "non_basis_field_differences": 0,
    }


@pytest.mark.parametrize("basis", [None, "adjusted", ""])
def test_unknown_daily_basis_cannot_be_normalized(basis: str | None) -> None:
    batch = pa.record_batch(
        {"interval_size": ["1d"], "price_basis": pa.array([basis], pa.string())}
    )
    with pytest.raises(SourceAssessmentError, match="UNKNOWN_DAILY_PRICE_BASIS"):
        canonical_daily_basis(batch)


@pytest.mark.parametrize("change", ["acquisition", "body", "missing"])
def test_rolling_events_prove_business_coverage_without_inventing_clocks(
    tmp_path: Path,
    change: str,
) -> None:
    old = tmp_path / "weekend.parquet"
    rolling = tmp_path / "monday.parquet"
    row = {
        "stock_code": "synthetic",
        "ann_id": "event-1",
        "title": "original",
        "publish_time": "2026-08-01T16:00:00+08:00",
        "acquired_at": "2026-08-01T20:00:00+08:00",
    }
    pq.write_table(pa.Table.from_pylist([row]), old)
    current = dict(row, acquired_at="2026-08-03T20:00:00+08:00")
    if change == "body":
        current["title"] = "revision"
    if change == "missing":
        current["ann_id"] = "another"
    pq.write_table(pa.Table.from_pylist([current]), rolling)
    result = announcement_coverage([old], rolling)
    assert result["status"] == ("PASS" if change == "acquisition" else "BLOCKED")
    assert pq.read_table(old).to_pylist() == [row]
    assert pq.read_table(rolling).to_pylist() == [current]


@pytest.mark.parametrize("fault", [None, "duplicate", "basis", "precision", "identity", "hash"])
def test_retained_tail_adapter_never_fabricates_prices_or_admission(
    tmp_path: Path,
    fault: str | None,
) -> None:
    path = tmp_path / "synthetic-tail.json"
    fields = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adjustflag",
        "turn",
        "pctChg",
        "tradestatus",
        "isST",
    ]
    values = [
        "2026-07-24",
        "sz.000919",
        "1.0000000001",
        "2",
        "0",
        "",
        "0",
        "",
        "3",
        "",
        "",
        "0",
        "1",
    ]
    if fault == "basis":
        values[8] = "2"
    if fault == "precision":
        values[2] = "1.00000000001"
    if fault == "identity":
        values[1] = "sz.000001"
    rows = [values, values] if fault == "duplicate" else [values]
    path.write_text(
        json.dumps(
            {
                "fields": fields,
                "rows": rows,
                "n_rows": len(rows),
                "bs_code": "sz.000919",
                "meta": {"error_code": "0"},
            }
        )
    )
    sha = "sha256:" + "0" * 64 if fault == "hash" else file_sha(path)
    if fault is not None:
        with pytest.raises(SourceAssessmentError):
            tail_raw_projection(path, sha, "000919")
    else:
        table = tail_raw_projection(path, sha, "000919")
        row = table.to_pylist()[0]
        assert row["open"] == Decimal("1.0000000001")
        assert row["close"] is None and row["low"] == Decimal("0")
        assert row["tradestatus"] == "0" and row["isST"] == "1"
        assert "knowledge_from" not in table.schema.names


def test_assessment_retains_independent_work_without_ready_or_admission(tmp_path: Path) -> None:
    first = _fixture(tmp_path / "first")
    second = _fixture(tmp_path / "second")
    expected = file_sha(first / "release.json")
    (first / "artifacts/dividends.parquet").unlink()
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "purpose": "p10_base22_source_assessment",
                "image_contract_sha256": CONTRACT_SHA,
                "source_roots": [str(first), str(second)],
                "packages": [
                    {
                        "label": label,
                        "root": str(root),
                        "manifest_name": "release.json",
                        "expected_manifest_sha256": sha,
                    }
                    for label, root, sha in [
                        ("first", first, expected),
                        ("second", second, file_sha(second / "release.json")),
                    ]
                ],
            }
        )
    )
    output = tmp_path / "run"
    result = run_assessment(selection, output)
    assert result["physical_integrity"] == "FAIL"
    assert [x["status"] for x in result["packages"]] == ["FAIL", "PASS"]
    assert result["candidate_admission"] == "NOT_GRANTED"
    assert (output / "first.json").is_file() and (output / "second.json").is_file()
    assert not (output / "READY").exists()
    before = (output / "assessment.json").read_bytes()
    with pytest.raises(FileExistsError):
        run_assessment(selection, output)
    assert (output / "assessment.json").read_bytes() == before


@pytest.mark.parametrize(
    "fault", ["purpose", "label", "digest", "overlap", "broader_root", "outside_root"]
)
def test_real_selection_rejected_before_mkdir(tmp_path: Path, fault: str) -> None:
    root = _fixture(tmp_path / "input")
    selection = tmp_path / "selection.json"
    payload = {
        "schema_version": 1,
        "purpose": "p10_base22_source_assessment",
        "image_contract_sha256": CONTRACT_SHA,
        "source_roots": [str(root)],
        "packages": [
            {
                "label": "input",
                "root": str(root),
                "manifest_name": "release.json",
                "expected_manifest_sha256": file_sha(root / "release.json"),
            }
        ],
    }
    if fault == "purpose":
        payload["purpose"] = "real_image_qualified"
    elif fault == "label":
        payload["packages"][0]["label"] = "../escape"
    elif fault == "digest":
        payload["packages"][0]["expected_manifest_sha256"] = "missing"
    elif fault == "broader_root":
        payload["source_roots"] = [str(tmp_path)]
    elif fault == "outside_root":
        payload["source_roots"] = [str(tmp_path / "another")]
    selection.write_text(json.dumps(payload))
    output = root / "run" if fault == "overlap" else tmp_path / "run"
    with pytest.raises(SourceAssessmentError):
        run_assessment(selection, output)
    assert not output.exists()


def test_capacity_floor_does_not_sample_prices_or_ignore_missing_tables() -> None:
    schema = pa.schema([("price", pa.decimal128(38, 10)), ("clock", pa.timestamp("us", tz="UTC"))])
    result = history_document_lower_bound({"table": schema}, {"table": 100})
    assert result["minimum_document_bytes"] == len(b'{"clock":0,"price":0}') * 100
    assert not result["includes_indexes_or_merge_copy"]
    with pytest.raises(SourceAssessmentError, match="CAPACITY_CENSUS_INVALID"):
        history_document_lower_bound({"table": schema}, {})


def test_september_release_rejected_before_payload_hashing(tmp_path: Path, monkeypatch) -> None:
    root = _fixture(tmp_path / "input")
    path = root / "release.json"
    manifest = json.loads(path.read_text())
    manifest["market_date"] = "2026-09-01"
    path.write_text(json.dumps(manifest))
    (root / "READY").write_text(file_sha(path))

    def no_payload_hash(path):
        raise AssertionError("out-of-scope payload was read")

    monkeypatch.setattr(
        "quanthus.pipelines.securities_warehouse.base22_sources.file_sha", no_payload_hash
    )
    with pytest.raises(SourceAssessmentError, match="SOURCE_BUSINESS_DATE_OUTSIDE_SCOPE"):
        assess_package(root, manifest_name="release.json")


@pytest.mark.parametrize("repair", [False, True])
def test_dwd_legacy_identity_preserves_financial_repair_and_row_census(
    tmp_path: Path,
    repair: bool,
) -> None:
    root = tmp_path / "dwd"
    (root / "payload/domain/table").mkdir(parents=True)
    member = root / "payload/domain/table/dwd_synthetic.parquet"
    pq.write_table(pa.table({"key": ["synthetic"]}), member)
    manifest = {
        "kind": "dwd_domain_base_package",
        "schema_version": 1,
        "package_id": "synthetic",
        "run_id": "run",
        "through_date": "2026-07-31",
        "input_plan_digest": "synthetic",
        "ods_base": {},
        "model_sha256": "synthetic",
        "model_extension_sha256": "synthetic",
        "code_revision": "synthetic",
        "upstream_candidates": {},
        "row_counts": {"dwd_synthetic": 1},
        "files": [
            {
                "path": member.relative_to(root).as_posix(),
                "sha256": file_sha(member),
                "size_bytes": member.stat().st_size,
            }
        ],
    }
    if repair:
        manifest["repair_provenance"] = {"kind": "financial-effective-time-repair"}
    identity = {k: v for k, v in manifest.items() if k not in {"kind", "schema_version"}}
    manifest["content_digest"] = digest(identity)
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest))
    (root / "READY").write_text(file_sha(path))
    assert assess_package(root)["status"] == "PASS"
    manifest["row_counts"]["dwd_synthetic"] = 2
    identity["row_counts"]["dwd_synthetic"] = 2
    manifest["content_digest"] = digest(identity)
    path.write_text(json.dumps(manifest))
    (root / "READY").write_text(file_sha(path))
    result = assess_package(root)
    assert result["status"] == "FAIL"
    assert result["failures"][-1]["reason"] == "DECLARED_ROW_CENSUS_MISMATCH"
