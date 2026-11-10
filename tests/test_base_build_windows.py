from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base_build_inputs import canonical, file_sha, identity
from quanthus.pipelines.securities_warehouse.base_build_period import LEDGER_CONTRACT
from quanthus.pipelines.securities_warehouse.base_build_windows import (
    ACQUISITION,
    PLAN_PATH,
    audit_windows,
    build_windows,
    held_coordinates,
    read_predecessor,
    validate_rows,
)
from tests.unit.test_base24_ods_image import _make_acquisition


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def fixture(tmp_path, *, held=False):
    source = tmp_path / "source"
    source.mkdir()
    acquisition, plan_path = _make_acquisition(source)
    if held:
        from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import (
            _seal_hold,
        )

        plan = json.loads(plan_path.read_text())
        window = plan["windows"][0]
        wid = window["partition_id"]
        shutil.rmtree(acquisition / "partitions" / wid)
        failures = acquisition / "failures"
        path = failures / (wid + "-synthetic-attempt") / "FAILURE.json"
        save(
            path,
            {
                "contract": "p10-base24-native5m-acquisition-v1",
                "partition_id": wid,
                "reason_code": "SYNTHETIC_NO_SOURCE",
            },
        )
        _seal_hold(
            acquisition / "held" / wid,
            failures=failures,
            window=window,
            plan_sha=file_sha(plan_path)[7:],
            attempts=[path],
        )
        checkpoint = json.loads((acquisition / "CHECKPOINT.json").read_text())
        checkpoint.update(completed=0, held=1, status="COMPLETE_WITH_HELD")
        save(acquisition / "CHECKPOINT.json", checkpoint)
        (acquisition / "READY").write_text(file_sha(acquisition / "CHECKPOINT.json")[7:] + "\n")
    predecessor = tmp_path / "predecessor"
    (predecessor / Path(ACQUISITION).parent).mkdir(parents=True)
    shutil.copytree(acquisition, predecessor / ACQUISITION)
    p = predecessor / PLAN_PATH
    p.parent.mkdir()
    shutil.copyfile(plan_path, p)
    files = [
        {
            "path": p.relative_to(predecessor).as_posix(),
            "size_bytes": p.stat().st_size,
            "sha256": file_sha(p),
        }
        for p in sorted(predecessor.rglob("*"))
        if p.is_file()
    ]
    save(
        predecessor / "manifest.json",
        {
            "kind": "quanthus_ods_base24_bundle",
            "content": {
                "through_date": "2026-09-30",
                "native5m": {
                    "completed_partitions": 0 if held else 1,
                    "held_partitions": 1 if held else 0,
                },
                "files": files,
            },
        },
    )
    (predecessor / "READY").write_text(file_sha(predecessor / "manifest.json") + "\n")
    period = tmp_path / "period"
    period.mkdir()
    scope = {
        "contract": LEDGER_CONTRACT,
        "venue": "SSE",
        "stock_code": "600000",
        "market_date": "2026-10-08",
        "intervals": ["1d", "5m"],
    }
    row = {
        "window_id": identity(scope),
        "venue": "SSE",
        "stock_code": "600000",
        "market_date": "2026-10-08",
        "status": "completed",
        "disposition": "R0",
        "parent_daily_manifest_sha256": "sha256:" + "c" * 64,
    }
    pq.write_table(pa.Table.from_pylist([row]), period / "security-days.parquet")
    binding = {"predecessor_through": "2026-09-30", "through_date": "2026-10-31"}
    admission = {
        "binding": binding,
        "ledger_sha256": file_sha(period / "security-days.parquet"),
        "new_window_contract": LEDGER_CONTRACT,
        "new_windows": 1,
    }
    admission["identity"] = identity(admission)
    save(period / "ADMISSION.json", admission)
    return predecessor, period, binding


def execute(tmp_path, f):
    predecessor, period, binding = f
    with Budget(tmp_path / "job", [predecessor, period], Limits(free_floor_bytes=1)) as budget:
        return build_windows(
            predecessor=predecessor,
            period=period,
            binding=binding,
            output=budget.root / "windows",
            budget=budget,
        )


def test_window_count_uses_final_partitions_and_distinct_daily_contract(tmp_path):
    result = execute(tmp_path, fixture(tmp_path))
    assert result["counts"] == {
        "plan_total": 2,
        "completed": 2,
        "held": 0,
        "failed": 0,
        "pending": 0,
    }
    assert result["new_quality_held"] == 0
    assert result["formal_READY"] is False
    assert result["historical_price_replay"] == "SEPARATE_AUDIT_REQUIRED"


def test_exact_held_coordinate_survives_with_failure_attempts_separate(tmp_path):
    f = fixture(tmp_path, held=True)
    result = execute(tmp_path, f)
    assert result["counts"] == {
        "plan_total": 2,
        "completed": 1,
        "held": 1,
        "failed": 0,
        "pending": 0,
    }
    root = tmp_path / "job/windows"
    rows = pq.ParquetFile(root / "WINDOWS.parquet").read().to_pylist()
    plan = json.loads((root / "PLAN.json").read_text())
    assert held_coordinates(rows) == plan["inherited_held"]
    assert not (root / "READY").exists()
    # Next version reads the same top-level standard window representation.
    successor = tmp_path / "successor"
    shutil.copytree(root, successor / "evidence/windows")
    save(successor / "manifest.json", {"kind": "quanthus_ods_base_bundle_v1"})
    (successor / "READY").write_text(file_sha(successor / "manifest.json"))
    with Budget(tmp_path / "read-job", [successor], Limits(free_floor_bytes=1)) as budget:
        recovered, _ = read_predecessor(successor, budget)
    assert recovered == rows


@pytest.mark.parametrize(
    "damage", ["missing-partition", "both-terminal", "plan-hash", "extra-terminal"]
)
def test_legacy_terminal_mismatch_cannot_be_hidden_by_declared_zero_failure(tmp_path, damage):
    f = fixture(tmp_path)
    predecessor = f[0]
    root = predecessor / ACQUISITION
    partition = next((root / "partitions").iterdir())
    if damage == "missing-partition":
        shutil.rmtree(partition)
    elif damage == "both-terminal":
        (root / "held" / partition.name).mkdir()
    elif damage == "extra-terminal":
        (root / "partitions" / ("f" * 64)).mkdir()
    else:
        checkpoint = json.loads((root / "CHECKPOINT.json").read_text())
        checkpoint["plan_sha256"] = "f" * 64
        save(root / "CHECKPOINT.json", checkpoint)
    with pytest.raises(BoundedError, match="BUILD_WINDOWS_"):
        execute(tmp_path, f)


def test_extra_or_changed_held_coordinate_is_not_an_accepted_limitation(tmp_path):
    execute(tmp_path, fixture(tmp_path, held=True))
    root = tmp_path / "job/windows"
    ledger = root / "WINDOWS.parquet"
    table = pq.ParquetFile(ledger).read()
    rows = table.to_pylist()
    rows[0]["scope_identity"] = "sha256:" + "f" * 64
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), ledger)
    plan = json.loads((root / "PLAN.json").read_text())
    plan["ledger_sha256"] = file_sha(ledger)
    plan["identity"] = identity({k: v for k, v in plan.items() if k != "identity"})
    save(root / "PLAN.json", plan)
    checkpoint = json.loads((root / "CHECKPOINT.json").read_text())
    checkpoint.update(plan_sha256=file_sha(root / "PLAN.json"), ledger_sha256=file_sha(ledger))
    save(root / "CHECKPOINT.json", checkpoint)
    with (
        Budget(tmp_path / "audit-job", [root], Limits(free_floor_bytes=1)) as budget,
        pytest.raises(BoundedError, match="FINAL_CLOSURE_OR_HELD_SET_CHANGED"),
    ):
        audit_windows(root, budget)


def test_different_ids_cannot_cover_the_same_contract_and_security_date():
    row = dict.fromkeys(
        (
            "window_id",
            "contract",
            "scope_identity",
            "status",
            "disposition",
            "evidence_identity",
            "venue",
            "stock_code",
            "first_date",
            "last_date",
        ),
        "x",
    )
    row.update(first_date=str(date(2026, 10, 8)), last_date="2026-10-08", status="completed")
    with pytest.raises(BoundedError, match="DIFFERENT_IDS_OVERLAP"):
        validate_rows([row, {**row, "window_id": "different-id"}])
