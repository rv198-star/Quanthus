"""Synthetic public witnesses for the C13 pure later-signal gate."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.product_daily.c13_later_signal import (
    Eligibility,
    LaterSignalPolicy,
    LaterSignalStatus,
    OriginalAttemptState,
    OriginalOpportunity,
    SignalCause,
    SignalObservation,
    SyntheticCalendar,
    evaluate_later_signal,
    parent_opportunity_identity,
)
from quanthus.shared.fact_contracts import FactError

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads(
    (ROOT / "config/research/r11/continuation-025/c13-synthetic-policy.v1.json").read_text(
        encoding="utf-8"
    )
)


def policy() -> LaterSignalPolicy:
    raw = CONFIG["policy"]
    return LaterSignalPolicy(
        raw["policy_version"],
        raw["producer_version"],
        raw["predicate_version"],
        raw["calendar_version"],
        raw["timezone_name"],
        raw["observation_lifetime_sessions"],
        raw["execution_window_id"],
        raw["zero_fill_retry_policy_version"],
        datetime.fromisoformat(raw["expiry"]),
        raw["attempt_limit"],
    )


def calendar() -> SyntheticCalendar:
    raw = CONFIG["calendar"]
    return SyntheticCalendar(
        raw["version"],
        raw["timezone_name"],
        tuple(date.fromisoformat(item) for item in raw["sessions"]),
        tuple(datetime.fromisoformat(item) for item in raw["opens"]),
        tuple(datetime.fromisoformat(item) for item in raw["closes"]),
        raw["execution_window_id"],
    )


def opportunity(**changes: object) -> OriginalOpportunity:
    value = OriginalOpportunity(
        parent_opportunity_identity("event:parent", "snapshot:parent", "sha256:" + "1" * 64),
        "event:parent",
        "snapshot:parent",
        "sha256:" + "1" * 64,
        "SYN-C13",
        "security-cn-000001",
        datetime.fromisoformat("2026-11-17T15:00:00+08:00"),
        True,
        OriginalAttemptState.NEVER_SUBMITTED,
    )
    return replace(value, **changes)


def prior(**changes: object) -> SignalObservation:
    value = SignalObservation(
        opportunity().identity,
        "security-cn-000001",
        policy().predicate_version,
        policy().producer_version,
        "synthetic-source-v1",
        "evidence:prior",
        date(2026, 11, 17),
        date(2026, 11, 17),
        datetime.fromisoformat("2026-11-17T15:00:00+08:00"),
        datetime.fromisoformat("2026-11-17T15:05:00+08:00"),
        datetime.fromisoformat("2026-11-17T15:10:00+08:00"),
        Eligibility.EXPLICIT_FALSE,
    )
    return replace(value, **changes)


def current(**changes: object) -> SignalObservation:
    value = SignalObservation(
        opportunity().identity,
        "security-cn-000001",
        policy().predicate_version,
        policy().producer_version,
        "synthetic-source-v1",
        "evidence:current",
        date(2026, 11, 18),
        date(2026, 11, 18),
        datetime.fromisoformat("2026-11-18T15:00:00+08:00"),
        datetime.fromisoformat("2026-11-18T15:05:00+08:00"),
        datetime.fromisoformat("2026-11-18T15:10:00+08:00"),
        Eligibility.EXPLICIT_TRUE,
    )
    return replace(value, **changes)


def decide(**changes: object):
    values = {
        "opportunity": opportunity(),
        "prior": prior(),
        "current": current(),
        "policy": policy(),
        "calendar": calendar(),
    }
    values.update(changes)
    return evaluate_later_signal(**values)


def test_a01_original_t_plus_one_is_unchanged_while_window_open() -> None:
    result = decide(opportunity=opportunity(original_window_closed=False))
    assert result.status is LaterSignalStatus.ORIGINAL_T1_UNCHANGED
    assert result.candidate is None


def test_a02_missing_policy_binding_is_observation_only() -> None:
    result = decide(policy=None)
    assert result.status is LaterSignalStatus.OBSERVATION_ONLY
    assert result.reason == "POLICY_NOT_ADOPTED"


@pytest.mark.parametrize(
    "attempt_state",
    [OriginalAttemptState.NEVER_SUBMITTED, OriginalAttemptState.TERMINAL_ZERO_FILL_RECONCILED],
)
def test_a03_a04_only_proven_zero_original_attempt_yields_one_candidate(
    attempt_state: OriginalAttemptState,
) -> None:
    result = decide(opportunity=opportunity(original_attempt_state=attempt_state))
    assert result.status is LaterSignalStatus.ELIGIBLE_FOR_ONE_ATTEMPT
    assert result.slot is not None and result.candidate is not None
    assert result.candidate.execution_at == datetime.fromisoformat("2026-11-19T09:30:00+08:00")


@pytest.mark.parametrize(
    "attempt_state",
    [
        OriginalAttemptState.PENDING,
        OriginalAttemptState.ANY_FILL,
        OriginalAttemptState.HELD,
        OriginalAttemptState.EXITING,
        OriginalAttemptState.OPEN_FEE,
        OriginalAttemptState.UNRESOLVED_CORRECTION,
        OriginalAttemptState.UNSETTLED,
    ],
)
def test_a05_nonzero_or_unreconciled_original_attempt_blocks(
    attempt_state: OriginalAttemptState,
) -> None:
    result = decide(opportunity=opportunity(original_attempt_state=attempt_state))
    assert result.status is LaterSignalStatus.BLOCK_LATER_ATTEMPT
    assert result.candidate is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"prior": prior(eligibility=Eligibility.UNKNOWN)}, "UNKNOWN_IS_NOT_FALSE"),
        ({"prior": prior(eligibility=Eligibility.EXPLICIT_TRUE)}, "NO_FALSE_TO_TRUE_TRANSITION"),
        ({"current": current(evidence_id="evidence:prior")}, "DUPLICATE_EVIDENCE"),
        (
            {"current": current(cause=SignalCause.FUNDING_OR_PRICE_REFRESH)},
            "NON_QUALIFYING_CHANGE",
        ),
        (
            {"current": current(cause=SignalCause.SAME_SESSION_CORRECTION)},
            "NON_QUALIFYING_CHANGE",
        ),
        ({"prior": None}, "PRIOR_EVIDENCE_MISSING"),
    ],
)
def test_a06_unknown_refresh_repeated_true_missing_prior_are_observation(
    changes: dict[str, object], reason: str
) -> None:
    result = decide(**changes)
    assert result.status is LaterSignalStatus.OBSERVATION_ONLY
    assert result.reason == reason


@pytest.mark.parametrize(
    "changes",
    [
        {"prior": prior(signal_session=date(2026, 11, 16), feature_session=date(2026, 11, 16))},
        {"prior": prior(rule_version="other-rule")},
        {"current": current(producer_version="other-producer")},
        {"current": current(source_version="other-source")},
        {"prior": prior(security_id="security-cn-000002")},
    ],
)
def test_a07_non_adjacent_or_wrong_identity_blocks(changes: dict[str, object]) -> None:
    assert decide(**changes).status is LaterSignalStatus.BLOCK_LATER_ATTEMPT


@pytest.mark.parametrize(
    "changes",
    [
        {"current": current(available_at=datetime.fromisoformat("2026-11-18T15:11:00+08:00"))},
        {"current": current(feature_session=date(2026, 11, 17))},
        {
            "current": current(
                evidence_at=datetime.fromisoformat("2026-11-18T15:00:00+08:00"),
                available_at=datetime.fromisoformat("2026-11-18T14:59:00+08:00"),
            )
        },
    ],
)
def test_a08_late_or_wrong_session_evidence_blocks(changes: dict[str, object]) -> None:
    assert decide(**changes).status is LaterSignalStatus.BLOCK_LATER_ATTEMPT


def test_a09_expiry_cannot_rearm_or_extend() -> None:
    expired = replace(policy(), expiry=datetime.fromisoformat("2026-11-19T09:00:00+08:00"))
    assert decide(policy=expired).status is LaterSignalStatus.TERMINAL_NO_REARM
    backdated = current(
        evidence_at=datetime.fromisoformat("2026-11-17T14:00:00+08:00"),
        available_at=datetime.fromisoformat("2026-11-17T14:05:00+08:00"),
    )
    assert decide(current=backdated).status is LaterSignalStatus.TERMINAL_NO_REARM


def test_attempt_slot_identity_is_stable_across_policy_versions() -> None:
    first = decide()
    upgraded = replace(policy(), policy_version="c13-synthetic-qualified-v2")
    second = decide(policy=upgraded)
    assert first.status is second.status is LaterSignalStatus.ELIGIBLE_FOR_ONE_ATTEMPT
    assert first.slot is not None and second.slot is not None
    assert first.slot.identity == second.slot.identity
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.later_signal_id == second.candidate.later_signal_id
    assert first.candidate.policy_version != second.candidate.policy_version
    assert first.candidate.identity != second.candidate.identity


def test_calendar_order_and_opening_identity_are_strict() -> None:
    with pytest.raises(Exception, match="calendar sessions must be unique and ordered"):
        SyntheticCalendar(
            "bad",
            "Asia/Shanghai",
            (date(2026, 11, 18), date(2026, 11, 17)),
            (
                datetime.fromisoformat("2026-11-18T09:30:00+08:00"),
                datetime.fromisoformat("2026-11-17T09:30:00+08:00"),
            ),
            (
                datetime.fromisoformat("2026-11-18T15:00:00+08:00"),
                datetime.fromisoformat("2026-11-17T15:00:00+08:00"),
            ),
            "next-adopted-open-v1",
        )


def test_intraday_evidence_cannot_establish_a_close_signal() -> None:
    found = decide(
        current=current(
            evidence_at=datetime.fromisoformat("2026-11-18T10:00:00+08:00"),
            available_at=datetime.fromisoformat("2026-11-18T10:01:00+08:00"),
            decision_cutoff=datetime.fromisoformat("2026-11-18T10:02:00+08:00"),
        )
    )
    assert found.status is LaterSignalStatus.BLOCK_LATER_ATTEMPT
    assert found.candidate is None


def test_prior_cutoff_cannot_be_after_current_decision() -> None:
    found = decide(prior=prior(decision_cutoff=datetime.fromisoformat("2026-11-19T15:10:00+08:00")))
    assert found.status is LaterSignalStatus.BLOCK_LATER_ATTEMPT
    assert found.candidate is None


@pytest.mark.parametrize("value", [True, 1.0, Decimal("1")])
def test_attempt_limit_requires_an_exact_integer(value: object) -> None:
    with pytest.raises(FactError, match="attempt_limit must be an integer"):
        replace(policy(), attempt_limit=value)


def test_observation_lifetime_caps_a_later_absolute_expiry() -> None:
    second_day = current(
        feature_session=date(2026, 11, 19),
        signal_session=date(2026, 11, 19),
        evidence_at=datetime.fromisoformat("2026-11-19T15:00:00+08:00"),
        available_at=datetime.fromisoformat("2026-11-19T15:05:00+08:00"),
        decision_cutoff=datetime.fromisoformat("2026-11-19T15:10:00+08:00"),
    )
    preceding = current(eligibility=Eligibility.EXPLICIT_FALSE, evidence_id="second-day-prior")
    long_expiry = replace(policy(), expiry=datetime.fromisoformat("2026-11-20T10:00:00+08:00"))
    assert (
        decide(prior=preceding, current=second_day, policy=long_expiry).status
        is LaterSignalStatus.TERMINAL_NO_REARM
    )
    two_sessions = replace(long_expiry, observation_lifetime_sessions=2)
    assert (
        decide(prior=preceding, current=second_day, policy=two_sessions).status
        is LaterSignalStatus.ELIGIBLE_FOR_ONE_ATTEMPT
    )


def test_policy_window_must_bind_calendar_window() -> None:
    found = decide(calendar=replace(calendar(), execution_window_id="different-window"))
    assert found.status is LaterSignalStatus.BLOCK_LATER_ATTEMPT and found.candidate is None
    assert found.reason == "CALENDAR_BINDING_MISMATCH"


def test_unknown_execution_window_is_rejected_even_when_input_ids_match() -> None:
    unregistered = "UNREGISTERED-WINDOW-V9"
    found = decide(
        policy=replace(policy(), execution_window_id=unregistered),
        calendar=replace(calendar(), execution_window_id=unregistered),
    )
    assert found.status is LaterSignalStatus.BLOCK_LATER_ATTEMPT and found.candidate is None
    assert found.reason == "UNREGISTERED_EXECUTION_WINDOW"


@pytest.mark.parametrize(
    "changes", [{"opportunity_id": "alias"}, {"source_snapshot_id": "snapshot:other"}]
)
def test_parent_key_cannot_be_aliased_or_rebound(changes: dict[str, str]) -> None:
    with pytest.raises(FactError, match="opportunity identity does not match"):
        opportunity(**changes)


def test_signal_identity_binds_evidence_and_decision_clocks() -> None:
    baseline = decide()
    changed = decide(
        current=current(decision_cutoff=datetime.fromisoformat("2026-11-18T15:20:00+08:00"))
    )
    assert baseline.slot == changed.slot
    assert baseline.candidate is not None and changed.candidate is not None
    assert baseline.candidate.later_signal_id != changed.candidate.later_signal_id
