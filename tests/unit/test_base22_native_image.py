from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22 as legacy
from quanthus.pipelines.securities_warehouse import base22_native_image as n
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base22_delivery import PURPOSE


def test_full_typed_summary_preserves_decimal_null_zero_and_original_instant(tmp_path):
    table = pa.table(
        {
            "decimal": pa.array(
                [Decimal("1.1234567890123"), None, Decimal("0.0000000000000")] * 4001,
                type=pa.decimal256(41, 13),
            ),
            "instant": pa.array(
                [datetime(2026, 8, 31, 16, tzinfo=timezone.utc)] * 12003,
                type=pa.timestamp("us", tz="UTC"),
            ),
            "text": ["exact", None, "零"] * 4001,
        }
    )
    p = tmp_path / "whole.parquet"
    pq.write_table(table, p, row_group_size=611)
    paths = []
    for j, (start, end) in enumerate([(0, 11), (11, 9555), (9555, 12003)]):
        q = tmp_path / (str(j) + ".parquet")
        pq.write_table(table.slice(start, end - start), q)
        paths.append(q)
    before = n.typed_summary([p], table.schema, check=lambda: None)
    assert before == n.typed_summary(paths, table.schema, check=lambda: None)
    moved = tmp_path / "moved.parquet"
    moved.write_bytes(p.read_bytes())
    assert before == n.typed_summary([moved], table.schema, check=lambda: None)
    changed = table.set_column(
        0, "decimal", pa.array([Decimal("1.1234567890124")] * 12003, type=pa.decimal256(41, 13))
    )
    pq.write_table(changed, moved)
    assert before != n.typed_summary([moved], table.schema, check=lambda: None)


def selection():
    descriptor = {"files": [{"path": "payload/table/a.parquet", "sha256": "sha256:" + "a" * 64}]}
    item = {
        "source_id": "native",
        "path": "payload/table/a.parquet",
        "table": "table",
        "sha256": "sha256:" + "a" * 64,
        "rows": 1,
        "size_bytes": 5,
        "partition": "all",
    }
    plan = {
        "purpose": PURPOSE,
        "through_date": "2026-08-31",
        "timezone": "Asia/Shanghai",
        "sources": {
            "native": {
                "quality_gate": "QUALIFIED_FOR_GOVERNANCE_RETENTION",
                "role": "retained_native",
            }
        },
        "tables": {
            "table": {
                "source_ids": ["native"],
                "files": [item],
                "expected_partition_rows": {"all": 1},
                "rows": 1,
            }
        },
    }
    return plan, {"tables": {"table": {}}}, {"native": descriptor}


def test_correct_offline_input_closure_and_physical_census():
    n.validate_selection(*selection())


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_sources",
        "unknown_source",
        "wrong_purpose",
        "unqualified",
        "extra_empty",
        "renamed_empty",
        "unknown_hash",
    ],
)
def test_offline_repacked_selection_cannot_bypass_closure(mutation):
    plan, rules, descriptors = copy.deepcopy(selection())
    entry = plan["tables"]["table"]
    if mutation == "empty_sources":
        entry["source_ids"] = []
    elif mutation == "unknown_source":
        entry["files"][0]["source_id"] = "unknown"
    elif mutation == "wrong_purpose":
        plan["purpose"] = "production_trading"
    elif mutation == "unqualified":
        plan["sources"]["native"]["quality_gate"] = "unqualified"
    elif mutation == "unknown_hash":
        entry["files"][0]["sha256"] = "sha256:" + "b" * 64
    elif mutation == "renamed_empty":
        entry["files"][0]["rows"] = 0
        entry["files"][0]["partition"] = "empty-renamed"
        entry["expected_partition_rows"] = {"empty-renamed": 0}
        entry["rows"] = 0
    else:
        item = {
            **entry["files"][0],
            "path": "payload/table/extra.parquet",
            "partition": "extra",
            "rows": 0,
        }
        descriptors["native"]["files"].append({"path": item["path"], "sha256": item["sha256"]})
        entry["files"].append(item)
        entry["expected_partition_rows"]["extra"] = 0
    with pytest.raises(BoundedError):
        n.validate_selection(plan, rules, descriptors)


def test_legitimate_unique_empty_partition_passes():
    plan, rules, descriptors = selection()
    entry = plan["tables"]["table"]
    entry["files"][0].update(rows=0, partition="empty")
    entry.update(rows=0, expected_partition_rows={"empty": 0})
    n.validate_selection(plan, rules, descriptors)


def _native_fixture(tmp_path, *, intraday_bar=False):
    from tests.unit.test_base22_independent_image import Fixture

    f = Fixture(tmp_path / "legacy-synthetic")
    f.history["dwd_index_bar"][0]["calendar_session_id"] = None
    if intraday_bar:
        f.history["dwd_security_bar"][0]["interval_size"] = "1m"
    config = Path(__file__).resolve().parents[2] / "config/pipelines"
    source = tmp_path / "source"
    source.mkdir()
    ss = n.image_schemas(f.model, f.extension)
    plan = {
        "purpose": PURPOSE,
        "through_date": "2026-08-31",
        "timezone": "Asia/Shanghai",
        "sources": {
            "fixture": {
                "root": str(source),
                "descriptor": "manifest.json",
                "role": "qualified_domain_output",
                "quality_gate": "QUALIFIED_FOR_GOVERNANCE_RETENTION",
            }
        },
        "tables": {},
    }
    manifest = {"files": []}
    lineage = ["fixture", "v1", "sha256:" + "a" * 64]
    for table, rows in f.history.items():
        rows = copy.deepcopy(rows)
        for row in rows:
            row.update(
                record_source=lineage[0],
                source_revision=lineage[1],
                source_artifact_sha256=lineage[2],
                source_record_id="synthetic-" + table,
            )
            if table == "dwd_shareholder_count_observation":
                row["average_holding_exact"] = None
        path = source / (table + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows, schema=ss[table]), path)
        part = legacy.partition(rows[0], f.contract["tables"][table])
        item = {
            "path": path.name,
            "table": table,
            "source_id": "fixture",
            "sha256": legacy.file_sha(path),
            "size_bytes": path.stat().st_size,
            "rows": len(rows),
            "partition": part,
        }
        plan["tables"][table] = {
            "files": [item],
            "source_ids": ["fixture"],
            "rows": len(rows),
            "expected_partition_rows": {part: len(rows)},
            "admitted_lineage": [lineage],
            "lineage_by_source": {"fixture": [lineage]},
        }
        manifest["files"].append({"path": path.name, "sha256": item["sha256"]})
    (source / "manifest.json").write_text(json.dumps(manifest))
    plan["sources"]["fixture"]["descriptor_sha256"] = legacy.file_sha(source / "manifest.json")
    return f, config, source, plan


def test_mixed_shanghai_month_input_fails_before_materialization_directory(tmp_path):
    f, config, source, plan = _native_fixture(tmp_path, intraday_bar=True)
    table = "dwd_security_bar"
    path = source / (table + ".parquet")
    original = pq.read_table(path)
    rows = original.to_pylist() * 2
    for index, row in enumerate(rows):
        row["observation_start"] = datetime(2026, 7, 31, 15 + index, tzinfo=timezone.utc)
        row["observation_end"] = row["observation_start"].replace(minute=1)
    pq.write_table(pa.Table.from_pylist(rows, schema=original.schema), path)
    item = plan["tables"][table]["files"][0]
    item.update(rows=2, size_bytes=path.stat().st_size, sha256=legacy.file_sha(path))
    plan["tables"][table].update(rows=2, expected_partition_rows={item["partition"]: 2})
    manifest = json.loads((source / "manifest.json").read_bytes())
    for member in manifest["files"]:
        if member["path"] == path.name:
            member["sha256"] = item["sha256"]
    (source / "manifest.json").write_text(json.dumps(manifest))
    plan["sources"]["fixture"]["descriptor_sha256"] = legacy.file_sha(source / "manifest.json")
    before = path.read_bytes()
    output = tmp_path / "must-not-exist"
    with pytest.raises(BoundedError, match="NATIVE_IMAGE_PARTITION_COORDINATE_CHANGED"):
        n.materialize_native_image(
            plan=plan,
            rules=f.contract,
            contract=json.loads((config / "base22-delivery.v2.json").read_bytes()),
            model=f.model,
            extension=f.extension,
            limitations=[],
            local_evidence={},
            output=output,
            allocated_limit=64 * 1024**2,
            free_floor=1,
            rss_limit=4 * 1024**3,
            wall_seconds=30,
            config_root=config,
        )
    assert not output.exists()
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "intraday_bar,affected", [(False, "dwd_security_bar"), (True, "dwd_tradability_state")]
)
def test_whole_native_33_tables_and_offline_calendar_date_negative(
    tmp_path, monkeypatch, intraday_bar, affected
):
    f, config, source, plan = _native_fixture(tmp_path, intraday_bar=intraday_bar)
    output = tmp_path / "image"
    n.materialize_native_image(
        plan=plan,
        rules=f.contract,
        contract=json.loads((config / "base22-delivery.v2.json").read_bytes()),
        model=f.model,
        extension=f.extension,
        limitations=[],
        local_evidence={},
        output=output,
        allocated_limit=64 * 1024**2,
        free_floor=1,
        rss_limit=4 * 1024**3,
        wall_seconds=30,
        config_root=config,
    )
    assert not (output / "READY").exists()
    real_statvfs = os.statvfs

    def readonly(path):
        value = real_statvfs(path)
        if Path(path) == output or output in Path(path).parents:
            return SimpleNamespace(
                **{k: getattr(value, k) for k in dir(value) if k.startswith("f_")}
                | {"f_flag": value.f_flag | os.ST_RDONLY}
            )
        return value

    monkeypatch.setattr(n.os, "statvfs", readonly)

    def audit(label):
        with Budget(
            tmp_path / label,
            [output],
            Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2),
        ) as budget:
            return n.audit_native_image(output, budget)

    receipt = audit("good-audit")
    assert len(receipt["summaries"]) == 33 and receipt["assessment"]["build_eligible"]
    assert not receipt["assessment"]["independently_verified"]
    # Freshly sign the source member, source manifest, frozen selection and all
    # local file records. An existing same-venue session now has the wrong date.
    # Thus hashes, business keys and input closure pass; the calendar relation fails.
    materialization = json.loads((output / "MATERIALIZATION.json").read_bytes())
    item = materialization["layout"]["dwd_trading_calendar"]["files"][0]
    p = output / item["path"]
    rows = pq.ParquetFile(p).read().to_pylist()
    rows[0]["trade_date"] = rows[0]["trade_date"].replace(month=8, day=3)
    pq.write_table(pa.Table.from_pylist(rows, schema=pq.ParquetFile(p).schema_arrow), p)
    item["sha256"], item["size_bytes"] = legacy.file_sha(p), p.stat().st_size
    item["partition"] = "trade_date_year=2026"
    plan["tables"]["dwd_trading_calendar"]["files"][0].update(
        sha256=item["sha256"], size_bytes=item["size_bytes"]
    )
    manifest_path = output / "evidence/sources/fixture.json"
    manifest = json.loads(manifest_path.read_bytes())
    next(v for v in manifest["files"] if v["path"] == "dwd_trading_calendar.parquet")["sha256"] = (
        item["sha256"]
    )
    manifest_path.write_text(json.dumps(manifest))
    plan["sources"]["fixture"]["descriptor_sha256"] = legacy.file_sha(manifest_path)
    (output / "evidence/input-plan.json").write_text(json.dumps(plan))
    materialization["plan_digest"] = legacy.digest(plan)
    materialization["evidence"]["evidence/sources/fixture.json"] = legacy.file_sha(manifest_path)
    materialization["evidence"]["evidence/input-plan.json"] = legacy.file_sha(
        output / "evidence/input-plan.json"
    )
    (output / "MATERIALIZATION.json").write_text(json.dumps(materialization))
    with pytest.raises(
        BoundedError, match="GLOBAL_FOREIGN_KEY_DATE_VENUE_CLOSURE_FAILED:" + affected
    ):
        audit("bad-calendar-audit")
    with pytest.raises(BoundedError, match="OUTPUT_ALREADY_EXISTS"):
        n.materialize_native_image(
            plan=plan,
            rules=f.contract,
            contract=json.loads((config / "base22-delivery.v2.json").read_bytes()),
            model=f.model,
            extension=f.extension,
            limitations=[],
            local_evidence={},
            output=output,
            allocated_limit=64 * 1024**2,
            free_floor=1,
            rss_limit=4 * 1024**3,
            wall_seconds=30,
            config_root=config,
        )
