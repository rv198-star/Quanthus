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

from datetime import date, datetime, time, timedelta, timezone
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

EXCHANGE_TZ = timezone(timedelta(hours=8))

# The configured daily publish time-of-day (audit R15): the next workbench
# check is the next authoritative session at this time, the same moment the
# nightly timer fires.  A pending placeholder publishes then too, so the
# publication clock and the displayed next_check_at stay consistent.
DEFAULT_PUBLISH_TIME = time(22, 30, tzinfo=EXCHANGE_TZ)


def calendar_next_session_provider(job_root: Path, calendar_root: Path | None = None):
    '''Build the governed next_session provider over the P10 forward
    calendar (issue #743 r2).

    The retired manifest calendar.next_session and the dwd_trading_calendar
    member fallback are removed: the next session comes only from the
    forward-calendar pointer at ``calendar_root`` (default: the CN02
    product root, or a job_root-local "calendar/" replica when present).
    Returns a callable(today) -> date | None; an unresolvable calendar
    yields None and the caller blocks rather than inventing a date.'''

    root = calendar_root
    if root is None:
        job_local = Path(job_root) / "calendar"
        try:
            from quanthus.p12_daily.forward_calendar import (
                DEFAULT_CALENDAR_ROOT,
            )

            root = job_local if job_local.is_dir() else DEFAULT_CALENDAR_ROOT
        except Exception:  # noqa: BLE001 - no calendar source available
            root = job_local
    from quanthus.p12_daily.forward_calendar import forward_calendar_provider

    return forward_calendar_provider(Path(root))


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
        next_session_provider=None,
        publish_time: time = DEFAULT_PUBLISH_TIME,
    ) -> None:
        self._store = DailyPackageStore(Path(state_dir))
        self._clock = clock
        self._mode = mode
        self._accounts = account_ids
        self._cache: dict[str, MarketDecisionSet] = {}
        # M2 F2 / audit R6: the pending placeholder's next_session comes from
        # the governed calendar via this provider, never a calendar-day-plus-one
        # guess.  When no authoritative session is resolvable the placeholder
        # is blocked rather than fabricating a date.
        self._next_session_provider = next_session_provider
        self._publish_time = publish_time

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
        # Audit #720 R13 (r2): the reader only trusts a package written by
        # the publish gate into the publisher ledger under THIS package's
        # (source_id, market_date).  The hot cache is keyed by the package's
        # digest identity; a hit still re-checks the registry so a corrupted
        # file or a stale entry is refused rather than replayed.
        source_id = str(stored.decision_set.get("source_id") or "")
        registered = self._store.publisher_registry().get(
            (source_id, stored.market_date.isoformat())
        ) or self._store.publisher_registry().get((source_id, "*"))
        if registered is None:
            raise FactError(
                "POLICY_NOT_ADOPTED",
                f"package source_id {source_id!r} is not a registered "
                "publisher for " + stored.market_date.isoformat(),
            )
        # The hot cache keys the verified decision set by market date.  A
        # same-reader hit still re-checks the registry: the cached package
        # serves only while the registered digest for this (source_id,
        # market_date) still equals the digest this reader already
        # verified - a re-registered or deregistered entry is refused
        # rather than replayed.  A reader that never verified the file
        # falls through and must prove the stored bytes match the
        # registry itself, so a corrupted file is refused.
        cached = self._cache.get(stored.market_date.isoformat())
        if cached is not None and registered == cached[0]:
            return cached[1]
        if registered != stored.file_sha256:
            raise FactError(
                "POLICY_NOT_ADOPTED",
                "package digest does not match the publisher registry for "
                + repr(source_id) + " " + stored.market_date.isoformat(),
            )
        value = load_decision_set_dict(stored.decision_set, mode=self._mode)
        self._cache[stored.market_date.isoformat()] = (stored.file_sha256, value)
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

    def publish_time(self) -> time:
        """Configured daily publish time-of-day for the next-check display."""
        return self._publish_time

    def pending_source_ref(self) -> VersionRef:
        '''Identity of the not-yet-published placeholder.

        Used by binders that must record a source identity even when the
        placeholder itself cannot be served (no authoritative next_session
        resolvable on this host - e.g. the governed calendar member is
        absent).  The account then binds to the pending identity and the
        first real package still migrates the binding normally.'''
        return VersionRef(
            PENDING_REF_ID,
            PENDING_VERSION,
            digest({"ref": PENDING_REF_ID, "pending": "unresolved"}),
        )

    def _pending_set(self) -> MarketDecisionSet:
        # A not-yet-published placeholder: the workbench reads it as "today's
        # advice has not been generated" through the publication status and
        # the latest run receipt, while the account binding can still open.
        today = self._clock().date()
        cutoff = datetime(today.year, today.month, today.day, 0, 0, tzinfo=timezone.utc)
        # Authoritative next session only (audit R6): resolve through the
        # injected provider; an unresolvable calendar fails the placeholder
        # closed instead of fabricating a date by natural-day arithmetic.
        next_session = (
            self._next_session_provider(today)
            if callable(self._next_session_provider)
            else None
        )
        if next_session is None or next_session <= today:
            raise FactError(
                "INPUT_UNAVAILABLE",
                "no authoritative next_session for the pending placeholder",
            )
        # The placeholder publishes at the configured publish point of the
        # next authoritative session (audit R15).  Using that moment keeps the
        # visibility check (now >= published_at) and the displayed
        # next_check_at on the same configured time-of-day instead of a fixed
        # next-day wall-clock guess.
        published_at = datetime.combine(next_session, self._publish_time)
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
                next_session=next_session,
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
