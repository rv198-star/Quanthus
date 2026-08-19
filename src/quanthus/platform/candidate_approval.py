"""Independent evidence-review and governance workflows for candidate versions."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from quanthus.platform.identity import AuthenticatedPrincipal
from quanthus.platform.models import CandidateApprovalEventRecord
from quanthus.platform.persistence import PlatformStore

_REVIEW_ACTION_STATE = {
    "claim-review": "reviewing",
    "request-changes": "changes-requested",
    "approve-review": "review-passed",
    "reject-review": "review-failed",
}
_GOVERNANCE_ACTION_STATE = {
    "submit-governance": "governance-under-review",
    "resubmit-governance": "governance-under-review",
    "return-governance": "governance-returned",
    "withdraw-governance": "governance-withdrawn",
    "reject-governance": "governance-rejected",
    "admit-governance": "governance-admitted",
}
_TERMINAL_GOVERNANCE_STATES = {"governance-rejected", "governance-admitted"}
_REVIEW_TRANSITIONS = {
    "pending-review": ("claim-review",),
    "reviewing": ("request-changes", "approve-review", "reject-review"),
}
_GOVERNANCE_TRANSITIONS = {
    "governance-not-submitted": ("submit-governance",),
    "governance-under-review": (
        "return-governance",
        "withdraw-governance",
        "reject-governance",
        "admit-governance",
    ),
    "governance-returned": ("resubmit-governance",),
    "governance-withdrawn": ("resubmit-governance",),
}


def public_candidate_approval_event(
    event: CandidateApprovalEventRecord,
) -> dict[str, object]:
    value = asdict(event)
    value["created_at"] = event.created_at.isoformat()
    return value


class CandidateApprovalService:
    def __init__(
        self,
        store: PlatformStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def history(
        self, strategy_id: str, strategy_version: str
    ) -> tuple[CandidateApprovalEventRecord, ...]:
        return self._store.candidate_approval_events(strategy_id, strategy_version)

    def _evidence_events(
        self,
        strategy_id: str,
        strategy_version: str,
        evidence_digest: str,
    ) -> tuple[CandidateApprovalEventRecord, ...]:
        return tuple(
            event
            for event in self.history(strategy_id, strategy_version)
            if event.evidence_digest == evidence_digest
        )

    def state(
        self,
        strategy_id: str,
        strategy_version: str,
        evidence_digest: str,
    ) -> dict[str, object]:
        events = self._evidence_events(strategy_id, strategy_version, evidence_digest)
        review_events = tuple(event for event in events if event.axis == "evidence-review")
        governance_events = tuple(event for event in events if event.axis == "governance")
        review_state = review_events[-1].state if review_events else "pending-review"
        governance_state = (
            governance_events[-1].state if governance_events else "governance-not-submitted"
        )
        if governance_state == "governance-returned" and (
            not review_events or review_events[-1].created_at <= governance_events[-1].created_at
        ):
            review_state = "pending-review"
        review_actions = (
            ()
            if governance_state in _TERMINAL_GOVERNANCE_STATES
            else _REVIEW_TRANSITIONS.get(review_state, ())
        )
        governance_actions = _GOVERNANCE_TRANSITIONS.get(governance_state, ())
        if review_state != "review-passed":
            governance_actions = tuple(
                action
                for action in governance_actions
                if action not in {"submit-governance", "resubmit-governance"}
            )
        return {
            "replay": {
                "state": "replay-succeeded",
                "evidence_digest": evidence_digest,
            },
            "review": {
                "state": review_state,
                "available_actions": list(review_actions),
            },
            "governance": {
                "state": governance_state,
                "package_id": governance_events[-1].package_id if governance_events else None,
                "admitted": governance_state == "governance-admitted",
                "terminal": governance_state in _TERMINAL_GOVERNANCE_STATES,
                "available_actions": list(governance_actions),
            },
            "execution": {"state": "not-authorized", "authorized": False},
            "history": [public_candidate_approval_event(event) for event in events],
        }

    @staticmethod
    def _note(action: str, note: str) -> str:
        normalized = note.strip()
        if action != "claim-review" and len(normalized) < 8:
            raise ValueError("candidate approval note must contain at least 8 characters")
        if len(normalized) > 2000:
            raise ValueError("candidate approval note is too long")
        return normalized

    def _append(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        evidence_digest: str,
        axis: str,
        action: str,
        state: str,
        note: str,
        package_id: str | None,
        principal: AuthenticatedPrincipal,
        request_id: str | None,
    ) -> CandidateApprovalEventRecord:
        history = self.history(strategy_id, strategy_version)
        created_at = self._clock()
        if history and created_at <= history[-1].created_at:
            created_at = history[-1].created_at + timedelta(microseconds=1)
        event = CandidateApprovalEventRecord(
            event_id=f"cae_{uuid.uuid4().hex}",
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            evidence_digest=evidence_digest,
            axis=axis,
            action=action,
            state=state,
            note=self._note(action, note),
            package_id=package_id,
            actor_user_id=principal.user_id,
            actor_username=principal.username,
            request_id=request_id,
            created_at=created_at,
        )
        self._store.append_candidate_approval_event(event)
        return event

    def record_review(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        evidence_digest: str,
        action: str,
        note: str,
        principal: AuthenticatedPrincipal,
        request_id: str | None,
    ) -> CandidateApprovalEventRecord:
        if action not in _REVIEW_ACTION_STATE:
            raise ValueError("candidate review action is invalid")
        current = self.state(strategy_id, strategy_version, evidence_digest)
        review_state = str(current["review"]["state"])  # type: ignore[index]
        governance_state = str(current["governance"]["state"])  # type: ignore[index]
        if governance_state in _TERMINAL_GOVERNANCE_STATES:
            raise ValueError("terminal governance decision cannot be reviewed again")
        if action not in _REVIEW_TRANSITIONS.get(review_state, ()):
            raise ValueError(f"candidate review transition {review_state} -> {action} is invalid")
        return self._append(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            evidence_digest=evidence_digest,
            axis="evidence-review",
            action=action,
            state=_REVIEW_ACTION_STATE[action],
            note=note,
            package_id=None,
            principal=principal,
            request_id=request_id,
        )

    def record_governance(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        evidence_digest: str,
        action: str,
        note: str,
        principal: AuthenticatedPrincipal,
        request_id: str | None,
    ) -> CandidateApprovalEventRecord:
        if action not in _GOVERNANCE_ACTION_STATE:
            raise ValueError("candidate governance action is invalid")
        current = self.state(strategy_id, strategy_version, evidence_digest)
        review_state = str(current["review"]["state"])  # type: ignore[index]
        governance = current["governance"]  # type: ignore[assignment]
        governance_state = str(governance["state"])  # type: ignore[index]
        if action not in _GOVERNANCE_TRANSITIONS.get(governance_state, ()):
            raise ValueError(
                f"candidate governance transition {governance_state} -> {action} is invalid"
            )
        if action in {"submit-governance", "resubmit-governance"} and review_state != (
            "review-passed"
        ):
            raise ValueError("governance submission requires a current passed evidence review")
        package_id: str | None = (
            f"gpk_{uuid.uuid4().hex}"
            if action in {"submit-governance", "resubmit-governance"}
            else str(governance["package_id"])
        )
        if not package_id or package_id == "None":
            raise ValueError("governance action requires an evidence package")
        return self._append(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            evidence_digest=evidence_digest,
            axis="governance",
            action=action,
            state=_GOVERNANCE_ACTION_STATE[action],
            note=note,
            package_id=package_id,
            principal=principal,
            request_id=request_id,
        )
