"""Explicit two-clock R2 business ports; no historical-PIT or D admission.

The strict bridge, EventRecord and NativeJob remain strict. This module reuses
their numeric/field adapters after checking the distinct snapshot contract; it
does not create a legacy event or alter source knowledge/observation times.
Support declarations bind exact rows but do not themselves qualify the evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any

from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.backtest.inputs import P1DailyExecutionEvidence
from quanthus.contracts import SnapshotRef
from quanthus.event_library.rule_card import CandidateRuleCard
from quanthus.expert_strategy import p3_base24_bridge as strict
from quanthus.expert_strategy import p3_base24_native as native

VERSION = "quanthus.p3-retrospective-business-ports.v1"


@dataclass(frozen=True)
class RetrospectivePort:
    selected: strict.DecisionPort | None
    security_id: str
    listing_id: str
    business_date: date
    information_cutoff: datetime
    source_manifest_sha256: str
    row_sha256: str
    execution_only: bool
    unavailable_reasons: tuple[str, ...]


def decision_port(
    row: Mapping[str, Any],
    *,
    information_cutoff: datetime,
    source_manifest_sha256: str,
    support: strict.DailySupport | None,
    static_industry_id: str | None,
    warmup_dates: Sequence[date] = (),
) -> RetrospectivePort:
    """Translate verified snapshot input; a missing session stays unavailable."""
    day, role = row.get("trade_date"), row.get("r2_observation_role")
    through = row.get("r2_business_read_through")
    if (
        not strict._aware(information_cutoff)
        or row.get("r2_information_cutoff") != information_cutoff
        or type(day) is not date
        or type(through) is not date
        or not day <= through <= date(2026, 9, 18)
        or not native._SHA.fullmatch(source_manifest_sha256)
        or role
        != (
            "WARMUP"
            if day < date(2025, 1, 1)
            else "EXECUTION_ONLY"
            if day > date(2026, 9, 17)
            else "DECISION_HISTORY"
        )
        or (day < date(2025, 1, 1) and day not in warmup_dates)
    ):
        raise native.InputError("RETRO_PORT_BOUNDARY")
    if any(not strict._visible(row.get(k), information_cutoff) for k in strict._CLOCKS):
        raise native.InputError("RETRO_PORT_SNAPSHOT_NOT_VISIBLE")
    for name in (
        "knowledge_to",
        "listing_knowledge_to",
        "security_knowledge_to",
        "calendar_knowledge_to",
        "venue_knowledge_to",
        "state_knowledge_to",
    ):
        value = row.get(name)
        if value is not None and (not strict._aware(value) or value <= information_cutoff):
            raise native.InputError("RETRO_PORT_EXPIRED_VERSION")
    if (
        row.get("mic_code") not in {"XSHE", "XSHG"}
        or not (
            row.get("instrument_type") == "A_SHARE"
            or (
                row.get("instrument_type") == "common_equity"
                and row.get("instrument_currency") == "CNY"
            )
        )
        or row.get("interval_size") != "1d"
        or row.get("price_basis") not in {"none", "UNADJUSTED"}
    ):
        raise native.InputError("RETRO_PORT_MARKET_OR_BASIS")
    for name in ("security_id", "listing_id", "listing_code", "company_id", "venue_id"):
        if not isinstance(row.get(name), str) or not row[name].strip():
            raise native.InputError("RETRO_PORT_IDENTITY")
    start, end = row.get("observation_start"), row.get("observation_end")
    upper = datetime.combine(day + timedelta(days=1), time.min, native.TZ)
    if (
        not isinstance(start, datetime)
        or not isinstance(end, datetime)
        or not strict._aware(start)
        or not strict._aware(end)
        or start.astimezone(native.TZ).date() != day
        or not start < end <= upper
        or end > information_cutoff
    ):
        raise native.InputError("RETRO_PORT_DAILY_INTERVAL")
    reasons = []
    status = row.get("r2_calendar_status")
    if status != "OPEN":
        reasons.append("CALENDAR_CLOSED" if status == "CLOSED" else "CALENDAR_UNAVAILABLE")
    op, cl = row.get("open_at"), row.get("close_at")
    if (
        not isinstance(op, datetime)
        or not isinstance(cl, datetime)
        or not strict._aware(op)
        or not strict._aware(cl)
    ):
        reasons.append("OPEN_SESSION_CLOCK_UNAVAILABLE")
    elif (
        op.astimezone(native.TZ).date() != day
        or cl.astimezone(native.TZ).date() != day
        or not start <= op < cl <= end
        or cl > information_cutoff
    ):
        raise native.InputError("RETRO_PORT_SESSION_DATE")
    raw_digest = strict.row_digest(row)
    if reasons:
        selected = None
    else:
        selected = strict._selected_port(
            row,
            cutoff=information_cutoff,
            source_manifest_sha256=source_manifest_sha256,
            support=support,
            static_industry_id=static_industry_id,
            execution_only=role == "EXECUTION_ONLY",
        )
        reasons.extend(selected.unavailable_reasons)
    return RetrospectivePort(
        selected,
        row["security_id"],
        row["listing_id"],
        day,
        information_cutoff,
        source_manifest_sha256,
        raw_digest,
        role == "EXECUTION_ONLY",
        tuple(reasons),
    )


def common_entry_mask(
    ports: Sequence[RetrospectivePort],
    *,
    expected_dates: Sequence[date],
    card: CandidateRuleCard,
    feature: BaselineFeatureRow | None,
    feature_known_at: datetime | None,
    feature_evidence_digest: str,
) -> strict.SharedEntryMask:
    """Same frozen union rule for all four variants, at the declared R2 snapshot."""
    card.require_stage_b_authority()
    if card.semantic_digest != strict.RULE or card.computed_semantic_digest != strict.RULE:
        raise native.InputError("BRIDGE_FROZEN_RULE")
    if len(expected_dates) != 22 or tuple(expected_dates) != tuple(sorted(set(expected_dates))):
        raise native.InputError("RETRO_MASK_EXPECTED_DATES")
    if (
        any(p.execution_only for p in ports)
        or len({(p.information_cutoff, p.source_manifest_sha256) for p in ports}) > 1
    ):
        raise native.InputError("RETRO_MASK_ROLE_OR_SNAPSHOT")
    if not native._SHA.fullmatch(feature_evidence_digest):
        raise native.InputError("RETRO_MASK_FEATURE_DIGEST")
    if tuple(p.business_date for p in ports) != tuple(expected_dates) or any(
        p.selected is None for p in ports
    ):
        return strict.SharedEntryMask(
            tuple(sorted({"observation_gap"} | {r for p in ports for r in p.unavailable_reasons})),
            strict._digest(
                {
                    "version": VERSION,
                    "dates": list(expected_dates),
                    "rows": [p.row_sha256 for p in ports],
                    "feature": feature_evidence_digest,
                }
            ),
        )
    return strict._common_entry_mask(
        tuple(p.selected for p in ports if p.selected is not None),
        expected_dates=expected_dates,
        card=card,
        feature=feature,
        feature_known_at=feature_known_at,
        feature_evidence_digest=feature_evidence_digest,
        information_cutoff=ports[0].information_cutoff,
    )


def execution_evidence(
    port: RetrospectivePort,
    *,
    snapshot: SnapshotRef,
    decision_date: date,
    next_session_date: date,
    trading_dates: Sequence[date],
) -> P1DailyExecutionEvidence:
    """Role-isolated T+1 P1/P2 port, at the R2 information snapshot.

    A historical observation can be T's decision history and the preceding
    decision's execution input. This operation changes only its consumer role,
    never a source clock or value, and returns no strategy-decision object.
    """
    if (
        port.selected is None
        or port.business_date != next_session_date
        or not trading_dates
        or tuple(trading_dates) != tuple(sorted(set(trading_dates)))
        or decision_date not in trading_dates
    ):
        raise native.InputError("RETRO_EXECUTION_ROLE_OR_CLOCK")
    index = trading_dates.index(decision_date)
    if index + 1 >= len(trading_dates) or trading_dates[index + 1] != next_session_date:
        raise native.InputError("RETRO_EXECUTION_NEXT_SESSION")
    selected = replace(port.selected, candidate=None, execution_only=True)
    return strict.execution_evidence(
        selected,
        snapshot=snapshot,
        open_at=selected.open_at,
        close_at=selected.close_at,
        decision_date=decision_date,
        next_session_date=next_session_date,
    )
