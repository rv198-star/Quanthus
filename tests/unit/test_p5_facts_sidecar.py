"""Exact, bounded accepted-source identity projection; no new market data."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from quanthus.stage_learning.p5_facts_sidecar import project_members, run_sidecar
from quanthus.stage_learning.p5_input_census import CensusError, file_digest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/research/p5-stage-001/facts-sidecar.v1.json"


def rows(count: int = 121) -> list[dict]:
    return [
        {
            "security_code": "000001",
            "market_date": (date(2019, 10, 8) + timedelta(days=i)).isoformat(),
            "session_index": i,
            "cutoff": "2020-03-31T07:00:00+00:00",
            "security_id": "sec1",
            "listing_id": "listing1",
            "exchange": "SZSE",
            "source_record_id": f"row{i}",
            "source_revision": "v1",
            "source_artifact_sha256": "sha256:" + "a" * 64,
            "dwd_known_from": "2026-08-03",
            "coordinate_epoch": "epoch1",
            "finality": "final",
            "quality_reason": None,
            "reset_reason": None,
            "calendar_member_digest": "sha256:" + "b" * 64,
            "action_member_digest": "sha256:" + "c" * 64,
            "high": "3",
            "low": "1",
            "close": "2",
            "volume": "10",
        }
        for i in range(count)
    ]


def encoded(items: list[dict]) -> bytes:
    return ("\n".join(json.dumps(row) for row in items) + "\n").encode()


def test_source_windows_do_not_become_active_or_feature_qualification() -> None:
    projected, summary = project_members(encoded(rows()), json.loads(CONFIG.read_text()))
    assert summary["contiguous_120_member_identity_windows"] == 2
    assert summary["active_population_count"] is None
    assert summary["qualified_P5_feature_windows"] is None
    assert all("close" not in row and row["ACTIVE"] is None for row in projected)


@pytest.mark.parametrize("change", ["gap", "epoch", "reset", "quality"])
def test_identity_windows_do_not_cross_discontinuities(change: str) -> None:
    items = rows()
    if change == "gap":
        for row in items[60:]:
            row["session_index"] += 1
    elif change == "epoch":
        for row in items[60:]:
            row["coordinate_epoch"] = "epoch2"
    elif change == "reset":
        items[60]["reset_reason"] = "reset"
    else:
        items[60]["quality_reason"] = "missing"
    _, summary = project_members(encoded(items), json.loads(CONFIG.read_text()))
    assert summary["contiguous_120_member_identity_windows"] == 0


@pytest.mark.parametrize("change", ["protected", "duplicate", "security"])
def test_scope_and_identity_are_fail_closed(change: str) -> None:
    items = rows(2)
    if change == "protected":
        items[0]["market_date"] = "2025-01-01"
    elif change == "duplicate":
        items[1] = dict(items[0])
    else:
        items[0]["security_code"] = "600000"
    with pytest.raises(CensusError):
        project_members(encoded(items), json.loads(CONFIG.read_text()))


def test_exact_two_copy_member_verification_and_no_other_f9_reads(tmp_path: Path) -> None:
    request = json.loads(CONFIG.read_text())
    for source in request["sources"]:
        run = tmp_path / source["run_id"]
        run.mkdir()
        member = run / "source-inputs.jsonl"
        member.write_bytes(encoded(rows(2)))
        request["member_sha256"] = file_digest(member)
        manifest = {
            "schema_version": "quanthus.structure-f9-artifact.v1",
            "research_only": True,
            "execution_authorized": False,
            "code_revision": request["source_code_revision"],
            "request_digest": request["source_request_digest"],
            "files": {"source-inputs.jsonl": request["member_sha256"]},
        }
        (run / "manifest.json").write_text(json.dumps(manifest))
        source["manifest_sha256"] = file_digest(run / "manifest.json")
        (run / "READY").write_text(
            json.dumps(
                {
                    "schema_version": "quanthus.structure-f9-ready.v1",
                    "manifest_sha256": source["manifest_sha256"],
                }
            )
        )
    config = tmp_path / "request.json"
    config.write_text(json.dumps(request))
    result = run_sidecar(tmp_path, config, tmp_path / "output", "a" * 40)
    assert result["source_rows"] == 2
    second = tmp_path / request["sources"][1]["run_id"] / "source-inputs.jsonl"
    second.write_text("changed")
    with pytest.raises(CensusError, match="member differs"):
        run_sidecar(tmp_path, config, tmp_path / "failed", "a" * 40)
    assert not (tmp_path / "failed").exists()
