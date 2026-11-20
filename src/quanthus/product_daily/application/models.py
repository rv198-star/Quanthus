"""Transport-free P12 synthetic facts DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quanthus.capabilities.accounting import AccountProjection
from quanthus.capabilities.execution import SyntheticContext
from quanthus.shared.fact_contracts import FactError, digest

POLICY_REFERENCE = "p12-synthetic-facts-0.1"


@dataclass(frozen=True)
class SyntheticRuntimeAuthorization:
    """Trusted composition configuration; never populated from a fact/report request."""

    authority_id: str
    allowed_accounts: frozenset[str]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.authority_id, str)
            or not self.authority_id.strip()
            or not isinstance(self.allowed_accounts, frozenset)
        ):
            raise FactError("UNAUTHORIZED_CONTEXT", "explicit runtime authority required")
        if not self.allowed_accounts or any(
            not isinstance(x, str) or not x.startswith("SYN-") for x in self.allowed_accounts
        ):
            raise FactError("UNAUTHORIZED_CONTEXT", "synthetic allowlist required")

    def require(self, context: SyntheticContext) -> None:
        if context.mode != "synthetic" or context.account_id not in self.allowed_accounts:
            raise FactError("UNAUTHORIZED_CONTEXT", "account not admitted by runtime policy")


@dataclass(frozen=True)
class Baseline:
    context: SyntheticContext
    request_id: str
    cash: Decimal
    quantity: int
    security_code: str
    unsettled_quantity: int
    dependent_plan_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(
            not isinstance(x, str) or not x.strip() for x in (self.request_id, self.security_code)
        ):
            raise FactError("INVALID_REPORT_SHAPE", "baseline identity is required")
        if not isinstance(self.cash, Decimal) or not self.cash.is_finite() or self.cash < 0:
            raise FactError("INVALID_REPORT_SHAPE", "baseline cash is invalid")
        for name in ("quantity", "unsettled_quantity"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise FactError("INVALID_REPORT_SHAPE", f"{name} is invalid")
        if (
            not isinstance(self.dependent_plan_ids, tuple)
            or any(not isinstance(x, str) or not x.strip() for x in self.dependent_plan_ids)
            or len(set(self.dependent_plan_ids)) != len(self.dependent_plan_ids)
        ):
            raise FactError("INVALID_REPORT_SHAPE", "explicit unique dependent plans required")
        exponent = self.cash.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -2:
            raise FactError("INVALID_REPORT_SHAPE", "baseline cash precision is invalid")
        object.__setattr__(self, "cash", self.cash.quantize(Decimal("0.01")))
        if self.unsettled_quantity:
            raise FactError(
                "UNSUPPORTED_BASELINE_HOLDS",
                "first slice requires explicitly zero unsettled quantity",
            )

    def to_dict(self) -> dict[str, object]:
        c = self.context
        return {
            "request_id": self.request_id,
            "account_id": c.account_id,
            "mode": c.mode,
            "source_id": c.source_id,
            "as_of": c.as_of.isoformat(),
            "coverage_id": c.coverage_id,
            "cash": format(self.cash, "f"),
            "quantity": self.quantity,
            "security_code": self.security_code,
            "unsettled_quantity": self.unsettled_quantity,
            "dependent_plan_ids": self.dependent_plan_ids,
        }

    @property
    def payload_digest(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    status: str
    receipt_digest: str | None = None
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubmissionResult:
    receipt_id: str
    status: str
    projection: AccountProjection
    deviations: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    affected_plans: tuple[str, ...] = ()

    @property
    def current_revision(self) -> int:
        return self.projection.revision

    @property
    def next_action(self) -> str:
        if self.status == "IDEMPOTENCY_CONFLICT":
            return "RESOLVE_CONFLICT"
        if self.status == "NEEDS_RECONCILIATION":
            return "RECONCILE_FACTS"
        return "REASSESS_AFFECTED_PLANS" if self.affected_plans else "NONE"


@dataclass(frozen=True)
class CurrentFactChain:
    fact_id: str
    security_code: str
    fact_revision: int
    quantity: int
    price: Decimal
    confirmed_fee: Decimal
    correction_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "p12-current-fact-chain-v1",
            "fact_id": self.fact_id,
            "security_code": self.security_code,
            "fact_revision": self.fact_revision,
            "quantity": self.quantity,
            "price": format(self.price, "f"),
            "confirmed_fee": format(self.confirmed_fee, "f"),
            "correction_ids": list(self.correction_ids),
        }


@dataclass(frozen=True)
class CorrectionResult:
    receipt_id: str
    status: str
    projection: AccountProjection
    cash_delta: Decimal
    quantity_delta: int
    conflicts: tuple[str, ...] = ()
    affected_plans: tuple[str, ...] = ()
    current_chain: CurrentFactChain | None = None

    @property
    def current_revision(self) -> int:
        return self.projection.revision

    @property
    def next_action(self) -> str:
        if self.status == "IDEMPOTENCY_CONFLICT":
            return "RESOLVE_CONFLICT"
        if self.status == "NEEDS_RECONCILIATION":
            return "RECONCILE_FACTS"
        return "REASSESS_AFFECTED_PLANS" if self.affected_plans else "NONE"
