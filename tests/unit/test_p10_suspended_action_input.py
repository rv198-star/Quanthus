"""Suspended action reference carry without any trade, action-date or price rewrite."""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal as Q

import pytest

from quanthus.pipelines.securities_warehouse import p10_suspended_action_input as p
from quanthus.pipelines.securities_warehouse.p10_historical_action_source import ActionFacts


def case():
    days = [date(2017, 5, d) for d in (11, 12, 15, 16)]
    f = ActionFacts(
        p.CODE,
        days[0],
        days[1],
        days[1],
        days[1],
        days[1],
        Q(".1"),
        Q(1),
        datetime(2026, 10, 25, tzinfo=timezone.utc),
        "synthetic",
        "a" * 64,
        "b" * 64,
    )
    bars, limits = [], []
    for i, d in enumerate(days):
        close = Q("10.1") if i == 0 else Q("5")
        bars.append(
            dict(
                security_id=p.CODE,
                date=d,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=100 if i in (0, 3) else 0,
                amount=500 if i in (0, 3) else 0,
            )
        )
        limits.append(
            dict(
                security_id=p.CODE,
                execution_date=d,
                price_reference=Q("10.1"),
                price_reference_kind="prior_trading_close",
                upper_limit_pct=Q(10),
                lower_limit_pct=Q(10),
                upper_limit_price=Q("11.11"),
                lower_limit_price=Q("9.09"),
                observed_tradestatus="1" if i in (0, 3) else "0",
                is_executable=i in (0, 3),
                knowledge_cutoff=f.acquired_at,
                revision="old",
                reference_exact_numerator=None,
                reference_exact_denominator=None,
            )
        )
    actions = [
        dict(
            security_id=p.CODE,
            effective_date=p.EX,
            cash_dividend_per_share=Q(".1"),
            bonus_share_ratio=Q(1),
        )
    ]
    return bars, limits, actions, f


def test_old_guard_false_positive_is_repaired_through_resume_not_new_action():
    bars, limits, actions, f = case()
    assert bars[2]["close"] < limits[2]["lower_limit_price"]
    plan = p.carry_plan(bars, limits, actions, f)
    assert [r["execution_date"] for r in plan] == [date(2017, 5, d) for d in (12, 15, 16)]
    assert all(r["price_reference"] == Q("5") for r in plan)
    assert all(r["lower_limit_price"] <= Q("5") <= r["upper_limit_price"] for r in plan)
    assert actions[0]["effective_date"] == p.EX
    assert [r["is_executable"] for r in limits] == [True, False, False, True]
    assert "close" not in plan[0] and "observed_tradestatus" not in plan[0]


@pytest.mark.parametrize(
    "mode",
    [
        "terms",
        "prior",
        "resume",
        "status",
        "volume",
        "placeholder",
        "reference",
        "duplicate",
        "second-action",
    ],
)
def test_conflicting_or_incomplete_exact_evidence_fails_closed(mode):
    bars, limits, actions, f = case()
    if mode == "terms":
        actions[0]["cash_dividend_per_share"] = Q(".2")
    if mode == "prior":
        limits[0]["observed_tradestatus"] = "0"
    if mode == "resume":
        limits[-1]["observed_tradestatus"] = "0"
    if mode == "status":
        limits[2]["observed_tradestatus"] = None
    if mode == "volume":
        bars[2]["volume"] = 1
    if mode == "placeholder":
        bars[2]["close"] = Q("5.01")
    if mode == "reference":
        limits[2]["price_reference"] = Q("10")
    if mode == "duplicate":
        actions.append(actions[0].copy())
    if mode == "second-action":
        actions.append({**actions[0], "effective_date": date(2017, 5, 15)})
    with pytest.raises(ValueError, match="SUSPENDED_"):
        p.carry_plan(bars, limits, actions, f)


def test_true_tradable_jump_is_still_outside_corrected_bounds():
    bars, limits, actions, f = case()
    bars[-1]["close"] = Q("20")
    plan = p.carry_plan(bars, limits, actions, f)
    assert bars[-1]["close"] > plan[-1]["upper_limit_price"]
    assert limits[-1]["observed_tradestatus"] == "1"


def test_streamed_verifier_checks_all_rows_and_exact_physical_schema(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    bars, limits, actions, f = case()
    plan = p.carry_plan(bars, limits, actions, f)
    # Explicit types mirror the native contract; null exact columns stay strings.
    table = pa.Table.from_pylist(limits)
    for name in ("reference_exact_numerator", "reference_exact_denominator"):
        table = table.set_column(
            table.schema.get_field_index(name), name, pa.array([None] * len(table), pa.string())
        )
    source, target = tmp_path / "source.parquet", tmp_path / "target.parquet"
    pq.write_table(table, source)
    before = source.read_bytes()
    stats = p.rewrite_limits(source, target, plan)
    assert stats["changed_keys"] == 3 and stats["unchanged_rows"] == 1
    assert p.rewrite_limits(source, target, plan, verify_only=True) == stats
    changed = pq.read_table(target).to_pylist()
    changed[0]["is_executable"] = False
    pq.write_table(pa.Table.from_pylist(changed, schema=table.schema), target)
    with pytest.raises(ValueError, match="UNAPPROVED_VALUE_CHANGE"):
        p.rewrite_limits(source, target, plan, verify_only=True)
    assert source.read_bytes() == before


@pytest.mark.parametrize("mode", ["same", "ancestor", "descendant", "link"])
def test_isolation_before_source_open_or_any_mutation(tmp_path, monkeypatch, mode):
    source, official = tmp_path / "source", tmp_path / "official"
    source.mkdir()
    official.mkdir()
    (source / "retained").write_bytes(b"exact source bytes")
    target = {"same": source, "ancestor": tmp_path, "descendant": source / "new"}.get(
        mode, tmp_path / "link"
    )
    if mode == "link":
        target.symlink_to(source, target_is_directory=True)
    monkeypatch.setattr(p.parent, "load_input", lambda *a: pytest.fail("source opened"))
    with pytest.raises(ValueError, match="ISOLATION"):
        p.materialize(source, official, p.OFFICIAL_SHA, target)
    assert (source / "retained").read_bytes() == b"exact source bytes"
    assert sorted(q.name for q in source.iterdir()) == ["retained"]


def test_unknown_official_identity_cannot_open_source(tmp_path, monkeypatch):
    monkeypatch.setattr(p.official, "load_source", lambda *a: pytest.fail("source opened"))
    with pytest.raises(ValueError, match="SOURCE_IDENTITY"):
        p._facts(tmp_path, "a" * 64)


def test_event_date_not_inferred_from_later_bar(tmp_path, monkeypatch):
    _, _, _, f = case()
    monkeypatch.setattr(
        p.official, "load_source", lambda *a: (replace(f, ex_date=date(2017, 5, 15)),)
    )
    with pytest.raises(ValueError, match="SOURCE_TERMS"):
        p._facts(tmp_path, p.OFFICIAL_SHA)


@pytest.mark.parametrize("real_jump", [False, True])
def test_ordinary_reader_guard_keeps_real_jump_after_exact_reference_carry(tmp_path, real_jump):
    from types import SimpleNamespace

    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.backtest.historical_frames import (
        HistoricalFrameReaderError,
        HistoricalReplayFrameReader,
    )

    bars, limits, actions, fact = case()
    if real_jump:
        bars[-1]["close"] = Q("20")
    plan = p.carry_plan(bars, limits, actions, fact)
    patches = {r["execution_date"]: r for r in plan}
    corrected = [{**r, **patches.get(r["execution_date"], {})} for r in limits]
    data = {
        "daily_ohlcv_none": pa.Table.from_pylist(bars),
        "price_limit_execution_daily": pa.Table.from_pylist(corrected),
        "corporate_actions_normalized": pa.Table.from_pylist(actions),
        "corporate_action_exceptions": pa.table(
            {
                "security_id": pa.array([], pa.string()),
                "blocked_from": pa.array([], pa.date32()),
                "blocked_through": pa.array([], pa.date32()),
                "reason_code": pa.array([], pa.string()),
            }
        ),
        "daily_universe": pa.Table.from_pylist(
            [{"security_id": p.CODE, "market_date": b["date"]} for b in bars]
        ),
        "security_code_validity": pa.table({"security_id": [p.CODE], "stock_code": [p.CODE]}),
        "trading_calendar": pa.table({"trade_date": [b["date"] for b in bars]}),
    }
    for name, table in data.items():
        pq.write_table(table, tmp_path / (name + ".parquet"))
    reader = HistoricalReplayFrameReader.__new__(HistoricalReplayFrameReader)
    reader.bundle = SimpleNamespace(partition_paths=lambda n: (tmp_path / (n + ".parquet"),))
    reader._connection = duckdb.connect()
    try:
        # Exercise the ordinary production SQL, without changing or bypassing it.
        reader._create_views()
        assert reader._block_reason(p.CODE, date(2017, 5, 15)) is None
        reason = reader._block_reason(p.CODE, date(2017, 5, 16))
        assert reason == ("unexplained_price_transition" if real_jump else None)
        if real_jump:
            with pytest.raises(HistoricalFrameReaderError, match="unexplained_price_transition"):
                reader._corporate_actions(p.CODE, date(2017, 5, 16), Q("5"))
    finally:
        reader._connection.close()
