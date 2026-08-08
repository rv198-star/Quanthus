import json
import sys
from pathlib import Path

import pytest

from quanthus.pipelines.dwd_replay_determinism_audit import audit_dwd_replay_determinism
from quanthus.pipelines.dwd_v21_rebuild_pilot import (
    DwdV21PilotError,
    _execution_python,
    _replay_receipt,
    _run_isolated_worker,
    _scan_parquet,
    select_representative_workloads,
)

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
ROOT = Path(__file__).resolve().parents[2]


def _write_ods_manifest(root: Path, files: list[dict]) -> None:
    (root / "manifest.json").write_text(
        json.dumps({"content": {"files": files}}), encoding="ascii"
    )


def _parquet(path: Path, rows: int) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "stock_code": [f"{index:06d}" for index in range(rows)],
            "value": [float(index) for index in range(rows)],
        }
    )
    pq.write_table(table, path)
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "format": "parquet",
        "path": str(path),
        "row_count": rows,
        "sha256": "sha256:" + digest,
        "size_bytes": path.stat().st_size,
    }


def test_selects_largest_bound_partition_for_each_heavy_builder(tmp_path: Path) -> None:
    ods = tmp_path / "ods"
    ods.mkdir()
    market_small = _parquet(ods / "payload/minute/prod_5m/000001_5m.parquet", 2)
    market_large = _parquet(ods / "payload/minute/prod_5m/000002_5m.parquet", 20)
    financial = _parquet(
        ods
        / "payload/source-evidence/weekly-full-fundamentals/2026-07-31/run"
        / "artifacts/financial_statements.parquet",
        4,
    )
    for value in (market_small, market_large):
        value["dataset_id"] = "minute_5m_formal_archive"
        value["path"] = str(Path(value["path"]).relative_to(ods))
    financial["dataset_id"] = "source_evidence_weekly_full_fundamentals_20260731"
    financial["path"] = str(Path(financial["path"]).relative_to(ods))
    _write_ods_manifest(ods, [market_small, financial, market_large])

    selected = select_representative_workloads(ods)

    assert [item["builder"] for item in selected] == [
        "financial_reporting",
        "security_market_observation",
    ]
    assert selected[1]["path"].endswith("000002_5m.parquet")


def test_scan_decodes_batches_and_rejects_digest_drift(tmp_path: Path) -> None:
    entry = _parquet(tmp_path / "source.parquet", 5)
    result = _scan_parquet(
        tmp_path / "source.parquet", expected_sha256=entry["sha256"], batch_size_rows=2
    )
    assert result["row_count"] == 5
    assert result["batch_count"] == 3
    assert result["column_null_counts"] == {"stock_code": 0, "value": 0}
    with pytest.raises(DwdV21PilotError, match="digest mismatch"):
        _scan_parquet(
            tmp_path / "source.parquet",
            expected_sha256="sha256:" + "0" * 64,
            batch_size_rows=2,
        )


def test_isolated_worker_applies_bounded_read_only_scan(tmp_path: Path) -> None:
    ods = tmp_path / "ods"
    entry = _parquet(ods / "payload/minute/prod_5m/000001_5m.parquet", 7)
    entry.update(
        {
            "builder": "security_market_observation",
            "dataset_id": "minute_5m_formal_archive",
            "path": "payload/minute/prod_5m/000001_5m.parquet",
            "declared_row_count": 7,
            "declared_schema_sha256": None,
        }
    )
    result = _run_isolated_worker(
        python=Path(sys.executable),
        ods_base=ods,
        workloads=[entry],
        spill_root=tmp_path / "spill/replay-a",
        memory_limit_bytes=12 * 1024**3,
        spill_limit_bytes=1024**3,
        thread_limit=2,
        batch_size_rows=3,
    )
    assert result["workloads"][0]["scan"]["row_count"] == 7
    assert result["peak_rss_bytes"] < 12 * 1024**3
    assert result["spill_bytes"] == 0


def test_execution_python_preserves_virtualenv_symlink(tmp_path: Path) -> None:
    system_python = tmp_path / "system-python"
    system_python.write_bytes(b"binary")
    venv_python = tmp_path / "venv/bin/python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(system_python)

    selected = _execution_python(venv_python)

    assert selected == venv_python.absolute()
    assert selected != venv_python.resolve()


def test_replay_receipts_bind_plan_and_verify_determinism(tmp_path: Path) -> None:
    base = tmp_path / "base"
    plan = tmp_path / "plan"
    base.mkdir()
    plan.mkdir()
    base_manifest = {
        "package_id": "dwd-base-202607-v2",
        "content_digest": "sha256:" + "1" * 64,
        "through_date": "2026-07-31",
    }
    (base / "manifest.json").write_text(json.dumps(base_manifest), encoding="ascii")
    input_identity = {"run_id": "run", "plan_digest": "sha256:" + "2" * 64}
    (plan / "input-plan.json").write_text(
        json.dumps({**input_identity, "kind": "dwd_dws_input_plan"}), encoding="ascii"
    )
    import hashlib

    base_release = {
        **base_manifest,
        "manifest_sha256": "sha256:"
        + hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest(),
    }
    deterministic = [{"builder": "financial_reporting", "scan": {"row_count": 5}}]
    paths = [tmp_path / "replay-a.json", tmp_path / "replay-b.json"]
    for index, path in enumerate(paths):
        receipt = _replay_receipt(
            base_release=base_release,
            input_plan=input_identity,
            deterministic_results=deterministic,
            started_at=f"2026-08-08T00:00:0{index}+00:00",
            completed_at=f"2026-08-08T00:00:1{index}+00:00",
        )
        path.write_text(json.dumps(receipt), encoding="ascii")

    report = audit_dwd_replay_determinism(
        base_root=base, plan_root=plan, replay_a=paths[0], replay_b=paths[1]
    )

    assert report["summary"]["status"] == "verified"
    assert report["summary"]["independent_execution_count"] == 2


def test_replay_audit_rejects_resealed_wrong_input_plan(tmp_path: Path) -> None:
    base = tmp_path / "base"
    plan = tmp_path / "plan"
    base.mkdir()
    plan.mkdir()
    (base / "manifest.json").write_text(
        json.dumps(
            {
                "package_id": "dwd-base-202607-v2",
                "content_digest": "sha256:" + "1" * 64,
                "through_date": "2026-07-31",
            }
        ),
        encoding="ascii",
    )
    (plan / "input-plan.json").write_text(
        json.dumps({"run_id": "run", "plan_digest": "sha256:" + "2" * 64}),
        encoding="ascii",
    )
    import hashlib

    manifest = json.loads((base / "manifest.json").read_text(encoding="ascii"))
    base_release = {
        **manifest,
        "manifest_sha256": "sha256:"
        + hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest(),
    }
    wrong_plan = {"run_id": "other", "plan_digest": "sha256:" + "3" * 64}
    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    for path in paths:
        path.write_text(
            json.dumps(
                _replay_receipt(
                    base_release=base_release,
                    input_plan=wrong_plan,
                    deterministic_results=[],
                    started_at="a",
                    completed_at="b",
                )
            ),
            encoding="ascii",
        )
    report = audit_dwd_replay_determinism(
        base_root=base, plan_root=plan, replay_a=paths[0], replay_b=paths[1]
    )
    assert report["summary"]["status"] == "finding"
    assert "replay_evidence_input_plan_mismatch" in report["summary"]["reason_codes"]


def test_systemd_pilot_is_persistent_and_resource_bounded() -> None:
    service = (ROOT / "ops/systemd/quanthus-dwd-v2-1-pilot.service").read_text(
        encoding="ascii"
    )
    assert "ConditionPathIsMountPoint=/srv/quanthus/gpu-p100-dwd-workspace" in service
    assert "pilot-dwd-v2-1-rebuild" in service
    assert "PrivateNetwork=true" in service
    assert "Environment=GIT_CONFIG_KEY_0=safe.directory" in service
    assert "ReadWritePaths=/srv/quanthus/gpu-p100-dwd-workspace" in service
    assert "MemoryMax=14G" in service
    assert "CPUQuota=400%" in service
    assert "--memory-limit-bytes 12884901888" in service
    assert "--thread-limit 4" in service
    assert "run-dwd-v2-1-rebuild" not in service
