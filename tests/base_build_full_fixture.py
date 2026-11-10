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


def source_pair(tmp_path, source_closure, original, monkeypatch):
    """One complete synthetic source pair for existing and continuous-version tests."""
    import shutil
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base_build_audit import audit_pair
    from quanthus.pipelines.securities_warehouse.base_build_ods import materialize_ods
    from quanthus.pipelines.securities_warehouse.base_build_pair import materialize_dwd
    from quanthus.pipelines.securities_warehouse.base_build_period import prepare_period
    from quanthus.pipelines.securities_warehouse.base_build_profile import delivery_contract
    from quanthus.pipelines.securities_warehouse.base_build_windows import build_windows
    from tests.base_build_full_fixture import native_predecessor, ods_predecessor
    from tests.test_base_build_period import fixture as period_fixture

    args = period_fixture(
        tmp_path,
        original,
        base_factory=lambda root, schemas: native_predecessor(root, schemas, monkeypatch),
        ods_factory=lambda: ods_predecessor(tmp_path, source_closure),
    )
    inputs = args["inputs"]
    roots = [Path(p) for p in inputs.roots.values()]
    c = delivery_contract(
        json.loads(Path("config/pipelines/base24-delivery.v1.json").read_bytes()),
        version="2.5",
        predecessor_version="2.4",
        through="2026-10-09",
    )
    prior_ods = next(
        Path(inputs.roots[r["id"]])
        for r in inputs.frozen["content"]["selection"]["packages"]
        if r["role"] == "ods"
    )
    prior_dwd = next(
        Path(inputs.roots[r["id"]])
        for r in inputs.frozen["content"]["selection"]["packages"]
        if r["role"] == "dwd"
    )
    with Budget(tmp_path / "materialize-job", roots, limits()) as budget:
        period = budget.root / "period"
        prepare_period(**args, output=period, budget=budget)
        windows = budget.root / "windows"
        build_windows(
            predecessor=prior_ods,
            period=period,
            binding=args["binding"],
            output=windows,
            budget=budget,
        )
        ods = budget.root / "ODS"
        materialize_ods(
            inputs=inputs, period=period, windows=windows, contract=c, output=ods, budget=budget
        )
        dwd = budget.root / "DWD"
        materialize_dwd(
            predecessor=prior_dwd,
            period=period,
            windows=windows,
            ods=ods,
            binding=args["binding"],
            contract=c,
            rules=args["rules"],
            model=json.loads(Path("config/pipelines/dwd-domain-model.v1.json").read_bytes()),
            extension=json.loads(
                Path("config/pipelines/dwd-domain-model.v1.1-extension.json").read_bytes()
            ),
            config_root=Path("config/pipelines"),
            output=dwd,
            budget=budget,
        )
    for root in roots:
        shutil.rmtree(root)
    shutil.rmtree(period)
    shutil.rmtree(windows)
    readonly(monkeypatch, [ods, dwd])
    with Budget(tmp_path / "full-pair-audit", [ods, dwd], limits()) as budget:
        receipt = audit_pair(
            ods=ods,
            dwd=dwd,
            contract=c,
            expected_input_identity=inputs.verified_identity,
            budget=budget,
        )
    return ods, dwd, receipt, c, inputs


def standard_next_inputs(root, ods, dwd, revision):
    """Four ordinary days after a generic Base, including overlapping observations."""
    from quanthus.pipelines.contracts import OdsRelease
    from quanthus.pipelines.securities_warehouse.base_build_standard_daily import select
    from quanthus.pipelines.securities_warehouse.standard_daily import publish
    from tests.test_base_build_composition import limits
    from tests.test_base_build_period import fixture, write_source
    from tests.unit.test_native5m_daily_source import original as make_wire

    next_area = root / "next-inputs"
    next_area.mkdir()
    wire_area = root / "next-wire"
    wire_area.mkdir()
    extra = {
        "stock_code": "000001",
        "announce_date": "2026-10-10",
        "publish_time": "2026-10-10 23:59:59",
        "ann_id": "ann-2026-10-10",
        "title": "original-title",
        "url": "https://example.invalid/document",
        "acquired_at": "2026-10-12T10:00:00+00:00",
    }
    raw = fixture(
        next_area,
        make_wire.__wrapped__(wire_area, day="2026-10-12"),
        base_factory=lambda root, schemas: dwd,
        market_day="2026-10-12",
        closed_day="2026-10-13",
        assemble_inputs=False,
        extra_events=[extra],
    )
    calendar_source = raw["market"]
    universe = (
        json.loads((calendar_source / "source_universe.json").read_bytes())
        if (calendar_source / "source_universe.json").exists()
        else None
    )
    # Sources use the original helper's full quality/capture representation.
    if universe is None:
        # The raw release declares the actual universe filename.
        release = OdsRelease.load(calendar_source / "release.json")
        art = next(a for a in release.artifacts if a.name == "source_universe")
        universe = json.loads((calendar_source / art.relative_path).read_bytes())
    paths = []
    for day in ("2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"):
        event_root = raw["source_root"] / day / "events"
        if not (event_root / "release.json").exists():
            write_source(
                event_root,
                day=day,
                lane="calendar_daily_events",
                universe=universe,
                frames={
                    "announcements_delta": [
                        {
                            **extra,
                            "announce_date": day,
                            "publish_time": day + " 23:59:59",
                            "ann_id": "ann-" + day,
                            "acquired_at": day + "T10:00:00+00:00",
                        }
                    ]
                },
            )
        output = root / ("delivery-" + day)
        with Budget(root / ("publisher-" + day), [dwd, next_area], limits()) as budget:
            publish(
                projection=raw["projection"] if day == "2026-10-12" else None,
                sources={
                    "calendar_daily_events": event_root,
                    "market_daily_core"
                    if day == "2026-10-12"
                    else "calendar_reference": calendar_source,
                },
                config=raw["config"],
                base_root=dwd,
                output=output,
                code_revision=revision,
                budget=budget,
            )
        paths.append(output)
    selection, roots, configuration = select(
        ods=ods, dwd=dwd, daily_roots=paths, target_version="2.6", through_date="2026-10-13"
    )
    return selection, roots, configuration
