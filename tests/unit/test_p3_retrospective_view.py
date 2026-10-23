"""The weaker input purpose cannot silently become strict P3/P4/P2 admission."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.expert_strategy import p3_base24_bridge as bridge
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_retrospective_bridge as retro_bridge
from quanthus.expert_strategy import p3_retrospective_view as reader
from tests.unit.test_p3_base24_native import CLOCK, TZ, mutate, seal
from tests.unit.test_p3_retrospective_input import build
from tests.unit.test_p3_retrospective_input import source as source_fixture


@pytest.fixture
def source(tmp_path):
    return source_fixture.__wrapped__(tmp_path)


def warehouse_security(source, currency):
    mutate(source, "dwd_security", lambda rows: rows[0].update(instrument_type="common_equity"))
    if currency is not None:
        path = source / "native/dwd_security.parquet"
        table = pq.read_table(path).append_column(
            "instrument_currency", pa.array([currency], type=pa.string())
        )
        pq.write_table(table, path)
        seal(source)


def test_actual_warehouse_common_equity_is_selected_without_rewriting_it(source, tmp_path):
    warehouse_security(source, "CNY")
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        rows = [
            row
            for b in view.history_batches(business_read_through=CLOCK.date())
            for row in b.to_pylist()
        ]
    assert len(rows) == 1 and rows[0]["instrument_type"] == "common_equity"


def test_canonical_currency_survives_reader_to_business_port(source, tmp_path):
    warehouse_security(source, "CNY")
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        mutate(source, table, lambda rows: rows[0].update(
            observation_start=CLOCK.replace(hour=0),
            observation_end=(CLOCK + timedelta(days=1)).replace(hour=0),
        ))
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        rows = [
            row
            for b in view.history_batches(business_read_through=CLOCK.date())
            for row in b.to_pylist()
        ]
        assert rows[0]["instrument_currency"] == "CNY"
        port = retro_bridge.decision_port(
            rows[0],
            information_cutoff=view.information_cutoff,
            source_manifest_sha256=result["manifest_sha256"],
            support=None,
            static_industry_id=None,
        )
    assert port.selected is not None
    assert "EXECUTION_QUALIFICATION_UNAVAILABLE" in port.unavailable_reasons


def test_unknown_warehouse_currency_is_not_inferred_or_silently_dropped(source, tmp_path):
    warehouse_security(source, None)
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with (
        reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view,
        pytest.raises(native.InputError, match="ASOF_IDENTITY_UNAVAILABLE"),
    ):
        list(view.history_batches(business_read_through=CLOCK.date()))


def test_foreign_currency_common_equity_does_not_satisfy_nonempty_A_share_gate(source, tmp_path):
    warehouse_security(source, "USD")
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        report = reader.input_census(view, CLOCK.date())
    assert report["joined_rows"] == 0
    assert report["status"] == "BLOCKED_EMPTY_REQUIRED_UNIVERSE"


@pytest.mark.parametrize("opened", [False, True, None])
def test_absent_session_clocks_are_classified_without_inventing_times(source, tmp_path, opened):
    def change(rows):
        for r in rows:
            if r["calendar_session_id"] == "sz-2025-01-02":
                r.update(is_open=opened, open_at=None, close_at=None)

    mutate(source, "dwd_trading_calendar", change)
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        report = reader.input_census(view, CLOCK.date())
    assert report["joined_rows"] == 1 and report["open_session_observations"] == 0
    assert report["calendar_closed_observations"] == int(opened is False)
    assert report["calendar_unavailable_observations"] == int(opened is None)
    assert report["open_session_invalid_or_missing_clocks"] == int(opened is True)
    assert report["status"] == "BLOCKED_NO_OPEN_SESSION_OBSERVATIONS"
    assert report["original_B_accepted"] is report["D_activated"] is False


def test_two_clocks_are_preserved_and_strict_bridge_still_rejects(source, tmp_path):
    late = datetime(2026, 10, 1, tzinfo=TZ)
    mutate(source, "dwd_security_bar", lambda rows: rows[0].update(knowledge_from=late))
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        values = [
            row
            for b in view.history_batches(business_read_through=CLOCK.date())
            for row in b.to_pylist()
        ]
        assert len(values) == 1
        row = values[0]
        assert row["bar_knowledge_from"] == late
        assert row["close"] == Decimal("1.1000000001")
        assert row["r2_business_read_through"] == CLOCK.date()
        assert row["r2_information_cutoff"] > CLOCK
        assert row["r2_observation_role"] == "DECISION_HISTORY"
        assert view.prepared_manifest is None
        with pytest.raises(native.InputError, match="BRIDGE_VERIFIED_PREPARED_INPUT_REQUIRED"):
            list(
                bridge.ports_for_dates(
                    view, cutoff=CLOCK, dates=[CLOCK.date()], supports={}, static_industry={}
                )
            )
        with pytest.raises(native.InputError, match="BRIDGE_NOT_VISIBLE"):
            bridge.decision_port(
                row,
                cutoff=CLOCK,
                source_manifest_sha256="sha256:" + "a" * 64,
                support=None,
                static_industry_id=None,
            )


def test_business_boundary_excludes_later_observations(source, tmp_path):
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        assert (
            sum(
                len(b)
                for b in view.history_batches(
                    business_read_through=CLOCK.date() - timedelta(days=1)
                )
            )
            == 0
        )


def test_reused_scope_is_rebuilt_for_each_explicit_business_boundary(source, tmp_path):
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        counts = [
            sum(len(b) for b in view.history_batches(business_read_through=boundary))
            for boundary in (
                CLOCK.date() - timedelta(days=1),
                CLOCK.date(),
                CLOCK.date() - timedelta(days=1),
            )
        ]
    # A later read cannot leak observations into an earlier feature boundary.
    assert counts == [0, 1, 0]


def test_partitioned_read_covers_warmup_and_month_boundary_once_without_retiming(source, tmp_path):
    def add(rows):
        for day in (datetime(2024, 12, 31, tzinfo=TZ), datetime(2025, 2, 3, tzinfo=TZ)):
            rows.append(
                dict(
                    rows[0],
                    calendar_session_id=f"sz-{day.date()}",
                    observation_start=day,
                    observation_end=day + timedelta(hours=15),
                )
            )

    mutate(source, "dwd_security_bar", add)
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        rows = [
            r
            for b in view.history_batches(business_read_through=date(2025, 2, 3))
            for r in b.to_pylist()
        ]
    assert [r["trade_date"].isoformat() for r in rows] == ["2024-12-31", "2025-01-02", "2025-02-03"]
    assert [r["r2_observation_role"] for r in rows] == [
        "WARMUP",
        "DECISION_HISTORY",
        "DECISION_HISTORY",
    ]
    assert all(
        r["close"] == Decimal("1.1000000001") and r["bar_knowledge_from"] == CLOCK for r in rows
    )


@pytest.mark.parametrize("boundary", [datetime(2025, 1, 2), date(2024, 1, 1)])
def test_invalid_partition_boundary_cannot_silently_become_empty(source, tmp_path, boundary):
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with (
        reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view,
        pytest.raises(
            native.InputError, match="RETRO_BUSINESS_DATE_REQUIRED|FROZEN_DECISION_WINDOW"
        ),
    ):
        list(view.history_batches(business_read_through=boundary))


def test_unknown_clock_stays_unavailable(source, tmp_path):
    mutate(source, "dwd_security_bar", lambda rows: rows[0].update(knowledge_from=None))
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        report = reader.input_census(view, CLOCK.date())
        assert report["joined_rows"] == 0
        assert report["original_B_accepted"] is report["D_activated"] is False


def test_changed_metadata_fails_during_read(source, tmp_path):
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        p = out / "plan.json"
        value = json.loads(p.read_text())
        value["information_cutoff"] = "2026-11-01T00:00:00+08:00"
        p.write_text(json.dumps(value))
        with pytest.raises(native.InputError, match="RETRO_INPUT_CHANGED_DURING_READ"):
            list(view.history_batches(business_read_through=CLOCK.date()))


def test_execution_tail_cannot_be_mislabelled_as_decision_history(source, tmp_path):
    def tail(rows):
        rows[0].update(
            calendar_session_id="sz-2026-09-18",
            observation_start=datetime(2026, 9, 18, tzinfo=TZ),
            observation_end=datetime(2026, 9, 19, tzinfo=TZ),
        )

    mutate(source, "dwd_security_bar", tail)
    mutate(source, "dwd_tradability_state", tail)
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        report = reader.input_census(view, date(2026, 9, 18))
        assert report["observation_roles"] == {"EXECUTION_ONLY": 1}
        with pytest.raises(native.InputError, match="FROZEN_DECISION_WINDOW"):
            list(view.history_batches(business_read_through=date(2026, 9, 19)))


def test_daily_half_open_interval_is_not_a_knowledge_cutoff_or_next_day_observation(
    source, tmp_path
):
    def change(rows):
        rows[0]["observation_end"] = datetime(2025, 1, 3, tzinfo=TZ)
        rows.append(
            dict(
                rows[0],
                calendar_session_id="sz-2025-01-03",
                observation_start=datetime(2025, 1, 3, tzinfo=TZ),
                observation_end=datetime(2025, 1, 4, tzinfo=TZ),
            )
        )

    mutate(source, "dwd_security_bar", change)
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        rows = [
            r
            for b in view.history_batches(business_read_through=CLOCK.date())
            for r in b.to_pylist()
        ]
    assert len(rows) == 1 and rows[0]["trade_date"] == CLOCK.date()
    assert rows[0]["observation_end"] == datetime(2025, 1, 3, tzinfo=TZ)
    assert rows[0]["r2_interval_upper_bound"] == rows[0]["observation_end"]
    assert rows[0]["bar_knowledge_from"] == CLOCK
    assert rows[0]["r2_information_cutoff"] > rows[0]["r2_interval_upper_bound"]


def test_full_scope_census_blocks_a_missing_required_execution_tail(source, tmp_path):
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        report = reader.input_census(view, date(2026, 9, 18))
    assert report["joined_rows"] == 1
    assert report["status"] == "BLOCKED_REQUIRED_EXECUTION_TAIL"
    assert report["D_activated"] is False


def test_reading_does_not_grant_support_or_publish_price_values(source, tmp_path):
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with reader.RetrospectiveView(out, manifest_sha256=result["manifest_sha256"]) as view:
        report = reader.input_census(view, CLOCK.date())
    assert report["can_buy_unknown"] == 1
    assert report["candidate_feature_execution_qualification"] == "NOT_GRANTED_BY_READING"
    assert report["strategy_or_economic_results_read"] is False
    assert "1.1000000001" not in json.dumps(report)
