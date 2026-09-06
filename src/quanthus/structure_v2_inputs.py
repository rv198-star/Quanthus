"""Bounded B2 daily source admission; no acquisition, timestamp inference or orders."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, time, timezone
from decimal import Context, Decimal, localcontext
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

VIEW = "retrospective"
COORDINATE_POLICY = "raw-cny-action-epoch-reset-v1"


def canonical(value: object) -> bytes:
    def encode(item: object) -> object:
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        if isinstance(item, datetime):
            if item.tzinfo is None:
                # Preserve raw source precision, not a fabricated UTC clock.
                return item.isoformat()
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise ValueError("non-finite market value")
            return format(item, "f")
        if isinstance(item, Enum):
            return item.value
        raise TypeError(f"unserializable evidence type: {type(item).__name__}")
    return json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def signed_distance(close: Decimal, anchor: Decimal) -> Decimal:
    if anchor <= 0:
        raise ValueError("price reference must be positive")
    with localcontext(Context(prec=28)):
        return close / anchor - Decimal(1)


def calculation_spec(request: dict[str, Any]) -> dict[str, Any]:
    """Definition identity excludes report scope, stock subset, Run and physical budgets."""
    return {
        "schema_version": "quanthus.structure-v2-definition.v1",
        "algorithm_version": "causal-prior-anchor-loss-reclaim-v1",
        "lookbacks": request["lookbacks"],
        "coordinate_policy": request["coordinate_policy"],
        "calendar_profile": "SSE-SZSE-final-daily-v1",
        "prior_window_excludes_t": True,
        "crossing": "previous-close-inclusive-current-close-strict-same-target",
        "gap_policy": "censor-and-rewarm-without-invented-bars",
        "process_policy": "one-high-breakout-per-scale-terminal-on-reclaim",
        "numeric_policy": "decimal-context28-cny-unadjusted",
    }


def exact_sha(value: str) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(
        char in "0123456789abcdef" for char in value[7:]
    )


@dataclass(frozen=True)
class InputPolicy:
    spec_digest: str
    security_codes: tuple[str, ...]
    calendar_ref: str
    view: str = VIEW
    finality: str = "final"
    partition: str = "development"
    coordinate_policy: str = COORDINATE_POLICY

    def __post_init__(self) -> None:
        if not exact_sha(self.spec_digest) or not self.calendar_ref:
            raise ValueError("admission policy requires spec and calendar identity")
        if type(self.security_codes) is not tuple or not self.security_codes or len(
            set(self.security_codes)
        ) != len(self.security_codes):
            raise ValueError("configured securities must be a unique immutable tuple")
        if (self.view, self.finality, self.partition, self.coordinate_policy) != (
            VIEW, "final", "development", COORDINATE_POLICY,
        ):
            raise ValueError("first v2 adapter admits only explicit final-daily retrospective lane")


@dataclass(frozen=True)
class DailyInput:
    """One configured security/session attempt from verified source projection membership.

    dwd_known_from is preserved source metadata, NOT claimed public arrival or actual historical
    platform use. B2's fixed adapter obtains finality from the closed-session dataset contract.
    Missing attempts remain objects; they are not synthesized flat bars.
    """
    security_code: str
    market_date: date
    session_index: int
    cutoff: datetime
    security_id: str
    listing_id: str
    exchange: str
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None
    source_record_id: str
    source_revision: str
    source_artifact_sha256: str
    dwd_known_from: str
    coordinate_epoch: str
    finality: str
    quality_reason: str | None = None
    reset_reason: str | None = None
    calendar_member_digest: str = ""
    action_member_digest: str = ""

    @property
    def member_digest(self) -> str:
        # Exact causal member/version identity; no provider materialization or Run inventory hash.
        return digest({key: value for key, value in asdict(self).items()
                       if key not in {"session_index", "cutoff"}})

    @property
    def result_as_of(self) -> datetime:
        # Fixed daily business clock, not source arrival or a caller's use timestamp.
        return datetime.combine(self.market_date, time(15), ZoneInfo("Asia/Shanghai"))

    @property
    def coordinate_ref(self) -> str:
        return digest({"policy": COORDINATE_POLICY, "currency": "CNY", "price_unit": "CNY/share",
                       "security_id": self.security_id, "listing_id": self.listing_id,
                       "action_epoch": self.coordinate_epoch})


@dataclass(frozen=True)
class Admission:
    row: DailyInput
    admitted: bool
    reason: str
    missing_volume: bool

    @property
    def key(self) -> tuple[str, date]:
        return self.row.security_code, self.row.market_date

    def to_dict(self) -> dict[str, Any]:
        return {"security_code": self.row.security_code, "market_date": self.row.market_date,
                "admitted": self.admitted, "reason": self.reason,
                "missing_volume": self.missing_volume,
                "source_member_digest": self.row.member_digest,
                "reset_reason": self.row.reset_reason}


def admit_daily(row: DailyInput, policy: InputPolicy) -> Admission:
    reason = None
    if row.security_code not in policy.security_codes:
        reason = "unconfigured_security"
    elif row.cutoff.tzinfo is None or row.cutoff.utcoffset() is None:
        reason = "cutoff_timezone_missing"
    elif row.result_as_of > row.cutoff:
        reason = "future_market_observation"
    elif not row.security_id or not row.listing_id or row.exchange not in {"SSE", "SZSE"}:
        reason = "identity_unproven"
    elif row.finality != policy.finality:
        reason = "daily_finality_unproven"
    elif row.quality_reason:
        reason = row.quality_reason
    elif not row.coordinate_epoch:
        reason = "coordinate_epoch_unproven"
    elif not exact_sha(row.calendar_member_digest) or not exact_sha(row.action_member_digest):
        reason = "required_reference_member_unproven"
    elif not row.source_record_id or not row.source_revision or not exact_sha(
        row.source_artifact_sha256
    ):
        reason = "source_version_unproven"
    elif any(not isinstance(value, Decimal) or not value.is_finite() or value <= 0
             for value in (row.high, row.low, row.close)):
        reason = "invalid_or_missing_daily_price"
    elif not row.low <= row.close <= row.high:
        reason = "inconsistent_ohlc"
    volume_missing = row.volume is None or not isinstance(row.volume, Decimal) or (
        not row.volume.is_finite() or row.volume < 0
    )
    return Admission(row, reason is None, reason or "admitted_retrospective_only", volume_missing)


def build_readiness(
    admissions: tuple[Admission, ...], policy: InputPolicy, provider_identity: dict[str, Any],
    *, report_start: date, report_end: date,
) -> dict[str, Any]:
    if len({item.key for item in admissions}) != len(admissions):
        raise ValueError("duplicate security/session attempt")
    pit = provider_identity.get("point_in_time", {})
    if pit.get("strategy_cutoff_enforced") is not True or pit.get(
        "record_view"
    ) != "retrospective_dwd_record_view":
        raise ValueError("provider cannot support the explicitly requested research lane")
    if not exact_sha(provider_identity.get("provider_lineage_digest", "")):
        raise ValueError("verified provider lineage missing")
    report = [item for item in admissions if report_start <= item.row.market_date <= report_end]
    dates = sorted({item.row.market_date for item in report})
    expected = {(code, day) for day in dates for code in policy.security_codes}
    if {item.key for item in report} != expected:
        raise ValueError("attempt denominator omits a configured security/session")
    reasons: dict[str, int] = {}
    for item in report:
        if not item.admitted:
            reasons[item.reason] = reasons.get(item.reason, 0) + 1
    return {
        "schema_version": "quanthus.structure-v2-input-readiness.v1",
        "partition": policy.partition, "permitted_view": policy.view,
        "public_reconstruction_admitted": False, "platform_as_known_admitted": False,
        "per_decision_publication_proven": False,
        "reason": "source_public_platform_availability_not_proven_by_retrospective_projection",
        "spec_digest": policy.spec_digest, "calendar_ref": policy.calendar_ref,
        "coordinate_policy": policy.coordinate_policy, "security_codes": policy.security_codes,
        "attempted": len(report), "admitted": sum(item.admitted for item in report),
        "rejected": sum(not item.admitted for item in report),
        "missing_volume": sum(item.admitted and item.missing_volume for item in report),
        "rejection_reasons": dict(sorted(reasons.items())),
        "history_reset_rows": sum(bool(item.row.reset_reason) for item in report),
        "source_member_closure_digest": digest([item.row.member_digest for item in admissions]),
        "provider_identity": provider_identity,
        "data_integrity_verified_by": "accepted ReplayDataProvider immutable source verifier",
        "no_execution_authority": True,
    }
