"""Synthetic source-proof tests; no historic manifest or market corpus is recovered."""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.corporate_action_corpus import CorporateActionCorpusPaths
from quanthus.corporate_action_normalization import load_corporate_action_normalization_policy
from quanthus.corporate_action_revision import (
    CorporateActionRevisionError,
    build_corporate_action_revision,
    check_corporate_action_revision,
)
from quanthus.pipelines.securities_warehouse import dwd_correction as correction

ROOT = Path(__file__).resolve().parents[2]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=3) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def revision(tmp_path: Path) -> Path:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    datasets = []
    for name, rows in {
        "daily_ohlcv_none": [{"stock_code": "000001", "date": date(2020, 5, 27), "close": 13.0}],
        "daily_ohlcv_qfq": [{"stock_code": "000001", "date": date(2020, 5, 27), "close": 13.0}],
        "adjustment_factors": [
            {
                "stock_code": "000001",
                "effective_date": date(2020, 5, 28),
                "fore_adjust_factor": 1.0,
                "knowledge_cutoff": date(2020, 5, 22),
            }
        ],
    }.items():
        name += ".parquet"
        pq.write_table(pa.Table.from_pylist(rows), snapshot / name)
        datasets.append(
            {
                "dataset": name.removesuffix(".parquet"),
                "partitions": [name],
                "content_checksums": {name: correction._sha256(snapshot / name)},
            }
        )
    write_json(
        snapshot / "snapshot.json",
        {
            "snapshot": {"snapshot_id": "synthetic-closure-only"},
            "datasets": datasets,
        },
    )
    authority = tmp_path / "authority"
    raw = authority / "em_raw"
    raw.mkdir(parents=True)
    write_json(authority / "META.json", {"revision": "synthetic-authority-v1"})
    write_json(
        raw / "000001.json",
        {
            "code": "000001",
            "rows": [
                {
                    "SECURITY_CODE": "000001",
                    "EX_DIVIDEND_DATE": "2020-05-28 00:00:00",
                    "NOTICE_DATE": "2020-05-22 00:00:00",
                    "PRETAX_BONUS_RMB": 2.18,
                    "BONUS_RATIO": None,
                    "IT_RATIO": None,
                    "ASSIGN_PROGRESS": "实施分配",
                    "IMPL_PLAN_PROFILE": "10派2.18元(含税)",
                }
            ],
            "error": None,
            "fetched_at": "2026-07-29T00:00:00+00:00",
        },
    )
    actions = tmp_path / "actions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "action_id": "synthetic-valid",
                    "stock_code": "000001",
                    "ex_date": date(2020, 5, 28),
                    "announce_date": date(2020, 5, 22),
                    "cash_per_share": 0.218,
                    "bonus_shares": None,
                    "transfer_shares": None,
                    "progress": "实施",
                    "source": "synthetic",
                    "source_row_key": "synthetic:valid",
                },
                {
                    "action_id": "synthetic-exception",
                    "stock_code": "000002",
                    "ex_date": None,
                    "announce_date": date(2020, 5, 22),
                    "cash_per_share": None,
                    "bonus_shares": 1.0,
                    "transfer_shares": None,
                    "progress": "实施",
                    "source": "synthetic",
                    "source_row_key": "synthetic:exception",
                },
            ]
        ),
        actions,
    )
    result = build_corporate_action_revision(
        paths=CorporateActionCorpusPaths(snapshot, actions, raw),
        policy=load_corporate_action_normalization_policy(
            ROOT / "config/p1-corporate-action-normalization.v3.json"
        ),
        scope_start=date(2020, 1, 1),
        scope_end=date(2020, 12, 31),
        output=tmp_path / correction.NORMALIZED_REVISION,
        revision=correction.NORMALIZED_REVISION,
        knowledge_cutoff=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )
    # Deliberately noncanonical whitespace demonstrates exact-byte retention.
    write_json(result.output / "manifest.json", read_json(result.output / "manifest.json"))
    return result.output


@pytest.fixture
def build(tmp_path: Path, revision: Path, monkeypatch: pytest.MonkeyPatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    parent_identity = {
        "package_id": correction.PARENT_PACKAGE_ID,
        "manifest_sha256": correction.PARENT_MANIFEST_SHA256,
        "content_digest": correction.PARENT_CONTENT_DIGEST,
    }
    monkeypatch.setattr(correction, "_parent_identity", lambda root: parent_identity)

    def payload(**kwargs):
        target = kwargs["staging"] / "payload/replay"
        target.mkdir(parents=True)
        counts = {}
        for name in correction._REVISION_MEMBERS[:2]:
            shutil.copyfile(kwargs["revision"] / name, target / name)
            counts[name.removesuffix(".parquet")] = pq.ParquetFile(target / name).metadata.num_rows
        return counts, {"synthetic_only": True}

    monkeypatch.setattr(correction, "_build_payload", payload)
    monkeypatch.setattr(correction, "_verify_overlay_semantics", lambda **kwargs: None)

    def run():
        output = tmp_path / f"{correction.PARENT_PACKAGE_ID}-correction-synthetic"
        return correction.build_dwd_correction(
            parent=parent,
            normalization_revision=revision,
            output=output,
            correction_id=output.name,
            built_at="2026-10-03T00:00:00Z",
            code_revision="synthetic",
        )

    run.parent = parent
    run.output = tmp_path / f"{correction.PARENT_PACKAGE_ID}-correction-synthetic"
    return run


def rebind_revision(root: Path) -> None:
    """Update transport checksums only; do not repair semantic lineage under test."""
    manifest = read_json(root / "manifest.json")
    manifest["checksums"] = {
        name: correction._sha256(root / name) for name in correction._REVISION_MEMBERS
    }
    write_json(root / "manifest.json", manifest)
    (root / "CHECKSUMS.sha256").write_text(
        "".join(
            f"{digest.removeprefix('sha256:')}  {name}\n"
            for name, digest in sorted(manifest["checksums"].items())
        ),
        encoding="utf-8",
    )


def reseal(root: Path) -> None:
    manifest = read_json(root / "manifest.json")
    manifest["files"] = correction._file_inventory(root)
    identity = {key: manifest[key] for key in ("parent", "normalization_revision", "files")}
    if manifest["schema_version"] == 2:
        identity.update(schema_version=2, source_closure=manifest["source_closure"])
    manifest["content_digest"] = correction._canonical_digest(identity)
    write_json(root / "manifest.json", manifest)
    (root / "READY").write_text(correction._sha256(root / "manifest.json") + "\n")


def test_exact_revision_bytes_retained_and_original_can_be_removed(revision, build):
    expected = {path.name: path.read_bytes() for path in revision.iterdir()}
    result = build()
    for name, original in expected.items():
        assert (result.root / "payload/replay" / name).read_bytes() == original
    manifest = read_json(result.root / "manifest.json")
    assert manifest["schema_version"] == 2
    assert "payload/replay/manifest.json" in {item["path"] for item in manifest["files"]}
    assert result.source_closure_status == "verified_normalization_revision_closure"
    assert manifest["source_closure"]["upstream_raw_sources_retained"] is False
    shutil.rmtree(revision)
    verified = correction.verify_dwd_correction(result.root, parent=build.parent)
    assert verified.source_closure_status == result.source_closure_status
    assert verified.row_counts == {
        "corporate_actions_normalized": 1,
        "corporate_action_exceptions": 1,
    }


@pytest.mark.parametrize(
    "name", [*correction._REVISION_MEMBERS, "manifest.json", "CHECKSUMS.sha256"]
)
def test_missing_or_corrupt_retained_member_fails_even_without_deep(build, name):
    result = build()
    member = result.root / "payload/replay" / name
    member.write_bytes(b"!" + member.read_bytes()[1:])
    with pytest.raises(correction.DwdCorrectionError):
        correction.verify_dwd_correction(result.root, parent=build.parent)
    member.unlink()
    with pytest.raises(correction.DwdCorrectionError):
        correction.verify_dwd_correction(result.root, parent=build.parent)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "wrong"),
        ("schema_version", 2),
        ("schema_version", True),
        ("revision", "other"),
        ("ready_for_snapshot", False),
        ("execution_authorized", True),
        ("execution_authorized", None),
        ("policy", []),
        ("source", {}),
        ("scope", {}),
        ("scope", {"start": "2021-01-01", "end": "2020-01-01"}),
        ("scope", {"start": "2012-01-01", "end": "2020-12-31"}),
        ("checksums", {}),
        ("snapshot_source_revisions", {}),
    ],
)
def test_invalid_source_identity_fails_before_publication(revision, build, field, value):
    manifest = read_json(revision / "manifest.json")
    manifest[field] = value
    write_json(revision / "manifest.json", manifest)
    with pytest.raises(correction.DwdCorrectionError):
        build()
    assert not build.output.exists()


@pytest.mark.parametrize(
    "filename,field,value",
    [
        ("audit_report.json", "scope_start", "2012-01-01"),
        ("audit_report.json", "execution_authorized", True),
        ("exception_changes.json", "execution_authorized", True),
        ("exception_changes.json", "current_exception_ledger_digest", "sha256:" + "0" * 64),
    ],
)
def test_resealed_transport_cannot_hide_report_lineage_change(
    revision, build, filename, field, value
):
    data = read_json(revision / filename)
    data[field] = value
    write_json(revision / filename, data)
    rebind_revision(revision)
    with pytest.raises(correction.DwdCorrectionError):
        build()
    assert not build.output.exists()


@pytest.mark.parametrize("filename", correction._REVISION_MEMBERS[:2])
@pytest.mark.parametrize("value", [True, None])
def test_member_execution_authority_is_always_false(revision, build, filename, value):
    table = pq.read_table(revision / filename)
    index = table.schema.get_field_index("execution_authorized")
    table = table.set_column(index, "execution_authorized", pa.array([value], type=pa.bool_()))
    pq.write_table(table, revision / filename)
    rebind_revision(revision)
    with pytest.raises(correction.DwdCorrectionError, match="policy or authority"):
        build()
    assert not build.output.exists()


def test_duplicate_checksum_line_rejected(revision, build):
    path = revision / "CHECKSUMS.sha256"
    path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
    with pytest.raises(correction.DwdCorrectionError, match="duplicate"):
        build()


def test_duplicate_json_and_extra_physical_source_member_rejected(revision, build):
    path = revision / "manifest.json"
    original = path.read_text()
    path.write_text(original.replace("{", '{"revision":"other",', 1))
    with pytest.raises(correction.DwdCorrectionError, match="duplicate"):
        build()
    path.write_text(original)
    (revision / "unbound.json").write_text("{}")
    with pytest.raises(correction.DwdCorrectionError, match="inventory"):
        build()


def test_source_symlink_rejected(revision, build, tmp_path):
    source = revision / "audit_report.json"
    target = tmp_path / "external-report.json"
    source.rename(target)
    source.symlink_to(target)
    with pytest.raises(correction.DwdCorrectionError, match="symlink"):
        build()


@pytest.mark.parametrize("name", ["manifest.json", "CHECKSUMS.sha256"])
def test_copy_time_source_metadata_drift_fails_before_publication(
    revision, build, monkeypatch, name
):
    original = correction._build_payload

    def drift(**kwargs):
        result = original(**kwargs)
        with (revision / name).open("a") as stream:
            stream.write(" ")
        return result

    monkeypatch.setattr(correction, "_build_payload", drift)
    with pytest.raises(correction.DwdCorrectionError, match="bytes changed during copy"):
        build()
    assert not build.output.exists()
    assert not list(build.output.parent.glob(f".{build.output.name}.*"))


def test_overlay_validation_happens_before_publication(build, monkeypatch):
    def fail(**kwargs):
        assert not build.output.exists()
        assert kwargs["correction"] != build.output
        raise correction.DwdCorrectionError("synthetic overlay failure")

    monkeypatch.setattr(correction, "_verify_overlay_semantics", fail)
    with pytest.raises(correction.DwdCorrectionError, match="synthetic overlay failure"):
        build()
    assert not build.output.exists()
    assert not list(build.output.parent.glob(f".{build.output.name}.*"))


def test_resealed_manifest_cannot_replace_bound_original_source_bytes(build):
    result = build()
    source = result.root / "payload/replay/manifest.json"
    source.write_text(source.read_text() + " ")
    reseal(result.root)
    with pytest.raises(correction.DwdCorrectionError, match="manifest identity"):
        correction.verify_dwd_correction(result.root, parent=build.parent)


def test_v1_compatibility_never_claims_source_manifest_recovered(build):
    result = build()
    for name in correction._REVISION_METADATA:
        (result.root / "payload/replay" / name).unlink()
    manifest = read_json(result.root / "manifest.json")
    manifest["schema_version"] = 1
    del manifest["source_closure"]
    del manifest["execution_authorized"]
    write_json(result.root / "manifest.json", manifest)
    reseal(result.root)
    before = (result.root / "manifest.json").read_bytes()
    legacy = correction.verify_dwd_correction(result.root, parent=build.parent, deep=True)
    assert legacy.source_closure_status == "legacy_manifest_unretained"
    assert legacy.to_dict()["source_closure_status"] == "legacy_manifest_unretained"
    assert (result.root / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("version", [1, 3, True, None])
def test_unknown_or_downgraded_schema_cannot_claim_retained_closure(build, version):
    result = build()
    manifest = read_json(result.root / "manifest.json")
    manifest["schema_version"] = version
    write_json(result.root / "manifest.json", manifest)
    reseal(result.root)
    with pytest.raises(correction.DwdCorrectionError):
        correction.verify_dwd_correction(result.root, parent=build.parent)


def test_existing_checker_directory_binding_preserved_with_explicit_relocation(revision, tmp_path):
    relocated = tmp_path / "replay"
    shutil.copytree(revision, relocated)
    with pytest.raises(CorporateActionRevisionError, match="directory"):
        check_corporate_action_revision(relocated)
    assert (
        check_corporate_action_revision(
            relocated, expected_revision=correction.NORMALIZED_REVISION
        ).revision
        == correction.NORMALIZED_REVISION
    )
