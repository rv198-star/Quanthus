"""Synthetic complete-order reduction with residual lot and later full exit."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.platform.adapters.product_daily_cycle_sqlite import build_funding_bridge_service
from quanthus.product_daily.application.funding_bridge import (
    LateFeeFact,
    ReductionSellProfile,
    SellExecutionReport,
    SellProfile,
    SyntheticTemporalProfile,
)
from quanthus.shared.fact_contracts import FactError, digest
from tests.unit.test_product_daily_u05_two_cycle import (
    AUTH,
    baseline,
    buy_profile,
    buy_report,
    candidate,
    context,
    entry_intent,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads(
    (ROOT / "config/research/r11/continuation-020/reduction-profile.v1.json").read_text(
        encoding="utf-8"
    )
)
REDUCE_CONFIG = CONFIG["profile"]
TEMPORAL_CONFIG = CONFIG["temporal"]
TEMPORAL = SyntheticTemporalProfile(
    TEMPORAL_CONFIG["version"],
    tuple(datetime.fromisoformat(item["received_at"]) for item in TEMPORAL_CONFIG["events"]),
    tuple(item["role"] for item in TEMPORAL_CONFIG["events"]),
    tuple(datetime.fromisoformat(item["occurred_at"]) for item in TEMPORAL_CONFIG["events"]),
    tuple(datetime.fromisoformat(item["decision_at"]) for item in TEMPORAL_CONFIG["events"]),
)
S01 = datetime(2026, 11, 17, 16, tzinfo=timezone.utc)
S02 = datetime(2026, 11, 18, 1, tzinfo=timezone.utc)
S03 = datetime(2026, 11, 19, 1, tzinfo=timezone.utc)
S04 = datetime(2026, 11, 19, 2, tzinfo=timezone.utc)
S05 = datetime(2026, 11, 19, 3, tzinfo=timezone.utc)
S06 = datetime(2026, 11, 20, tzinfo=timezone.utc)


def reduction_profile(source_sequence: str) -> ReductionSellProfile:
    del source_sequence
    return ReductionSellProfile(
        REDUCE_CONFIG["profile_version"],
        REDUCE_CONFIG["source_id"],
        REDUCE_CONFIG["order_total_quantity"],
        REDUCE_CONFIG["final_cumulative_fill_quantity"],
        REDUCE_CONFIG["cancelled_quantity"],
        Decimal(REDUCE_CONFIG["confirmed_fee"]),
        REDUCE_CONFIG["terminal"],
    )


def exit_profile() -> SellProfile:
    return SellProfile(
        "single-fill-terminal-v1",
        "fixture:two-cycle:broker",
        300,
        300,
        0,
        Decimal("5"),
        True,
    )


def sell(
    report_id: str,
    cycle_id: str,
    quantity: int,
    price: str,
    at: datetime,
    source_sequence: str,
    *,
    source_id: str = "fixture:two-cycle:broker",
    fee: str = "5",
) -> SellExecutionReport:
    return SellExecutionReport(
        report_id,
        "SYN001",
        cycle_id,
        quantity,
        Decimal(price),
        Decimal(fee),
        at,
        source_id,
        source_sequence,
    )


def service_with_buy(path: Path, *, temporal: bool = True):
    service = build_funding_bridge_service(
        path,
        context=context(),
        authorization=AUTH,
        temporal_profile=TEMPORAL if temporal else None,
    )
    service.establish_baseline(baseline())
    intent = entry_intent(
        service,
        candidate_entry=candidate("OP-REDUCE", "SYN001"),
        command_id="reduce-entry",
        expected_revision=0,
        at=S01,
    )
    entry = service.enter(context(S01), intent)
    cycle_id = entry.obligation.cycle_id if entry.obligation is not None else None
    assert entry.accepted and cycle_id
    service.submit_buy(
        context(S02),
        buy_report("reduce-buy", "SYN001", at=S02, source_sequence="reduce-buy"),
        buy_profile("reduce-buy"),
    )
    return service, cycle_id


def test_complete_order_reduction_residual_lot_exit_settle_and_cold_rebuild(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reduction.sqlite"
    service, cycle_id = service_with_buy(path)
    bought = service.current(context(S02))
    reduced = service.submit_sell(
        context(S03),
        sell("reduce-200", cycle_id, 200, "12", S03, "reduce-200"),
        reduction_profile("reduce-200"),
    )
    assert reduced.account.cash == Decimal("18390.00")
    assert reduced.account.cash == reduced.funding.cash_total
    assert reduced.funding.bucket("U1").cash == Decimal("17390.00")
    assert reduced.account.quantity("SYN001") == 300
    assert reduced.funding.reserve == Decimal("1000.00")
    assert reduced.funding.bucket("U1").locked_cycle_budget == Decimal("20000.00")
    assert {item.account: item.amount for item in reduced.postings} == {
        "ASSET_CASH": Decimal("2395.00"),
        "ASSET_POSITION": Decimal("-2000.00"),
        "REALIZED_PNL": Decimal("-400.00"),
        "EXPENSE_CONFIRMED_FEE": Decimal("5.00"),
    }
    lots = service._port.lots(context(S03))  # type: ignore[attr-defined]
    assert len(lots) == 1
    assert lots[0].remaining_quantity == 300
    assert lots[0].remaining_cost == Decimal("3000.00")
    assert lots[0].cost_basis == Decimal("5000.00")

    exited = service.submit_sell(
        context(S04),
        sell("exit-300", cycle_id, 300, "9", S04, "exit-300"),
        exit_profile(),
    )
    assert exited.account.cash == Decimal("21085.00")
    assert exited.account.cash == exited.funding.cash_total
    assert exited.funding.bucket("U1").cash == Decimal("20085.00")
    assert exited.account.quantity("SYN001") == 0
    assert {item.account: item.amount for item in exited.postings} == {
        "ASSET_CASH": Decimal("2695.00"),
        "ASSET_POSITION": Decimal("-3000.00"),
        "REALIZED_PNL": Decimal("300.00"),
        "EXPENSE_CONFIRMED_FEE": Decimal("5.00"),
    }
    settled = service.settle_cycle(context(S05), settlement_id="reduce-settle", cycle_id=cycle_id)
    assert settled.funding.bucket("U1").cash == Decimal("20000.00")
    assert settled.funding.reserve == Decimal("1085.00")
    late = service.apply_late_fee(
        context(S06),
        LateFeeFact(
            "reduce-late",
            "U1",
            cycle_id,
            Decimal("-100"),
            "fixture:reduce-late",
            "reduce-late",
            datetime(2026, 11, 18, 16, tzinfo=timezone.utc),
        ),
    )
    assert late.account.cash == Decimal("20985.00")
    assert late.funding.reserve == Decimal("985.00")
    assert late.funding.bucket("U1").cash == Decimal("20000.00")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0] == 6
        assert db.execute(
            "SELECT sum(CAST(amount AS REAL)) FROM bridge_postings WHERE account='ASSET_POSITION'"
        ).fetchone()[0] == pytest.approx(0.0)
        assert db.execute("SELECT remaining_quantity FROM bridge_lots").fetchone()[0] == 0
        assert db.execute("SELECT remaining_cost FROM bridge_lots").fetchone()[0] == "0.00"
        db.execute("DELETE FROM bridge_obligations")
        db.execute("DELETE FROM bridge_lots")
    reopened = build_funding_bridge_service(
        path, context=context(S06), authorization=AUTH, temporal_profile=TEMPORAL
    )
    rebuilt = reopened.rebuild(context(S06))
    assert rebuilt == reopened.current(context(S06))
    output = os.environ.get("R11_REDUCTION_ACTUALS")
    if output:
        Path(output).write_text(
            json.dumps(
                {
                    "buy": {
                        "cash": str(bought.account.cash),
                        "bucket": str(bought.funding.bucket("U1").cash),
                        "quantity": bought.account.quantity("SYN001"),
                    },
                    "reduce": {
                        "cash": str(reduced.account.cash),
                        "bucket": str(reduced.funding.bucket("U1").cash),
                        "remaining_quantity": reduced.account.quantity("SYN001"),
                        "remaining_cost": str(lots[0].remaining_cost),
                    },
                    "exit": {
                        "cash": str(exited.account.cash),
                        "bucket": str(exited.funding.bucket("U1").cash),
                        "quantity": exited.account.quantity("SYN001"),
                    },
                    "settle": {
                        "cash": str(settled.funding.cash_total),
                        "bucket": str(settled.funding.bucket("U1").cash),
                        "reserve": str(settled.funding.reserve),
                    },
                    "late_fee": {
                        "cash": str(late.account.cash),
                        "bucket": str(late.funding.bucket("U1").cash),
                        "reserve": str(late.funding.reserve),
                    },
                    "journal_count": 6,
                    "cold_rebuild_equal": True,
                },
                indent=2,
                sort_keys=True,
            )
            + chr(10)
        )


def test_reduction_settlement_is_refused_while_residual_position_remains(
    tmp_path: Path,
) -> None:
    service, cycle_id = service_with_buy(tmp_path / "held.sqlite", temporal=False)
    service.submit_sell(
        context(S03),
        sell("held-reduce", cycle_id, 200, "12", S03, "held-reduce"),
        reduction_profile("held-reduce"),
    )
    before = service.current(context(S03))
    with pytest.raises(FactError, match="SETTLEMENT_NOT_READY"):
        service.settle_cycle(context(S03), settlement_id="held-settle", cycle_id=cycle_id)
    assert service.current(context(S03)) == before


def test_reduction_fault_rolls_back_and_retry_posts_once(tmp_path: Path) -> None:
    fired: list[str] = []

    def fault(stage: str) -> None:
        if stage == "after_sell_funding_before_account" and not fired:
            fired.append(stage)
            raise RuntimeError("reduction fault")

    path = tmp_path / "fault.sqlite"
    service = build_funding_bridge_service(
        path,
        context=context(),
        authorization=AUTH,
        temporal_profile=TEMPORAL,
        fault_hook=fault,
    )
    service.establish_baseline(baseline())
    intent = entry_intent(
        service,
        candidate_entry=candidate("OP-REDUCE-FAULT", "SYN001"),
        command_id="fault-entry",
        expected_revision=0,
        at=S01,
    )
    service.enter(context(S01), intent)
    cycle_id = service.current(context(S01)).funding.bucket("U1").cycle_id
    assert cycle_id is not None
    service.submit_buy(
        context(S02),
        buy_report("fault-buy", "SYN001", at=S02, source_sequence="fault-buy"),
        buy_profile("fault-buy"),
    )
    before = service.current(context(S02))
    report = sell("fault-reduce", cycle_id, 200, "12", S03, "fault-reduce")
    with pytest.raises(RuntimeError, match="reduction fault"):
        service.submit_sell(context(S03), report, reduction_profile("fault-reduce"))
    assert service.current(context(S03)) == before
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT status FROM bridge_operations WHERE kind='sell' AND identity='fault-reduce'"
            ).fetchone()[0]
            == "RECEIVED"
        )
        assert db.execute("SELECT remaining_quantity FROM bridge_lots").fetchone()[0] == 500
        assert db.execute("SELECT remaining_cost FROM bridge_lots").fetchone()[0] == "5000"
        assert db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM bridge_outbox").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM bridge_source_facts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM bridge_events").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM bridge_postings").fetchone()[0] == 3
    retried = service.submit_sell(context(S03), report, reduction_profile("fault-reduce"))
    assert retried.account.quantity("SYN001") == 300
    assert service.submit_sell(context(S03), report, reduction_profile("fault-reduce")) == retried


def test_reduction_idempotency_alias_and_changed_content(tmp_path: Path) -> None:
    service, cycle_id = service_with_buy(tmp_path / "idempotency.sqlite")
    report = sell("reduce-idem", cycle_id, 200, "12", S03, "reduce-idem")
    result = service.submit_sell(context(S03), report, reduction_profile("reduce-idem"))
    with sqlite3.connect(tmp_path / "idempotency.sqlite") as db:
        journals = db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0]
        original_payload = db.execute(
            "SELECT payload_json FROM bridge_operations WHERE kind='sell' "
            "AND identity='reduce-idem'"
        ).fetchone()[0]
    assert service.submit_sell(context(S03), report, reduction_profile("reduce-idem")) == result
    with pytest.raises(FactError, match="SOURCE_FACT_DUPLICATE"):
        service.submit_sell(
            context(S03),
            replace(report, report_id="reduce-alias"),
            reduction_profile("reduce-idem"),
        )
    with pytest.raises(FactError, match="IDEMPOTENCY_CONFLICT"):
        service.submit_sell(
            context(S03),
            replace(report, price=Decimal("13")),
            reduction_profile("reduce-idem"),
        )
    with sqlite3.connect(tmp_path / "idempotency.sqlite") as db:
        assert db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0] == journals
        assert (
            db.execute(
                "SELECT payload_json FROM bridge_operations WHERE kind='sell' "
                "AND identity='reduce-idem'"
            ).fetchone()[0]
            == original_payload
        )
        assert (
            db.execute(
                "SELECT status FROM bridge_operations WHERE kind='sell' AND identity='reduce-alias'"
            ).fetchone()[0]
            == "RECEIVED"
        )
        assert (
            db.execute(
                "SELECT count(*) FROM bridge_conflict_register WHERE state='UNRESOLVED'"
            ).fetchone()[0]
            >= 1
        )


@pytest.mark.parametrize("case", ["full_profile", "too_large", "future", "source", "fee"])
def test_reduction_rejections_do_not_change_economics(tmp_path: Path, case: str) -> None:
    service, cycle_id = service_with_buy(tmp_path / f"{case}.sqlite")
    before = service.current(context(S02))
    report = sell("reject", cycle_id, 200, "12", S03, "reject")
    profile = reduction_profile("reject")
    if case == "full_profile":
        profile = SellProfile(
            "single-fill-terminal-v1", report.source_id, 200, 200, 0, Decimal("5"), True
        )
    elif case == "too_large":
        report = replace(report, quantity=600)
        profile = replace(
            profile,
            order_total_quantity=600,
            final_cumulative_fill_quantity=600,
        )
    elif case == "future":
        report = replace(report, occurred_at=S04)
    elif case == "source":
        report = replace(report, source_id="fixture:other")
        profile = replace(profile, source_id="fixture:other")
    else:
        report = replace(report, confirmed_fee=Decimal("6"))
    with pytest.raises(FactError):
        service.submit_sell(context(S03), report, profile)
    assert service.current(context(S03)) == before


def test_reduction_profile_rejects_invalid_scalar_and_unknown_version() -> None:
    with pytest.raises(FactError, match="invalid_boolean|INVALID_REPORT_SHAPE"):
        ReductionSellProfile(
            "complete-order-residual-lot-v1",
            "fixture",
            200,
            200,
            0,
            Decimal("5"),
            1,  # type: ignore[arg-type]
        )
    with pytest.raises(FactError, match="UNSUPPORTED_SELL_PROFILE"):
        ReductionSellProfile("unknown-v9", "fixture", 200, 200, 0, Decimal("5"), True)
    with pytest.raises(FactError, match="INVALID_REPORT_SHAPE"):
        replace(reduction_profile("scalar"), final_cumulative_fill_quantity=True)
    with pytest.raises(FactError, match="INVALID_REPORT_SHAPE"):
        replace(reduction_profile("scalar"), confirmed_fee=Decimal("NaN"))
    with pytest.raises(FactError, match="INVALID_REPORT_SHAPE"):
        sell("zero", "cycle", 0, "12", S03, "zero")
    with pytest.raises(FactError, match="INVALID_REPORT_SHAPE"):
        sell("nan", "cycle", 100, "NaN", S03, "nan")


@pytest.mark.parametrize(
    "profile_kind", ["future-unsupported-v99", None, 1, True, "", [], {}, "__missing__"]
)
def test_cold_rebuild_unknown_sell_kind_refuses_without_writes(
    tmp_path: Path, profile_kind: object
) -> None:
    path = tmp_path / "unknown-kind.sqlite"
    service, cycle = service_with_buy(path)
    service.submit_sell(
        context(S03),
        sell("kind-reduce", cycle, 200, "12", S03, "kind-reduce"),
        reduction_profile("kind-reduce"),
    )
    service.submit_sell(
        context(S04),
        sell("kind-exit", cycle, 300, "9", S04, "kind-exit"),
        exit_profile(),
    )
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT sequence,payload_json FROM bridge_journal WHERE kind='sell' "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        event = json.loads(row[1])
        if profile_kind == "__missing__":
            event.pop("profile_kind")
        else:
            event["profile_kind"] = profile_kind
        db.execute(
            "UPDATE bridge_journal SET payload_json=?,digest=? WHERE sequence=?",
            (json.dumps(event, sort_keys=True), digest(event), row[0]),
        )
    with sqlite3.connect(path) as db:
        before = tuple(db.iterdump())
    with pytest.raises(FactError, match="REBUILD_MISMATCH"):
        service.rebuild(context(S04))
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before
