"""Assemble the dedicated P12 paper account, separate from the demo fixture.

The paper account is its own synthetic identity (``SYN-PAPER-A``): it binds
an empty decision package on the system clock, owns its own fact store, and
never inherits the sealed demo fixture's hard-coded plans, synthetic clock
or placeholder security names.  Demo accounts stay for internal comparison.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quanthus.capabilities.decision_access import MarketDecisionSet
from quanthus.capabilities.execution import SyntheticContext
from quanthus.platform.adapters.product_daily_facts_sqlite import build_synthetic_fact_service
from quanthus.product_daily.application.desktop import P12DesktopApplication
from quanthus.product_daily.application.desktop.policy import DesktopPolicyBundle
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
from quanthus.shared.fact_contracts import digest
from quanthus.shared.values import VersionRef

PAPER_ACCOUNT_ID = "SYN-PAPER-A"
PAPER_FACTS_STORE = "p12-paper-facts.sqlite"
PAPER_BASELINE_REQUEST = "p12-paper-baseline-v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).astimezone()


class PaperDecisionSource:
    """An always-empty decision package: the paper account has no system plan.

    It returns a validated empty ``MarketDecisionSet`` so the desktop binds
    and serves pages without surfacing any written-off demo plan.
    """

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now

    def _set(self) -> MarketDecisionSet:
        today = self._now().date()
        cutoff = datetime(today.year, today.month, today.day, 0, 0, tzinfo=timezone.utc)
        return MarketDecisionSet(
            source_ref=VersionRef(
                "p12-paper-decisions", "v1", digest({"ref": "p12-paper-decisions", "v": 1})
            ),
            mode="synthetic",
            synthetic=True,
            market_date=today,
            knowledge_cutoff=cutoff,
            published_at=cutoff,
            next_session=today.fromordinal(today.toordinal() + 1),
            decisions=(),
        )

    def current(self, scope) -> MarketDecisionSet:  # noqa: ANN001
        return self._set()

    def published(self, scope) -> tuple[MarketDecisionSet, ...]:  # noqa: ANN001
        return (self._set(),)

    def history(self, scope) -> tuple[MarketDecisionSet, ...]:  # noqa: ANN001
        return (self._set(),)


def build_paper_application(
    *,
    state_dir: Path,
    policy: DesktopPolicyBundle,
    authorization: SyntheticRuntimeAuthorization,
    baseline_cash: Decimal = Decimal("100000.00"),
    now: Callable[[], datetime] | None = None,
) -> P12DesktopApplication:
    """Bind the dedicated paper account on the system clock.

    ``authorization`` must already admit ``SYN-PAPER-A``.  A fresh store gets
    a default 100,000-cash baseline so the paper account is usable on first
    login; reopening the same store replays the recorded baseline.
    """
    clock = now or _utcnow
    as_of = clock()
    context = SyntheticContext(
        PAPER_ACCOUNT_ID, "synthetic", "p12-paper-service", as_of, "complete-cash-only"
    )
    facts = build_synthetic_fact_service(
        Path(state_dir) / PAPER_FACTS_STORE,
        context=context,
        authorization=authorization,
        clock=clock,
    )
    app = P12DesktopApplication(
        scope=__import__("quanthus.shared.values", fromlist=["AccountScope"]).AccountScope(
            PAPER_ACCOUNT_ID, "synthetic"
        ),
        context=context,
        facts=facts,
        decisions=PaperDecisionSource(clock),
        policy=policy,
        clock=clock,
        clock_kind="system",
    )
    app.establish_baseline(request_id=PAPER_BASELINE_REQUEST, cash=baseline_cash)
    app.bind_configuration()
    return app
