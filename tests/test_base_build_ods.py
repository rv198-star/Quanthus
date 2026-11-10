from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base24_ods_image import materialize_ods24, seal_ods24
from quanthus.pipelines.securities_warehouse.base_build_freeze import freeze
from quanthus.pipelines.securities_warehouse.base_build_inputs import canonical, file_sha, identity
from quanthus.pipelines.securities_warehouse.base_build_ods import audit_ods, materialize_ods
from quanthus.pipelines.securities_warehouse.base_build_period import (
    LEDGER_CONTRACT,
    VerifiedInputs,
)
from quanthus.pipelines.securities_warehouse.base_build_profile import delivery_contract
from quanthus.pipelines.securities_warehouse.base_build_windows import build_windows
from tests.unit.test_base24_ods_image import Budget as LegacyBudget
from tests.unit.test_base24_ods_image import _freeze
from tests.unit.test_base24_ods_image import source_closure as source_closure


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def seal_header(root, manifest):
    save(root / "manifest.json", manifest)
    (root / "READY").write_text(file_sha(root / "manifest.json") + "\n")


def source_inputs(tmp_path, source_closure, *, include_empty=False):
    """ODS closure test only; mock DWD/Daily headers do not grant paired A/B."""
    ods = tmp_path / "base-ods"
    plan = _freeze(source_closure)
    materialize_ods24(plan, ods, LegacyBudget())
    from quanthus.pipelines.securities_warehouse.base24_ods_image import audit_ods24

    receipt = audit_ods24(ods, LegacyBudget())
    seal_ods24(ods, receipt, code_revision="a" * 40, budget=LegacyBudget())
    dwd = tmp_path / "base-dwd"
    p = dwd / "payload/controlled-test.parquet"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"paired DWD owns this payload; ODS keeps only its control declarations")
    record = {
        "layout": {
            "synthetic_only": {
                "files": [
                    {
                        "path": p.relative_to(dwd).as_posix(),
                        "sha256": file_sha(p),
                        "size_bytes": p.stat().st_size,
                        "rows": 1,
                    }
                ]
            }
        },
        "evidence": {},
    }
    if include_empty:
        empty = dwd / "payload/empty-table/empty/original.parquet"
        empty.parent.mkdir(parents=True)
        pq.write_table(pa.table({"unchanged_schema": pa.array([], type=pa.int64())}), empty)
        record["layout"]["empty-table"] = {
            "files": [
                {
                    "path": empty.relative_to(dwd).as_posix(),
                    "rows": 0,
                    "sha256": file_sha(empty),
                    "size_bytes": empty.stat().st_size,
                }
            ]
        }
    save(dwd / "MATERIALIZATION.json", record)
    seal_header(
        dwd,
        {
            "kind": "dwd_base24_native_1d5m_purpose_qualified_candidate_v1",
            "business_through": "2026-09-30",
            "materialization_sha256": file_sha(dwd / "MATERIALIZATION.json"),
        },
    )
    daily = tmp_path / "daily"
    p = daily / "original/raw.json"
    save(
        p,
        {"literal_source": "unchanged-string", "actually_acquired_at": "2026-10-08T10:00:00+00:00"},
    )
    seal_header(
        daily,
        {
            "kind": "quanthus_daily_1d5m_v1",
            "content": {
                "role": "daily",
                "market_date": "2026-10-08",
                "files": [
                    {
                        "path": "original/raw.json",
                        "size_bytes": p.stat().st_size,
                        "sha256": file_sha(p),
                    }
                ],
            },
        },
    )
    roots = {"ods": str(ods), "dwd": str(dwd), "daily": str(daily)}
    selection = {
        "contract": "p10-base-build-input-selection-v2",
        "target_version": "2.5",
        "predecessor_version": "2.4",
        "predecessor_through": "2026-09-30",
        "through_date": "2026-10-31",
        "expected_days": ["2026-10-08"],
        "evidence": [],
        "packages": [
            {
                "id": sid,
                "role": sid if sid != "daily" else "daily",
                "day": day,
                "manifest_sha256": file_sha(Path(roots[sid]) / "manifest.json"),
            }
            for sid, day in (("ods", None), ("dwd", None), ("daily", "2026-10-08"))
        ],
        "expected_days_evidence": {
            "package_id": "daily",
            "path": "original/raw.json",
            "sha256": file_sha(daily / "original/raw.json"),
        },
    }
    frozen = freeze(selection, roots)
    c = delivery_contract(
        json.loads(Path("config/pipelines/base24-delivery.v1.json").read_text()),
        version="2.5",
        predecessor_version="2.4",
        through="2026-10-31",
    )
    binding = {
        "predecessor_through": "2026-09-30",
        "through_date": "2026-10-31",
        "frozen_input_identity": frozen["identity"],
        "target_version": "2.5",
        "predecessor_manifest_sha256": file_sha(dwd / "manifest.json"),
        "policy_identity": identity(c),
        "code_revision": "e" * 40,
    }
    period = tmp_path / "period"
    period.mkdir()
    source_configuration = {
        "source_evidence_id": "synthetic-unit-no-source-replay",
        "closed_days": [],
        "calendar_reference": {},
    }
    save(period / "CONFIG.json", source_configuration)
    scope = {
        "contract": LEDGER_CONTRACT,
        "venue": "SSE",
        "stock_code": "600000",
        "market_date": "2026-10-08",
        "intervals": ["1d", "5m"],
    }
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "window_id": identity(scope),
                    "venue": "SSE",
                    "stock_code": "600000",
                    "market_date": "2026-10-08",
                    "status": "completed",
                    "disposition": "R0",
                    "parent_daily_manifest_sha256": file_sha(daily / "manifest.json"),
                }
            ]
        ),
        period / "security-days.parquet",
    )
    admission = {
        "binding": binding,
        "source_configuration_identity": identity(source_configuration),
        "ledger_sha256": file_sha(period / "security-days.parquet"),
        "new_windows": 1,
        "new_window_contract": LEDGER_CONTRACT,
    }
    admission["identity"] = identity(admission)
    save(period / "ADMISSION.json", admission)
    with Budget(tmp_path / "windows-job", [ods, period], Limits(free_floor_bytes=1)) as budget:
        windows = budget.root / "windows"
        build_windows(
            predecessor=ods, period=period, binding=binding, output=windows, budget=budget
        )
    return VerifiedInputs.verify(frozen, roots), period, windows, c


def readonly(monkeypatch, roots):
    real = os.statvfs

    def view(path):
        value = real(path)
        if any(Path(path) == r or r in Path(path).parents for r in roots):
            fields = {k: getattr(value, k) for k in dir(value) if k.startswith("f_")}
            return SimpleNamespace(**fields | {"f_flag": value.f_flag | os.ST_RDONLY})
        return value

    monkeypatch.setattr(os, "statvfs", view)


def test_unsealed_ods_retains_inputs_and_audits_after_original_sources_are_removed(
    tmp_path,
    source_closure,
    monkeypatch,
):
    inputs, period, windows, c = source_inputs(tmp_path, source_closure)
    with Budget(
        tmp_path / "ods-job",
        [Path(p) for p in inputs.roots.values()] + [period, windows],
        Limits(free_floor_bytes=1),
    ) as budget:
        output = budget.root / "ODS"
        result = materialize_ods(
            inputs=inputs, period=period, windows=windows, contract=c, output=output, budget=budget
        )
    assert result["READY"] is False
    assert not (output / "READY").exists()
    assert not (output / "members/dwd/payload").exists()
    assert (output / "members/dwd/MATERIALIZATION.json").exists()
    assert (output / "members/ods/READY").exists()  # unchanged predecessor readiness only
    original = Path(inputs.roots["daily"]) / "original/raw.json"
    assert (output / "members/daily/original/raw.json").read_bytes() == original.read_bytes()
    for path in [Path(p) for p in inputs.roots.values()] + [period, windows]:
        shutil.rmtree(path)
    readonly(monkeypatch, [output])
    with Budget(tmp_path / "audit-job", [output], Limits(free_floor_bytes=1)) as budget:
        receipt = audit_ods(output, budget, expected_input_identity=inputs.verified_identity)
    assert receipt["input_identity"] == inputs.verified_identity
    assert receipt["quality_held_added"] == 0
    assert not (output / "READY").exists()


@pytest.mark.parametrize("damage", ["raw", "extra", "missing-window", "wrong-input"])
def test_ods_bytes_namespace_and_window_binding_fail_closed(
    tmp_path, source_closure, monkeypatch, damage
):
    inputs, period, windows, c = source_inputs(tmp_path, source_closure)
    with Budget(
        tmp_path / "ods-job",
        [Path(p) for p in inputs.roots.values()] + [period, windows],
        Limits(free_floor_bytes=1),
    ) as budget:
        output = budget.root / "ODS"
        materialize_ods(
            inputs=inputs, period=period, windows=windows, contract=c, output=output, budget=budget
        )
    if damage == "raw":
        (output / "members/daily/original/raw.json").write_bytes(b"changed")
    elif damage == "extra":
        (output / "unexpected.txt").write_text("undeclared")
    elif damage == "missing-window":
        (output / "evidence/windows/WINDOWS.parquet").unlink()
    readonly(monkeypatch, [output])
    with (
        Budget(tmp_path / "audit-job", [output], Limits(free_floor_bytes=1)) as budget,
        pytest.raises(BoundedError, match="BUILD_ODS_"),
    ):
        audit_ods(
            output,
            budget,
            expected_input_identity=("sha256:" + "f" * 64)
            if damage == "wrong-input"
            else inputs.verified_identity,
        )
    assert not (output / "READY").exists()


def test_ods_cannot_relabel_a_valid_frozen_period_as_a_later_version(
    tmp_path,
    source_closure,
    monkeypatch,
):
    inputs, period, windows, c = source_inputs(tmp_path, source_closure)
    with Budget(
        tmp_path / "ods-job",
        [Path(p) for p in inputs.roots.values()] + [period, windows],
        Limits(free_floor_bytes=1),
    ) as budget:
        output = budget.root / "ODS"
        materialize_ods(
            inputs=inputs, period=period, windows=windows, contract=c, output=output, budget=budget
        )
    c.update(base_version="2.6", predecessor_version="2.5", business_through="2026-11-30")
    save(output / "contract/base-build-delivery.v1.json", c)
    record = json.loads((output / "MATERIALIZATION.json").read_bytes())
    record.update(
        base_version="2.6", through_date="2026-11-30", delivery_contract_identity=identity(c)
    )
    row = next(r for r in record["files"] if r["path"] == "contract/base-build-delivery.v1.json")
    p = output / row["path"]
    row.update(sha256=file_sha(p), size_bytes=p.stat().st_size)
    record["identity"] = identity({k: v for k, v in record.items() if k != "identity"})
    save(output / "MATERIALIZATION.json", record)
    readonly(monkeypatch, [output])
    with (
        Budget(tmp_path / "audit-job", [output], Limits(free_floor_bytes=1)) as budget,
        pytest.raises(BoundedError, match="BUILD_ODS_PERIOD_OR_CONTRACT_SCOPE_CHANGED"),
    ):
        audit_ods(output, budget)
    assert not (output / "READY").exists()


def test_ods_capacity_reserves_the_full_archive_before_any_input_copy(tmp_path, source_closure):
    inputs, period, windows, c = source_inputs(tmp_path, source_closure)
    with Budget(
        tmp_path / "small-job",
        [Path(p) for p in inputs.roots.values()] + [period, windows],
        Limits(free_floor_bytes=1, allocated_bytes=4096),
    ) as budget:
        output = budget.root / "ODS"
        with pytest.raises(BoundedError, match="ALLOCATED_WRITE_BUDGET_EXCEEDED"):
            materialize_ods(
                inputs=inputs,
                period=period,
                windows=windows,
                contract=c,
                output=output,
                budget=budget,
            )
    assert not list(output.rglob("*.partial"))
    assert not (output / "members").exists()
    assert not (output / "READY").exists()


def test_rehashed_new_held_cannot_be_claimed_as_inherited_inside_ods(
    tmp_path,
    source_closure,
    monkeypatch,
):
    from quanthus.pipelines.securities_warehouse.base_build_windows import (
        audit_windows,
        held_coordinates,
        validate_rows,
    )

    inputs, period, windows, c = source_inputs(tmp_path, source_closure)
    with Budget(
        tmp_path / "ods-job",
        [Path(p) for p in inputs.roots.values()] + [period, windows],
        Limits(free_floor_bytes=1),
    ) as budget:
        output = budget.root / "ODS"
        materialize_ods(
            inputs=inputs, period=period, windows=windows, contract=c, output=output, budget=budget
        )
    window_root = output / "evidence/windows"
    ledger = window_root / "WINDOWS.parquet"
    table = pq.ParquetFile(ledger).read()
    rows = table.to_pylist()
    inherited = next(r for r in rows if r["contract"] != LEDGER_CONTRACT)
    inherited.update(status="held", disposition="INHERITED_ORIGINAL_HELD")
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), ledger)
    counts = validate_rows(rows)
    held = held_coordinates(rows)
    plan = json.loads((window_root / "PLAN.json").read_bytes())
    plan.update(ledger_sha256=file_sha(ledger), counts=counts, inherited_held=held)
    plan["identity"] = identity({k: v for k, v in plan.items() if k != "identity"})
    save(window_root / "PLAN.json", plan)
    checkpoint = json.loads((window_root / "CHECKPOINT.json").read_bytes())
    checkpoint.update(counts)
    checkpoint.update(
        plan_sha256=file_sha(window_root / "PLAN.json"),
        ledger_sha256=file_sha(ledger),
        held_set_identity=identity(held),
    )
    save(window_root / "CHECKPOINT.json", checkpoint)
    with Budget(tmp_path / "self-check-job", [output], Limits(free_floor_bytes=1)) as budget:
        coherent = audit_windows(window_root, budget)
    # All local hashes/counts agree; the original predecessor still has no held.
    assert coherent["counts"]["held"] == 1 and coherent["new_quality_held"] == 0
    record = json.loads((output / "MATERIALIZATION.json").read_bytes())
    record["window_identity"] = coherent["identity"]
    for row in record["files"]:
        if row["path"].startswith("evidence/windows/"):
            p = output / row["path"]
            row.update(sha256=file_sha(p), size_bytes=p.stat().st_size)
    record["identity"] = identity({k: v for k, v in record.items() if k != "identity"})
    save(output / "MATERIALIZATION.json", record)
    readonly(monkeypatch, [output])
    with (
        Budget(tmp_path / "audit-job", [output], Limits(free_floor_bytes=1)) as budget,
        pytest.raises(BoundedError, match="BUILD_WINDOWS_PREDECESSOR_OR_PERIOD_ANCESTRY_CHANGED"),
    ):
        audit_ods(output, budget)
    assert not (output / "READY").exists()


def test_original_empty_dwd_bytes_survive_when_successor_can_drop_the_placeholder(
    tmp_path,
    source_closure,
    monkeypatch,
):
    inputs, period, windows, c = source_inputs(tmp_path, source_closure, include_empty=True)
    original = Path(inputs.roots["dwd"]) / "payload/empty-table/empty/original.parquet"
    expected = original.read_bytes()
    with Budget(
        tmp_path / "ods-job",
        [Path(p) for p in inputs.roots.values()] + [period, windows],
        Limits(free_floor_bytes=1),
    ) as budget:
        output = budget.root / "ODS"
        materialize_ods(
            inputs=inputs, period=period, windows=windows, contract=c, output=output, budget=budget
        )
    assert (
        output / "members/dwd/payload/empty-table/empty/original.parquet"
    ).read_bytes() == expected
    assert not (output / "members/dwd/payload/controlled-test.parquet").exists()
    for path in inputs.roots.values():
        shutil.rmtree(path)
    readonly(monkeypatch, [output])
    with Budget(tmp_path / "audit-job", [output], Limits(free_floor_bytes=1)) as budget:
        audit_ods(output, budget, expected_input_identity=inputs.verified_identity)
