"""Retained numeric quantities, bounded domains and genuine source clocks."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base23_measures as m
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
    day = date(2026, 9, 3)
    raw["date"] = raw["market_date"] = str(day)
    raw["acquired_at"] = "2026-09-03T17:30:00+08:00"
    args["day"] = args["calendar"]["trade_date"] = day
    args["observed_at"] = datetime(2026, 10, 9, tzinfo=timezone.utc)
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
    assert rows[0]["observation_start"] == datetime(2026, 9, 2, 16, tzinfo=timezone.utc)
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
        "october",
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
    if fault == "october":
        args["day"] = date(2026, 10, 1)
        raw["date"] = "2026-10-01"
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


def test_august_adapter_remains_frozen_and_september_scope_is_explicit():
    from quanthus.pipelines.securities_warehouse import base22_daily_measures as old

    raw, args, _ = valuation_case()
    with pytest.raises(BoundedError, match="BUSINESS_DATE"):
        old.valuation_observation(raw, **args)
    m.valuation_observation(raw, **args)
    args["day"] = date(2026, 8, 31)
    raw["date"] = raw["market_date"] = "2026-08-31"
    with pytest.raises(BoundedError, match="BUSINESS_DATE"):
        m.valuation_observation(raw, **args)


def test_revision_union_preserves_all_captures_and_holds_actual_atom_conflict():
    val, va, ss = valuation_case()
    idx, ia, _ = index_case()
    duplicate = dict(val, acquired_at="2026-09-04T17:30:00+08:00")
    sources = {
        m.TABLES[0]: [
            (va["source_sha"], "first", [val]),
            ("sha256:" + "b" * 64, "later", [duplicate]),
        ],
        m.TABLES[1]: [(ia["source_sha"], "first", [idx])],
    }
    kwargs = dict(
        day=va["day"],
        listings=[va["listing"]],
        calendars=[va["calendar"]],
        indexes=[ia["index"]],
        observed_at=va["observed_at"],
        ss=ss,
    )
    rows, proofs = m.project_revisions(sources, **kwargs)
    assert len(rows[m.TABLES[0]]) == 4 and len(rows[m.TABLES[1]]) == 1
    assert proofs[m.TABLES[0]]["union"]["identical_revision_overlap"] == 1
    assert len(proofs[m.TABLES[0]]["proofs"][0]["retained_captures"][0]) == 2
    duplicate["pb"] = 1.0
    rows, proofs = m.project_revisions(sources, **kwargs)
    assert not rows[m.TABLES[0]] and len(proofs[m.TABLES[0]]["held"]) == 1
    assert len(rows[m.TABLES[1]]) == 1


def scope_fixture(tmp_path, monkeypatch):
    import json

    from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
    from quanthus.pipelines.securities_warehouse.base22_sources import _tree
    from tests.unit import test_base22_daily_measures as old_tests
    from tests.unit.test_base22_independent_image import _row

    monkeypatch.setattr(old_tests, "valuation_case", valuation_case)
    monkeypatch.setattr(old_tests, "index_case", index_case)
    monkeypatch.setattr(old_tests, "m", m)
    root, old, ss, _ = old_tests.scope_fixture(tmp_path, monkeypatch)
    (root / "QUALIFICATION.json").unlink()
    parents = {}
    for table, items in old["parents"].items():
        parents[table] = {"path": items[0], "archive_path": "native/" + table + ".parquet"}
    venues = pq.ParquetFile(root / parents["dwd_trading_venue"]["path"]).read().to_pylist()
    for n in (2, 3):
        venues.append(dict(venues[0], venue_id="venue-" + str(n), mic_code="mic-" + str(n)))
    pq.write_table(
        pa.Table.from_pylist(venues, schema=ss["dwd_trading_venue"]),
        root / parents["dwd_trading_venue"]["path"],
    )
    val, va, _ = valuation_case()
    idx, ia, _ = index_case()
    calendars = []
    for v in venues:
        for n in range(30):
            day = date(2026, 9, 1) + timedelta(days=n)
            row = _row(ss["dwd_trading_calendar"])
            row.update(va["calendar"])
            row.update(
                venue_id=v["venue_id"],
                trade_date=day,
                calendar_session_id="session-" + v["venue_id"] + str(day),
            )
            if v["venue_id"] == va["listing"]["venue_id"] and day == va["day"]:
                row["calendar_session_id"] = va["calendar"]["calendar_session_id"]
            calendars.append(row)
    pq.write_table(
        pa.Table.from_pylist(calendars, schema=ss["dwd_trading_calendar"]),
        root / parents["dwd_trading_calendar"]["path"],
    )
    history = {
        "layout": {
            t: {"files": [dict(i, sha256=file_sha(root / i["path"]), path=i["archive_path"])]}
            for t, i in parents.items()
            if t != "dwd_trading_calendar"
        }
    }
    (root / "history.json").write_bytes(canonical(history))
    cp = parents["dwd_trading_calendar"]
    cal = {
        "physical_members": {cp["archive_path"]: {"sha256": file_sha(root / cp["path"])}},
        "calendar_projection_proof": {
            "full_month_coverage": True,
            "output_rows_sha256": digest(calendars),
        },
    }
    (root / "calendar.json").write_bytes(canonical(cal))
    manifest = json.loads((root / "release.json").read_bytes())
    manifest["release_id"] = "fixture-v1"
    (root / "release.json").write_bytes(canonical(manifest))
    (root / "READY").write_text(file_sha(root / "release.json") + "\n")
    members = [
        dict(
            table=t,
            sha256=old["days"][0]["sources"][t]["sha256"],
            path=old["days"][0]["sources"][t]["raw"],
        )
        for t in m.TABLES
    ]
    admitted = {
        "manifest_sha256": file_sha(root / "release.json"),
        "market_date": str(va["day"]),
        "revision": "fixture-v1",
        "members": members,
    }
    closure = dict(
        old["input_closure"],
        sources=[admitted],
        history_materialization_sha256=file_sha(root / "history.json"),
        calendar_scope_sha256=file_sha(root / "calendar.json"),
    )
    raw = {
        m.TABLES[0]: [(members[0]["sha256"], "fixture-v1", [val])],
        m.TABLES[1]: [(members[1]["sha256"], "fixture-v1", [idx])],
    }
    _, proof = m.project_revisions(
        raw,
        day=va["day"],
        listings=[va["listing"]],
        calendars=calendars,
        indexes=[ia["index"]],
        observed_at=va["observed_at"],
        ss=ss,
    )
    (root / "proof.json").write_bytes(canonical(proof))
    body = dict(
        contract_id=m.CONTRACT_ID,
        input_closure=closure,
        projection_module_sha256=file_sha(Path(m.__file__)),
        business_window=["2026-09-01", "2026-09-30"],
        READY=False,
        complete_Base23=False,
        consumer_admission="NOT_GRANTED",
        observed_at=va["observed_at"].isoformat(),
        parents=parents,
        history_materialization="history.json",
        calendar_scope="calendar.json",
        sources=[dict(manifest="release.json", ready="READY", members=members)],
        days=[
            dict(date=str(va["day"]), outputs=old["days"][0]["outputs"], proof_path="proof.json")
        ],
    )

    def reseal():
        body["physical_members"] = {
            p: {"sha256": file_sha(root / p), "size_bytes": (root / p).stat().st_size}
            for p in _tree(root)
            if p != "SCOPE.json"
        }
        body["input_digest"] = digest(body["input_closure"])
        body.pop("identity", None)
        body["identity"] = digest(body)
        (root / "SCOPE.json").write_bytes(canonical(body))

    reseal()
    return root, body, ss, reseal


def audit_fixture(root, tmp_path):
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
    from quanthus.pipelines.securities_warehouse.base23_measures_audit import audit_measures_scope

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
        return audit_measures_scope(root, budget)


def test_complete_offline_synthetic_replay(tmp_path, monkeypatch):
    root, _, _, _ = scope_fixture(tmp_path, monkeypatch)
    result = audit_fixture(root, tmp_path)
    assert result["rows"] == {m.TABLES[0]: 4, m.TABLES[1]: 1}


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("empty-source", "SOURCE_PLAN"),
        ("wrong-table-source", "SOURCE_PLAN"),
        ("unqualified", "CONTRACT_UNQUALIFIED"),
        ("wrong-purpose", "CONTRACT_UNQUALIFIED"),
        ("extra-empty", "PHYSICAL_PARTITIONS"),
        ("value", "TYPED_REPLAY"),
    ],
)
def test_repackaged_synthetic_faults_fail_closed(tmp_path, monkeypatch, fault, reason):
    from quanthus.pipelines.securities_warehouse.base22_bounded import semantic_scan

    root, body, ss, reseal = scope_fixture(tmp_path, monkeypatch)
    plan = body["input_closure"]
    if fault == "empty-source":
        plan["source_ids_by_table"][m.TABLES[0]] = []
    elif fault == "wrong-table-source":
        plan["source_ids_by_table"][m.TABLES[0]] = plan["source_ids_by_table"][m.TABLES[1]]
    elif fault == "unqualified":
        plan["qualification"] = "unqualified"
    elif fault == "wrong-purpose":
        plan["purpose"] = "production"
    elif fault == "extra-empty":
        p = root / f"payload/{m.TABLES[0]}/observation_start_month=2026-10/extra.parquet"
        p.parent.mkdir()
        pq.write_table(pa.Table.from_pylist([], schema=ss[m.TABLES[0]]), p)
    elif fault == "value":
        item = body["days"][0]["outputs"][m.TABLES[0]]
        p = root / item["path"]
        rs = pq.ParquetFile(p).read().to_pylist()
        rs[0]["measure_value"] = Decimal("2")
        pq.write_table(pa.Table.from_pylist(rs, schema=ss[m.TABLES[0]]), p)
        item["semantic_scan"] = semantic_scan([p], ss[m.TABLES[0]])
    reseal()
    with pytest.raises(BoundedError, match=reason):
        audit_fixture(root, tmp_path)
