"""Independent development-only future target settlement for P3-STAGE-001-D.

The settlement ledger is deliberately separate from T-only semantic references.  It describes a
frozen future price-path target and is never allowed to become the semantic truth for stage 3/4.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from enum import Enum
from typing import Any

from quanthus.stage_learning.reference import ReferenceContext, ReferencePilotError
from quanthus.structure_v2 import EpisodeObservation
from quanthus.structure_v2_inputs import Admission, digest


class SettlementState(str, Enum):
    SETTLED = "settled"
    PENDING = "pending"
    CENSORED = "censored"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StageFutureSettlement:
    context_id: str
    state: SettlementState
    outcome: str | None
    anchor_date: date
    anchor_close: Decimal | None
    frozen_reference: Decimal
    horizon_sessions: int
    observed_sessions: int
    terminal_date: date | None
    terminal_reason: str
    source_member_digest: str

    @property
    def revision_id(self) -> str:
        return digest(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return {"revision_id": self.revision_id, **asdict(self)}


def _threshold(anchor: Decimal, relative: Decimal) -> Decimal:
    with localcontext(Context(prec=28)):
        return anchor * (Decimal(1) + relative)


def settle_reference_contexts(
    contexts: Sequence[ReferenceContext],
    episodes: Sequence[EpisodeObservation],
    admissions: Sequence[Admission],
    *,
    horizon_sessions: int,
    positive_threshold: Decimal,
    adverse_threshold: Decimal,
    same_session_overlap_policy: str,
) -> tuple[StageFutureSettlement, ...]:
    """Settle a frozen 20-session first-passage target using only development rows after T.

    The anchor is T close, not an execution price.  A close below the context's frozen reference is
    an independent structural-failure condition.  Any missing/invalid future business attempt before
    terminal resolution makes the path unavailable rather than silently neutral.
    """
    if horizon_sessions < 1:
        raise ReferencePilotError("settlement horizon must be positive")
    if positive_threshold <= 0 or adverse_threshold >= 0:
        raise ReferencePilotError("settlement thresholds must be +positive and -adverse")
    if same_session_overlap_policy != "failure_wins":
        raise ReferencePilotError("first stage target freezes same-session overlap to failure_wins")
    episode_by_context = {
        digest(
            {
                "security_code": item.security_code,
                "market_date": item.market_date,
                "anchor_scale": item.scale,
                "episode_id": item.episode_id,
                "anchor_revision": item.anchor_revision,
            }
        ): item
        for item in episodes
    }
    by_security: dict[str, list[Admission]] = {}
    by_key: dict[tuple[str, date], Admission] = {}
    for item in sorted(
        admissions,
        key=lambda value: (value.row.security_code, value.row.market_date),
    ):
        by_security.setdefault(item.row.security_code, []).append(item)
        by_key[(item.row.security_code, item.row.market_date)] = item
    output: list[StageFutureSettlement] = []
    for context in contexts:
        episode = episode_by_context.get(context.context_id)
        if episode is None:
            raise ReferencePilotError("settlement context cannot be resolved to episode")
        anchor_attempt = by_key.get((context.security_code, context.market_date))
        anchor_close = None if anchor_attempt is None else anchor_attempt.row.close
        if anchor_attempt is None or not anchor_attempt.admitted or anchor_close is None:
            output.append(
                StageFutureSettlement(
                    context.context_id,
                    SettlementState.UNAVAILABLE,
                    None,
                    context.market_date,
                    anchor_close,
                    episode.frozen_level,
                    horizon_sessions,
                    0,
                    None,
                    "anchor_business_row_unavailable",
                    digest([]),
                )
            )
            continue
        future = [
            item
            for item in by_security.get(context.security_code, ())
            if item.row.market_date > context.market_date
        ][:horizon_sessions]
        if len(future) < horizon_sessions:
            output.append(
                StageFutureSettlement(
                    context.context_id,
                    SettlementState.CENSORED,
                    None,
                    context.market_date,
                    anchor_close,
                    episode.frozen_level,
                    horizon_sessions,
                    len(future),
                    future[-1].row.market_date if future else None,
                    "development_tail_before_full_horizon",
                    digest([item.row.member_digest for item in future]),
                )
            )
            continue
        positive = _threshold(anchor_close, positive_threshold)
        adverse = _threshold(anchor_close, adverse_threshold)
        terminal: StageFutureSettlement | None = None
        members: list[str] = []
        for session_number, item in enumerate(future, start=1):
            members.append(item.row.member_digest)
            if item.row.reset_reason:
                terminal = StageFutureSettlement(
                    context.context_id,
                    SettlementState.CENSORED,
                    None,
                    context.market_date,
                    anchor_close,
                    episode.frozen_level,
                    horizon_sessions,
                    session_number,
                    item.row.market_date,
                    "corporate_action_coordinate_change",
                    digest(members),
                )
                break
            if not item.admitted or any(
                value is None for value in (item.row.high, item.row.low, item.row.close)
            ):
                terminal = StageFutureSettlement(
                    context.context_id,
                    SettlementState.UNAVAILABLE,
                    None,
                    context.market_date,
                    anchor_close,
                    episode.frozen_level,
                    horizon_sessions,
                    session_number,
                    item.row.market_date,
                    "future_business_evidence_unavailable",
                    digest(members),
                )
                break
            assert (
                item.row.high is not None
                and item.row.low is not None
                and item.row.close is not None
            )
            failure = item.row.low <= adverse or item.row.close < episode.frozen_level
            success = item.row.high >= positive
            if failure:
                terminal = StageFutureSettlement(
                    context.context_id,
                    SettlementState.SETTLED,
                    "failure",
                    context.market_date,
                    anchor_close,
                    episode.frozen_level,
                    horizon_sessions,
                    session_number,
                    item.row.market_date,
                    "adverse_threshold_or_frozen_reference_loss",
                    digest(members),
                )
                break
            if success:
                terminal = StageFutureSettlement(
                    context.context_id,
                    SettlementState.SETTLED,
                    "positive",
                    context.market_date,
                    anchor_close,
                    episode.frozen_level,
                    horizon_sessions,
                    session_number,
                    item.row.market_date,
                    "positive_first_passage",
                    digest(members),
                )
                break
        if terminal is None:
            terminal = StageFutureSettlement(
                context.context_id,
                SettlementState.SETTLED,
                "neutral",
                context.market_date,
                anchor_close,
                episode.frozen_level,
                horizon_sessions,
                horizon_sessions,
                future[-1].row.market_date,
                "no_terminal_condition_within_horizon",
                digest(members),
            )
        output.append(terminal)
    return tuple(output)


def settlement_ledger_summary(values: Sequence[StageFutureSettlement]) -> dict[str, int]:
    states = {state.value: 0 for state in SettlementState}
    outcomes = {"positive": 0, "neutral": 0, "failure": 0}
    for value in values:
        states[value.state.value] += 1
        if value.outcome in outcomes:
            outcomes[value.outcome] += 1
    return {**{f"state_{key}": count for key, count in states.items()}, **outcomes}
