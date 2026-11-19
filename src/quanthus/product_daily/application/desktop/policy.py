"""Versioned P12 composition, account policy and adoption bindings."""

from __future__ import annotations

from dataclasses import dataclass

from quanthus.capabilities.funding import FixedBucketPolicy
from quanthus.capabilities.risk import AccountRiskPolicy
from quanthus.shared.fact_contracts import FactError
from quanthus.shared.values import ADMITTED_MODES, VersionRef

ADOPTION_STATUSES = (
    "available_for_reuse",
    "adaptation_required",
    "research_admission_required",
    "data_handoff_required",
    "configured",
    "adopted",
)
ACTION_BINDINGS = ("A01", "A02", "A03", "A04", "A05", "A06")


@dataclass(frozen=True)
class AdoptionBinding:
    binding_id: str
    title: str
    consumer: str
    mode: str
    version: str
    digest: str | None
    source_ref: str
    allowed_uses: tuple[str, ...]
    dependencies: tuple[str, ...]
    status: str
    missing_fields: tuple[str, ...]
    owner: str
    acceptance_ref: str | None

    def __post_init__(self) -> None:
        if self.status not in ADOPTION_STATUSES:
            raise FactError("INVALID_POLICY", f"unknown adoption status {self.status}")
        if self.mode not in ADMITTED_MODES:
            raise FactError("INVALID_POLICY", "unknown adoption mode")
        if self.status == "adopted" and (self.digest is None or self.missing_fields):
            raise FactError("INVALID_POLICY", "adopted bindings need a digest and no gaps")

    def admits_action(self, mode: str) -> bool:
        if self.mode != mode:
            return False
        if self.status == "adopted":
            return True
        # A configured synthetic binding is sufficient only for synthetic accounts.
        return mode == "synthetic" and self.status == "configured"


@dataclass(frozen=True)
class DesktopPolicyBundle:
    composition_ref: VersionRef
    funding: FixedBucketPolicy
    risk: AccountRiskPolicy
    execution_profile: str
    adoption: tuple[AdoptionBinding, ...]

    def __post_init__(self) -> None:
        keys = [(item.binding_id, item.mode) for item in self.adoption]
        if len(keys) != len(set(keys)):
            raise FactError("INVALID_POLICY", "one binding per id and mode")

    def missing_for_action(self, mode: str) -> tuple[str, ...]:
        """Binding ids that block a new risk action for this mode; facts are never blocked."""

        admitted = {item.binding_id for item in self.adoption if item.admits_action(mode)}
        return tuple(item for item in ACTION_BINDINGS if item not in admitted)
