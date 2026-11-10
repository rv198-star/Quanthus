from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse import base231_new_listing as subject

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
SHA = "sha256:" + "a" * 64


def inputs():
    response = dict(
        status="COMPLETE_SDK_FIELD_STRINGS_WITH_RAW_WIRE",
        fields=list(subject.FIELDS),
        request=dict(
            code="sz.301716",
            fields=",".join(subject.FIELDS),
            start_date="2026-09-29",
            end_date="2026-09-30",
            frequency="d",
            adjustflag="3",
        ),
        completed_at="2026-10-13T22:00:00+00:00",
        original_provider_acquisition=None,
        rows=[
            [
                "2026-09-29",
                "sz.301716",
                "1.1234000000",
                "2",
                "1",
                "1.5",
                "100",
                "123.4000",
                "1",
                "0",
            ],
            ["2026-09-30", "sz.301716", "0", "0", "0", "0", "0", "0", "0", "1"],
        ],
    )
    calendar = [
        dict(
            calendar_session_id="c" + str(day),
            trade_date=date(2026, 9, day),
            venue_id="v",
            session_type="day",
            is_open=True,
            knowledge_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
            knowledge_to=None,
        )
        for day in [29, 30]
    ]
    listing = dict(
        listing_id="listing",
        listing_code="301716",
        venue_id="v",
        listing_currency="CNY",
        effective_from=datetime(2026, 9, 28, 16, tzinfo=timezone.utc),
        effective_to=None,
        knowledge_from=NOW,
        knowledge_to=None,
    )
    return response, listing, calendar


def call(response, listing, calendar):
    return subject.project_daily(
        response,
        listing=listing,
        calendars=calendar,
        source_sha256=SHA,
        observed_at=NOW,
        input_identity=SHA,
    )


def test_exact_decimal_zero_flags_and_true_new_clock():
    response, listing, calendar = inputs()
    rows, proof = call(response, listing, calendar)
    assert rows["dwd_security_bar"][0]["open"] == Decimal("1.1234000000")
    assert rows["dwd_security_bar"][1]["open"] == 0
    assert rows["dwd_tradability_state"][1]["is_tradable"] is False
    assert rows["dwd_tradability_state"][1]["reason_code"] == "SOURCE_NOT_TRADABLE"
    assert rows["dwd_security_bar"][0]["knowledge_from"] == NOW
    assert rows["dwd_security_bar"][0]["observation_start"].hour == 16
    assert proof["provider_acquisition"] is None
    assert proof["raw_lexical_tokens_preserved_in_input"]
    assert not proof["historical_PIT_admission"]


def test_native_regular_day_calendar_and_mixed_duplicate_fail_closed():
    response, listing, calendar = inputs()
    for row in calendar:
        row["session_type"] = "REGULAR_DAY"
    rows, proof = call(response, listing, calendar)
    assert proof["actual_sessions"] == 2
    assert len(rows["dwd_tradability_state"]) == 2
    calendar.append({**calendar[0], "session_type": "day"})
    with pytest.raises(RuntimeError, match="CALENDAR_SESSION_NOT_UNIQUE"):
        call(response, listing, calendar)


def test_intraday_calendar_cannot_supply_daily_closure():
    response, listing, calendar = inputs()
    calendar[0]["session_type"] = "INTRADAY"
    with pytest.raises(RuntimeError, match="DATE_CALENDAR_CLOSURE"):
        call(response, listing, calendar)


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "wrong-code",
        "october",
        "adjust",
        "precision",
        "nan",
        "flag",
        "venue",
        "late-parent",
        "late-source",
    ],
)
def test_dated_daily_closure_fails_closed(fault):
    response, listing, calendar = inputs()
    if fault == "missing":
        response["rows"].pop()
    elif fault == "duplicate":
        response["rows"].append(response["rows"][0])
    elif fault == "wrong-code":
        response["rows"][0][1] = "sh.301716"
    elif fault == "october":
        response["rows"][0][0] = "2026-10-01"
    elif fault == "adjust":
        response["request"]["adjustflag"] = "1"
    elif fault == "precision":
        response["rows"][0][2] = "1.12345678901"
    elif fault == "nan":
        response["rows"][0][2] = "NaN"
    elif fault == "flag":
        response["rows"][0][-2] = ""
    elif fault == "venue":
        calendar[0]["venue_id"] = "other"
    elif fault == "late-parent":
        calendar[0]["knowledge_from"] = datetime(2026, 10, 15, tzinfo=timezone.utc)
    elif fault == "late-source":
        response["completed_at"] = "2026-10-15T00:00:00+00:00"
    with pytest.raises(RuntimeError):
        call(response, listing, calendar)


def test_official_listing_date_requires_exact_label_and_venue():
    body = "证券代码：301716 人民币普通股 深圳证券交易所创业板 上市时间：2026年9月29日"
    assert subject.official_listing_date(body, code="301716", venue="SZSE") == date(2026, 9, 29)
    for wrong in [
        body.replace("上市时间", "认购时间"),
        body.replace("深圳证券交易所", "上海证券交易所"),
        body.replace("301716", "301717"),
        body + "\n上市时间：2026年9月30日",
    ]:
        with pytest.raises(RuntimeError):
            subject.official_listing_date(wrong, code="301716", venue="SZSE")


def identity_inputs():
    basic = dict(
        status="COMPLETE_SDK_FIELD_STRINGS_WITH_RAW_WIRE",
        fields=["code", "code_name", "ipoDate", "outDate", "type", "status"],
        request=dict(code="sz.301716"),
        completed_at="2026-10-13T20:00:00+00:00",
        rows=[["sz.301716", "合成公司", "2026-09-29", "", "1", "1"]],
    )
    issuer = dict(code="301716", orgId="issuer", zwjc="合成公司", category="A股")
    notice = dict(
        code="301716",
        orgId="issuer",
        announcement_id="doc",
        pdf_sha256=SHA,
        pdf_received_at="2026-10-13T21:00:00+00:00",
        issuer_map_received_at="2026-10-13T21:00:00+00:00",
    )
    venue = dict(mic_code="XSHE", venue_id="v", knowledge_from=NOW, knowledge_to=None)
    body = "证券代码：301716 人民币普通股 深圳证券交易所创业板 上市时间：2026年9月29日"
    return dict(
        basic=basic,
        issuer=issuer,
        notice=notice,
        body=body,
        venue=venue,
        observed_at=NOW,
        input_identity=SHA,
    )


def test_official_identity_keeps_native_ids_and_has_no_fabricated_lot_size():
    rows = subject.project_identity(**identity_inputs())
    listing = rows["dwd_listing"][0]
    assert listing["board_code"] == "ChiNext" and listing["lot_size"] is None
    assert listing["listing_code"] == "301716"
    assert listing["effective_from"] == datetime(2026, 9, 28, 16, tzinfo=timezone.utc)
    assert listing["knowledge_from"] == NOW
    assert listing["security_id"] == rows["dwd_security"][0]["security_id"]


@pytest.mark.parametrize("fault", ["ipo-date", "issuer", "name", "currency", "venue", "received"])
def test_identity_needs_full_official_closure(fault):
    args = identity_inputs()
    if fault == "ipo-date":
        args["basic"]["rows"][0][2] = "2026-09-30"
    elif fault == "issuer":
        args["notice"]["orgId"] = "other"
    elif fault == "name":
        args["basic"]["rows"][0][1] = "other"
    elif fault == "currency":
        args["body"] = args["body"].replace("人民币普通股", "")
    elif fault == "venue":
        args["venue"]["mic_code"] = "XSHG"
    elif fault == "received":
        args["notice"]["pdf_received_at"] = "2026-10-15T00:00:00+00:00"
    with pytest.raises(RuntimeError):
        subject.project_identity(**args)


def synthetic_scope(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from pathlib import Path

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import (
        canonical,
        digest,
        file_sha,
        load_contract,
        schemas,
    )
    from quanthus.pipelines.securities_warehouse.base22_sources import _tree

    root = tmp_path / "scope"
    root.mkdir()
    (root / "contract").mkdir()
    for p in Path("config/pipelines").glob("*.json"):
        shutil.copyfile(p, root / "contract" / p.name)
    args = identity_inputs()
    response, _, calendars = inputs()
    venue = args["venue"]
    (root / "inputs").mkdir()
    basic = "inputs/basic.json"
    daily = "inputs/daily.json"
    index = "inputs/index.json"
    pdf = "inputs/doc.pdf"
    (root / basic).write_bytes(canonical(args["basic"]))
    (root / daily).write_bytes(canonical(response))
    (root / pdf).write_bytes(b"synthetic-PDF-only-pdftotext-mocked-in-unit-test")
    args["notice"].update(pdf_sha256=file_sha(root / pdf), adjunctUrl="final/doc.pdf")
    (root / index).write_bytes(
        canonical(
            {
                "announcements": [
                    {
                        "announcementId": "doc",
                        "orgId": "issuer",
                        "secCode": "301716",
                        "adjunctUrl": "final/doc.pdf",
                    }
                ]
            }
        )
    )
    (root / "inputs/map.json").write_bytes(canonical({"stockList": [args["issuer"]]}))
    _, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    for table, records in [("dwd_trading_venue", [venue]), ("dwd_trading_calendar", calendars)]:
        for row in records:
            for field in ss[table]:
                if not field.nullable and field.name not in row:
                    row[field.name] = (
                        NOW
                        if pa.types.is_timestamp(field.type)
                        else date(2026, 9, 29)
                        if pa.types.is_date(field.type)
                        else True
                        if pa.types.is_boolean(field.type)
                        else "synthetic"
                    )
    pq.write_table(
        pa.Table.from_pylist([venue], schema=ss["dwd_trading_venue"]),
        root / "inputs/venues.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(calendars, schema=ss["dwd_trading_calendar"]),
        root / "inputs/calendar.parquet",
    )
    spec = dict(
        code="301716",
        basic=basic,
        daily=daily,
        official_index=index,
        pdf=pdf,
        notice=args["notice"],
        mic="XSHE",
    )
    members = {
        p: {"bytes": (root / p).stat().st_size, "sha256": file_sha(root / p)} for p in _tree(root)
    }
    plan = dict(
        contract_id=subject.CONTRACT_ID,
        business_through="2026-09-30",
        consumer_admission="NOT_GRANTED",
        historical_PIT_admission=False,
        evaluator_sha256=file_sha(Path(subject.__file__)),
        parent_manifest_sha256=SHA,
        observed_at=NOW.isoformat(),
        calendar="inputs/calendar.parquet",
        venues="inputs/venues.parquet",
        issuer_map="inputs/map.json",
        selection=[spec],
        members=members,
        outputs={},
    )
    spec["input_identity"] = digest(
        {
            "code": "301716",
            "observed_at": plan["observed_at"],
            "evaluator_sha256": plan["evaluator_sha256"],
            "parent_manifest_sha256": SHA,
            "sources": {
                k: members[spec[k]]["sha256"] for k in ["basic", "daily", "official_index", "pdf"]
            },
            "parents": {
                k: members[plan[k]]["sha256"] for k in ["calendar", "venues", "issuer_map"]
            },
        }
    )
    args["input_identity"] = spec["input_identity"]
    ids = subject.project_identity(**args)
    rows, _ = subject.project_daily(
        response,
        listing=ids["dwd_listing"][0],
        calendars=calendars,
        source_sha256=file_sha(root / daily),
        observed_at=NOW,
        input_identity=spec["input_identity"],
    )
    (root / "outputs").mkdir()
    for table, values in {**ids, **rows}.items():
        name = "outputs/" + table + ".parquet"
        pq.write_table(pa.Table.from_pylist(values, schema=ss[table]), root / name)
        plan["outputs"][table] = name
        members[name] = dict(bytes=(root / name).stat().st_size, sha256=file_sha(root / name))
    plan["identity"] = digest(plan)
    (root / "INPUTS.json").write_bytes(canonical(plan))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=args["body"].encode()),
    )
    report = subject.replay_scope(root, sealed=False)
    (root / "REPLAY.json").write_bytes(canonical(report))
    (root / "READY").write_bytes(
        canonical(
            dict(
                inputs_sha256=file_sha(root / "INPUTS.json"),
                replay_sha256=file_sha(root / "REPLAY.json"),
            )
        )
    )
    assert subject.replay_scope(root) == report
    return root, plan


def repack(root, plan):
    import json

    from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha

    for name in plan["members"]:
        p = root / name
        if p.is_file():
            plan["members"][name] = dict(bytes=p.stat().st_size, sha256=file_sha(p))
    plan.pop("identity")
    plan["identity"] = digest(plan)
    (root / "INPUTS.json").write_bytes(canonical(plan))
    report = json.loads((root / "REPLAY.json").read_bytes())
    report["input_identity"] = plan["identity"]
    (root / "REPLAY.json").write_bytes(canonical(report))
    (root / "READY").write_bytes(
        canonical(
            dict(
                inputs_sha256=file_sha(root / "INPUTS.json"),
                replay_sha256=file_sha(root / "REPLAY.json"),
            )
        )
    )


@pytest.mark.parametrize(
    "fault",
    ["purpose", "admission", "PIT", "raw-code", "raw-date", "native-byte", "missing", "extra"],
)
def test_cold_local_scope_rejects_repacked_invalid_inputs(tmp_path, monkeypatch, fault):
    import json

    from quanthus.pipelines.securities_warehouse.base22 import canonical

    root, plan = synthetic_scope(tmp_path, monkeypatch)
    if fault == "purpose":
        plan["contract_id"] = "wrong"
    elif fault == "admission":
        plan["consumer_admission"] = "GRANTED"
    elif fault == "PIT":
        plan["historical_PIT_admission"] = True
    elif fault in {"raw-code", "raw-date"}:
        p = root / plan["selection"][0]["daily"]
        raw = json.loads(p.read_bytes())
        raw["rows"][0][1 if fault == "raw-code" else 0] = (
            "sz.301717" if fault == "raw-code" else "2026-09-28"
        )
        p.write_bytes(canonical(raw))
    elif fault == "native-byte":
        p = root / plan["outputs"]["dwd_security_bar"]
        p.write_bytes(b"broken parquet")
    elif fault == "missing":
        (root / plan["outputs"]["dwd_security_bar"]).unlink()
    else:
        (root / "unlisted.dat").write_bytes(b"extra")
    if fault not in {"native-byte", "missing", "extra"}:
        repack(root, plan)
    with pytest.raises((RuntimeError, OSError)):
        subject.replay_scope(root)
