"""Small native-to-P4/P2 ports, with one complete-case mask for B/T/R/TR.

These pure ports consume already selected native versions. They do not qualify
historical clocks, calculate replacement factors, select sources, or start a
research run. The source package remains the once-only shared prerequisite.
Unknown support stays unavailable; a native can_buy flag is not P2 admission.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.backtest.inputs import CoverageStatus, P1DailyExecutionEvidence
from quanthus.backtest.strategy_replay import PITDailyBar
from quanthus.contracts import SnapshotRef
from quanthus.event_library.rule_card import CandidateRuleCard
from quanthus.event_library.scan import CandidateDailyInput, _window_reason
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as native

VERSION = "quanthus.p3-base24-domain-ports.v1"
RULE = "sha256:b04464c838705e71331515332ceb9e796ce26698c784d4615cff00dfdcf41c50"
VARIANTS = ("B", "T", "R", "TR")
REQUIRED_FEATURES = (
    "total_return_index",
    "sma20",
    "sma60",
    "momentum20",  # Trend, exact v1
    "rsi14",
    "realized_volatility20",  # Position Risk, including B/T mask
)
_CLOCKS = (
    "bar_knowledge_from",
    "listing_knowledge_from",
    "security_knowledge_from",
    "calendar_knowledge_from",
    "venue_knowledge_from",
)


def _aware(value: Any) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _visible(clock: Any, cutoff: datetime) -> bool:
    return _aware(clock) and clock <= cutoff


def _digest(value: Any) -> str:
    """Typed evidence identity: preserve Decimal scale and timestamp instants."""

    def encode(item: Any) -> Any:
        if isinstance(item, Decimal):
            return {"Decimal": str(item)}
        if isinstance(item, datetime):
            return {"datetime": item.isoformat()}
        if isinstance(item, date):
            return {"date": item.isoformat()}
        if isinstance(item, Mapping):
            return {str(k): encode(v) for k, v in item.items()}
        if isinstance(item, (tuple, list)):
            return [encode(v) for v in item]
        return item

    return digest_value(encode(value))


def row_digest(row: Mapping[str, Any]) -> str:
    return _digest(row)


def ports_for_dates(
    view: native.NativeView,
    *,
    cutoff: datetime,
    dates: Sequence[date],
    supports: Mapping[tuple[str, date], DailySupport],
    static_industry: Mapping[str, str],
) -> Iterator[DecisionPort]:
    """One bounded scan of verified job-local input; stream once to shared TMP.

    Missing observations stay absent and produce a gap in common_entry_mask;
    do not construct the authoritative date list from observed bar membership.
    This is still an engineering port, not formal candidate/B/D admission.
    """
    m = view.prepared_manifest
    if m is None or native._sha(view.root / "manifest.json") != m["manifest_sha256"]:
        raise native.InputError("BRIDGE_VERIFIED_PREPARED_INPUT_REQUIRED")
    if (
        not _aware(cutoff)
        or not 1 <= len(dates) <= 61
        or tuple(dates) != tuple(sorted(set(dates)))
        or dates[-1] != cutoff.astimezone(native.TZ).date()
    ):
        raise native.InputError("BRIDGE_DATE_SELECTION")
    plan_path = view.root / "run-plan.json"
    native._check_member(plan_path, m["files"]["run-plan.json"])
    warmup = native._json(plan_path)["warmup_calendar"]
    allowed_warmup = {s["trade_date"] for s in native._warmup_binding(warmup)}
    if any(d < date(2025, 1, 1) and d.isoformat() not in allowed_warmup for d in dates):
        raise native.InputError("BRIDGE_WARMUP_SCOPE")
    selected = frozenset(dates)
    for batch in view.market_batches(cutoff):
        for row in batch.to_pylist():
            native._deadline(view.deadline)
            if row["trade_date"] in selected:
                yield decision_port(
                    row,
                    cutoff=cutoff,
                    source_manifest_sha256=m["identity"]["source_manifest_sha256"],
                    support=supports.get((row["listing_id"], row["trade_date"])),
                    static_industry_id=static_industry.get(row["security_id"]),
                    warmup_authority=warmup,
                )
    view._unchanged()
    native._check_member(plan_path, m["files"]["run-plan.json"], deadline=view.deadline)
    if native._sha(view.root / "manifest.json", deadline=view.deadline) != m["manifest_sha256"]:
        raise native.InputError("BRIDGE_PREPARED_METADATA_CHANGED")


@dataclass(frozen=True)
class DailySupport:
    """Exact-key support supplied by a separately qualified consumer input.

    This object is a port, not a qualifier. Its evidence digest must be part of
    the B input closure before real use; assigning this dataclass grants no B/D.
    No later P6 snapshot may stand in for historical support.
    """

    source_manifest_sha256: str
    row_sha256: str
    evidence_sha256: str
    listing_id: str
    market_date: date
    listing_sessions: int | None = None
    listing_sessions_known_at: datetime | None = None
    adjustment_effective: bool | None = None
    adjustment_known_at: datetime | None = None
    execution_known_at: datetime | None = None
    entry_policy_allows: bool | None = None
    is_executable: bool | None = None
    upper_limit_price: Decimal | None = None
    lower_limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not all(
            native._SHA.fullmatch(x)
            for x in (self.source_manifest_sha256, self.row_sha256, self.evidence_sha256)
        ):
            raise native.InputError("BRIDGE_SUPPORT_DIGEST")
        if not self.listing_id or type(self.market_date) is not date:
            raise native.InputError("BRIDGE_SUPPORT_KEY")
        if self.listing_sessions is not None and (
            type(self.listing_sessions) is not int or self.listing_sessions < 0
        ):
            raise native.InputError("BRIDGE_LISTING_SESSIONS")
        for v in (self.adjustment_effective, self.entry_policy_allows, self.is_executable):
            if v is not None and type(v) is not bool:
                raise native.InputError("BRIDGE_SUPPORT_BOOLEAN")
        for instant in (
            self.listing_sessions_known_at,
            self.adjustment_known_at,
            self.execution_known_at,
        ):
            if instant is not None and not _aware(instant):
                raise native.InputError("BRIDGE_SUPPORT_CLOCK")
        for bound in (self.upper_limit_price, self.lower_limit_price):
            if bound is not None and (
                not isinstance(bound, Decimal) or not bound.is_finite() or bound <= 0
            ):
                raise native.InputError("BRIDGE_SUPPORT_PRICE")
        if (
            self.upper_limit_price is not None
            and self.lower_limit_price is not None
            and self.upper_limit_price <= self.lower_limit_price
        ):
            raise native.InputError("BRIDGE_SUPPORT_BOUNDS")


@dataclass(frozen=True)
class DecisionPort:
    candidate: CandidateDailyInput | None
    bar: PITDailyBar
    source_evidence_digest: str
    unavailable_reasons: tuple[str, ...]
    source_manifest_sha256: str
    selected_at: datetime
    open_at: datetime
    close_at: datetime
    execution_only: bool
    adjustment_effective: bool | None = None


def decision_port(
    row: Mapping[str, Any],
    *,
    cutoff: datetime,
    source_manifest_sha256: str,
    support: DailySupport | None,
    static_industry_id: str | None,
    warmup_authority: dict[str, Any] | None = None,
    execution_only: bool = False,
) -> DecisionPort:
    """Translate one visible native join. Never project a late clock to T close."""
    if not _aware(cutoff):
        raise native.InputError("BRIDGE_AWARE_CUTOFF")
    if not native._SHA.fullmatch(source_manifest_sha256):
        raise native.InputError("BRIDGE_SOURCE_DIGEST")
    if type(execution_only) is not bool:
        raise native.InputError("BRIDGE_ROLE")
    end = date(2026, 9, 18) if execution_only else date(2026, 9, 17)
    if not date(2025, 1, 1) <= cutoff.astimezone(native.TZ).date() <= end:
        raise native.InputError("BRIDGE_FROZEN_CUTOFF")
    if any(not _visible(row.get(k), cutoff) for k in _CLOCKS) or any(
        row.get(k) is not None and (not _aware(row[k]) or row[k] <= cutoff)
        for k in (
            "knowledge_to",
            "listing_knowledge_to",
            "security_knowledge_to",
            "calendar_knowledge_to",
            "venue_knowledge_to",
        )
    ):
        raise native.InputError("BRIDGE_NOT_VISIBLE")
    day = row.get("trade_date")
    if type(day) is not date or not all(
        _aware(row.get(k)) for k in ("observation_start", "observation_end", "open_at", "close_at")
    ):
        raise native.InputError("BRIDGE_SESSION_CLOCK")
    if day < date(2025, 1, 1):
        warmup_sessions = native._warmup_binding(warmup_authority)
        if day.isoformat() not in {s["trade_date"] for s in warmup_sessions}:
            raise native.InputError("BRIDGE_WARMUP_SCOPE")
    elif day > end:
        raise native.InputError("BRIDGE_FROZEN_ROW")
    if (
        row["observation_start"].astimezone(native.TZ).date() != day
        or row["open_at"].astimezone(native.TZ).date() != day
        or row["close_at"].astimezone(native.TZ).date() != day
        or not row["open_at"] < row["close_at"]
        or row["observation_end"] > cutoff
        or row["close_at"] > cutoff
    ):
        raise native.InputError("BRIDGE_SESSION_DATE")
    if row.get("interval_size") != "1d" or row.get("price_basis") not in {"none", "UNADJUSTED"}:
        raise native.InputError("BRIDGE_PRICE_BASIS")
    if row.get("instrument_type") != "A_SHARE" or row.get("mic_code") not in {"XSHE", "XSHG"}:
        raise native.InputError("BRIDGE_MARKET_SCOPE")
    for key in ("security_id", "listing_id", "listing_code", "company_id", "venue_id"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise native.InputError("BRIDGE_IDENTITY")
    return _selected_port(
        row,
        cutoff=cutoff,
        source_manifest_sha256=source_manifest_sha256,
        support=support,
        static_industry_id=static_industry_id,
        execution_only=execution_only,
    )


def _selected_port(
    row: Mapping[str, Any],
    *,
    cutoff: datetime,
    source_manifest_sha256: str,
    support: DailySupport | None,
    static_industry_id: str | None,
    execution_only: bool,
) -> DecisionPort:
    """Pure field port after the caller validates its own time/scope contract.

    Strict decision_port always validates historical T first. The explicitly
    separate retrospective port validates the source snapshot and business date;
    it never edits a clock to pass the strict entrypoint.
    """
    day = row["trade_date"]
    raw_digest = row_digest(row)
    if support is not None and (
        support.source_manifest_sha256 != source_manifest_sha256
        or support.row_sha256 != raw_digest
        or support.listing_id != row["listing_id"]
        or support.market_date != day
    ):
        raise native.InputError("BRIDGE_SUPPORT_BINDING")
    values = [row.get(k) for k in ("open", "high", "low", "close", "volume", "turnover")]
    if any(v is not None and (not isinstance(v, Decimal) or not v.is_finite()) for v in values):
        raise native.InputError("BRIDGE_DECIMAL_REQUIRED")
    op, high, low, close, volume, amount = values
    valid = (
        all(v is not None and v > 0 for v in (op, high, low, close))
        and volume is not None
        and volume >= 0
        and amount is not None
        and amount >= 0
    )
    if valid:
        assert op is not None and high is not None and low is not None and close is not None
        valid = low <= op <= high and low <= close <= high
    state_visible = _visible(row.get("state_knowledge_from"), cutoff) and (
        row.get("state_knowledge_to") is None
        or (_aware(row["state_knowledge_to"]) and row["state_knowledge_to"] > cutoff)
    )
    state = row.get("is_tradable") if state_visible else None
    for key in ("is_tradable", "can_buy", "can_sell"):
        if row.get(key) is not None and type(row[key]) is not bool:
            raise native.InputError("BRIDGE_STATE_BOOLEAN")
    reasons = []
    if state is None:
        reasons.append("STATE_UNAVAILABLE")
    sessions = None
    adjustment = None
    clocks = [row[k] for k in _CLOCKS]
    if state_visible:
        clocks.append(row["state_knowledge_from"])
    if support is not None:
        if _visible(support.listing_sessions_known_at, cutoff):
            sessions = support.listing_sessions
            clocks.append(support.listing_sessions_known_at)
        if _visible(support.adjustment_known_at, cutoff):
            adjustment = support.adjustment_effective
            clocks.append(support.adjustment_known_at)
    if sessions is None:
        reasons.append("LISTING_SESSION_UNAVAILABLE")
    if adjustment is None:
        reasons.append("ADJUSTMENT_BOUNDARY_UNAVAILABLE")
    execution_visible = bool(
        support is not None
        and _visible(support.execution_known_at, cutoff)
        and support.entry_policy_allows is not None
        and support.is_executable is not None
    )
    executable = bool(
        execution_visible
        and support is not None
        and support.is_executable is True
        and state is True
        and row.get("can_buy") is True
        and row.get("can_sell") is True
        and adjustment is False
    )
    entry = bool(
        execution_visible
        and support is not None
        and support.entry_policy_allows is True
        and state is True
        and row.get("can_buy") is True
        and adjustment is False
    )
    liquid = volume is not None and amount is not None and volume > 0 and amount > 0
    if not execution_visible:
        reasons.append("EXECUTION_QUALIFICATION_UNAVAILABLE")
    evidence = _digest(
        {
            "version": VERSION,
            "source": source_manifest_sha256,
            "row": raw_digest,
            "support": None if support is None else vars(support),
            "static_industry_id": static_industry_id,
        }
    )
    venue = {"XSHG": "SSE", "XSHE": "SZSE"}[row["mic_code"]]
    candidate = (
        None
        if execution_only
        else CandidateDailyInput(
            security_id=row["security_id"],
            listing_id=row["listing_id"],
            listing_code=row["listing_code"],
            company_id=row["company_id"],
            venue_id=venue,
            instrument_type="common_equity",
            static_level1_industry_id=static_industry_id,
            session_date=day,
            close_at=row["close_at"].astimezone(native.TZ),
            knowledge_cutoff=max(clocks),
            open_price=op,
            high_price=high,
            low_price=low,
            close_price=close,
            turnover=amount,
            is_tradable=state,
            listing_sessions=sessions,
            adjustment_effective=adjustment,
            source_evidence_digest=evidence,
        )
    )
    bar = PITDailyBar(
        security_code=row["security_id"],
        exchange=venue,
        market_date=day,
        coverage_status=(
            CoverageStatus.TRADING
            if state is True
            else CoverageStatus.SUSPENDED
            if state is False
            else CoverageStatus.MISSING
        ),
        data_valid=valid,
        is_executable=executable and valid and liquid,
        entry_policy_allows=entry and valid and liquid,
        reason_code=(
            "STATE_UNAVAILABLE"
            if state is None
            else "session_not_tradable"
            if not state
            else "invalid_bar"
            if not valid
            else "zero_liquidity"
            if not liquid
            else "EXECUTION_QUALIFICATION_UNAVAILABLE"
            if not execution_visible
            else "ADJUSTMENT_EXECUTION_UNAVAILABLE"
            if adjustment is not False
            else "NATIVE_EXECUTION_STATE_UNAVAILABLE"
            if any(row.get(key) is not True for key in ("can_buy", "can_sell"))
            else "ENTRY_POLICY_BLOCKED"
            if not entry or not executable
            else "qualified_native_daily_support"
        ),
        open_price=op,
        high_price=high,
        low_price=low,
        close_price=close,
        volume=volume,
        amount=amount,
        upper_limit_price=(support.upper_limit_price if execution_visible and support else None),
        lower_limit_price=(support.lower_limit_price if execution_visible and support else None),
    )
    return DecisionPort(
        candidate,
        bar,
        evidence,
        tuple(reasons),
        source_manifest_sha256,
        cutoff,
        row["open_at"],
        row["close_at"],
        execution_only,
        adjustment,
    )


@dataclass(frozen=True)
class SharedEntryMask:
    reasons: tuple[str, ...]
    evidence_digest: str

    @property
    def eligible(self) -> bool:
        return not self.reasons

    def for_variant(self, variant_id: str) -> bool:
        if variant_id not in VARIANTS:
            raise native.InputError("BRIDGE_VARIANT")
        return self.eligible


def common_entry_mask(
    ports: Sequence[DecisionPort],
    *,
    expected_dates: Sequence[date],
    card: CandidateRuleCard,
    feature: BaselineFeatureRow | None,
    feature_known_at: datetime | None,
    feature_evidence_digest: str,
) -> SharedEntryMask:
    """Strict historical T-close mask; retrospective input is rejected."""
    return _common_entry_mask(
        ports,
        expected_dates=expected_dates,
        card=card,
        feature=feature,
        feature_known_at=feature_known_at,
        feature_evidence_digest=feature_evidence_digest,
        information_cutoff=None,
    )


def _common_entry_mask(
    ports: Sequence[DecisionPort],
    *,
    expected_dates: Sequence[date],
    card: CandidateRuleCard,
    feature: BaselineFeatureRow | None,
    feature_known_at: datetime | None,
    feature_evidence_digest: str,
    information_cutoff: datetime | None,
) -> SharedEntryMask:
    """Union of candidate + Trend + Position Risk evidence, computed once."""
    if len(ports) > 22 or len(expected_dates) != 22:
        raise native.InputError("BRIDGE_EXACT_CANDIDATE_HISTORY")
    card.require_stage_b_authority()
    if card.semantic_digest != RULE or card.computed_semantic_digest != RULE:
        raise native.InputError("BRIDGE_FROZEN_RULE")
    if (
        len({p.source_manifest_sha256 for p in ports}) > 1
        or len({p.selected_at for p in ports}) > 1
    ):
        raise native.InputError("BRIDGE_MIXED_SOURCE_OR_CUTOFF")
    if tuple(expected_dates) != tuple(sorted(set(expected_dates))):
        raise native.InputError("BRIDGE_EXPECTED_DATES")
    if not native._SHA.fullmatch(feature_evidence_digest):
        raise native.InputError("BRIDGE_FEATURE_DIGEST")
    if not ports:
        return SharedEntryMask(
            ("observation_gap",),
            _digest(
                {
                    "version": VERSION,
                    "expected_dates": list(expected_dates),
                    "rows": [],
                    "feature_evidence": feature_evidence_digest,
                }
            ),
        )
    reasons: list[str] = []
    candidates = [p.candidate for p in ports]
    current = ports[-1]
    if (
        information_cutoff is None
        and current.bar.market_date == expected_dates[-1]
        and current.selected_at != current.close_at
    ):
        raise native.InputError("BRIDGE_T_CLOSE_REQUIRED")
    if any(c is None for c in candidates):
        reasons.append("CANDIDATE_EVIDENCE_UNAVAILABLE")
    else:
        window = tuple(c for c in candidates if c is not None)
        if len({(c.security_id, c.listing_id, c.venue_id, c.company_id) for c in window}) != 1:
            raise native.InputError("BRIDGE_MIXED_IDENTITY")
        reason = _window_reason(
            window, expected_dates=expected_dates, card=card, information_cutoff=information_cutoff
        )
        if reason is not None:
            reasons.append(reason.value)
        industry = window[-1].static_level1_industry_id
        if not isinstance(industry, str) or not industry.strip():
            reasons.append("static_industry_missing")
    if not current.bar.entry_policy_allows or not current.bar.is_executable:
        reasons.append("ENTRY_SUPPORT_UNAVAILABLE")
    if feature is None:
        reasons.append("FEATURE_UNAVAILABLE")
    else:
        if (
            feature.security_id != current.bar.security_code
            or feature.market_date != expected_dates[-1]
        ):
            raise native.InputError("BRIDGE_FEATURE_KEY")
        cutoff = (
            information_cutoff
            if information_cutoff is not None
            else current.candidate.close_at
            if current.candidate
            else None
        )
        if cutoff is None or not _visible(feature_known_at, cutoff):
            reasons.append("FEATURE_CLOCK_UNAVAILABLE")
        if not feature.feature_ready or not feature.entry_eligible:
            reasons.append("FEATURE_NOT_READY")
        for field in REQUIRED_FEATURES:
            v = getattr(feature, field)
            if v is None or not isinstance(v, Decimal) or not v.is_finite():
                reasons.append("FEATURE_MISSING:" + field)
    unique = tuple(sorted(set(reasons)))
    return SharedEntryMask(
        unique,
        _digest(
            {
                "version": VERSION,
                "request": native.REQUEST,
                "rows": [p.source_evidence_digest for p in ports],
                "expected_dates": list(expected_dates),
                "candidate_rule": card.semantic_digest,
                "feature": None if feature is None else vars(feature),
                "feature_known_at": feature_known_at,
                "feature_evidence": feature_evidence_digest,
                "reasons": unique,
            }
        ),
    )


def execution_evidence(
    port: DecisionPort,
    *,
    snapshot: SnapshotRef,
    open_at: datetime,
    close_at: datetime,
    decision_date: date,
    next_session_date: date,
) -> P1DailyExecutionEvidence:
    """Execution-only T+1 port. The strategy decision view never receives it."""
    if (
        not port.execution_only
        or not date(2025, 1, 1) <= decision_date <= date(2026, 9, 17)
        or not decision_date < next_session_date <= date(2026, 9, 18)
        or port.bar.market_date != next_session_date
        or snapshot.market_date != next_session_date
        or snapshot.knowledge_cutoff < close_at
        or snapshot.knowledge_cutoff != port.selected_at
        or snapshot.source_revision != port.source_manifest_sha256
        or open_at != port.open_at
        or close_at != port.close_at
    ):
        raise native.InputError("BRIDGE_EXECUTION_SCOPE")
    b = port.bar
    # Invalid zero/null OHLC cannot enter the stricter P1 execution dataclass.
    prices = (b.open_price, b.high_price, b.low_price, b.close_price)
    safe_prices = prices if b.data_valid else (None, None, None, None)
    return P1DailyExecutionEvidence(
        snapshot=snapshot,
        security_code=b.security_code,
        open_at=open_at,
        close_at=close_at,
        coverage_status=b.coverage_status,
        data_valid=b.data_valid,
        is_executable=b.is_executable,
        entry_policy_allows=b.entry_policy_allows,
        reason_code=b.reason_code,
        open_price=safe_prices[0],
        high_price=safe_prices[1],
        low_price=safe_prices[2],
        close_price=safe_prices[3],
        upper_limit_price=b.upper_limit_price,
        lower_limit_price=b.lower_limit_price,
    )
