from __future__ import annotations

import copy
import hashlib
import json
import shutil
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.contracts import OdsRelease
from quanthus.pipelines.securities_warehouse import daily_base24
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
)
from quanthus.pipelines.securities_warehouse.base23_native_image import image_schemas
from quanthus.pipelines.securities_warehouse.base24_delivery import (
    ONE_MINUTE_DISPOSITION,
    PURPOSE,
)
from quanthus.pipelines.securities_warehouse.base24_image import KIND
from quanthus.pipelines.securities_warehouse.base_build_freeze import freeze
from quanthus.pipelines.securities_warehouse.base_build_inputs import (
    canonical,
    file_sha,
    identity,
)
from quanthus.pipelines.securities_warehouse.base_build_period import (
    PARENTS,
    POLICY,
    VerifiedInputs,
    _views,
    compare_tables,
    expected_days,
    prepare_period,
    qualified_calendar_union,
)
from quanthus.pipelines.securities_warehouse.base_build_profile import delivery_contract
from quanthus.pipelines.securities_warehouse.daily_core_domain import (
    _project_daily_tables,
)
from quanthus.pipelines.securities_warehouse.daily_core_ods_catalog import (
    _daily_core_quality_sha256,
)
from tests.unit.test_base22_independent_image import _row
from tests.unit.test_daily_base24 import seal as seal_core
from tests.unit.test_native5m_daily_source import original as original
from tests.unit.test_native5m_daily_source import seal as seal_native

CONFIG = Path("config/pipelines")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def sealed_header(root, body):
    save(root / "manifest.json", body)
    (root / "READY").write_text(file_sha(root / "manifest.json") + "\n")


def write_source(root, *, day, lane, frames, universe):
    root.mkdir(parents=True)
    members = []
    for name, rows in frames.items():
        p = root / "artifacts" / (name + ".parquet")
        p.parent.mkdir(exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), p)
        members.append(
            {
                "name": name,
                "relative_path": p.relative_to(root).as_posix(),
                "sha256": file_sha(p),
                "size_bytes": p.stat().st_size,
                "source_revision": lane + "-" + day,
                "knowledge_cutoff": day + "T10:00:00+00:00",
            }
        )
    master_sha = next((r["sha256"] for r in members if r["name"] == "security_master"), None)
    if master_sha:
        universe["files"] = {"security_master.parquet": master_sha}
    excluded = json.loads(POLICY.read_text())["daily_core_excluded_domains"]
    datasets = [
        {
            "dataset": n,
            "physical_status": "ready",
            "row_count": len(rows),
            "daily_core_blocked": False,
            "state": "changed",
        }
        for n, rows in frames.items()
    ]
    datasets += [
        {
            "dataset": n,
            "current_refresh_proven": False,
            "watermark_advanced": False,
            "reason_code": "SOURCE_NOT_REFRESHED",
            "projection_rows": None,
            "blocked_uses": [n],
        }
        for n in excluded
    ]
    raw = root / "artifacts/original-responses.tar.gz"
    raw.write_bytes(b"synthetic retained capture bytes; no supplier or market data")
    quality = {
        "policy_id": "p10-acquisition-prevention-v1",
        "policy_sha256": file_sha(POLICY),
        "market_date": day,
        "original_responses_sha256": file_sha(raw),
        "datasets": datasets,
    }
    save(root / "artifacts/source_universe.json", universe)
    save(root / "artifacts/acquisition_quality.json", quality)
    for name, p in (
        ("source_universe", root / "artifacts/source_universe.json"),
        ("acquisition_quality", root / "artifacts/acquisition_quality.json"),
        ("original_responses", raw),
    ):
        members.append(
            {
                "name": name,
                "relative_path": p.relative_to(root).as_posix(),
                "sha256": file_sha(p),
                "size_bytes": p.stat().st_size,
                "source_revision": lane + "-" + day,
                "knowledge_cutoff": day + "T10:00:00+00:00",
            }
        )
    save(
        root / "release.json",
        {
            "schema_version": 1,
            "release_id": lane + "-" + day,
            "market_date": day,
            "schedule": "daily",
            "created_at": day + "T10:00:00+00:00",
            "producer": "synthetic-period-fixture",
            "status": "ready",
            "artifacts": members,
        },
    )
    release = OdsRelease.load(root / "release.json")
    return {
        "release_id": release.release_id,
        "manifest_sha256": file_sha(root / "release.json"),
        "artifacts": {a.name: (root / a.relative_path, a) for a in release.artifacts},
    }


def fixture(tmp_path, original, *, base_factory=None, ods_factory=None):
    """Period-only integration fixture; its seven-parent Base is not full A/B."""
    schemas = image_schemas(
        json.loads((CONFIG / "dwd-domain-model.v1.json").read_text()),
        json.loads((CONFIG / "dwd-domain-model.v1.1-extension.json").read_text()),
    )
    base = tmp_path / "base"
    layout, summaries, parent_paths = {}, {}, {}
    for table in PARENTS:
        rows = []
        for n in (1, 2):
            row = _row(schemas[table])
            row.update(
                record_source="fixture",
                source_record_id=f"parent-{table}-{n}",
                source_revision="v1",
                source_artifact_sha256="sha256:" + "a" * 64,
            )
            if table == "dwd_trading_venue":
                row.update(venue_id="venue" + str(n), mic_code="XSHE" if n == 1 else "XSHG")
            elif table == "dwd_market_index":
                if n == 2:
                    continue
                row.update(index_id="index1", index_code="000300", base_currency="CNY")
            else:
                row.update(
                    {
                        k: k.removesuffix("_id") + str(n)
                        for k in ("listing_id", "security_id", "company_id")
                        if k in row
                    }
                )
                if table == "dwd_listing":
                    row.update(
                        listing_code="00000" + str(n),
                        venue_id="venue1",
                        listing_currency="CNY",
                    )
            rows.append(row)
        p = base / "payload" / table / "all/part.parquet"
        p.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=schemas[table]), p)
        item = {
            "path": p.relative_to(base).as_posix(),
            "table": table,
            "partition": "all",
            "rows": len(rows),
            "size_bytes": p.stat().st_size,
            "sha256": file_sha(p),
        }
        layout[table] = {"files": [item], "expected_partition_rows": {"all": len(rows)}}
        summaries[table] = {"all": {"rows": len(rows)}}
        parent_paths[table] = [p]
    save(
        base / "MATERIALIZATION.json",
        {"kind": KIND, "purpose": PURPOSE, "layout": layout, "evidence": {}},
    )
    sealed_header(
        base,
        {
            "kind": KIND,
            "purpose": PURPOSE,
            "version": "2.4",
            "business_through": "2026-09-30",
            "business_timezone": "Asia/Shanghai",
            "default_intervals": ["1d", "5m"],
            "one_minute_disposition": ONE_MINUTE_DISPOSITION,
            "materialization_sha256": file_sha(base / "MATERIALIZATION.json"),
            "content_digest": "sha256:" + "e" * 64,
            "summaries": summaries,
            "consumer_admission": "NOT_GRANTED",
        },
    )
    if base_factory is not None:
        base = base_factory(base, schemas)
    cfg = json.loads((CONFIG / "base24-daily-governance.v1.json").read_text())
    cfg["layers"]["dwd"].update(
        manifest_sha256=file_sha(base / "manifest.json"),
        materialization_sha256=file_sha(base / "MATERIALIZATION.json"),
        content_digest=json.loads((base / "manifest.json").read_bytes())["content_digest"],
        package_id="synthetic-parent",
    )
    config = tmp_path / "config.json"
    save(config, cfg)
    universe = {
        "market_date": "2026-10-08",
        "historical_pit_authorized": False,
        "policy_id": "p10-acquisition-prevention-v1",
        "population": 2,
        "scope": "SSE_SZSE_A_SHARE_00_30_60_68",
        "codes": ["000001", "000002"],
        "company_org_ids": {},
        "warehouse_observed_at": "2026-10-08T10:00:00+00:00",
    }
    calendar = [
        {
            "trade_date": date(2026, 10, 1) + timedelta(days=n),
            "exchange": venue,
            "is_open": int(n == 7),
            "session": "day",
            "built_at": "2026-10-08T10:00:00+00:00",
            "source": "fixture",
        }
        for n in range(9)
        for venue in ("SSE", "SZSE")
    ]
    common = {
        "date": "2026-10-08",
        "market_date": "2026-10-08",
        "acquired_at": "2026-10-08T10:00:00+00:00",
    }
    price = {
        **common,
        "open": "2.0000000001",
        "high": "2.0000000001",
        "low": "2.0000000001",
        "close": "2.0000000001",
        "volume": "4800",
        "amount": "9600",
    }
    source_root = tmp_path / "sources"
    market_path = source_root / "2026-10-08/market"
    market = write_source(
        market_path,
        day="2026-10-08",
        lane="market_daily_core",
        universe=universe,
        frames={
            "daily_ohlcv_none_delta": [{**price, "stock_code": "000001"}],
            "daily_status_observation_delta": [
                {
                    **common,
                    "stock_code": "00000" + str(n),
                    "tradestatus": "1" if n == 1 else "0",
                    "isST": "0",
                }
                for n in (1, 2)
            ],
            "valuation_daily_delta": [
                {
                    **common,
                    "stock_code": "000001",
                    "pe_ttm": "1",
                    "pb": "1",
                    "ps_ttm": "1",
                    "pcf_ttm": "1",
                }
            ],
            "security_master": [
                {
                    "stock_code": "00000" + str(n),
                    "exchange": "SZSE",
                    "target_date": "2026-10-08",
                    "list_date": "2010-01-01",
                    "delist_date": None,
                }
                for n in (1, 2)
            ],
            "trading_calendar": calendar,
            "index_daily_delta": [{**price, "index_code": "000300"}],
        },
    )

    def event(day):
        return write_source(
            source_root / day / "events",
            day=day,
            lane="calendar_daily_events",
            universe=universe,
            frames={
                "announcements_delta": [
                    {
                        "stock_code": "000001",
                        "announce_date": day,
                        "publish_time": day + " 23:59:59",
                        "ann_id": "ann-" + day,
                        "title": "original-title",
                        "url": "https://example.invalid/document",
                        "acquired_at": day + "T10:00:00+00:00",
                    }
                ]
            },
        )

    events, closed = event("2026-10-08"), event("2026-10-09")
    selected = {"market_daily_core": market, "calendar_daily_events": events}
    core = tmp_path / "core"
    core.mkdir()
    with duckdb.connect() as connection:
        _views(connection, parent_paths, {})
        path, artifact = market["artifacts"]["daily_ohlcv_none_delta"]
        outputs, held = _project_daily_tables(
            connection,
            selected=selected,
            work=core,
            market_date=date(2026, 10, 8),
            covered_after=date(2026, 9, 30),
            catalog_digest="sha256:" + "9" * 64,
            bar_sources=[(path, artifact, "1d", "INTERVAL 0 SECOND")],
            check_identity=True,
        )
    assert held == 0
    parent_binding = {k: v for k, v in cfg["layers"]["dwd"].items() if k != "format"}
    parent_binding.update(
        kind=KIND,
        business_through="2026-09-30",
        governance_use="governance_baseline",
        source_consumer_admission="NOT_GRANTED",
    )
    sources = {
        lane: {k: value[k] for k in ("release_id", "manifest_sha256")}
        for lane, value in selected.items()
    }
    save(core / "audit.json", {"result": "accepted", "market_date": "2026-10-08"})
    save(core / "source-universe.json", universe)
    body = {
        "schema_version": 2,
        "kind": "cn01_daily_core_dwd_increment",
        "package_id": "synthetic-core",
        "market_date": "2026-10-08",
        "covered_after_date": "2026-09-30",
        "product_scope": "daily-core-v1",
        "quality_policy": "p10-business-value-warehouse-quality.v1",
        "quality_policy_sha256": _daily_core_quality_sha256(),
        "consumable_intervals": ["1d"],
        "selected_releases": sources,
        "base": parent_binding,
        "code_revision": "d" * 40,
        "catalog_sha256": "sha256:" + "f" * 64,
        "catalog_digest": "sha256:" + "9" * 64,
        "prior": None,
        "purpose": "isolated_daily_governance",
        "execution_authorized": False,
        "consumer_admission": "NOT_GRANTED",
        "acquisition_universe": universe,
        "row_counts": {t: pq.ParquetFile(p).metadata.num_rows for t, p in outputs.items()},
        "unavailable_products": {
            "intraday_1m": "not_part_of_daily_core_not_qualified_as_daily",
            "intraday_5m": "not_part_of_daily_core_see_separate_product_status",
        },
    }
    body["job_identity"] = {
        "base": parent_binding,
        "catalog": body["catalog_sha256"],
        "code": body["code_revision"],
        "config": file_sha(config),
        "prior": None,
    }
    seal_core(core, body)
    native = original[0]
    ref = json.loads((native / "daily-reference.json").read_text())
    ref.update(
        source_ods_manifest_sha256=market["manifest_sha256"],
        event_ods_manifest_sha256=events["manifest_sha256"],
    )
    ref["reference_digest"] = (
        "sha256:"
        + hashlib.sha256(
            canonical({k: v for k, v in ref.items() if k != "reference_digest"}) + b"\n"
        ).hexdigest()
    )
    save(native / "daily-reference.json", ref)
    statuses = [json.loads(s) for s in (native / "quality-status.jsonl").read_text().splitlines()]
    for row in statuses:
        row["reference_ods_manifest_sha256"] = market["manifest_sha256"]
    (native / "quality-status.jsonl").write_text("".join(json.dumps(r) + "\n" for r in statuses))
    seal_native(native)
    manifest = json.loads((native / "manifest.json").read_text())
    manifest["source_ods_manifest_sha256"] = market["manifest_sha256"]
    sealed_header(native, manifest)
    daily = tmp_path / "daily"
    daily_base24.build(
        core=core, native5m=native, config=config, output=daily, code_revision="a" * 40
    )
    ods = tmp_path / "ods"
    sealed_header(
        ods,
        {
            "kind": "quanthus_ods_base24_bundle",
            "content": {"through_date": "2026-09-30", "files": []},
        },
    )
    if ods_factory is not None:
        ods = ods_factory()
    reference = tmp_path / "calendar"
    reference.mkdir()
    for source, name in (
        (market["artifacts"]["trading_calendar"][0], "raw.parquet"),
        (market["artifacts"]["trading_calendar"][0], "selected.parquet"),
        (market_path / "release.json", "source-release.json"),
        (core / "manifest.json", "parent-core.json"),
        (daily / "manifest.json", "parent-daily.json"),
    ):
        shutil.copyfile(source, reference / name)
    roots = {
        "base-dwd": str(base),
        "base-ods": str(ods),
        "daily": str(daily),
        "sources": str(source_root),
        "calendar": str(reference),
    }
    selection = {
        "contract": "p10-base-build-input-selection-v2",
        "predecessor_version": "2.4",
        "target_version": "2.5",
        "predecessor_through": "2026-09-30",
        "through_date": "2026-10-09",
        "expected_days": ["2026-10-08"],
        "packages": [
            {
                "id": key,
                "role": role,
                "day": day,
                "manifest_sha256": file_sha(Path(roots[key]) / "manifest.json"),
            }
            for key, role, day in (
                ("base-dwd", "dwd", None),
                ("base-ods", "ods", None),
                ("daily", "daily", "2026-10-08"),
            )
        ],
        "expected_days_evidence": {
            "package_id": "calendar",
            "path": "selected.parquet",
            "sha256": file_sha(reference / "selected.parquet"),
        },
        "evidence": [],
    }
    for key, purpose in (
        ("sources", "daily_source_evidence"),
        ("calendar", "calendar_reference"),
    ):
        root = Path(roots[key])
        selection["evidence"].append(
            {
                "id": key,
                "purpose": purpose,
                "parent_manifests": [file_sha(daily / "manifest.json")],
                "files": [
                    {
                        "path": p.relative_to(root).as_posix(),
                        "size_bytes": p.stat().st_size,
                        "sha256": file_sha(p),
                    }
                    for p in sorted(root.rglob("*"))
                    if p.is_file()
                ],
            }
        )
    frozen = freeze(selection, roots)
    c = delivery_contract(
        json.loads((CONFIG / "base24-delivery.v1.json").read_text()),
        version="2.5",
        predecessor_version="2.4",
        through="2026-10-09",
    )
    binding = {
        "predecessor_manifest_sha256": file_sha(base / "manifest.json"),
        "frozen_input_identity": frozen["identity"],
        "code_revision": "c" * 40,
        "policy_identity": identity(c),
        "predecessor_through": "2026-09-30",
        "through_date": "2026-10-09",
        "target_version": "2.5",
    }
    return {
        "inputs": VerifiedInputs.verify(frozen, roots),
        "binding": binding,
        "rules": json.loads((CONFIG / "base22-independent-image.v1.json").read_text()),
        "schemas": schemas,
        "source_evidence_id": "sources",
        "calendar_reference": {
            "evidence_id": "calendar",
            "path": "raw.parquet",
            "parent_release": "source-release.json",
            "parent_core": "parent-core.json",
            "parent_daily": "parent-daily.json",
            "artifact_name": "trading_calendar",
        },
        "closed_days": [
            {
                "evidence_id": "sources",
                "day": "2026-10-09",
                "release_path": "2026-10-09/events/release.json",
                "release_id": closed["release_id"],
                "manifest_sha256": closed["manifest_sha256"],
            }
        ],
    }


def test_period_replays_sources_native_minutes_and_closed_events_without_original_markers(
    tmp_path,
    original,
):
    args = fixture(tmp_path, original)
    with Budget(
        tmp_path / "job",
        [Path(p) for p in args["inputs"].roots.values()],
        Limits(free_floor_bytes=1, wall_seconds=120),
    ) as budget:
        result = prepare_period(**args, output=budget.root / "period", budget=budget)
    assert result["new_windows"] == 2
    assert result["new_quality_held"] == 0 and result["READY"] is False
    assert result["daily"][-1]["disclosures"] == result["daily"][-1]["subjects"] == 1
    delta = json.loads((tmp_path / "job/period/delta/DELTA.json").read_text())
    assert delta["tables"]["dwd_disclosure"]["output_rows"] == 2
    assert delta["tables"]["dwd_trading_calendar"]["output_rows"] == 18
    assert delta["tables"]["dwd_security_bar"]["output_rows"] == 49
    assert not list(Path(args["inputs"].roots["sources"]).rglob("READY"))


def test_exact_comparison_rejects_value_or_duplicate_changes_even_with_equal_counts(
    tmp_path,
):
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    pq.write_table(pa.table({"fact": ["original", "original"]}), a)
    pq.write_table(pa.table({"fact": ["original", "changed"]}), b)
    with (
        duckdb.connect() as connection,
        pytest.raises(BoundedError, match="SOURCE_REPLAY_DIFFERS"),
    ):
        compare_tables([a], [b], connection)


def test_calendar_does_not_infer_closed_days_from_missing_source_rows(tmp_path):
    p = tmp_path / "calendar.parquet"
    pq.write_table(
        pa.Table.from_pylist([{"exchange": "SSE", "trade_date": date(2026, 10, 1), "is_open": 0}]),
        p,
    )
    with pytest.raises(BoundedError, match="CALENDAR_DATE_MISSING"):
        expected_days(p, "2026-09-30", "2026-10-01")


def test_self_consistent_daily_valuation_repack_still_fails_independent_source_replay(
    tmp_path,
    original,
):
    args = fixture(tmp_path, original)
    inputs = args["inputs"]
    original_daily = Path(inputs.roots["daily"])
    original_sha = file_sha(original_daily / "manifest.json")
    bad = tmp_path / "repacked-bad-daily"
    shutil.copytree(original_daily, bad)
    core = bad / "members/core"
    p = next(core.glob("payload/*/dwd_security_valuation/*/*.parquet"))
    observed = pq.ParquetFile(p).read()
    rows = observed.to_pylist()
    rows[0]["measure_value"] += Decimal("0.0000000001")
    pq.write_table(pa.Table.from_pylist(rows, schema=observed.schema), p)
    seal_core(core, json.loads((core / "manifest.json").read_text()))
    top = json.loads((bad / "manifest.json").read_text())
    top["identity"]["core"] = file_sha(core / "manifest.json")
    seal_core(bad, top)
    # The existing Daily verifier accepts integrity and native prices; it does
    # not establish that these valuation values match the selected ODS source.
    daily_base24.verify(bad)
    selection = copy.deepcopy(inputs.frozen["content"]["selection"])
    next(r for r in selection["packages"] if r["role"] == "daily")["manifest_sha256"] = file_sha(
        bad / "manifest.json"
    )
    next(r for r in selection["evidence"] if r["id"] == "sources")["parent_manifests"] = [
        file_sha(bad / "manifest.json")
    ]
    roots = {**inputs.roots, "daily": str(bad)}
    frozen = freeze(selection, roots)
    args["inputs"] = VerifiedInputs.verify(frozen, roots)
    args["binding"] = {**args["binding"], "frozen_input_identity": frozen["identity"]}
    with (
        Budget(
            tmp_path / "bad-job",
            [Path(p) for p in roots.values()],
            Limits(free_floor_bytes=1, wall_seconds=120),
        ) as budget,
        pytest.raises(BoundedError, match="SOURCE_REPLAY_DIFFERS"),
    ):
        prepare_period(**args, output=budget.root / "period", budget=budget)
    assert file_sha(original_daily / "manifest.json") == original_sha
    assert not list((tmp_path / "bad-job").rglob("ADMISSION.json"))


def test_entry_point_shares_verified_inputs_and_reuses_the_period_stage(
    tmp_path, original, monkeypatch
):
    from quanthus.pipelines.securities_warehouse import base_build as entry

    args = fixture(tmp_path, original)
    inputs = args["inputs"]
    controls = tmp_path / "controls"
    controls.mkdir()
    save(controls / "frozen.json", inputs.frozen)
    save(controls / "selection.json", inputs.frozen["content"]["selection"])
    save(controls / "roots.json", inputs.roots)
    c = delivery_contract(
        json.loads((CONFIG / "base24-delivery.v1.json").read_text()),
        version="2.5",
        predecessor_version="2.4",
        through="2026-10-09",
    )
    job = {
        "contract": entry.JOB,
        "builder_revision": "c" * 40,
        "selection": str(controls / "selection.json"),
        "frozen": str(controls / "frozen.json"),
        "roots": str(controls / "roots.json"),
        "work_root": str(tmp_path / "entry-job"),
        "environment_lock": "/synthetic/environment-lock.json",
        "wheelhouse": "/synthetic/wheels",
        "source_evidence_id": args["source_evidence_id"],
        "closed_days": args["closed_days"],
        "calendar_reference": args["calendar_reference"],
        "limits": {"free_floor_bytes": 1},
    }
    # This test owns only pipeline/reuse semantics. Actual readonly/network/runtime
    # admission is tested separately on the clean pinned node, not mocked as A.
    monkeypatch.setattr(entry, "_require_pinned_code", lambda revision: None)
    monkeypatch.setattr(entry, "_preflight_paths", lambda *args: None)
    monkeypatch.setattr(
        entry,
        "check_environment",
        lambda **kwargs: {"lock_sha256": "sha256:" + "a" * 64},
    )
    calls = []
    original_prepare = entry.prepare_period

    def count_build(**kwargs):
        calls.append("period")
        return original_prepare(**kwargs)

    monkeypatch.setattr(entry, "prepare_period", count_build)
    first = entry.run(action="prepare-period", job=job, contract=c)
    second = entry.run(action="prepare-period", job=job, contract=c)
    assert not first["reused"] and second["reused"]
    assert calls == ["period"]
    assert first["output_identity"] == second["output_identity"]


def test_entry_point_rejects_a_job_that_tries_to_disable_quality_checks():
    from quanthus.pipelines.securities_warehouse.base_build import validate_job

    job = json.loads(Path("config/pipelines/base-build-job.example.v1.json").read_text())
    validate_job(job)
    job["new_quality_held_allowed"] = 1
    with pytest.raises(BoundedError, match="JOB_SCHEMA"):
        validate_job(job)


def test_entry_point_rejects_a_connected_namespace_before_input_work(tmp_path, monkeypatch):
    from quanthus.pipelines.securities_warehouse import base_build as entry

    monkeypatch.setattr(entry.socket, "if_nameindex", lambda: [(1, "lo"), (2, "eth0")])
    with pytest.raises(BoundedError, match="ISOLATED_OFFLINE_NETWORK_REQUIRED"):
        entry._preflight_paths({}, tmp_path / "not-created", Limits(), 1)
    assert not (tmp_path / "not-created").exists()


def test_offline_gate_uses_current_namespace_even_when_host_sysfs_is_visible(tmp_path, monkeypatch):
    from quanthus.pipelines.securities_warehouse import base_build as entry

    monkeypatch.setattr(entry.socket, "if_nameindex", lambda: [(1, "lo")])
    monkeypatch.setattr(entry.os, "listdir", lambda path: ["lo", "eth0", "host-bridge"])
    entry._preflight_paths(
        {}, tmp_path / "not-created", Limits(free_floor_bytes=1, allocated_bytes=1), 1
    )


def calendars(tmp_path, *, conflict=False, missing_observed=False):
    sources = []
    for number in (1, 2):
        rows = []
        for day in (1, 2):
            for venue in ("SSE", "SZSE"):
                if day != number and not (number == 2 and conflict):
                    # This is a cached placeholder and must not become evidence.
                    known, source, opened = None, "unknown", 0
                else:
                    known, source, opened = "2026-10-03T10:00:00+00:00", "observed", 1
                if conflict and number == 2 and day == 1:
                    opened = 0
                if missing_observed and day == 2:
                    known, source = None, "unknown"
                rows.append(
                    {
                        "exchange": venue,
                        "trade_date": date(2026, 10, day),
                        "is_open": opened,
                        "session": "day",
                        "built_at": known,
                        "source": source,
                    }
                )
        p = tmp_path / f"calendar-{number}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), p)
        sources.append((p, SimpleNamespace(sha256=file_sha(p))))
    return sources


def test_partial_observed_calendars_combine_without_promoting_unknown_cached_cells(tmp_path):
    sources = calendars(tmp_path)
    days, selected, receipt = qualified_calendar_union(sources, "2026-09-30", "2026-10-02")
    assert days == ["2026-10-01", "2026-10-02"]
    assert selected["2026-10-01"][0] == sources[0][0]
    assert selected["2026-10-02"][0] == sources[1][0]
    assert receipt["unknown_cached_cells_ignored"] == 4
    assert receipt["trusted_cells"] == 4


@pytest.mark.parametrize("damage", ["missing_observed", "conflict"])
def test_calendar_union_rejects_missing_evidence_or_conflicting_known_cells(tmp_path, damage):
    sources = calendars(tmp_path, **{damage: True})
    with pytest.raises(BoundedError, match="CALENDAR_DATE_MISSING|CALENDAR_CONFLICT"):
        qualified_calendar_union(sources, "2026-09-30", "2026-10-02")
