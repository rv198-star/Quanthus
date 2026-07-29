"""Fail-closed normalization of mixed-unit P1 corporate-action evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.policy import ConfigurationError, find_project_root

NORMALIZATION_VERSION = "2.0.0"
VALUE_QUANTUM = Decimal("0.00000001")
ERROR_QUANTUM = Decimal("0.000000000001")
PER_TEN_SHARES = Decimal("10")
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


class CorporateActionNormalizationError(ValueError):
    """Raised when one action or normalization batch cannot be resolved safely."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class CashUnit(str, Enum):
    ZERO = "zero"
    PER_SHARE = "per_share"
    PER_TEN_SHARES = "per_ten_shares"


class CrossCheckBaseline(str, Enum):
    PRIOR_FACTOR = "prior_factor"
    PRIOR_QFQ_RATIO = "prior_qfq_ratio"


class NormalizationBasis(str, Enum):
    EASTMONEY_IMPLEMENTATION_PLAN = "eastmoney_implementation_plan"
    FACTOR_RATIO = "factor_ratio"


class FactorCrossCheckStatus(str, Enum):
    MATCHED = "matched"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"


_IMPLEMENTATION_PLAN = re.compile(
    r"^10"
    r"(?:(?:送(?P<bonus>\d+(?:\.\d+)?)(?:股)?))?"
    r"(?:(?:转(?P<transfer>\d+(?:\.\d+)?)(?:股)?))?"
    r"(?:(?:派(?P<cash>\d+(?:\.\d+)?)元))?"
    r"(?:\(.*\))?$"
)


def _value(value: Decimal) -> Decimal:
    return value.quantize(VALUE_QUANTUM, rounding=ROUND_HALF_UP)


def _error(value: Decimal) -> Decimal:
    return value.quantize(ERROR_QUANTUM, rounding=ROUND_HALF_UP)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _canonical_json_value(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        default=_canonical_json_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorporateActionNormalizationError(
            "invalid_evidence", f"{field_name} must be a non-empty string"
        )
    return value


def _require_nonnegative(value: Decimal, field_name: str) -> None:
    if not value.is_finite() or value < 0:
        raise CorporateActionNormalizationError(
            "invalid_evidence", f"{field_name} must be finite and nonnegative"
        )


@dataclass(frozen=True)
class CorporateActionNormalizationPolicy:
    policy_version: str
    maximum_factor_ratio_error: Decimal
    minimum_candidate_error_separation: Decimal
    cross_check_baselines: tuple[CrossCheckBaseline, ...]
    authority_precedence: tuple[NormalizationBasis, ...] = (NormalizationBasis.FACTOR_RATIO,)
    source_input_quantum: Decimal | None = None
    source_match_quantum_floor: Decimal | None = None
    cash_per_share_tolerance: Decimal | None = None
    share_per_ten_tolerance: Decimal | None = None
    eastmoney_assignment_progress: str | None = None
    require_eastmoney_raw: bool = False
    exception_ledger_version: str | None = None

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ConfigurationError("policy_version must be a non-empty string")
        if not self.maximum_factor_ratio_error.is_finite() or self.maximum_factor_ratio_error <= 0:
            raise ConfigurationError("maximum_factor_ratio_error must be finite and positive")
        if (
            not self.minimum_candidate_error_separation.is_finite()
            or self.minimum_candidate_error_separation <= 0
        ):
            raise ConfigurationError(
                "minimum_candidate_error_separation must be finite and positive"
            )
        if self.cross_check_baselines != tuple(CrossCheckBaseline):
            raise ConfigurationError("cross-check baselines must preserve the frozen exact order")
        valid_precedence = {
            (NormalizationBasis.FACTOR_RATIO,),
            (
                NormalizationBasis.EASTMONEY_IMPLEMENTATION_PLAN,
                NormalizationBasis.FACTOR_RATIO,
            ),
        }
        if self.authority_precedence not in valid_precedence:
            raise ConfigurationError("authority precedence is not a supported frozen order")
        uses_eastmoney = (
            NormalizationBasis.EASTMONEY_IMPLEMENTATION_PLAN in self.authority_precedence
        )
        if uses_eastmoney:
            if (
                self.source_input_quantum is None
                or not self.source_input_quantum.is_finite()
                or self.source_input_quantum <= 0
            ):
                raise ConfigurationError("source_input_quantum must be finite and positive")
            if (
                self.source_match_quantum_floor is None
                or not self.source_match_quantum_floor.is_finite()
                or self.source_match_quantum_floor <= 0
            ):
                raise ConfigurationError("source_match_quantum_floor must be finite and positive")
            for tolerance, field_name in (
                (self.cash_per_share_tolerance, "cash_per_share_tolerance"),
                (self.share_per_ten_tolerance, "share_per_ten_tolerance"),
            ):
                if tolerance is None or not tolerance.is_finite() or tolerance <= 0:
                    raise ConfigurationError(f"{field_name} must be finite and positive")
            if not self.eastmoney_assignment_progress:
                raise ConfigurationError("eastmoney_assignment_progress must be frozen")
            if not self.require_eastmoney_raw:
                raise ConfigurationError("the Eastmoney authority input must be required")
            if not self.exception_ledger_version:
                raise ConfigurationError("the exception ledger version must be frozen")
        elif (
            any(
                value is not None
                for value in (
                    self.source_input_quantum,
                    self.source_match_quantum_floor,
                    self.cash_per_share_tolerance,
                    self.share_per_ten_tolerance,
                    self.eastmoney_assignment_progress,
                    self.exception_ledger_version,
                )
            )
            or self.require_eastmoney_raw
        ):
            raise ConfigurationError("factor-only policy must not declare Eastmoney controls")


def load_corporate_action_normalization_policy(
    path: Path | None = None,
) -> CorporateActionNormalizationPolicy:
    resolved = (
        path or find_project_root() / "config" / "p1-corporate-action-normalization.v2.json"
    ).resolve()
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"configuration file does not exist: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid JSON in {resolved}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigurationError("corporate-action normalization policy must be an object")
    v1_fields = {
        "schema_version",
        "policy_version",
        "status",
        "share_quantity_unit",
        "cash_unit_candidates",
        "maximum_factor_ratio_error",
        "minimum_candidate_error_separation",
        "cross_check_baselines",
    }
    v2_fields = v1_fields | {
        "authority_precedence",
        "eastmoney_implementation_plan",
        "exception_ledger",
    }
    schema_version = raw.get("schema_version")
    expected_fields = v1_fields if schema_version == "1.0.0" else v2_fields
    if set(raw) != expected_fields:
        raise ConfigurationError("corporate-action normalization policy fields are not exact")
    if schema_version not in {"1.0.0", "2.0.0"} or raw["status"] != "frozen":
        raise ConfigurationError(
            "corporate-action normalization policy must be frozen schema 1.0.0 or 2.0.0"
        )
    if raw["share_quantity_unit"] != "per_ten_shares":
        raise ConfigurationError("share quantities must use the frozen per-ten-shares source unit")
    if raw["cash_unit_candidates"] != ["per_share", "per_ten_shares"]:
        raise ConfigurationError("cash unit candidates must preserve frozen priority")
    baselines = raw["cross_check_baselines"]
    if not isinstance(baselines, list):
        raise ConfigurationError("cross_check_baselines must be an array")
    try:
        if schema_version == "2.0.0":
            eastmoney = raw["eastmoney_implementation_plan"]
            ledger = raw["exception_ledger"]
            if not isinstance(eastmoney, Mapping) or set(eastmoney) != {
                "assignment_progress",
                "cash_unit",
                "exact_key",
                "quantity_unit",
                "require_notice_before_effective_date",
                "require_profile_consistency",
                "source_input_quantum",
                "source_match_quantum_floor",
                "cash_per_share_tolerance",
                "share_per_ten_tolerance",
                "source_value_match",
            }:
                raise ConfigurationError("Eastmoney policy fields are not exact")
            if not isinstance(ledger, Mapping) or set(ledger) != {
                "replay_disposition",
                "version",
            }:
                raise ConfigurationError("exception-ledger policy fields are not exact")
            if eastmoney["exact_key"] != ["security_code", "effective_date"]:
                raise ConfigurationError("Eastmoney exact key is not frozen")
            if (
                eastmoney["cash_unit"] != "per_ten_shares"
                or eastmoney["quantity_unit"] != "per_ten_shares"
            ):
                raise ConfigurationError("Eastmoney action values must be per ten shares")
            if eastmoney["source_value_match"] != "precision_interval_then_business_tolerance":
                raise ConfigurationError("Eastmoney source-value matching is not frozen")
            if eastmoney["require_notice_before_effective_date"] is not True:
                raise ConfigurationError("Eastmoney notice PIT check must be required")
            if eastmoney["require_profile_consistency"] is not True:
                raise ConfigurationError("Eastmoney profile consistency must be required")
            if ledger["replay_disposition"] != "security_fail_closed":
                raise ConfigurationError("exception-ledger replay disposition is not frozen")
            authority_precedence = tuple(
                NormalizationBasis(str(item)) for item in raw["authority_precedence"]
            )
            source_input_quantum = Decimal(
                _require_text(eastmoney["source_input_quantum"], "source_input_quantum")
            )
            source_match_quantum_floor = Decimal(
                _require_text(
                    eastmoney["source_match_quantum_floor"],
                    "source_match_quantum_floor",
                )
            )
            cash_per_share_tolerance = Decimal(
                _require_text(
                    eastmoney["cash_per_share_tolerance"],
                    "cash_per_share_tolerance",
                )
            )
            share_per_ten_tolerance = Decimal(
                _require_text(
                    eastmoney["share_per_ten_tolerance"],
                    "share_per_ten_tolerance",
                )
            )
            assignment_progress = _require_text(
                eastmoney["assignment_progress"], "assignment_progress"
            )
            exception_ledger_version = _require_text(ledger["version"], "ledger version")
        else:
            authority_precedence = (NormalizationBasis.FACTOR_RATIO,)
            source_input_quantum = None
            source_match_quantum_floor = None
            cash_per_share_tolerance = None
            share_per_ten_tolerance = None
            assignment_progress = None
            exception_ledger_version = None
        return CorporateActionNormalizationPolicy(
            policy_version=_require_text(raw["policy_version"], "policy_version"),
            maximum_factor_ratio_error=Decimal(
                _require_text(raw["maximum_factor_ratio_error"], "maximum_factor_ratio_error")
            ),
            minimum_candidate_error_separation=Decimal(
                _require_text(
                    raw["minimum_candidate_error_separation"],
                    "minimum_candidate_error_separation",
                )
            ),
            cross_check_baselines=tuple(CrossCheckBaseline(str(item)) for item in baselines),
            authority_precedence=authority_precedence,
            source_input_quantum=source_input_quantum,
            source_match_quantum_floor=source_match_quantum_floor,
            cash_per_share_tolerance=cash_per_share_tolerance,
            share_per_ten_tolerance=share_per_ten_tolerance,
            eastmoney_assignment_progress=assignment_progress,
            require_eastmoney_raw=schema_version == "2.0.0",
            exception_ledger_version=exception_ledger_version,
        )
    except (ValueError, ArithmeticError) as exc:
        raise ConfigurationError("corporate-action normalization policy is invalid") from exc


def _plan_value(match: re.Match[str], name: str) -> Decimal:
    value = match.group(name)
    return Decimal("0") if value is None else Decimal(value)


@dataclass(frozen=True)
class AuthoritativeCorporateActionEvidence:
    security_code: str
    effective_date: date
    known_at: datetime
    cash_per_ten_shares: Decimal
    bonus_per_ten_shares: Decimal
    transfer_per_ten_shares: Decimal
    source_cash_unit: CashUnit
    assignment_progress: str
    implementation_plan: str
    source_revision: str
    source_row_ref: str
    source_file_digest: str
    source_row_digest: str

    def __post_init__(self) -> None:
        for text_value, field_name in (
            (self.security_code, "security_code"),
            (self.assignment_progress, "assignment_progress"),
            (self.implementation_plan, "implementation_plan"),
            (self.source_revision, "source_revision"),
            (self.source_row_ref, "source_row_ref"),
            (self.source_file_digest, "source_file_digest"),
            (self.source_row_digest, "source_row_digest"),
        ):
            _require_text(text_value, field_name)
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise CorporateActionNormalizationError(
                "invalid_authoritative_evidence", "known_at must be timezone-aware"
            )
        if self.known_at.astimezone(MARKET_TIMEZONE).date() >= self.effective_date:
            raise CorporateActionNormalizationError(
                "authoritative_notice_not_point_in_time",
                "implementation notice must precede the effective date",
            )
        for value, field_name in (
            (self.cash_per_ten_shares, "cash_per_ten_shares"),
            (self.bonus_per_ten_shares, "bonus_per_ten_shares"),
            (self.transfer_per_ten_shares, "transfer_per_ten_shares"),
        ):
            _require_nonnegative(value, field_name)
        if not any(
            value > 0
            for value in (
                self.cash_per_ten_shares,
                self.bonus_per_ten_shares,
                self.transfer_per_ten_shares,
            )
        ):
            raise CorporateActionNormalizationError(
                "empty_authoritative_action", "implementation plan has no action value"
            )
        match = _IMPLEMENTATION_PLAN.fullmatch(self.implementation_plan)
        if match is None:
            raise CorporateActionNormalizationError(
                "authoritative_profile_invalid", "implementation plan cannot be parsed exactly"
            )
        parsed = (
            _plan_value(match, "cash"),
            _plan_value(match, "bonus"),
            _plan_value(match, "transfer"),
        )
        structured = (
            self.cash_per_ten_shares,
            self.bonus_per_ten_shares,
            self.transfer_per_ten_shares,
        )
        if parsed != structured:
            raise CorporateActionNormalizationError(
                "authoritative_profile_mismatch",
                "implementation plan text disagrees with structured action values",
            )
        for digest_value, field_name in (
            (self.source_file_digest, "source_file_digest"),
            (self.source_row_digest, "source_row_digest"),
        ):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest_value):
                raise CorporateActionNormalizationError(
                    "invalid_authoritative_evidence", f"{field_name} must be a sha256 digest"
                )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_code": self.security_code,
            "effective_date": self.effective_date.isoformat(),
            "known_at": self.known_at.isoformat(),
            "cash_per_ten_shares": _decimal_text(self.cash_per_ten_shares),
            "bonus_per_ten_shares": _decimal_text(self.bonus_per_ten_shares),
            "transfer_per_ten_shares": _decimal_text(self.transfer_per_ten_shares),
            "source_cash_unit": self.source_cash_unit.value,
            "assignment_progress": self.assignment_progress,
            "implementation_plan": self.implementation_plan,
            "source_revision": self.source_revision,
            "source_row_ref": self.source_row_ref,
            "source_file_digest": self.source_file_digest,
            "source_row_digest": self.source_row_digest,
        }


@dataclass(frozen=True)
class CorporateActionSourceEvidence:
    action_id: str
    security_code: str
    effective_date: date
    known_at: datetime
    raw_cash_value: Decimal
    raw_bonus_shares: Decimal
    raw_transfer_shares: Decimal
    prior_close: Decimal | None
    factor_before: Decimal | None
    factor_after: Decimal | None
    cross_check_baseline: CrossCheckBaseline | None
    source_revision: str
    source_row_ref: str
    snapshot_id: str
    authoritative_evidence: AuthoritativeCorporateActionEvidence | None = None

    def __post_init__(self) -> None:
        for text_value, field_name in (
            (self.action_id, "action_id"),
            (self.security_code, "security_code"),
            (self.source_revision, "source_revision"),
            (self.source_row_ref, "source_row_ref"),
            (self.snapshot_id, "snapshot_id"),
        ):
            _require_text(text_value, field_name)
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise CorporateActionNormalizationError(
                "invalid_evidence", "known_at must be timezone-aware"
            )
        if self.known_at.astimezone(MARKET_TIMEZONE).date() >= self.effective_date:
            raise CorporateActionNormalizationError(
                "late_action_evidence", "action must be known before its effective date"
            )
        for decimal_value, field_name in (
            (self.raw_cash_value, "raw_cash_value"),
            (self.raw_bonus_shares, "raw_bonus_shares"),
            (self.raw_transfer_shares, "raw_transfer_shares"),
        ):
            _require_nonnegative(decimal_value, field_name)
        if not any(
            value > 0
            for value in (
                self.raw_cash_value,
                self.raw_bonus_shares,
                self.raw_transfer_shares,
            )
        ):
            raise CorporateActionNormalizationError(
                "empty_action", "action must change cash or share quantity"
            )
        optional_values = (self.prior_close, self.factor_before, self.factor_after)
        for optional_value, field_name in zip(
            optional_values,
            ("prior_close", "factor_before", "factor_after"),
            strict=True,
        ):
            if optional_value is not None and (
                not optional_value.is_finite() or optional_value <= 0
            ):
                raise CorporateActionNormalizationError(
                    "invalid_evidence", f"{field_name} must be finite and positive when present"
                )
        has_all_cross_check = all(value is not None for value in optional_values)
        if has_all_cross_check != (self.cross_check_baseline is not None):
            raise CorporateActionNormalizationError(
                "invalid_evidence",
                "cross-check values and baseline must be declared together",
            )
        if self.authoritative_evidence is not None:
            if self.authoritative_evidence.security_code != self.security_code:
                raise CorporateActionNormalizationError(
                    "authoritative_key_mismatch", "authority security does not match source"
                )
            if self.authoritative_evidence.effective_date != self.effective_date:
                raise CorporateActionNormalizationError(
                    "authoritative_key_mismatch", "authority effective date does not match source"
                )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "security_code": self.security_code,
            "effective_date": self.effective_date.isoformat(),
            "known_at": self.known_at.isoformat(),
            "raw_cash_value": _decimal_text(self.raw_cash_value),
            "raw_bonus_shares": _decimal_text(self.raw_bonus_shares),
            "raw_transfer_shares": _decimal_text(self.raw_transfer_shares),
            "prior_close": (None if self.prior_close is None else _decimal_text(self.prior_close)),
            "factor_before": (
                None if self.factor_before is None else _decimal_text(self.factor_before)
            ),
            "factor_after": (
                None if self.factor_after is None else _decimal_text(self.factor_after)
            ),
            "cross_check_baseline": (
                None if self.cross_check_baseline is None else self.cross_check_baseline.value
            ),
            "source_revision": self.source_revision,
            "source_row_ref": self.source_row_ref,
            "snapshot_id": self.snapshot_id,
            "authoritative_evidence": (
                None
                if self.authoritative_evidence is None
                else self.authoritative_evidence.to_dict()
            ),
        }


@dataclass(frozen=True)
class NormalizedCorporateAction:
    action_id: str
    security_code: str
    effective_date: date
    known_at: datetime
    cash_dividend_per_share: Decimal
    bonus_share_ratio: Decimal
    cash_unit: CashUnit
    normalization_basis: NormalizationBasis
    factor_cross_check_status: FactorCrossCheckStatus
    factor_ratio_observed: Decimal | None
    factor_ratio_expected: Decimal | None
    factor_ratio_error: Decimal | None
    cross_check_baseline: CrossCheckBaseline | None
    normalization_policy_version: str
    source_revision: str
    source_row_ref: str
    snapshot_id: str
    evidence_digest: str
    authority_source_revision: str | None
    authority_source_row_ref: str | None
    authority_evidence_digest: str | None

    def __post_init__(self) -> None:
        factor_values = (
            self.factor_ratio_observed,
            self.factor_ratio_expected,
            self.factor_ratio_error,
            self.cross_check_baseline,
        )
        if self.factor_cross_check_status is FactorCrossCheckStatus.UNAVAILABLE:
            if any(value is not None for value in factor_values):
                raise CorporateActionNormalizationError(
                    "invalid_normalized_action",
                    "unavailable factor cross-check must not carry factor values",
                )
        elif self.factor_cross_check_status is FactorCrossCheckStatus.MATCHED and any(
            value is None for value in factor_values
        ):
            raise CorporateActionNormalizationError(
                "invalid_normalized_action", "factor cross-check values must be complete"
            )
        elif self.factor_cross_check_status is FactorCrossCheckStatus.MISMATCH and (
            self.factor_ratio_observed is None
            or self.cross_check_baseline is None
            or ((self.factor_ratio_expected is None) != (self.factor_ratio_error is None))
        ):
            raise CorporateActionNormalizationError(
                "invalid_normalized_action", "factor mismatch evidence is incomplete"
            )
        authority_values = (
            self.authority_source_revision,
            self.authority_source_row_ref,
            self.authority_evidence_digest,
        )
        if self.normalization_basis is NormalizationBasis.EASTMONEY_IMPLEMENTATION_PLAN:
            if any(value is None for value in authority_values):
                raise CorporateActionNormalizationError(
                    "invalid_normalized_action", "authority lineage must be complete"
                )
        elif any(value is not None for value in authority_values):
            raise CorporateActionNormalizationError(
                "invalid_normalized_action", "factor normalization must not claim authority lineage"
            )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "security_code": self.security_code,
            "effective_date": self.effective_date.isoformat(),
            "known_at": self.known_at.isoformat(),
            "cash_dividend_per_share": _decimal_text(self.cash_dividend_per_share),
            "bonus_share_ratio": _decimal_text(self.bonus_share_ratio),
            "cash_unit": self.cash_unit.value,
            "normalization_basis": self.normalization_basis.value,
            "factor_cross_check_status": self.factor_cross_check_status.value,
            "factor_ratio_observed": (
                None
                if self.factor_ratio_observed is None
                else _decimal_text(self.factor_ratio_observed)
            ),
            "factor_ratio_expected": (
                None
                if self.factor_ratio_expected is None
                else _decimal_text(self.factor_ratio_expected)
            ),
            "factor_ratio_error": (
                None if self.factor_ratio_error is None else _decimal_text(self.factor_ratio_error)
            ),
            "cross_check_baseline": (
                None if self.cross_check_baseline is None else self.cross_check_baseline.value
            ),
            "normalization_policy_version": self.normalization_policy_version,
            "source_revision": self.source_revision,
            "source_row_ref": self.source_row_ref,
            "snapshot_id": self.snapshot_id,
            "evidence_digest": self.evidence_digest,
            "authority_source_revision": self.authority_source_revision,
            "authority_source_row_ref": self.authority_source_row_ref,
            "authority_evidence_digest": self.authority_evidence_digest,
        }


@dataclass(frozen=True)
class CorporateActionNormalizationException:
    action_id: str
    security_code: str
    effective_date: date
    known_at: datetime
    reason_code: str
    evidence_digest: str
    source_revision: str
    source_row_ref: str

    def to_dict(self) -> dict[str, str]:
        return {
            "action_id": self.action_id,
            "security_code": self.security_code,
            "effective_date": self.effective_date.isoformat(),
            "known_at": self.known_at.isoformat(),
            "reason_code": self.reason_code,
            "evidence_digest": self.evidence_digest,
            "source_revision": self.source_revision,
            "source_row_ref": self.source_row_ref,
        }


@dataclass(frozen=True)
class CorporateActionNormalizationReport:
    normalization_version: str
    policy_version: str
    normalized: tuple[NormalizedCorporateAction, ...]
    exceptions: tuple[CorporateActionNormalizationException, ...]

    @property
    def input_count(self) -> int:
        return len(self.normalized) + len(self.exceptions)

    @property
    def ready_for_snapshot(self) -> bool:
        return self.input_count > 0 and not self.exceptions

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalization_version": self.normalization_version,
            "policy_version": self.policy_version,
            "input_count": self.input_count,
            "normalized_count": len(self.normalized),
            "exception_count": len(self.exceptions),
            "normalized": [item.to_dict() for item in self.normalized],
            "exceptions": [item.to_dict() for item in self.exceptions],
            "ready_for_snapshot": self.ready_for_snapshot,
            "execution_authorized": self.execution_authorized,
        }


def _expected_factor_ratio(
    *, prior_close: Decimal, cash_per_share: Decimal, share_ratio: Decimal
) -> Decimal:
    denominator = prior_close - cash_per_share
    if denominator <= 0:
        raise CorporateActionNormalizationError(
            "invalid_cash_candidate", "cash dividend must be below prior close"
        )
    return (Decimal("1") + share_ratio) * prior_close / denominator


def _factor_cross_check(
    evidence: CorporateActionSourceEvidence,
    policy: CorporateActionNormalizationPolicy,
    *,
    cash_per_share: Decimal,
    share_ratio: Decimal,
) -> tuple[
    FactorCrossCheckStatus,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    CrossCheckBaseline | None,
]:
    if (
        evidence.prior_close is None
        or evidence.factor_before is None
        or evidence.factor_after is None
        or evidence.cross_check_baseline is None
    ):
        return (FactorCrossCheckStatus.UNAVAILABLE, None, None, None, None)
    if evidence.cross_check_baseline not in policy.cross_check_baselines:
        raise CorporateActionNormalizationError(
            "unsupported_cross_check", "cross-check baseline is not enabled by policy"
        )
    observed_ratio = evidence.factor_after / evidence.factor_before
    try:
        expected_ratio = _expected_factor_ratio(
            prior_close=evidence.prior_close,
            cash_per_share=cash_per_share,
            share_ratio=share_ratio,
        )
    except CorporateActionNormalizationError as exc:
        if exc.reason_code != "invalid_cash_candidate":
            raise
        return (
            FactorCrossCheckStatus.MISMATCH,
            _error(observed_ratio),
            None,
            None,
            evidence.cross_check_baseline,
        )
    ratio_error = abs(observed_ratio - expected_ratio)
    status = (
        FactorCrossCheckStatus.MATCHED
        if ratio_error <= policy.maximum_factor_ratio_error
        else FactorCrossCheckStatus.MISMATCH
    )
    return (
        status,
        _error(observed_ratio),
        _error(expected_ratio),
        _error(ratio_error),
        evidence.cross_check_baseline,
    )


def _normalize_from_authority(
    evidence: CorporateActionSourceEvidence,
    policy: CorporateActionNormalizationPolicy,
) -> NormalizedCorporateAction:
    authority = evidence.authoritative_evidence
    if authority is None:
        raise AssertionError("authority normalization requires authority evidence")
    if authority.assignment_progress != policy.eastmoney_assignment_progress:
        raise CorporateActionNormalizationError(
            "authoritative_progress_mismatch", "authority row is not an implemented allocation"
        )
    cash_per_share = _value(authority.cash_per_ten_shares / PER_TEN_SHARES)
    share_ratio = _value(
        (authority.bonus_per_ten_shares + authority.transfer_per_ten_shares) / PER_TEN_SHARES
    )
    (
        cross_check_status,
        observed_ratio,
        expected_ratio,
        ratio_error,
        baseline,
    ) = _factor_cross_check(
        evidence,
        policy,
        cash_per_share=cash_per_share,
        share_ratio=share_ratio,
    )
    return NormalizedCorporateAction(
        action_id=evidence.action_id,
        security_code=evidence.security_code,
        effective_date=evidence.effective_date,
        known_at=max(evidence.known_at, authority.known_at),
        cash_dividend_per_share=cash_per_share,
        bonus_share_ratio=share_ratio,
        cash_unit=authority.source_cash_unit,
        normalization_basis=NormalizationBasis.EASTMONEY_IMPLEMENTATION_PLAN,
        factor_cross_check_status=cross_check_status,
        factor_ratio_observed=observed_ratio,
        factor_ratio_expected=expected_ratio,
        factor_ratio_error=ratio_error,
        cross_check_baseline=baseline,
        normalization_policy_version=policy.policy_version,
        source_revision=evidence.source_revision,
        source_row_ref=evidence.source_row_ref,
        snapshot_id=evidence.snapshot_id,
        evidence_digest=evidence.digest,
        authority_source_revision=authority.source_revision,
        authority_source_row_ref=authority.source_row_ref,
        authority_evidence_digest=authority.digest,
    )


def normalize_corporate_action(
    evidence: CorporateActionSourceEvidence,
    policy: CorporateActionNormalizationPolicy,
) -> NormalizedCorporateAction:
    """Prefer an exact implementation plan, otherwise require a safe factor candidate."""

    if (
        evidence.authoritative_evidence is not None
        and NormalizationBasis.EASTMONEY_IMPLEMENTATION_PLAN in policy.authority_precedence
    ):
        return _normalize_from_authority(evidence, policy)

    if (
        evidence.prior_close is None
        or evidence.factor_before is None
        or evidence.factor_after is None
        or evidence.cross_check_baseline is None
    ):
        raise CorporateActionNormalizationError(
            "missing_cross_check", "normalization requires prior price and factor evidence"
        )
    if evidence.cross_check_baseline not in policy.cross_check_baselines:
        raise CorporateActionNormalizationError(
            "unsupported_cross_check", "cross-check baseline is not enabled by policy"
        )

    share_ratio = _value(
        (evidence.raw_bonus_shares + evidence.raw_transfer_shares) / PER_TEN_SHARES
    )
    observed_ratio = evidence.factor_after / evidence.factor_before
    candidates: list[tuple[Decimal, CashUnit, Decimal, Decimal]] = []
    cash_candidates: tuple[tuple[Decimal, CashUnit], ...]
    if evidence.raw_cash_value == 0:
        cash_candidates = ((Decimal("0"), CashUnit.ZERO),)
    else:
        cash_candidates = (
            (_value(evidence.raw_cash_value), CashUnit.PER_SHARE),
            (_value(evidence.raw_cash_value / PER_TEN_SHARES), CashUnit.PER_TEN_SHARES),
        )
    for cash_value, cash_unit in cash_candidates:
        try:
            expected_ratio = _expected_factor_ratio(
                prior_close=evidence.prior_close,
                cash_per_share=cash_value,
                share_ratio=share_ratio,
            )
        except CorporateActionNormalizationError as exc:
            if exc.reason_code == "invalid_cash_candidate":
                continue
            raise
        candidates.append(
            (abs(observed_ratio - expected_ratio), cash_unit, cash_value, expected_ratio)
        )
    if not candidates:
        raise CorporateActionNormalizationError(
            "invalid_cash_candidate", "all cash source-unit candidates are invalid"
        )
    candidates.sort(key=lambda item: (item[0], item[1].value))
    best_error, cash_unit, cash_value, expected_ratio = candidates[0]
    if best_error > policy.maximum_factor_ratio_error:
        raise CorporateActionNormalizationError(
            "factor_ratio_mismatch", "no source-unit candidate matches factor evidence"
        )
    if (
        len(candidates) > 1
        and candidates[1][0] - best_error < policy.minimum_candidate_error_separation
    ):
        raise CorporateActionNormalizationError(
            "cash_unit_ambiguous", "factor evidence cannot distinguish the cash source unit"
        )

    return NormalizedCorporateAction(
        action_id=evidence.action_id,
        security_code=evidence.security_code,
        effective_date=evidence.effective_date,
        known_at=evidence.known_at,
        cash_dividend_per_share=cash_value,
        bonus_share_ratio=share_ratio,
        cash_unit=cash_unit,
        normalization_basis=NormalizationBasis.FACTOR_RATIO,
        factor_cross_check_status=FactorCrossCheckStatus.MATCHED,
        factor_ratio_observed=_error(observed_ratio),
        factor_ratio_expected=_error(expected_ratio),
        factor_ratio_error=_error(best_error),
        cross_check_baseline=evidence.cross_check_baseline,
        normalization_policy_version=policy.policy_version,
        source_revision=evidence.source_revision,
        source_row_ref=evidence.source_row_ref,
        snapshot_id=evidence.snapshot_id,
        evidence_digest=evidence.digest,
        authority_source_revision=None,
        authority_source_row_ref=None,
        authority_evidence_digest=None,
    )


def audit_corporate_action_normalization(
    evidence: Sequence[CorporateActionSourceEvidence],
    policy: CorporateActionNormalizationPolicy,
) -> CorporateActionNormalizationReport:
    """Normalize a canonical batch while retaining every fail-closed exception."""

    if not evidence:
        raise CorporateActionNormalizationError(
            "empty_normalization_batch", "normalization requires at least one action"
        )
    action_ids = [item.action_id for item in evidence]
    if len(action_ids) != len(set(action_ids)):
        raise CorporateActionNormalizationError(
            "duplicate_action", "action ids must be unique within a normalization batch"
        )
    normalized: list[NormalizedCorporateAction] = []
    exceptions: list[CorporateActionNormalizationException] = []
    ordered = sorted(
        evidence,
        key=lambda item: (item.effective_date, item.security_code, item.action_id),
    )
    for item in ordered:
        try:
            normalized.append(normalize_corporate_action(item, policy))
        except CorporateActionNormalizationError as exc:
            exceptions.append(
                CorporateActionNormalizationException(
                    action_id=item.action_id,
                    security_code=item.security_code,
                    effective_date=item.effective_date,
                    known_at=item.known_at,
                    reason_code=exc.reason_code,
                    evidence_digest=item.digest,
                    source_revision=item.source_revision,
                    source_row_ref=item.source_row_ref,
                )
            )
    return CorporateActionNormalizationReport(
        normalization_version=NORMALIZATION_VERSION,
        policy_version=policy.policy_version,
        normalized=tuple(normalized),
        exceptions=tuple(exceptions),
    )
