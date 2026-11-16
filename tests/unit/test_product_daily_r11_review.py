"""Independent boundary regressions from the R11 lead's internal acceptance."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from quanthus.product_daily.candidate import CandidateProjection
from quanthus.product_daily.contracts import CommandEnvelope
from quanthus.product_daily.funding import BucketPhase, BucketState, FundingState
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
