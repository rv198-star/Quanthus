from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quanthus.pipelines import lane_runner
from quanthus.pipelines.p10_delivery_common import atomic, encoded
from quanthus.providers.acquisition_evidence import capture
from quanthus.providers.baostock_transport import BaostockAccessDenied
from quanthus.providers.market_raw_reuse import STOCK_FIELDS, load

DAY = date(2026, 10, 14)
STAMP = "2026-10-14T10:33:00+00:00"


def row(day=str(DAY), code="sz.000001", close="1.0"):
    return [
        day,
        code,
        "1",
        "1.1",
        "0.9",
        close,
        "100",
        "100",
        "1",
        "0",
        "1",
        "0",
        "2",
        "1",
        "1",
        "1",
    ]


def fixture_capture(tmp_path, *, rows=None, stamp=STAMP, suffix="first"):
    shadow = tmp_path / "shadow"
    root = shadow / f"attempts/market-daily-core/{DAY}/{suffix}/landing"
    atomic(
        root / "provider_readiness.json",
        {
            "target_date": str(DAY),
            "ready": True,
            "checked_at": "2026-10-14T10:31:00+00:00",
        },
    )
    capture(
        root=root / "original-responses",
        request={
            "provider": "baostock",
            "api": "query_history_k_data_plus",
            "code": "sz.000001",
            "fields": STOCK_FIELDS,
            "start_date": str(DAY),
            "end_date": str(DAY),
            "frequency": "d",
            "adjustflag": "3",
        },
        raw=encoded(
            {
                "fields": STOCK_FIELDS.split(","),
                "rows": rows if rows is not None else [row()],
                "error_code": "0",
                "error_msg": "",
            }
        ),
        started_at="2026-10-14T10:31:30+00:00",
        completed_at=stamp,
        status="0",
    )
    return shadow, root


def replay(tmp_path):
    return load(
        shadow=tmp_path / "shadow",
        day=DAY,
        landing=tmp_path / "new",
        stock_codes={"sz.000001"},
        index_codes=set(),
        universe_digest="sha256:" + "a" * 64,
        converter_sha256="sha256:" + "b" * 64,
    )


def test_exact_original_response_is_reused_with_original_clock(tmp_path):
    _, source = fixture_capture(tmp_path)
    result = replay(tmp_path)
    assert result["sz.000001"]["observed_at"] == STAMP
    assert result["sz.000001"]["rows"] == [row()]
    original = next((source / "original-responses").glob("*.gz"))
    target = tmp_path / "new/original-responses" / original.name
    assert target.read_bytes() == original.read_bytes()
    assert result["sz.000001"]["receipt"]["source_disposition"].endswith("response_reuse")


@pytest.mark.parametrize(
    "rows", [[row(day="2026-10-13")], [row(code="sh.600001")], [row(), row()], []]
)
def test_wrong_date_code_or_ambiguous_response_is_not_reused(tmp_path, rows):
    fixture_capture(tmp_path, rows=rows)
    assert replay(tmp_path) == {}


def test_corrupted_capture_blocks_reuse(tmp_path):
    _, root = fixture_capture(tmp_path)
    path = next((root / "original-responses").glob("*.gz"))
    path.write_bytes(path.read_bytes()[:-3])
    with pytest.raises((ValueError, EOFError)):
        replay(tmp_path)


def test_same_clock_different_rows_are_not_silently_selected(tmp_path):
    fixture_capture(tmp_path)
    fixture_capture(tmp_path, rows=[row(close="1.01")], suffix="second")
    with pytest.raises(ValueError, match="SAME_CLOCK_CONFLICT"):
        replay(tmp_path)


def test_source_not_ready_cannot_supply_replay(tmp_path):
    _, root = fixture_capture(tmp_path)
    atomic(root / "provider_readiness.json", {"target_date": str(DAY), "ready": False})
    assert replay(tmp_path) == {}


def test_supplier_denial_never_reconnects_or_continues(monkeypatch):
    monkeypatch.delenv("HERMES_ODS_PREVENTION_POLICY", raising=False)
    calls = []
    bs = SimpleNamespace(
        query_history_k_data_plus=lambda *a, **kw: (
            calls.append(kw) or SimpleNamespace(error_code="10001011", error_msg="黑名单用户")
        )
    )
    monkeypatch.setattr(lane_runner.time, "sleep", lambda *a: pytest.fail("no reconnect"))
    with pytest.raises(BaostockAccessDenied):
        lane_runner._baostock_query_day_with_retry(
            bs, code="sz.000001", fields=STOCK_FIELDS, day=str(DAY)
        )
    assert len(calls) == 1


def test_denied_login_is_not_retried(monkeypatch):
    calls = []
    bs = SimpleNamespace(
        login=lambda: (
            calls.append(1) or SimpleNamespace(error_code="10001011", error_msg="黑名单用户")
        )
    )
    monkeypatch.setattr(lane_runner.time, "sleep", lambda *a: pytest.fail("no retry"))
    with pytest.raises(BaostockAccessDenied):
        lane_runner._baostock_login_with_retry(bs)
    assert calls == [1]


def test_complete_capture_set_needs_no_supplier_login(tmp_path, monkeypatch):
    import sys

    fixture_capture(tmp_path)
    saved = replay(tmp_path)
    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", "p10-acquisition-prevention-v1")
    monkeypatch.setitem(
        sys.modules,
        "baostock",
        SimpleNamespace(
            login=lambda: pytest.fail("no supplier login"),
            logout=lambda: pytest.fail("no supplier logout"),
        ),
    )
    bars, statuses, logs = lane_runner._baostock_fetch_day(
        ["000001"],
        DAY,
        captured_responses=saved,
    )
    assert len(bars) == len(statuses) == 1
    assert bars[0]["acquired_at"] == STAMP and logs[0]["attempts"] == 0
    metrics = lane_runner._market_acquisition_metrics(logs)
    assert metrics["request_count"] == 0 and metrics["reused_response_count"] == 1


def test_daily_failure_mail_only_requires_daily_resolution(tmp_path, monkeypatch):
    from quanthus.pipelines import p10_complete_daily_notifications as notices

    sent = []
    monkeypatch.setattr(
        notices.mail, "_send_resend", lambda **kw: sent.append(kw) or {"message_id": "synthetic"}
    )
    notices.failure(
        day=DAY,
        stage="daily_cycle",
        state_root=tmp_path / "notes",
        reason="synthetic supplier denial",
    )
    assert sent[0]["context"]["required_intervals"] == ["1d"]
    assert sent[0]["context"]["policy_id"] == "daily-core-v1"


def test_bad_seed_is_not_replayed_instead_of_source_revision(tmp_path, monkeypatch):
    import sys

    fixture_capture(tmp_path, rows=[row(close="")])
    saved = replay(tmp_path)
    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", "p10-acquisition-prevention-v1")
    monkeypatch.setenv("HERMES_ODS_RAW_CAPTURE_ROOT", str(tmp_path / "fresh"))
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "baostock",
        SimpleNamespace(
            login=lambda: calls.append("login") or SimpleNamespace(error_code="0", error_msg=""),
            logout=lambda: None,
        ),
    )
    monkeypatch.setattr(
        lane_runner,
        "_baostock_query_day_with_retry",
        lambda *a, **kw: calls.append("query") or ([row()], "0", "", 1, []),
    )
    bars, status, logs = lane_runner._baostock_fetch_day(
        ["000001"],
        DAY,
        captured_responses=saved,
        missing_set_retry_delay_seconds=0,
    )
    assert calls == ["login", "query"]
    assert len(bars) == len(status) == 1 and len(logs) == 2
    assert logs[0]["response_error_code"] == "invalid_numeric_or_status"
    assert logs[1]["source_disposition"] == "new_request"
    assert saved["sz.000001"]["rows"][0][5] == ""  # Prior evidence retained.


@pytest.mark.parametrize("verified", [False, True])
def test_shared_dependency_denial_is_not_repeated(tmp_path, monkeypatch, verified):
    import runpy
    from pathlib import Path

    from quanthus.providers import security_universe

    ns = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "ops/bin/run-cn01-ods-daily"),
        run_name="synthetic_ops",
    )
    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", "p10-acquisition-prevention-v1")
    log = tmp_path / "failed.log"
    log.write_text("BaostockAccessDenied: BAOSTOCK_ACCESS_DENIED: 10001011")
    state = {"lanes": {"market-daily-core": {"status": "failed", "logs": [str(log)]}}}
    if verified:
        atomic(tmp_path / f"source-universe-v1/{DAY}/universe.json", {})
        monkeypatch.setattr(security_universe, "verify", lambda root, day: {})
    result = ns["_denied_shared_universe"](state, home=tmp_path, market_date=DAY)
    assert (result is None) == verified
    if result:
        assert result["source_request_repeated"] is False


def test_daily_phase_is_persisted_before_supplier_call(tmp_path, monkeypatch):
    import json

    from quanthus.pipelines import daily_core_cycle as cycle

    monkeypatch.setenv("QUANTHUS_DWD_DAILY_CORE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        cycle, "_verified_pointer", lambda *a, **kw: cycle.BASELINE - timedelta(days=1)
    )

    def unavailable(*a):
        state = json.loads((tmp_path / "operations/LATEST.json").read_text())
        assert state["phase"] == "calendar" and state["status"] == "running"
        raise RuntimeError("BAOSTOCK_ACCESS_DENIED: 10001011")

    monkeypatch.setattr(cycle, "fetch_baostock_calendar", unavailable)
    assert cycle.main(["--through-date", "2026-10-14"]) == 2
    assert json.loads((tmp_path / "operations/LATEST.json").read_text())["phase"] == "calendar"


def test_daily_failure_html_shows_product_and_phase():
    from quanthus import warehouse_notifications as mail

    html = mail._email_html(
        event="p10_cn01_daily_cycle_failed",
        market_date=DAY,
        pointer=None,
        result=None,
        error="BAOSTOCK_ACCESS_DENIED",
        context={
            "failed_phase": "calendar",
            "cycle_status": "blocked_required_core_evidence",
            "product_scope": "daily-core-v1",
            "required_intervals": ["1d"],
        },
    )
    assert "失败步骤" in html and "calendar" in html
    assert "daily-core-v1" in html and "blocked_required_core_evidence" in html
    assert "1m" not in html and "5m" not in html
