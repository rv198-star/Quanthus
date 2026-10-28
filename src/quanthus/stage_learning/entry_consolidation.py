"""Fixed development-only entry diagnostics; no orders, fit or live admission.

The caller freezes actual B opportunities and source/exposure qualification first.
All controls share one immutable forty-session Facts value. Different decisions
are a feasibility observation, not evidence of economic or recognition quality.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from quanthus.stage_learning.head2_facts import Head2WindowFacts, build_head2_window_facts
from quanthus.stage_learning.head2_revision import FEATURE_NAMES, encode_reference_facts
from quanthus.stage_learning.head_comparison import stage2_predicate
from quanthus.stage_learning.local_head import HeadCheckpoint, predict_head
from quanthus.structure_v2_inputs import Admission, digest

VERSION = "entry-consolidation-development-v1"
RAW_THRESHOLD = 0.5


@dataclass(frozen=True)
class EntryOpportunity:
    event_id: str
    security_code: str
    issuer_id: str
    signal_date: date
    baseline_allowed: bool
    baseline_decision_digest: str


@dataclass(frozen=True)
class EntryDiagnostic:
    opportunity: EntryOpportunity
    anchor: date
    earliest_calendar_execution: date | None
    facts_digest: str
    model_digest: str
    rule_positive: bool | None
    raw_score: float | None
    c0_allowed: bool
    c1_allowed: bool
    c2_allowed: bool
    reason: str
    revision: str
    consumer_admission: str = "NOT_GRANTED"


def pre_entry_window(sessions: Sequence[date], signal: date) -> tuple[date, ...]:
    """Require the real calendar and choose T−1, never include the signal bar."""
    if not sessions or list(sessions) != sorted(set(sessions)) or signal not in sessions:
        raise ValueError("ENTRY_CALENDAR_IDENTITY")
    if not date(2012, 1, 1) <= signal <= date(2021, 12, 30):
        raise ValueError("ENTRY_DEVELOPMENT_SCOPE")
    index = sessions.index(signal)
    if index < 40:
        raise ValueError("ENTRY_REQUIRED40_UNAVAILABLE")
    return tuple(sessions[index - 40:index])


def diagnose_entry(
    opportunity: EntryOpportunity,
    *,
    sessions: Sequence[date],
    admissions: Sequence[Admission],
    checkpoint: HeadCheckpoint,
    rule: dict[str, Any],
) -> EntryDiagnostic:
    return diagnose_entry_with_facts(
        opportunity, sessions=sessions, admissions=admissions, checkpoint=checkpoint, rule=rule
    )[0]


def diagnose_entry_with_facts(
    opportunity: EntryOpportunity,
    *,
    sessions: Sequence[date],
    admissions: Sequence[Admission],
    checkpoint: HeadCheckpoint,
    rule: dict[str, Any],
) -> tuple[EntryDiagnostic, Head2WindowFacts]:
    """Only withhold a proposed entry; preserve B's rejection and existing exits."""
    if (
        type(opportunity.baseline_allowed) is not bool
        or not all((opportunity.event_id, opportunity.security_code,
                    opportunity.issuer_id, opportunity.baseline_decision_digest))
    ):
        raise ValueError("ENTRY_OPPORTUNITY_IDENTITY")
    window = pre_entry_window(sessions, opportunity.signal_date)
    if tuple(a.row.market_date for a in admissions) != window or any(
        a.row.security_code != opportunity.security_code for a in admissions
    ):
        raise ValueError("ENTRY_T_MINUS_ONE_OR_SECURITY")
    checkpoint.validate()
    if not checkpoint.complete or checkpoint.policy.feature_names != FEATURE_NAMES:
        raise ValueError("ENTRY_SEVEN_FEATURE_CHECKPOINT")
    # Every recorded fit/update/validation observation must precede the anchor.
    if max(date.fromisoformat(u.anchor) for u in checkpoint.uses) >= window[-1]:
        raise ValueError("ENTRY_MODEL_TIME_ORDER")
    facts = build_head2_window_facts(admissions, anchor=window[-1])
    positive, score = None, None
    reason = facts.reason
    if facts.available:
        primary, path = facts.reference_facts()
        encoding = encode_reference_facts(
            primary, path, anchor=window[-1].isoformat(),
            source_digest=facts.source_closure_digest,
        )
        if encoding.values is not None:
            positive = stage2_predicate(
                {name: Decimal(record["value"]) for name, record in primary.items()}, rule
            )
            score = predict_head(checkpoint, encoding.values).raw_score
            reason = "AVAILABLE_REUSED_DEVELOPMENT_ONLY"
        else:
            reason = encoding.reason
    index = sessions.index(opportunity.signal_date)
    execution = sessions[index + 1] if index + 1 < len(sessions) else None
    c0 = opportunity.baseline_allowed
    result = EntryDiagnostic(
        opportunity=opportunity, anchor=window[-1], earliest_calendar_execution=execution,
        facts_digest=facts.digest, model_digest=checkpoint.digest,
        rule_positive=positive, raw_score=score, c0_allowed=c0,
        c1_allowed=c0 and positive is True,
        c2_allowed=c0 and score is not None and score >= RAW_THRESHOLD,
        reason=reason, revision="",
    )
    return replace(result, revision=digest({"version": VERSION, **asdict(result)})), facts


def summarize(rows: Sequence[EntryDiagnostic]) -> dict[str, Any]:
    """Count the complete denominator; unknown inputs never create known deltas."""
    if len({r.opportunity.event_id for r in rows}) != len(rows):
        raise ValueError("ENTRY_DUPLICATE_OPPORTUNITY")
    known = [r for r in rows if r.rule_positive is not None and r.raw_score is not None]
    delta = [r for r in known if r.c1_allowed != r.c2_allowed]
    issuers = Counter(r.opportunity.issuer_id for r in delta)
    days = Counter(r.opportunity.signal_date.isoformat() for r in delta)
    largest_issuer = max(issuers.values(), default=0)
    largest_date = max(days.values(), default=0)
    if not rows:
        decision = "STOP_NO_QUALIFIED_OPPORTUNITIES"
    elif not delta:
        decision = "STOP_NO_KNOWN_INCREMENTAL_DECISION"
    elif len(delta) == largest_issuer or len(delta) == largest_date:
        decision = "STOP_CONCENTRATED_INCREMENTAL_DECISION"
    else:
        decision = "PROPOSE_INDEPENDENT_QUALIFICATION_ONLY"
    return {
        "version": VERSION, "opportunities": len(rows), "known_inputs": len(known),
        "unknown_inputs": len(rows) - len(known),
        "baseline_allowed": sum(r.c0_allowed for r in rows),
        "rule_allowed": sum(r.c1_allowed for r in rows),
        "model_allowed": sum(r.c2_allowed for r in rows),
        "known_c1_c2_differences": len(delta), "difference_issuers": len(issuers),
        "difference_dates": len(days),
        "differences_after_largest_issuer_removed": len(delta) - largest_issuer,
        "differences_after_largest_date_removed": len(delta) - largest_date,
        "unknown_reason_counts": dict(sorted(Counter(
            r.reason for r in rows if r.rule_positive is None or r.raw_score is None
        ).items())),
        "decision": decision, "consumer_admission": "NOT_GRANTED",
        "historical_PIT_claim": False, "economic_value_evaluated": False,
        "results_digest": digest([asdict(r) for r in rows]),
    }
