"""Synthetic 33-table/source fixtures; mocked mount flags are not runtime A/B/C."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse.base22 import partition
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget
from quanthus.pipelines.securities_warehouse.base23_native_image import (
    audit_native_image,
    materialize_native_image,
    seal_native_image,
)
from quanthus.pipelines.securities_warehouse.base24_image import PROFILE
from quanthus.pipelines.securities_warehouse.base24_ods_image import materialize_ods24, seal_ods24
from quanthus.pipelines.securities_warehouse.base_build_inputs import file_sha
from tests.test_base_build_composition import limits
from tests.test_base_build_ods import readonly, save
from tests.unit.test_base22_independent_image import _row
from tests.unit.test_base24_image import base24_limitations
from tests.unit.test_base24_ods_image import Budget as LegacyBudget
from tests.unit.test_base24_ods_image import _freeze

CONFIG = Path("config/pipelines")


def native_predecessor(root, schemas, monkeypatch):
    rules = json.loads((CONFIG / "base22-independent-image.v1.json").read_bytes())
    model = json.loads((CONFIG / "dwd-domain-model.v1.json").read_bytes())
    extension = json.loads((CONFIG / "dwd-domain-model.v1.1-extension.json").read_bytes())
    contract = json.loads((CONFIG / "base24-delivery.v1.json").read_bytes())
    existing = json.loads((root / "MATERIALIZATION.json").read_bytes())
    source = root.parent / "bootstrap-native-source"
    source.mkdir()
    descriptor = {"files": []}
    lineage = ["bootstrap-fixture", "v1", "sha256:" + "a" * 64]
    plan = {
        "purpose": PROFILE.purpose,
        "through_date": "2026-09-30",
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
    for table, schema in schemas.items():
        if table in existing["layout"]:
            old_path = root / existing["layout"][table]["files"][0]["path"]
            rows = pq.ParquetFile(old_path).read().to_pylist()
        elif table == "dwd_security_bar":
            from datetime import timedelta

            daily = _row(schema)
            daily.update(listing_id="listing1", calendar_session_id="bootstrap-session")
            minute = dict(
                daily,
                interval_size="5m",
                observation_end=daily["observation_start"] + timedelta(minutes=5),
            )
            rows = [daily, minute]
        elif table == "dwd_trading_calendar":
            rows = [dict(_row(schema), venue_id="venue1", calendar_session_id="bootstrap-session")]
        else:
            rows = []
        groups = {}
        for row in rows:
            if table in {"dwd_company_identifier", "dwd_security_identifier"}:
                owner = "company_id" if table == "dwd_company_identifier" else "security_id"
                row["identifier_value"] = row[owner]
            row.update(
                record_source=lineage[0],
                source_revision=lineage[1],
                source_artifact_sha256=lineage[2],
                source_record_id="bootstrap-" + table,
            )
            groups.setdefault(partition(row, rules["tables"][table]), []).append(row)
        if table in existing["layout"]:
            # Period fixture construction still uses these original temporary
            # reference files; keep their synthetic values equal to the full
            # predecessor, before any Daily package or input freeze is built.
            pq.write_table(pa.Table.from_pylist(rows, schema=schema), old_path)
        if not groups:
            groups = {"empty": []}
        files = []
        for index, (part, selected) in enumerate(sorted(groups.items())):
            p = source / (table + "-" + str(index) + ".parquet")
            pq.write_table(pa.Table.from_pylist(selected, schema=schema), p)
            item = {
                "path": p.name,
                "table": table,
                "partition": part,
                "source_id": "fixture",
                "rows": len(selected),
                "size_bytes": p.stat().st_size,
                "sha256": file_sha(p),
            }
            files.append(item)
            descriptor["files"].append(item)
        plan["tables"][table] = {
            "files": files,
            "source_ids": ["fixture"],
            "rows": len(rows),
            "expected_partition_rows": {part: len(v) for part, v in groups.items()},
            "admitted_lineage": [lineage],
            "lineage_by_source": {"fixture": [lineage]},
        }
    save(source / "manifest.json", descriptor)
    plan["sources"]["fixture"]["descriptor_sha256"] = file_sha(source / "manifest.json")
    limitations = base24_limitations()
    evidence = source / "limitations.json"
    save(evidence, {"synthetic_restrictions_only": limitations})
    output = root.parent / "complete-native-base24"
    materialize_native_image(
        plan=plan,
        rules=rules,
        contract=contract,
        model=model,
        extension=extension,
        limitations=limitations,
        local_evidence={limitations[0]["evidence_paths"][0]: evidence},
        output=output,
        allocated_limit=limits().allocated_bytes,
        free_floor=1,
        rss_limit=limits().rss_bytes,
        wall_seconds=120,
        config_root=CONFIG,
        profile=PROFILE,
    )
    readonly(monkeypatch, [output])
    with Budget(root.parent / "bootstrap-native-audit", [output], limits()) as budget:
        receipt = audit_native_image(output, budget, profile=PROFILE)
    seal_native_image(output, receipt, code_revision="a" * 40, profile=PROFILE)
    return output


def ods_predecessor(root, source_closure):
    plan = _freeze(source_closure)
    output = root / "complete-ods-base24"
    receipt = materialize_ods24(plan, output, LegacyBudget())
    seal_ods24(output, receipt, code_revision="a" * 40, budget=LegacyBudget())
    return output
