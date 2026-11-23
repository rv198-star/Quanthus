'''Published-package decision source for the P12 daily advice runtime.

Replaces the always-empty p12-paper-decisions source for accounts bound to the
daily package store.  It reads every immutable package the daily job wrote,
serves the latest one already published on the shared clock, and marks a
retracted package superseded so its unconfirmed plans stop confirming while
filled facts stay untouched.  A store with no package yet serves a
not-yet-published placeholder so the workbench can show "advice not
generated" from the publication status plus the latest run receipt.
'''

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from quanthus.capabilities.decision_access import (
    MarketDecisionSet,
    validate_decision_set,
)
from quanthus.p12_daily.decision_loader import load_decision_set_dict
from quanthus.p12_daily.package_store import DailyPackageStore, StoredPackage
from quanthus.shared.fact_contracts import FactError, digest
from quanthus.shared.values import AccountScope, VersionRef

PENDING_REF_ID = "p12-daily-advice"
PENDING_VERSION = "v1"


class PublishedPackageSource:
    '''DecisionPackageSource over the daily package store.

    The mode parameter is the account's admitted mode ("synthetic" for the
    batch-A isolated deployment; "simulation" once the migrated 8790 ledger
    binds the same store).  A wire package declaring a different mode is
    refused rather than served under the wrong identity.
    '''

    def __init__(
        self,
        state_dir: Path,
        clock,
        *,
        mode: str = "synthetic",
        account_ids: frozenset[str] | None = None,
    ) -> None:
        self._store = DailyPackageStore(Path(state_dir))
        self._clock = clock
        self._mode = mode
        self._accounts = account_ids
        self._cache: dict[str, MarketDecisionSet] = {}

    # ---- helpers --------------------------------------------------------------
    def _scope_ok(self, scope: AccountScope) -> None:
        if scope.mode != self._mode:
            raise FactError(
                "POLICY_NOT_ADOPTED",
                f"account mode {scope.mode!r} cannot read {self._mode!r} packages",
            )
        if self._accounts is not None and scope.account_id not in self._accounts:
            raise FactError("ACCOUNT_NOT_ADMITTED", "account is not bound to this source")

    def _load(self, stored: StoredPackage) -> MarketDecisionSet:
        cached = self._cache.get(stored.package_id)
        if cached is not None:
            return cached
        value = load_decision_set_dict(stored.decision_set, mode=self._mode)
        self._cache[stored.package_id] = value
        return value

    def _published(self) -> list[StoredPackage]:
        now = self._clock()
        return [item for item in self._store.packages() if now >= item.published_at]

    def stored_for(self, market_date: date) -> StoredPackage | None:
        return self._store.package_for(market_date)

    def retraction_for(self, market_date: date) -> dict | None:
        return self._store.retraction_for(market_date)

    def is_retracted(self, market_date: date) -> bool:
        return self._store.retraction_for(market_date) is not None

    def latest_run(self) -> dict | None:
        return self._store.latest_run()

    def _pending_set(self) -> MarketDecisionSet:
        # A not-yet-published placeholder: the workbench reads it as "today's
        # advice has not been generated" through the publication status and
        # the latest run receipt, while the account binding can still open.
        today = self._clock().date()
        cutoff = datetime(today.year, today.month, today.day, 0, 0, tzinfo=timezone.utc)
        published_at = cutoff + timedelta(days=1, hours=1)
        return validate_decision_set(
            MarketDecisionSet(
                source_ref=VersionRef(
                    PENDING_REF_ID,
                    PENDING_VERSION,
                    digest({"ref": PENDING_REF_ID, "pending": today.isoformat()}),
                ),
                mode=self._mode,
                synthetic=(self._mode == "synthetic"),
                market_date=today,
                knowledge_cutoff=cutoff,
                published_at=published_at,
                next_session=today + timedelta(days=1),
                decisions=(),
            )
        )

    # ---- DecisionPackageSource ---------------------------------------------------
    def current(self, scope: AccountScope) -> MarketDecisionSet:
        self._scope_ok(scope)
        published = self._published()
        if not published:
            return self._pending_set()
        return self._load(max(published, key=lambda item: item.published_at))

    def current_stored(self) -> StoredPackage | None:
        published = self._published()
        return max(published, key=lambda item: item.published_at) if published else None

    def published(self, scope: AccountScope) -> tuple[MarketDecisionSet, ...]:
        self._scope_ok(scope)
        return tuple(
            self._load(item)
            for item in sorted(self._published(), key=lambda x: x.published_at)
        )

    def history(self, scope: AccountScope) -> tuple[MarketDecisionSet, ...]:
        return self.published(scope)
