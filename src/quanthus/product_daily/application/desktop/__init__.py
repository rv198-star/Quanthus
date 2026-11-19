"""P12 desktop use cases (workbench, records, funding and rules, fact registration)."""

from quanthus.product_daily.application.desktop.policy import (
    ADOPTION_STATUSES,
    AdoptionBinding,
    DesktopPolicyBundle,
)
from quanthus.product_daily.application.desktop.ports import DecisionPackageSource
from quanthus.product_daily.application.desktop.service import (
    FACT_SOURCE_ID,
    PACKAGE_SCHEMA_VERSION,
    P12DesktopApplication,
    plan_ids,
)
from quanthus.product_daily.application.desktop.views import (
    CorrectFill,
    FactOutcome,
    FundingRulesView,
    RecordsView,
    SubmitFill,
    SubmitOrderEvent,
    WorkbenchView,
)

__all__ = [
    "ADOPTION_STATUSES",
    "FACT_SOURCE_ID",
    "PACKAGE_SCHEMA_VERSION",
    "AdoptionBinding",
    "CorrectFill",
    "DecisionPackageSource",
    "DesktopPolicyBundle",
    "FactOutcome",
    "FundingRulesView",
    "P12DesktopApplication",
    "RecordsView",
    "SubmitFill",
    "SubmitOrderEvent",
    "WorkbenchView",
    "plan_ids",
]
