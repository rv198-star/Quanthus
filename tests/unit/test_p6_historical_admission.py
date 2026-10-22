from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.forward_validation import historical_admission as history
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value
from quanthus.pipelines.securities_warehouse.base22 import file_sha

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "config/forward-validation/runtime/p6-historical-replay-plan.v1.json"
TZ = ZoneInfo("Asia/Shanghai")


def config():
    return json.loads(PLAN.read_bytes())


def synthetic_calendar():
    # These are explicitly synthetic weekdays, never a claimed market calendar.
    days = []
    d = date(2026, 1, 5)
    while len(days) < 190:
        if d.weekday() < 5:
            days.append(str(d))
        d += timedelta(days=1)
    rows = [
        {
            "trade_date": date.fromisoformat(d),
            "exchange": venue,
            "is_open": 1,
            "close_at": datetime.fromisoformat(d + "T15:00:00+08:00"),
        }
        for d in days
        for venue in ["SSE", "SZSE"]
    ]
    p = config()
    p["source"]["open_sessions_sha256"] = digest_value(days)
    p["scope"].update(start=days[0], end=days[-1])
    for name, selected in [
        ("warmup", days[:120]),
        ("decisions", days[120:-20]),
        ("settlement_tail", days[-20:]),
        ("smoke", days[120:125]),
    ]:
        p[name] = {"start": selected[0], "end": selected[-1], "sessions": len(selected)}
    p["report_slices"] = [
        {"id": name, "start": selected[0], "end": selected[-1], "sessions": len(selected)}
        for name, selected in zip(
            ["S1", "S2", "S3"], [days[120:137], days[137:154], days[154:170]], strict=True
        )
    ]
    return p, rows


def test_frozen_dates_use_one_shared_warmup_and_do_not_reuse_prospective_budget():
    p = config()
    assert p["warmup"] == {"start": "2026-01-05", "end": "2026-07-06", "sessions": 120}
    assert p["decisions"] == {"start": "2026-07-07", "end": "2026-09-14", "sessions": 50}
    assert p["settlement_tail"]["sessions"] == 20
    assert [v["sessions"] for v in p["report_slices"]] == [17, 17, 16]
    assert p["smoke"]["start"] == p["decisions"]["start"]
    assert p["semantics"]["slice_ledger_reset"] is False
    assert p["semantics"]["slice_independence_claim"] is False
    assert not any(p["authority"].values())


def test_exact_two_venue_calendar_partitions_once_and_smoke_is_prefix():
    p, rows = synthetic_calendar()
    bound = history.bind_calendar(p, rows)
    assert len(bound["all_sessions"]) == 190
    assert len(bound["warmup"]) == 120
    assert len(bound["decisions"]) == 50
    assert len(bound["settlement_tail"]) == 20
    assert bound["smoke"] == bound["decisions"][:5]
    assert list(bound["report_slices"].values()) == [
        bound["decisions"][:17],
        bound["decisions"][17:34],
        bound["decisions"][34:],
    ]


def test_missing_session_close_keeps_published_dates_without_inventing_a_clock():
    p, rows = synthetic_calendar()
    rows[240]["close_at"] = None
    rows[241]["close_at"] = None
    bound = history.bind_calendar(p, rows)
    assert len(bound["all_sessions"]) == 190
    assert bound["open_dates_with_unknown_close"] == [
        {"market_date": str(rows[240]["trade_date"]), "venue": "SSE"},
        {"market_date": str(rows[241]["trade_date"]), "venue": "SZSE"},
    ]
    assert rows[240]["close_at"] is rows[241]["close_at"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        "venue_missing",
        "duplicate",
        "wrong_close_day",
        "wrong_close_time",
        "wrong_digest",
        "slice_overlap",
        "slice_gap",
        "smoke_late",
        "warmup_short",
        "blind",
        "reset",
        "independence",
        "budget_write",
        "unknown_allowed",
    ],
)
def test_scope_calendar_or_authority_mutation_is_rejected(mutation):
    p, rows = synthetic_calendar()
    if mutation == "venue_missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "wrong_close_day":
        rows[0]["close_at"] += timedelta(days=1)
    elif mutation == "wrong_close_time":
        rows[0]["close_at"] += timedelta(minutes=1)
    elif mutation == "wrong_digest":
        p["source"]["open_sessions_sha256"] = "sha256:" + "0" * 64
    elif mutation == "slice_overlap":
        p["report_slices"][1] = copy.deepcopy(p["report_slices"][0])
        p["report_slices"][1]["id"] = "S2"
    elif mutation == "slice_gap":
        p["report_slices"][1]["sessions"] -= 1
    elif mutation == "smoke_late":
        p["smoke"] = {"start": p["decisions"]["end"], "end": p["decisions"]["end"], "sessions": 1}
    elif mutation == "warmup_short":
        p["warmup"]["sessions"] -= 1
    elif mutation == "blind":
        p["scope"]["start"] = "2025-01-01"
    elif mutation == "reset":
        p["semantics"]["slice_ledger_reset"] = True
    elif mutation == "independence":
        p["semantics"]["slice_independence_claim"] = True
    elif mutation == "budget_write":
        p["authority"]["prospective_budget_write"] = True
    else:
        p["semantics"]["unknown_knowledge_time"] = "infer_from_date"
    with pytest.raises(ForwardValidationContractError):
        history.bind_calendar(p, rows)


def source_fixture(tmp_path, *, clock="late"):
    p, cal = synthetic_calendar()
    root = tmp_path / "source"
    (root / "source/tables").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(cal), root / p["source"]["calendar_member"])
    bars = []
    for r in cal:
        day, close = r["trade_date"], r["close_at"]
        k = close - timedelta(seconds=1) if clock == "known" else close + timedelta(days=200)
        bars.append(
            {
                "date": day,
                "dwd_listing_id": "id-" + r["exchange"],
                "identity_exchange": r["exchange"],
                "dwd_bar_knowledge_from": None if clock == "unknown" else k,
                "dwd_bar_knowledge_to": k + timedelta(days=1) if clock == "expired" else None,
                "dwd_security_knowledge_from": k,
                "dwd_listing_knowledge_from": k,
                "dwd_state_knowledge_from": k,
            }
        )
    if clock == "expired":
        for b in bars:
            b["dwd_bar_knowledge_from"] = datetime(2026, 1, 1, tzinfo=TZ)
            b["dwd_bar_knowledge_to"] = datetime(2026, 1, 2, tzinfo=TZ)
    schema = pa.schema(
        [
            ("date", pa.date32()),
            ("dwd_listing_id", pa.string()),
            ("identity_exchange", pa.string()),
            *[
                (n, pa.timestamp("us", tz="UTC"))
                for n in [
                    "dwd_bar_knowledge_from",
                    "dwd_bar_knowledge_to",
                    "dwd_security_knowledge_from",
                    "dwd_listing_knowledge_from",
                    "dwd_state_knowledge_from",
                ]
            ],
        ]
    )
    pq.write_table(
        pa.Table.from_pylist(bars, schema=schema), root / "source/tables/daily_ohlcv_none.parquet"
    )
    (root / "assessment.json").write_text('{"historical_pit_granted": false}\n')
    files = {
        str(path.relative_to(root)): {"bytes": path.stat().st_size, "sha256": file_sha(path)}
        for path in root.rglob("*")
        if path.is_file()
    }
    m = {"schema_version": p["source"]["schema_version"], "files": files}
    (root / "manifest.json").write_bytes(canonical_json_bytes(m) + b"\n")
    sha = file_sha(root / "manifest.json")
    (root / "READY").write_text(sha + "\n")
    p["source"]["manifest_sha256"] = sha
    p["source"]["calendar_sha256"] = file_sha(root / p["source"]["calendar_member"])
    plan = tmp_path / "contract" / "plan.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps(p))
    return root, plan


@pytest.mark.parametrize(
    "clock,necessary", [("known", 70), ("late", 0), ("unknown", 0), ("expired", 0)]
)
def test_real_bounded_admission_preserves_clock_and_never_reports_empty_replay_pass(
    tmp_path, clock, necessary
):
    root, plan = source_fixture(tmp_path, clock=clock)
    before = {str(f): f.read_bytes() for f in root.rglob("*") if f.is_file()}
    output = tmp_path / "new-job"
    result = history.run_admission(
        plan_path=plan, source=root, output=output, code_revision="a" * 40, repository_root=ROOT
    )
    assert result["status"] == "BLOCKED"
    assert result["strategy_replay_executed"] is False
    assert result["days_with_necessary_historical_clocks"] == necessary
    assert all(
        d["bar_visible_at_close"] == (2 if clock == "known" else 0)
        for d in result["daily_admission"]
    )
    assert "P10_HISTORICAL_REPLAY_PURPOSE_NOT_GRANTED" in result["reason_codes"]
    assert result["strategy_decisions_created"] == result["paper_orders_created"] == 0
    assert result["existing_prospective_activation_required"] is False
    assert len(result["smoke"]) == 5
    assert len(result["daily_admission"]) == 70
    assert (output / "ASSESSMENT_COMPLETE").read_text().strip() == file_sha(output / "result.json")
    assert not (output / "READY").exists()
    assert before == {str(f): f.read_bytes() for f in root.rglob("*") if f.is_file()}
    with pytest.raises(ForwardValidationContractError, match="output_exists"):
        history.run_admission(
            plan_path=plan, source=root, output=output, code_revision="a" * 40, repository_root=ROOT
        )


@pytest.mark.parametrize(
    "mutation", ["corrupt", "extra", "missing", "calendar_hash", "policy_hash"]
)
def test_frozen_source_or_policy_drift_refused_before_output_creation(tmp_path, mutation):
    root, plan = source_fixture(tmp_path)
    if mutation == "corrupt":
        (root / "assessment.json").write_text("broken")
    elif mutation == "extra":
        (root / "extra").write_text("extra")
    elif mutation == "missing":
        (root / "assessment.json").unlink()
    else:
        p = json.loads(plan.read_bytes())
        if mutation == "calendar_hash":
            p["source"]["calendar_sha256"] = "sha256:" + "f" * 64
        else:
            p["policy_reference"]["sha256"] = "sha256:" + "f" * 64
        plan.write_text(json.dumps(p))
    output = tmp_path / "never-created"
    with pytest.raises(ForwardValidationContractError):
        history.run_admission(
            plan_path=plan, source=root, output=output, code_revision="a" * 40, repository_root=ROOT
        )
    assert not output.exists()


def test_plan_mutation_during_query_cannot_seal_an_assessment(tmp_path, monkeypatch):
    root, plan = source_fixture(tmp_path)
    unchanged = history.check_unchanged
    calls = 0

    def change_plan(source, before):
        nonlocal calls
        calls += 1
        unchanged(source, before)
        if calls == 2:
            plan.write_text(plan.read_text() + "\n")

    monkeypatch.setattr(history, "check_unchanged", change_plan)
    output = tmp_path / "interrupted"
    with pytest.raises(ForwardValidationContractError, match="plan_changed"):
        history.run_admission(
            plan_path=plan, source=root, output=output, code_revision="a" * 40, repository_root=ROOT
        )
    assert not (output / "ASSESSMENT_COMPLETE").exists()


def test_output_inside_source_rejected_without_touching_source(tmp_path):
    from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError

    root, plan = source_fixture(tmp_path)
    before = {str(f): f.read_bytes() for f in root.rglob("*") if f.is_file()}
    with pytest.raises(SourceAssessmentError, match="OVERLAP"):
        history.run_admission(
            plan_path=plan,
            source=root,
            output=root / "output",
            code_revision="a" * 40,
            repository_root=ROOT,
        )
    assert before == {str(f): f.read_bytes() for f in root.rglob("*") if f.is_file()}


def test_unknown_calendar_close_blocks_only_its_rows_and_is_never_filled(tmp_path):
    root, plan_path = source_fixture(tmp_path, clock="known")
    p = json.loads(plan_path.read_bytes())
    calendar = root / p["source"]["calendar_member"]
    table = pq.read_table(calendar)
    rows = table.to_pylist()
    first = p["decisions"]["start"]
    for r in rows:
        if str(r["trade_date"]) == first:
            r["close_at"] = None
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), calendar)
    m = json.loads((root / "manifest.json").read_bytes())
    rel = p["source"]["calendar_member"]
    m["files"][rel] = {"bytes": calendar.stat().st_size, "sha256": file_sha(calendar)}
    (root / "manifest.json").write_bytes(canonical_json_bytes(m) + b"\n")
    p["source"]["manifest_sha256"] = file_sha(root / "manifest.json")
    p["source"]["calendar_sha256"] = file_sha(calendar)
    (root / "READY").write_text(p["source"]["manifest_sha256"] + "\n")
    plan_path.write_text(json.dumps(p))
    before = calendar.read_bytes()
    result = history.run_admission(
        plan_path=plan_path,
        source=root,
        output=tmp_path / "scoped-hold",
        code_revision="a" * 40,
        repository_root=ROOT,
    )
    assert result["status"] == "BLOCKED"
    assert result["days_with_necessary_historical_clocks"] == 69
    assert result["smoke"][0]["calendar_close_unknown_rows"] == 2
    assert result["smoke"][0]["bar_visible_at_close"] == 0
    assert all(r["calendar_close_unknown_rows"] == 0 for r in result["daily_admission"][1:])
    assert calendar.read_bytes() == before
