"""P12 W1/W2 synthetic durable fact transaction acceptance."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.execution import Correction, ExecutionReport, SyntheticContext
from quanthus.platform.adapters.product_daily_facts_sqlite import (
    SQLiteSyntheticFactsAdapter,
    build_synthetic_fact_service,
)
from quanthus.product_daily.application import Baseline, SyntheticFactService
from quanthus.product_daily.application.models import (
    POLICY_REFERENCE,
    SyntheticRuntimeAuthorization,
)
from quanthus.shared.fact_contracts import FactError

AUTH = SyntheticRuntimeAuthorization("fixture-runtime", frozenset({"SYN-A", "SYN-B", "SYN-U05"}))
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def context(mode: str = "synthetic") -> SyntheticContext:
    return SyntheticContext("SYN-U05", mode, "fixture:u05", NOW, "complete")


def baseline() -> Baseline:
    return Baseline(
        context(), "baseline-u05", Decimal("10000.00"), 0, "600000", 0, ("plan-dependent",)
    )


def report(*, report_id: str = "synth-fill-1", quantity: int = 400) -> ExecutionReport:
    return ExecutionReport(
        report_id,
        "600000",
        "buy",
        quantity,
        Decimal("12.50"),
        Decimal("5.00"),
        NOW,
        "broker:synthetic",
        "sequence-1",
        400,
        Decimal("12.40"),
    )


def correction(expected: int = 1, quantity: int = 300) -> Correction:
    return Correction(
        "synth-correction-1",
        "synth-fill-1",
        expected,
        quantity,
        Decimal("12.50"),
        "broker correction",
    )


def test_ex01_ex03_ex04_public_results_and_immutability(tmp_path: Path) -> None:
    path = tmp_path / "facts.sqlite"
    service = build_synthetic_fact_service(path, context=context(), authorization=AUTH)
    service.establish_baseline(baseline())

    first = service.submit_report(context(), report())
    assert first.status == "POSTED"
    assert first.projection.cash == Decimal("4995.00")
    assert first.projection.quantity("600000") == 400
    assert first.deviations == ("PRICE_DEVIATION",)
    revision = first.projection.revision

    repeat = service.submit_report(context(), report())
    assert repeat.projection.revision == revision
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 3

    conflict = service.submit_report(context(), report(quantity=300))
    assert conflict.status == "IDEMPOTENCY_CONFLICT"
    assert conflict.projection.cash == Decimal("4995.00")
    assert conflict.projection.quantity("600000") == 400

    corrected = service.correct_fact(context(), correction())
    assert corrected.status == "POSTED"
    assert corrected.projection.cash == Decimal("6245.00")
    assert corrected.projection.quantity("600000") == 300
    assert corrected.cash_delta == Decimal("1250.00")
    assert corrected.quantity_delta == -100
    again = service.correct_fact(context(), correction())
    assert again == corrected
    assert again.projection == corrected.projection
    with sqlite3.connect(path) as connection:
        original = connection.execute(
            "SELECT quantity FROM facts WHERE fact_id='synth-fill-1'"
        ).fetchone()[0]
        assert original == 300
        for (event_id,) in connection.execute("SELECT event_id FROM events"):
            total = sum(
                Decimal(row[0])
                for row in connection.execute(
                    "SELECT amount FROM postings WHERE event_id=?", (event_id,)
                )
            )
            assert total == 0


def test_midwrite_fault_rolls_business_only_then_retry_once_and_rebuilds(tmp_path: Path) -> None:
    path = tmp_path / "facts.sqlite"
    fired: set[str] = set()

    def hook(stage: str) -> None:
        if stage not in fired:
            fired.add(stage)
            raise RuntimeError("injected after event")

    adapter = SQLiteSyntheticFactsAdapter(path, fault_hook=hook)
    service = SyntheticFactService(adapter, context(), AUTH)
    service.establish_baseline(baseline())
    with pytest.raises(RuntimeError, match="injected"):
        service.submit_report(context(), report())
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT status FROM inbox").fetchone()[0] == "RECEIVED"
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert connection.execute("SELECT revision FROM accounts").fetchone()[0] == 0

    retried = service.submit_report(context(), report())
    assert retried.status == "POSTED"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1

    reopened = build_synthetic_fact_service(path, context=context(), authorization=AUTH)
    stored = reopened.projection(context())
    rebuilt = reopened.rebuild(context())
    assert stored == rebuilt
    assert stored.to_dict() == retried.projection.to_dict()
    assert reopened.submit_report(context(), report()).projection.revision == 1


def test_context_shape_and_revision_cas_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "facts.sqlite"
    service = build_synthetic_fact_service(path, context=context(), authorization=AUTH)
    service.establish_baseline(baseline())
    before = service.projection(context())
    with pytest.raises(FactError, match="UNAUTHORIZED_CONTEXT"):
        service.submit_report(context("actual"), report())
    assert service.projection(context()) == before
    with pytest.raises(FactError, match="INVALID_REPORT_SHAPE"):
        ExecutionReport(
            "bad",
            "600000",
            "buy",
            True,  # type: ignore[arg-type]
            Decimal("12.50"),
            Decimal("5.00"),
            NOW,
            "fixture",
            "1",
            400,
            Decimal("12.40"),
        )
    service.submit_report(context(), report())
    corrected = service.correct_fact(context(), correction())
    assert corrected.projection.revision == 2
    with pytest.raises(FactError, match="STALE_FACT_REVISION.*2"):
        service.correct_fact(
            context(), replace(correction(), correction_id="synth-stale-correction")
        )
    assert POLICY_REFERENCE == "p12-synthetic-facts-0.1"
