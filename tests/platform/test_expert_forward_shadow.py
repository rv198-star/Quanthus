from __future__ import annotations

import pytest

from quanthus.expert_evaluation.lifecycle import (
    ExpertLifecycleError,
    load_shadow_schedule,
    main,
    submit_forward_shadow,
)
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.policy import find_project_root

ROOT = find_project_root()
SCHEDULE = ROOT / "config/expert-evaluations/shadow/schedule.v1.json"


def test_disabled_schedule_has_receipt_but_no_enabled_binding() -> None:
    schedule = load_shadow_schedule(SCHEDULE)
    assert schedule["bindings"] == []
    assert schedule["disable_receipts"] == [
        {
            "strategy_id": "candidate.p4-trend-risk",
            "strategy_version": "1.0.1",
            "decision_digest": (
                "sha256:2ebfc70329571cc188de7ea7abd0353c47b54d072b4114f6661dfc195e3df6ee"
            ),
            "reason_code": "forward_shadow_not_explicitly_authorized",
        }
    ]


def test_disabled_submission_is_rejected_before_zero_job_store_changes() -> None:
    store = InMemoryPlatformStore()
    assert store.list_jobs() == ()
    with pytest.raises(ExpertLifecycleError, match="shadow_binding_disabled"):
        submit_forward_shadow(
            schedule_path=SCHEDULE,
            store=store,
            strategy_id="candidate.p4-trend-risk",
            strategy_version="1.0.1",
            operation="forward_observation",
            observation_date="2026-08-23",
            dwd_release_digest="sha256:" + "1" * 64,
            actor_user_id="node-local-controller",
        )
    assert store.list_jobs() == ()


def test_node_command_rejects_disabled_identity_without_reading_postgres(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("QUANTHUS_PLATFORM_DATABASE_URL", raising=False)
    result = main(
        [
            "--schedule",
            str(SCHEDULE),
            "--strategy-id",
            "candidate.p4-trend-risk",
            "--strategy-version",
            "1.0.1",
            "--operation",
            "forward_observation",
            "--observation-date",
            "2026-08-23",
            "--dwd-release-digest",
            "sha256:" + "1" * 64,
        ]
    )
    assert result == 2
    assert '"reason_code": "shadow_binding_disabled"' in capsys.readouterr().out
