"""Cross-module value semantics shared by P12 capabilities and application use cases.

Only representation rules live here: decimal text, explicit unknown values, account scope
and versioned references.  Business policy (fees, funding, limits) stays with its owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from quanthus.shared.fact_contracts import FactError

CNY_QUANTUM = Decimal("0.01")
RATIO_QUANTUM = Decimal("0.0001")
ADMITTED_MODES = ("synthetic", "simulation", "real")


def decimal_text(value: Decimal, quantum: Decimal = CNY_QUANTUM) -> str:
    """Canonical fixed-point text; never routes money through binary floats."""

    if not isinstance(value, Decimal) or not value.is_finite():
        raise FactError("INVALID_DECIMAL", "finite Decimal required")
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), "f")


def parse_decimal_text(value: object, field: str, *, places: int = 2) -> Decimal:
    """Parse transport decimal text with an exact maximum precision."""

    if not isinstance(value, str) or not value.strip():
        raise FactError("INVALID_REPORT_SHAPE", f"{field} must be decimal text")
    text = value.strip()
    if text.startswith("+") or "e" in text.lower():
        raise FactError("INVALID_REPORT_SHAPE", f"{field} must be plain decimal text")
    try:
        parsed = Decimal(text)
    except ArithmeticError as exc:
        raise FactError("INVALID_REPORT_SHAPE", f"{field} must be decimal text") from exc
    exponent = parsed.as_tuple().exponent
    if not parsed.is_finite() or not isinstance(exponent, int) or exponent < -places:
        raise FactError("INVALID_REPORT_SHAPE", f"{field} supports at most {places} decimals")
    return parsed


@dataclass(frozen=True)
class MaybeDecimal:
    """A known decimal or an explicit unknown with a named reason; zero means known zero."""

    value: Decimal | None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.reason_code is None):
            raise FactError(
                "INVALID_UNKNOWN_VALUE", "exactly one of value or reason_code is required"
            )
        if self.value is not None and (
            not isinstance(self.value, Decimal) or not self.value.is_finite()
        ):
            raise FactError("INVALID_UNKNOWN_VALUE", "known value must be a finite Decimal")

    @classmethod
    def known(cls, value: Decimal) -> MaybeDecimal:
        return cls(value, None)

    @classmethod
    def unknown(cls, reason_code: str) -> MaybeDecimal:
        return cls(None, reason_code)

    @property
    def is_known(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class AccountScope:
    """Account identity plus mode; derived from trusted runtime binding, never a request field."""

    account_id: str
    mode: str

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise FactError("UNAUTHORIZED_CONTEXT", "account_id is required")
        if self.mode not in ADMITTED_MODES:
            raise FactError("UNAUTHORIZED_CONTEXT", "unknown account mode")


@dataclass(frozen=True)
class VersionRef:
    """Exact versioned identity of a policy, composition, package or fixture."""

    ref_id: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        for name in ("ref_id", "version", "digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise FactError("INVALID_VERSION_REF", f"{name} is required")
        if not self.digest.startswith("sha256:"):
            raise FactError("INVALID_VERSION_REF", "digest must be sha256-prefixed")

    def to_dict(self) -> dict[str, str]:
        return {"ref_id": self.ref_id, "version": self.version, "digest": self.digest}
