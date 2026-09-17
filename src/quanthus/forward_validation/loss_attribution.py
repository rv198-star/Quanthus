"""Material-loss attribution contract for prospective P6 evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.scorecards import P6ScorecardPolicy
from quanthus.forward_validation.serialization import digest_value


@dataclass(frozen=True)
class MaterialLossAttribution:
    event_id: str
    market_date: date
    loss_cny: Decimal
    mechanism_integrity_breach: bool
    material: bool
    classification: str | None
    rationale: str

    @property
    def digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "quanthus.p6-material-loss-attribution.v1",
            "event_id": self.event_id,
            "market_date": self.market_date.isoformat(),
            "loss_cny": format(self.loss_cny, "f"),
            "mechanism_integrity_breach": self.mechanism_integrity_breach,
            "material": self.material,
            "classification": self.classification,
            "rationale": self.rationale,
        }


def classify_loss(
    *,
    policy: P6ScorecardPolicy,
    event_id: str,
    market_date: date,
    loss_cny: Decimal,
    mechanism_integrity_breach: bool,
    classification: str | None,
    rationale: str,
) -> MaterialLossAttribution:
    if not event_id or event_id.strip() != event_id:
        fail("field_mismatch", "loss event_id must be canonical text")
    if not loss_cny.is_finite() or loss_cny < 0:
        fail("field_mismatch", "loss_cny must be finite and non-negative")
    if not rationale or rationale.strip() != rationale:
        fail("field_mismatch", "loss rationale must be canonical text")
    material = mechanism_integrity_breach or loss_cny >= policy.material_loss_threshold_cny
    if material:
        if classification not in policy.material_loss_classes:
            fail("unclassified_material_loss", "material loss requires one frozen classification")
    elif classification is not None and classification not in policy.material_loss_classes:
        fail("loss_classification_unknown", "loss classification is not frozen")
    return MaterialLossAttribution(
        event_id=event_id,
        market_date=market_date,
        loss_cny=loss_cny,
        mechanism_integrity_breach=mechanism_integrity_breach,
        material=material,
        classification=classification,
        rationale=rationale,
    )


def review_ready(
    *,
    policy: P6ScorecardPolicy,
    role_scorecard_roles: tuple[str, ...],
    losses: tuple[MaterialLossAttribution, ...],
    mechanism_integrity_breach_count: int,
    mature_ledger_closed: bool,
) -> bool:
    if type(mechanism_integrity_breach_count) is not int or mechanism_integrity_breach_count < 0:
        fail("field_mismatch", "mechanism_integrity_breach_count is invalid")
    if set(role_scorecard_roles) != set(policy.roles):
        return False
    if not mature_ledger_closed:
        return False
    if mechanism_integrity_breach_count != 0:
        return False
    if any(item.material and item.classification is None for item in losses):
        return False
    return True
