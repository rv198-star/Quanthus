"""Metadata compatibility does not imply P6 paper-entry authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.forward_validation.native_base_probe import (
    _RELATIVE_SUBTREES,
    _TABLE_COLUMNS,
    NativeBaseProbeError,
    inspect_native_base,
)


def _candidate(
    root: Path,
    *,
    version: str = "2.2",
    kind: str = "dwd_base22_native_purpose_qualified_candidate_v2",
    admitted: str = "NOT_GRANTED",
    missing_table: str | None = None,
    missing_column: str | None = None,
) -> str:
    root.mkdir()
    (root / "contract").mkdir()
    manifest = {
        "kind": kind,
        "version": version,
        "business_through": "2026-08-31",
        "purpose": "p10_governance_baseline_with_disclosed_coverage",
        "consumer_admission": admitted,
    }
    (root / "contract/base22-delivery.v2.json").write_text(
        json.dumps(
            {
                "purpose": manifest["purpose"],
                "business_through": manifest["business_through"],
                "restricted_uses": ["trading_entry", "historical_pit"],
            }
        ),
        encoding="utf-8",
    )
    for table, columns in _TABLE_COLUMNS.items():
        if table == missing_table:
            continue
        path = root / "payload" / table / _RELATIVE_SUBTREES[table] / "part-000.parquet"
        path.parent.mkdir(parents=True)
        cols = {column: pa.array(["fixture"]) for column in columns if column != missing_column}
        if not cols:
            cols = {"synthetic": pa.array(["fixture"])}
        pq.write_table(pa.table(cols), path)
    body = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
    (root / "manifest.json").write_bytes(body)
    digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
    (root / "READY").write_text(digest + "\n", encoding="ascii")
    return digest


def test_compatible_native_table_surface_is_still_not_p6_admission(tmp_path: Path) -> None:
    root = tmp_path / "image"
    sha = _candidate(root)
    result = inspect_native_base(root, expected_manifest_sha256=sha)
    assert result.structural_compatible is True
    assert result.consumer_authorized is False
    assert result.consumer_admission == "NOT_GRANTED"
    assert "consumer_admission_not_granted" in result.deficiencies
    assert "historical_pit_scope_restricted" in result.deficiencies
    assert len(result.verified_metadata_tables) == len(_TABLE_COLUMNS)
    assert result.claim_ceiling == "METADATA_ONLY_NO_PIT_NO_P6_ADMISSION_NO_OFFICIAL_CYCLE"


def test_even_manifest_claiming_grant_cannot_activate_p6(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    sha = _candidate(root, admitted="GRANTED_P6_TRADING_ENTRY")
    result = inspect_native_base(root, expected_manifest_sha256=sha)
    assert result.structural_compatible is True
    assert result.consumer_authorized is False
    assert "trading_entry_scope_restricted" in result.deficiencies


def test_rejects_tampered_manifest_or_ready_identity(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    sha = _candidate(root)
    with pytest.raises(NativeBaseProbeError, match="base_manifest_digest_mismatch"):
        inspect_native_base(root, expected_manifest_sha256="sha256:" + "0" * 64)
    (root / "READY").write_text("sha256:" + "0" * 64 + "\n")
    with pytest.raises(NativeBaseProbeError, match="base_ready_manifest_mismatch"):
        inspect_native_base(root, expected_manifest_sha256=sha)


@pytest.mark.parametrize("missing", ["dwd_security_bar", "dwd_trading_calendar", "dwd_listing"])
def test_required_p6_table_presence_is_explicit(tmp_path: Path, missing: str) -> None:
    root = tmp_path / "candidate"
    sha = _candidate(root, missing_table=missing)
    report = inspect_native_base(root, expected_manifest_sha256=sha)
    assert report.structural_compatible is False
    assert f"required_table_scope_missing:{missing}" in report.deficiencies


def test_failing_required_columns_fails_structural_probe(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    sha = _candidate(root, missing_column="knowledge_from")
    report = inspect_native_base(root, expected_manifest_sha256=sha)
    assert report.structural_compatible is False
    assert any("parquet_required_columns_or_rows_missing" in s for s in report.deficiencies)


def test_unknown_future_base23_contract_cannot_inherit_base22_admission(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    sha = _candidate(root, version="2.3")
    result = inspect_native_base(root, expected_manifest_sha256=sha)
    assert result.structural_compatible is False
    assert "unqualified_or_unknown_native_base_version" in result.deficiencies
    assert result.consumer_authorized is False


def test_does_not_read_market_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "candidate"
    sha = _candidate(root)
    monkeypatch.setattr(pq, "read_table", lambda *_args, **_kw: pytest.fail("row read forbidden"))
    monkeypatch.setattr(pq, "read_pandas", lambda *_args, **_kw: pytest.fail("row read forbidden"))
    assert inspect_native_base(root, expected_manifest_sha256=sha).structural_compatible


def test_cli_does_not_return_success_for_metadata_only_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    from quanthus.forward_validation.native_base_probe import main

    root = tmp_path / "candidate"
    digest = _candidate(root)
    monkeypatch.setattr(
        sys, "argv", ["native-base-probe", "--root", str(root), "--manifest-sha256", digest]
    )
    assert main() == 3
    obj = json.loads(capsys.readouterr().out)
    assert obj["structural_compatible"] is True
    assert obj["consumer_authorized"] is False
