from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quanthus.platform.candidate_approval import CandidateApprovalService
from quanthus.platform.identity import AuthenticatedPrincipal
from quanthus.platform.persistence import InMemoryPlatformStore

PRINCIPAL = AuthenticatedPrincipal(
    user_id="usr_approval",
    username="admin",
    role="admin",
    session_id="ses_approval",
)


def service() -> CandidateApprovalService:
    return CandidateApprovalService(
        InMemoryPlatformStore(),
        clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
    )


def review(
    approvals: CandidateApprovalService,
    action: str,
    note: str = "证据复核意见满足最小长度要求。",
) -> None:
    approvals.record_review(
        strategy_id="candidate.test",
        strategy_version="1.0.0",
        evidence_digest="sha256:evidence",
        action=action,
        note="" if action == "claim-review" else note,
        principal=PRINCIPAL,
        request_id="req-review",
    )


def govern(approvals: CandidateApprovalService, action: str) -> None:
    approvals.record_governance(
        strategy_id="candidate.test",
        strategy_version="1.0.0",
        evidence_digest="sha256:evidence",
        action=action,
        note="治理审批意见满足最小长度要求。",
        principal=PRINCIPAL,
        request_id="req-governance",
    )


def state(approvals: CandidateApprovalService) -> dict[str, object]:
    return approvals.state("candidate.test", "1.0.0", "sha256:evidence")


@pytest.mark.parametrize("terminal_action", ["reject-governance", "admit-governance"])
def test_governance_terminal_decisions_are_irreversible_and_never_authorize_execution(
    terminal_action: str,
) -> None:
    approvals = service()
    review(approvals, "claim-review")
    review(approvals, "approve-review")
    govern(approvals, "submit-governance")
    govern(approvals, terminal_action)

    observed = state(approvals)
    governance = observed["governance"]
    assert isinstance(governance, dict)
    assert governance["state"] == (
        "governance-rejected"
        if terminal_action == "reject-governance"
        else "governance-admitted"
    )
    assert governance["terminal"] is True
    assert observed["execution"] == {"state": "not-authorized", "authorized": False}
    with pytest.raises(ValueError, match="transition"):
        govern(approvals, "return-governance")
    with pytest.raises(ValueError, match="terminal governance"):
        review(approvals, "claim-review")


def test_changes_requested_and_review_failure_close_the_current_evidence_package() -> None:
    for terminal_review_action in ("request-changes", "reject-review"):
        approvals = service()
        review(approvals, "claim-review")
        review(approvals, terminal_review_action)
        observed = state(approvals)
        evidence_review = observed["review"]
        governance = observed["governance"]
        assert isinstance(evidence_review, dict)
        assert isinstance(governance, dict)
        assert evidence_review["available_actions"] == []
        assert governance["available_actions"] == []
        with pytest.raises(ValueError, match="transition"):
            review(approvals, "claim-review")


def test_withdrawal_can_reuse_current_pass_but_return_requires_a_newer_pass() -> None:
    approvals = service()
    review(approvals, "claim-review")
    review(approvals, "approve-review")
    govern(approvals, "submit-governance")
    govern(approvals, "withdraw-governance")
    withdrawn = state(approvals)
    governance = withdrawn["governance"]
    assert isinstance(governance, dict)
    assert governance["available_actions"] == ["resubmit-governance"]
    govern(approvals, "resubmit-governance")
    govern(approvals, "return-governance")
    returned = state(approvals)
    evidence_review = returned["review"]
    governance = returned["governance"]
    assert isinstance(evidence_review, dict)
    assert isinstance(governance, dict)
    assert evidence_review["state"] == "pending-review"
    assert governance["available_actions"] == []
    with pytest.raises(ValueError, match="current passed"):
        govern(approvals, "resubmit-governance")

    review(approvals, "claim-review")
    review(approvals, "approve-review")
    govern(approvals, "resubmit-governance")
    assert state(approvals)["execution"] == {
        "state": "not-authorized",
        "authorized": False,
    }


def test_events_are_bound_to_the_current_evidence_digest() -> None:
    approvals = service()
    review(approvals, "claim-review")
    old_state = state(approvals)
    new_state = approvals.state("candidate.test", "1.0.0", "sha256:new-evidence")
    assert len(old_state["history"]) == 1  # type: ignore[arg-type]
    assert new_state["review"] == {
        "state": "pending-review",
        "available_actions": ["claim-review"],
    }
    assert new_state["history"] == []
