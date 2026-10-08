"""P10 T/T+1 cutoff v2: stop starting new work, finish accepted atomic work."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.providers.minute_operation_cutoff import (
    POLICY_PATH,
    OperationAdmission,
    new_operation,
)


def test_owner_v2_cutoff_is_about_new_operations_not_absolute_finish() -> None:
    p = json.loads(POLICY_PATH.read_text())
    assert p["schema_version"] == 2
    assert p["supersedes_policy_id"] == "p10-1m-daily-and-tplus1-bounded-ops-v1"
    assert p["per_target_market_date"]["T"]["routine_target_new_operation_admission_seconds"] == 1800
    assert p["per_target_market_date"]["T"]["new_operation_admission_cutoff_seconds"] == 3600
    assert p["per_target_market_date"]["T_plus_1"]["new_operation_admission_cutoff_seconds"] == 3600
    assert p["per_target_market_date"]["combined_new_operation_admission_window_seconds"] == 7200
    assert p["per_target_market_date"]["combined_window_is_not_hard_wall_completion_deadline"]
    assert p["atomic_operation"]["already_started_operation_finalization_allowed_after_cutoff"]
    assert p["atomic_operation"]["max_operation_timeout_seconds"] == 600


@pytest.mark.parametrize("phase", ["T", "T_plus_1"])
def test_new_operation_started_at_59m50_can_finish_after_60m(phase: str) -> None:
    accepted = new_operation(
        phase=phase, cumulative_phase_seconds=3590, operation_timeout_seconds=60
    )
    assert isinstance(accepted, OperationAdmission)
    result = accepted.close(duration_seconds=50)
    assert result == {
        "phase_consumed_seconds": 3640,
        "operation_completed_after_cutoff": True,
        "post_cutoff_drain_seconds": 40,
    }
    assert new_operation(
        phase=phase,
        cumulative_phase_seconds=result["phase_consumed_seconds"],
        operation_timeout_seconds=20,
    ) is None


@pytest.mark.parametrize("consumed", [3600, 3600.01, 4000])
def test_operation_may_not_start_at_or_after_deadline(consumed: float) -> None:
    assert new_operation(
        phase="T", cumulative_phase_seconds=consumed, operation_timeout_seconds=20
    ) is None


def test_only_real_unlocked_operation_can_start() -> None:
    assert new_operation(
        phase="T",
        cumulative_phase_seconds=3599,
        operation_timeout_seconds=60,
        another_operation_running=True,
    ) is None
    assert new_operation(
        phase="T", cumulative_phase_seconds=3599, operation_timeout_seconds=60
    ) is not None


def test_last_accepted_op_can_complete_and_perform_its_final_commit() -> None:
    admit = new_operation(
        phase="T_plus_1", cumulative_phase_seconds=3599.5, operation_timeout_seconds=600
    )
    assert admit is not None
    finish = admit.close(duration_seconds=450)
    assert finish["post_cutoff_drain_seconds"] == 449.5
    assert finish["phase_consumed_seconds"] == 4049.5


def test_individual_operation_timeout_not_a_60m_global_termination() -> None:
    with pytest.raises(ValueError, match="independent timeout"):
        new_operation(
            phase="T", cumulative_phase_seconds=3599, operation_timeout_seconds=601
        )
    admit = new_operation(
        phase="T", cumulative_phase_seconds=3599, operation_timeout_seconds=5
    )
    assert admit is not None
    with pytest.raises(ValueError, match="own scoped timeout"):
        admit.close(duration_seconds=6)


def test_t_plus_1_has_a_separate_admission_account_not_an_extension_of_t() -> None:
    assert new_operation(
        phase="T", cumulative_phase_seconds=3601, operation_timeout_seconds=10
    ) is None
    assert new_operation(
        phase="T_plus_1", cumulative_phase_seconds=0, operation_timeout_seconds=10
    ) is not None


def test_restarts_and_retries_do_not_reset_cutoff() -> None:
    earlier = new_operation(
        phase="T", cumulative_phase_seconds=3550, operation_timeout_seconds=120
    )
    assert earlier is not None
    ledger = earlier.close(duration_seconds=80)
    # On a new process, the same persisted market-date/phase usage still denies.
    assert new_operation(
        phase="T",
        cumulative_phase_seconds=ledger["phase_consumed_seconds"],
        operation_timeout_seconds=30,
    ) is None
    p = json.loads(POLICY_PATH.read_text())
    assert p["immutable_accounting"]["restarts_cannot_reopen_admission_after_threshold"]


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True])
def test_invalid_phase_elapsed_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="cumulative phase seconds"):
        new_operation(
            phase="T", cumulative_phase_seconds=bad, operation_timeout_seconds=10
        )


def test_new_version_rejects_old_v1_policy(tmp_path: Path) -> None:
    old = json.loads(POLICY_PATH.read_text())
    old["schema_version"] = 1
    old["policy_id"] = "p10-1m-daily-and-tplus1-bounded-ops-v1"
    fake = tmp_path / "old.json"
    fake.write_text(json.dumps(old))
    with pytest.raises(ValueError, match="bound to P10 v2"):
        new_operation(
            phase="T", cumulative_phase_seconds=200, operation_timeout_seconds=100,
            policy_path=fake,
        )


def test_policy_records_pending_drain_and_forbids_prequeued_work() -> None:
    p = json.loads(POLICY_PATH.read_text())
    assert p["atomic_operation"]["queueing_or_reserving_future_operations_before_cutoff_is_not_real_start"]
    assert p["atomic_operation"]["no_new_retry_or_next_shard_after_cutoff"]
    assert p["atomic_operation"]["atomic_commit_and_invariant_verification_within_same_admitted_operation"]
    assert p["immutable_accounting"]["account_post_cutoff_tail_in_actual_elapsed"]
