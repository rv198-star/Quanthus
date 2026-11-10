"""Retained numeric quantities, bounded domains and genuine source clocks."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_daily_measures as m
from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError
from tests.unit.test_base22_status import case


def valuation_case():
    raw, args = case()
    _, model, ext = load_contract(Path("config/pipelines"))
    ss = schemas(model, ext)
    raw = {k: raw[k] for k in ["stock_code", "market_date", "date", "acquired_at"]}
    raw.update(
        security_id=raw["stock_code"],
        source="baostock.query_history_k_data_plus.valuation",
        pe_ttm=0.0,
        pb=None,
        ps_ttm=-1.25,
        pcf_ttm=0.123456789,
        total_share=None,
        total_mv=None,
    )
    args["listing"]["security_id"] = "security-1"
    args.pop("input_revision_digest")
    args["day"] = args.pop("market_date")
    args["source_sha"] = args.pop("source_sha256")
    args["schema"] = ss[m.TABLES[0]]
    return raw, args, ss


def index_case():
    v, args, ss = valuation_case()
    day = args["day"]
    index = {
        "index_id": "index-1",
        "index_code": "sh.000001",
        "effective_from": datetime(1990, 1, 1, tzinfo=timezone.utc),
        "effective_to": None,
        "knowledge_from": args["listing"]["knowledge_from"],
        "knowledge_to": None,
        "base_currency": "CNY",
    }
    raw = {
        "index_code": index["index_code"],
        "date": str(day),
        "source": "baostock.query_history_k_data_plus.adjustflag=3",
        "acquired_at": v["acquired_at"],
        "open": 2.25,
        "high": 2.5,
        "low": 2.0,
        "close": 2.4,
        "volume": 0.0,
        "amount": None,
        "pct_chg": 0.25,
    }
    args = {k: v for k, v in args.items() if k not in ["listing", "calendar", "schema"]}
    args["index"] = index
    args["schema"] = ss[m.TABLES[1]]
    return raw, args, ss


def test_four_declared_valuation_fields_preserve_null_zero_sign_precision_and_clocks(tmp_path):
    raw, args, _ = valuation_case()
    before = deepcopy((raw, args))
    rows, proof = m.valuation_observation(raw, **args)
    assert [r["measure_type"] for r in rows] == list(m.MEASURES)
    assert [r["measure_value"] for r in rows] == [
        Decimal(0),
        None,
        Decimal("-1.25"),
        Decimal("0.123456789"),
    ]
    assert (
        proof["provider_acquisition"] is None
        and proof["numeric_classifications"]["NULL_UNAVAILABLE"] == 1
    )
    assert proof["numeric_classifications"]["ZERO"] == 1
    assert all(r["knowledge_from"] == args["observed_at"] for r in rows)
    assert rows[0]["observation_start"] == datetime(2026, 8, 2, 16, tzinfo=timezone.utc)
    p = tmp_path / "ratio.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=args["schema"]), p)
    assert pq.ParquetFile(p).read().equals(pa.Table.from_pylist(rows, schema=args["schema"]))
    assert (raw, args) == before


def test_index_preserves_native_units_instants_and_explicit_nullable_calendar(tmp_path):
    raw, args, _ = index_case()
    before = deepcopy((raw, args))
    rows, proof = m.index_observation(raw, **args)
    assert rows[0]["calendar_session_id"] is None and rows[0]["currency"] == "CNY"
    assert rows[0]["volume"] == 0 and rows[0]["turnover"] is None
    assert rows[0]["open"] == Decimal("2.25") and rows[0]["knowledge_from"] == args["observed_at"]
    assert proof["raw_acquisition_instant"] == datetime.fromisoformat(raw["acquired_at"])
    p = tmp_path / "index.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=args["schema"]), p)
    assert pq.ParquetFile(p).read().equals(pa.Table.from_pylist(rows, schema=args["schema"]))
    assert (raw, args) == before


@pytest.mark.parametrize(
    "fault",
    [
        "date",
        "venue",
        "listing",
        "parent-clock",
        "acquisition",
        "precision",
        "nan",
        "provider",
        "nonempty-capital",
        "missing-field",
        "unknown-field",
        "vendor-code",
        "submicrosecond",
    ],
)
def test_valuation_unqualified_fields_never_become_inferred_rows(fault):
    raw, args, _ = valuation_case()
    if fault == "date":
        args["calendar"]["trade_date"] += timedelta(days=1)
    if fault == "venue":
        args["calendar"]["venue_id"] = "other"
    if fault == "listing":
        args["listing"]["listing_code"] = "other"
    if fault == "parent-clock":
        args["calendar"]["knowledge_from"] = args["observed_at"] + timedelta(days=1)
    if fault == "acquisition":
        raw["acquired_at"] = "2026-11-01T00:00:00+08:00"
    if fault == "precision":
        raw["pcf_ttm"] = Decimal("1.12345678901")
    if fault == "nan":
        raw["pcf_ttm"] = float("nan")
    if fault == "provider":
        raw["source"] = "unknown"
    if fault == "nonempty-capital":
        raw["total_share"] = 1
    if fault == "missing-field":
        del raw["pe_ttm"]
    if fault == "unknown-field":
        raw["new_family"] = 1
    if fault == "vendor-code":
        raw["security_id"] = "unproved-alias"
    if fault == "submicrosecond":
        raw["acquired_at"] = "2026-08-03T17:30:00.1234567+08:00"
    original_clock = raw["acquired_at"]
    before = deepcopy(args)
    with pytest.raises((BoundedError, SourceAssessmentError)):
        m.valuation_observation(raw, **args)
    assert raw["acquired_at"] == original_clock and args == before


@pytest.mark.parametrize(
    "fault",
    [
        "date",
        "september",
        "provider",
        "identity",
        "unknown-field",
        "precision",
        "nan",
        "ohlc",
        "negative-quantity",
        "parent-clock",
        "effective-date",
        "missing-price",
    ],
)
def test_index_invalid_semantics_or_identity_never_pass(fault):
    raw, args, _ = index_case()
    if fault == "date":
        raw["date"] = "2026-08-04"
    if fault == "september":
        args["day"] = date(2026, 9, 1)
        raw["date"] = "2026-09-01"
    if fault == "provider":
        raw["source"] = "unknown"
    if fault == "identity":
        raw["index_code"] = "unproved"
    if fault == "unknown-field":
        raw["new_family"] = 1
    if fault == "precision":
        raw["close"] = Decimal("1.12345678901")
    if fault == "nan":
        raw["close"] = float("nan")
    if fault == "ohlc":
        raw["high"] = 2.0
    if fault == "negative-quantity":
        raw["volume"] = -1
    if fault == "parent-clock":
        args["index"]["knowledge_from"] = args["observed_at"] + timedelta(days=1)
    if fault == "effective-date":
        args["index"]["effective_to"] = datetime(2026, 8, 1, tzinfo=timezone.utc)
    if fault == "missing-price":
        raw["open"] = None
    before = deepcopy(args)
    with pytest.raises((BoundedError, SourceAssessmentError)):
        m.index_observation(raw, **args)
    assert args == before


def test_shared_day_dependencies_and_missing_or_duplicate_source_closure():
    val, va, ss = valuation_case()
    idx, ia, _ = index_case()
    kw = {
        "day": va["day"],
        "listings": [va["listing"]],
        "calendars": [va["calendar"]],
        "indexes": [ia["index"]],
        "observed_at": va["observed_at"],
        "sources": {t: {"sha256": va["source_sha"], "revision": va["revision"]} for t in m.TABLES},
        "ss": ss,
    }
    rows, proof = m.project_day({m.TABLES[0]: [val], m.TABLES[1]: [idx]}, **kw)
    assert {t: len(v) for t, v in rows.items()} == {m.TABLES[0]: 4, m.TABLES[1]: 1} and len(
        proof
    ) == 2
    with pytest.raises(BoundedError, match="DUPLICATE"):
        m.project_day({m.TABLES[0]: [val, val], m.TABLES[1]: [idx]}, **kw)
    kw["listings"] = []
    with pytest.raises(BoundedError, match="CLOSURE_MISSING"):
        m.project_day({m.TABLES[0]: [val], m.TABLES[1]: [idx]}, **kw)


# Full scope fixtures are disposable; real cold jobs enforce kernel read-only mounts.
def scope_fixture(tmp_path, monkeypatch):
    import os
    import shutil
    from types import SimpleNamespace

    from quanthus.pipelines.securities_warehouse.base22 import (
        canonical,
        digest,
        file_sha,
        partition,
    )
    from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan
    from quanthus.pipelines.securities_warehouse.base22_sources import _tree
    from tests.unit.test_base22_independent_image import _row

    root = tmp_path / "scope"
    root.mkdir()
    real = os.statvfs

    def statvfs(p):
        info = real(p)
        if Path(p) != root:
            return info
        return SimpleNamespace(
            **(
                {k: getattr(info, k) for k in dir(info) if k.startswith("f_")}
                | {"f_flag": info.f_flag | os.ST_RDONLY}
            )
        )

    monkeypatch.setattr(os, "statvfs", statvfs)
    c, model, ext = load_contract(Path("config/pipelines"))
    ss = schemas(model, ext)
    (root / "contract").mkdir()
    for name in [
        "base22-independent-image.v1.json",
        "dwd-domain-model.v1.json",
        "dwd-domain-model.v1.1-extension.json",
    ]:
        shutil.copyfile(Path("config/pipelines") / name, root / "contract" / name)
    val, va, _ = valuation_case()
    idx, ia, _ = index_case()
    parent_rows = {
        "dwd_listed_company": {"company_id": "company-1"},
        "dwd_security": {"company_id": "company-1", "security_id": "security-1"},
        "dwd_listing": va["listing"],
        "dwd_trading_venue": {"venue_id": va["listing"]["venue_id"]},
        "dwd_trading_calendar": va["calendar"],
        "dwd_market_index": ia["index"],
    }
    parents = {}
    for table, fields in parent_rows.items():
        row = _row(ss[table])
        row["knowledge_from"] = va["listing"]["knowledge_from"]
        row.update(fields)
        rel = f"parents/{table}.parquet"
        (root / rel).parent.mkdir(exist_ok=True)
        pq.write_table(pa.Table.from_pylist([row], schema=ss[table]), root / rel)
        parents[table] = [rel]
    raws = {m.TABLES[0]: [val], m.TABLES[1]: [idx]}
    sources, artifacts = {}, []
    for t, rows in raws.items():
        rel = f"raw/{t}.parquet"
        (root / rel).parent.mkdir(exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), root / rel)
        sources[t] = {
            "raw": rel,
            "sha256": file_sha(root / rel),
            "revision": "fixture-v1",
            "artifact_name": "valuation_daily_delta" if t == m.TABLES[0] else "index_daily_delta",
        }
        artifacts.append(
            {
                "name": sources[t]["artifact_name"],
                "sha256": sources[t]["sha256"],
                "source_revision": "fixture-v1",
            }
        )
    manifest = {"market_date": str(va["day"]), "artifacts": artifacts}
    (root / "release.json").write_bytes(canonical(manifest))
    (root / "READY").write_text(file_sha(root / "release.json") + "\n")
    rows, proofs = m.project_day(
        raws,
        day=va["day"],
        listings=[va["listing"]],
        calendars=[va["calendar"]],
        indexes=[ia["index"]],
        observed_at=va["observed_at"],
        sources=sources,
        ss=ss,
    )
    outputs = {}
    for t, rs in rows.items():
        part = partition(rs[0], c["tables"][t])
        rel = f"payload/{t}/{part}/part.parquet"
        (root / rel).parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(rs, schema=ss[t]), root / rel)
        outputs[t] = {
            "path": rel,
            "partition": part,
            "semantic_scan": semantic_scan([root / rel], ss[t]),
        }
    body = {
        "contract_id": m.CONTRACT_ID,
        "projection_module_sha256": file_sha(Path(m.__file__)),
        "READY": False,
        "complete_Base22": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "input_closure": {
            "purpose": m.CONTRACT_ID,
            "qualification": m.QUALIFICATION,
            "source_ids_by_table": {t: [sources[t]["sha256"]] for t in m.TABLES},
        },
        "observed_at": va["observed_at"].isoformat(),
        "parents": parents,
        "days": [
            {
                "market_date": str(va["day"]),
                "manifest": "release.json",
                "READY": "READY",
                "sources": sources,
                "outputs": outputs,
                "proof_sha256": digest(proofs),
            }
        ],
    }

    def reseal():
        body.pop("identity", None)
        body["physical_members"] = {
            p: {"sha256": file_sha(root / p), "size_bytes": (root / p).stat().st_size}
            for p in _tree(root)
            if p != "QUALIFICATION.json"
        }
        body["identity"] = digest(body)
        (root / "QUALIFICATION.json").write_bytes(canonical(body))

    reseal()
    return root, body, ss, reseal


def audit_fixture(root, body, tmp_path):
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits

    with Budget(
        tmp_path / "audit",
        [root],
        Limits(
            memory_bytes=128 * 1024**2,
            temp_bytes=64 * 1024**2,
            allocated_bytes=128 * 1024**2,
            free_floor_bytes=1,
        ),
    ) as budget:
        return m.audit_scope(root, body, budget)


@pytest.mark.parametrize("decoded", [False, True])
def test_complete_scope_cold_reprojection_and_global_closure(tmp_path, monkeypatch, decoded):
    root, body, _, _ = scope_fixture(tmp_path, monkeypatch)
    if decoded:
        import json

        from quanthus.pipelines.securities_warehouse.base22 import canonical

        body = json.loads(canonical(body))
    result = audit_fixture(root, body, tmp_path)
    assert result["rows"] == {m.TABLES[0]: 4, m.TABLES[1]: 1}
    assert result["source_runtime_reads"] == 0 and result["complete_Base22"] is False


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("empty-source", "OFFLINE_SOURCE_PLAN"),
        ("unknown-source", "TABLE_LOCAL_SOURCE_UNKNOWN"),
        ("wrong-table-source", "TABLE_LOCAL_SOURCE_UNKNOWN"),
        ("quality", "OFFLINE_SOURCE_PLAN"),
        ("purpose", "OFFLINE_SOURCE_PLAN"),
        ("date", "CALENDAR_CLOSURE_MISSING"),
        ("venue", "CALENDAR_CLOSURE_MISSING"),
        ("value", "COMPLETE_TYPED_PROJECTION_CHANGED"),
        ("extra-empty", "PHYSICAL_PAYLOAD_SET_CHANGED"),
        ("parent-schema", "PARENT_SCHEMA_OR_FILES_CHANGED"),
        ("pit", "LOCAL_CONTRACT_INVALID"),
    ],
)
def test_resealed_scope_rejects_semantic_and_source_failures(tmp_path, monkeypatch, fault, reason):
    from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan

    root, body, ss, reseal = scope_fixture(tmp_path, monkeypatch)
    plan = body["input_closure"]
    day = body["days"][0]
    if fault == "empty-source":
        plan["source_ids_by_table"][m.TABLES[0]] = []
    elif fault == "unknown-source":
        plan["source_ids_by_table"][m.TABLES[0]] = ["sha256:" + "f" * 64]
    elif fault == "wrong-table-source":
        plan["source_ids_by_table"][m.TABLES[0]] = plan["source_ids_by_table"][m.TABLES[1]]
    elif fault == "quality":
        plan["qualification"] = "unqualified"
    elif fault == "purpose":
        plan["purpose"] = "production"
    elif fault in ["date", "venue"]:
        t = "dwd_trading_calendar"
        p = root / body["parents"][t][0]
        rs = pq.ParquetFile(p).read().to_pylist()
        rs[0]["trade_date" if fault == "date" else "venue_id"] = (
            date(2026, 7, 31) if fault == "date" else "other"
        )
        pq.write_table(pa.Table.from_pylist(rs, schema=ss[t]), p)
    elif fault == "value":
        out = day["outputs"][m.TABLES[0]]
        p = root / out["path"]
        rs = pq.ParquetFile(p).read().to_pylist()
        rs[0]["measure_value"] = Decimal("2")
        pq.write_table(pa.Table.from_pylist(rs, schema=ss[m.TABLES[0]]), p)
        out["semantic_scan"] = semantic_scan([p], ss[m.TABLES[0]])
    elif fault == "extra-empty":
        p = (
            root
            / f"payload/{m.TABLES[0]}"
            / "observation_start_year=2025/observation_start_month=7/extra.parquet"
        )
        p.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist([], schema=ss[m.TABLES[0]]), p)
    elif fault == "parent-schema":
        p = root / body["parents"]["dwd_market_index"][0]
        pq.write_table(pa.table({"index_id": ["index-1"]}), p)
    elif fault == "pit":
        body["historical_PIT_admission"] = True
    reseal()
    with pytest.raises(BoundedError, match=reason):
        audit_fixture(root, body, tmp_path)


def test_proof_array_is_independent_of_source_dictionary_order():
    val, va, ss = valuation_case()
    idx, ia, _ = index_case()
    kw = {
        "day": va["day"],
        "listings": [va["listing"]],
        "calendars": [va["calendar"]],
        "indexes": [ia["index"]],
        "observed_at": va["observed_at"],
        "sources": {t: {"sha256": va["source_sha"], "revision": va["revision"]} for t in m.TABLES},
        "ss": ss,
    }
    raw = {m.TABLES[0]: [val], m.TABLES[1]: [idx]}
    assert m.project_day(raw, **kw) == m.project_day(dict(reversed(list(raw.items()))), **kw)
