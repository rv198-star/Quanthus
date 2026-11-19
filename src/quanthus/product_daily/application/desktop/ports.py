"""Ports the desktop use cases consume; infrastructure implements them."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from quanthus.capabilities.decision_access import MarketDecisionSet
from quanthus.shared.values import AccountScope


class DecisionPackageSource(Protocol):
    """Supplies validated market decisions for one account scope.

    A sealed synthetic fixture implements it for synthetic accounts.  The adopted daily
    package reader (D1-B) is a separate implementation bound to its adoption receipts;
    an implementation must raise POLICY_NOT_ADOPTED rather than invent an empty package.
    """

    def current(self, scope: AccountScope) -> MarketDecisionSet: ...

    def history(self, scope: AccountScope) -> tuple[MarketDecisionSet, ...]: ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...
