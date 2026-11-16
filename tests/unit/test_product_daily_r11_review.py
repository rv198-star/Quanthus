"""Independent boundary regressions from the R11 lead's internal acceptance."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from quanthus.product_daily.candidate import CandidateProjection
from quanthus.product_daily.contracts import CommandEnvelope, ProductDailyError, ReasonCode
from quanthus.product_daily.funding import (
    BucketPhase,
    BucketState,
    CycleSettlementEvidence,
    FundingState,
    SettlementRequest,
    apply_late_cash_delta,
    late_cash_invocation_payload,
    settle_batch,
    settle_invocation_payload,
)
from quanthus.product_daily.risk import (
    BuyFeeRule,
    CandidateEntry,
    EntryLimits,
    ExpertEntryAction,
    ExpertEntryEligibility,
    QuantityLimits,
    RiskSnapshot,
    decide_entry,
    entry_invocation_payload,
)

ROOT = Path(__file__).resolve().parents[2]


def _entry_inputs(
    *,
    cash: str = "20000",
    reserve: str = "0",
    expert_cap: str = ".15",
    fraction: str | None = ".30",
) -> tuple[FundingState, dict[str, Any], CommandEnvelope]:
    state = FundingState(
        (
            BucketState(
                "U1", Decimal(cash), Decimal("20000"), Decimal("0"), None, BucketPhase.EMPTY
            ),
        ),
        Decimal(reserve),
        Decimal("0"),
        0,
    )
    expert = ExpertEntryEligibility(
        "OP",
        "SYN001",
        ExpertEntryAction.TRIAL_ENTRY,
        "sha256:" + "1" * 64,
        Decimal(expert_cap),
    )
    equity = Decimal(cash) + Decimal(reserve)
    args = {
        "candidate": CandidateEntry(
            "OP",
            "SYN001",
            "IND1",
            CandidateProjection.OBSERVED,
            True,
            True,
            True,
            expert,
        ),
        "snapshot": RiskSnapshot(
            "RS",
            datetime(2021, 1, 4, tzinfo=timezone.utc),
            equity,
            equity,
        ),
        "empty_bucket_ids": ("U1",),
        "active_or_reserved_security_codes": (),
        "active_industry_counts": (),
        "new_buckets_today": 0,
        "limits": EntryLimits(2, 2),
        "fraction": None if fraction is None else Decimal(fraction),
        "mark_price": Decimal("10"),
        "price_bound": Decimal("10"),
        "lot_size": 100,
        "quantity_limits": QuantityLimits(Decimal("1"), Decimal("1"), Decimal("1")),
        "fee_rule": BuyFeeRule(Decimal(".0003"), Decimal("5")),
    }
    command = CommandEnvelope.create("review-entry", entry_invocation_payload(**args), 0)
    return state, args, command


@pytest.mark.parametrize(("expert_cap", "fraction"), [(".15", ".30"), (".30", ".15")])
def test_expert_and_configured_position_caps_intersect(expert_cap: str, fraction: str) -> None:
    state, args, command = _entry_inputs(expert_cap=expert_cap, fraction=fraction)
    out = decide_entry(funding=state, command=command, **args)
    assert out.accepted
    assert out.quantity is not None
    assert out.quantity.quantity == 200
    assert out.quantity.target_cap == Decimal("3000")


def test_expert_cap_does_not_supply_missing_fraction() -> None:
    state, args, command = _entry_inputs(fraction=None)
    out = decide_entry(funding=state, command=command, **args)
    assert not out.accepted
    assert out.funding_state == state
    assert not out.funding_ledger_entries


def test_composite_funding_postings_are_balanced_retained_and_not_reapplied() -> None:
    state, args, command = _entry_inputs(cash="18000", reserve="500", expert_cap=".30")
    out = decide_entry(funding=state, command=command, **args)
    assert out.accepted
    assert out.funding_state.reserve == 0
    assert out.funding_state.bucket("U1").cash == Decimal("18500")
    assert out.funding_ledger_entries
    for entry in out.funding_ledger_entries:
        assert sum((posting.amount for posting in entry.postings), Decimal("0")) == 0
    reserve_delta = sum(
        (
            posting.amount
            for entry in out.funding_ledger_entries
            for posting in entry.postings
            if posting.account == "shared/reserve"
        ),
        Decimal("0"),
    )
    assert reserve_delta == Decimal("-500")
    stored = out.funding_state.entry_reservations[0].result
    assert stored["funding_ledger_entries"] == [
        entry.to_dict() for entry in out.funding_ledger_entries
    ]
    replay = decide_entry(funding=out.funding_state, command=command, **args)
    assert replay.replayed
    assert replay.funding_state == out.funding_state
    assert replay.quantity == out.quantity
    assert replay.result_digest == out.result_digest
    assert replay.funding_ledger_entries == ()
    assert replay.funding_state.entry_reservations[0].result == stored


def test_verifier_rejects_success_without_actual_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "review_r11_verify",
        ROOT / "scripts/r11_synthetic_verify.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "r11_synthetic_verify",
            "--output-dir",
            str(tmp_path),
            "--repo-root",
            str(ROOT),
        ],
    )

    def missing_outputs(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(module.subprocess, "run", missing_outputs)
    assert module.main() == 1
    receipt = json.loads((tmp_path / "synthetic-verification.json").read_text())
    assert receipt["passed"] is False
    assert receipt["golden_all_passed"] is False
    assert receipt["stories_rebuild_identical"] is False
    assert len(receipt["verification_errors"]) == 2


def test_two_complete_entry_cycles_preserve_history_replay_and_old_cycle_cash() -> None:
    state, first_args, first_command = _entry_inputs(reserve="1000", expert_cap=".30")
    first = decide_entry(funding=state, command=first_command, **first_args)
    assert first.accepted and first.quantity is not None and first.quantity.quantity == 500
    first_history = first.funding_state.entry_reservations[0]
    first_cycle = first_history.cycle_id
    assert first.funding_state.active_entry_reservations == (first_history,)

    def close_and_settle(current: FundingState, name: str) -> FundingState:
        # P12 supplies complete flat/order/fill/fee facts, not inferred execution.
        closed = replace(
            current,
            buckets=(
                replace(current.bucket("U1"), phase=BucketPhase.CLOSED, cash=Decimal("20000")),
            ),
            revision=current.revision + 1,
        )
        cycle = closed.bucket("U1").cycle_id
        assert cycle is not None
        requests = (
            SettlementRequest(
                name,
                "U1",
                cycle,
                CycleSettlementEvidence(True, True, True, True, True),
            ),
        )
        cmd = CommandEnvelope.create(name, settle_invocation_payload(requests), closed.revision)
        result = settle_batch(closed, cmd, requests)
        assert result.accepted
        assert result.state.active_entry_reservations == ()
        return result.state

    settled = close_and_settle(first.funding_state, "settle-first")
    assert settled.entry_reservations == (first_history,)
    second_args = {
        **first_args,
        "candidate": CandidateEntry(
            "OP2",
            "SYN002",
            "IND1",
            CandidateProjection.OBSERVED,
            True,
            True,
            True,
            ExpertEntryEligibility(
                "OP2", "SYN002", ExpertEntryAction.TRIAL_ENTRY, "sha256:" + "2" * 64, Decimal(".30")
            ),
        ),
        "snapshot": RiskSnapshot(
            "RS-OP2",
            datetime(2021, 3, 1, tzinfo=timezone.utc),
            Decimal("21000"),
            Decimal("21000"),
        ),
    }
    refused_args = {
        **second_args,
        "quantity_limits": QuantityLimits(Decimal("0"), Decimal("1"), Decimal("1")),
    }
    refused_command = CommandEnvelope.create(
        "failed-next-entry",
        entry_invocation_payload(**refused_args),
        settled.revision,
    )
    refused = decide_entry(funding=settled, command=refused_command, **refused_args)
    assert not refused.accepted and refused.funding_state == settled
    assert refused.funding_ledger_entries == ()

    second_command = CommandEnvelope.create(
        "entry-second",
        entry_invocation_payload(**second_args),
        settled.revision,
    )
    second = decide_entry(funding=settled, command=second_command, **second_args)
    assert second.accepted and second.quantity is not None and second.quantity.quantity == 500
    assert len(second.funding_state.entry_reservations) == 2
    second_history = second.funding_state.entry_reservations[1]
    assert second_history.cycle_id != first_cycle
    assert second.funding_state.active_entry_reservations == (second_history,)
    assert second.funding_state.entry_reservations[0] == first_history
    with pytest.raises(ProductDailyError, match="one bucket may hold one active reservation"):
        replace(second.funding_state, completed_settlements=())
    with pytest.raises(ProductDailyError, match="entry receipt and reservation closure differ"):
        replace(second.funding_state, entry_reservations=(second_history,))

    payload = late_cash_invocation_payload(
        bucket_id="U1",
        cycle_id=first_cycle,
        cash_delta=Decimal("-100"),
    )
    fee_command = CommandEnvelope.create("late-first-fee", payload, second.funding_state.revision)
    fee = apply_late_cash_delta(
        second.funding_state,
        fee_command,
        bucket_id="U1",
        cycle_id=first_cycle,
        cash_delta=Decimal("-100"),
    )
    assert fee.accepted and fee.state.reserve == Decimal("900")
    assert fee.state.bucket("U1") == second.funding_state.bucket("U1")
    assert fee.state.bucket("U1").locked_cycle_budget == Decimal("20000")
    assert fee.state.entry_reservations == second.funding_state.entry_reservations

    conflict_command = CommandEnvelope.create(
        "occupied-bucket-retry",
        entry_invocation_payload(**second_args),
        fee.state.revision,
    )
    conflict = decide_entry(funding=fee.state, command=conflict_command, **second_args)
    assert not conflict.accepted and conflict.reason_codes == (ReasonCode.NO_EMPTY_BUCKET,)
    assert conflict.funding_state == fee.state and conflict.funding_ledger_entries == ()

    replays = []
    for current in (fee.state, close_and_settle(fee.state, "settle-second")):
        for original, args, cmd in (
            (first, first_args, first_command),
            (second, second_args, second_command),
        ):
            replay = decide_entry(funding=current, command=cmd, **args)
            assert replay.replayed and replay.accepted
            assert replay.quantity == original.quantity
            assert replay.reservation_id == original.reservation_id
            assert replay.result_digest == original.result_digest
            assert replay.funding_state == current and replay.funding_ledger_entries == ()
            replays.append(replay.to_dict())
        assert len(current.entry_reservations) == 2
    assert current.active_entry_reservations == ()
    assert current.cash_total == Decimal("20900")
    actuals_path = os.environ.get("R11_TWO_CYCLE_ACTUALS")
    if actuals_path:
        Path(actuals_path).write_text(
            json.dumps(
                {
                    "schema_version": "quanthus.r11.two-cycle-actuals.v1",
                    "first_entry": first.to_dict(),
                    "first_settled": settled.to_dict(),
                    "refused_next_entry": refused.to_dict(),
                    "second_entry": second.to_dict(),
                    "old_cycle_fee": fee.to_dict(),
                    "active_conflict": conflict.to_dict(),
                    "first_history_before": first_history.to_dict(),
                    "first_history_after": current.entry_reservations[0].to_dict(),
                    "original_replays": replays,
                    "final_state": current.to_dict(),
                },
                indent=2,
            )
            + "\n"
        )
