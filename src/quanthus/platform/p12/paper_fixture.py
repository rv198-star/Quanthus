"""Assemble the dedicated P12 paper account, separate from the demo fixture.

The paper account is its own synthetic identity (``SYN-PAPER-A``): it binds
an empty decision package on the system clock, owns its own fact store, and
never inherits the sealed demo fixture's hard-coded plans, synthetic clock
or placeholder security names.  Demo accounts stay for internal comparison.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace as _dc_replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from quanthus.capabilities.decision_access import MarketDecisionSet
from quanthus.capabilities.execution import SyntheticContext
from quanthus.capabilities.funding import FixedBucketPolicy, initial_funding_state
from quanthus.platform.adapters.p12_synthetic_fixture import (
    FixtureDecisionSource,
    SyntheticDesktopFixture,
    load_synthetic_fixture,
)
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
            # An empty package carries no real publication event: stamp it at
            # the knowledge cutoff (not a fabricated intraday time) so the
            # surface reads "no system advice today" rather than a fake 00:00.
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


class PaperReplayDecisionSource:
    """Guided replay source: a flagged historical replay day drives the workbench.

    Wraps a dedicated synthetic replay fixture (``config/p12/paper-replay.v1.json``)
    that carries several dated packages: an executable entry task, a "no action
    needed" day and an "input not ready" day.  Like ``FixtureDecisionSource`` it
    returns the latest package already published on the shared clock, so a fixed
    replay ``--clock`` makes the replay set visible to the logged-in user.  The
    source is explicitly synthetic and separately labelled; it never claims to be
    the platform's adopted daily strategy.
    """

    def __init__(self, fixture: SyntheticDesktopFixture, clock) -> None:  # noqa: ANN001
        self._inner = FixtureDecisionSource(fixture, clock=clock)

    @classmethod
    def from_path(cls, path: Path, clock) -> PaperReplayDecisionSource:  # noqa: ANN001
        return cls(load_synthetic_fixture(Path(path)), clock)

    def current(self, scope) -> MarketDecisionSet:  # noqa: ANN001
        return self._inner.current(scope)

    def published(self, scope) -> tuple[MarketDecisionSet, ...]:  # noqa: ANN001
        return self._inner.published(scope)

    def history(self, scope) -> tuple[MarketDecisionSet, ...]:  # noqa: ANN001
        return self._inner.history(scope)


def build_paper_application(
    *,
    state_dir: Path,
    policy: DesktopPolicyBundle,
    authorization: SyntheticRuntimeAuthorization,
    baseline_cash: Decimal = Decimal("100000.00"),
    now: Callable[[], datetime] | None = None,
    decision_source=None,
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
    # The paper account keeps its own fixed-bucket funding policy so the
    # opening cash partitions cleanly instead of falling into an "unbound /
    # assignment-conflict" surface.  Two buckets plus a reserve partition the
    # 100,000 baseline; the structure mirrors the demo policy's fixed_bucket
    # mode, only sized to this account's cash.
    paper_funding = FixedBucketPolicy(
        policy_ref=policy.funding.policy_ref,
        unit_count=2,
        bucket_budget=Decimal("45000.00"),
        initial_reserve=Decimal("10000.00"),
        reserve_cap=policy.funding.reserve_cap,
        trial_fraction=policy.funding.trial_fraction,
    )
    paper_policy = _dc_replace(policy, funding=paper_funding)
    app = P12DesktopApplication(
        scope=__import__("quanthus.shared.values", fromlist=["AccountScope"]).AccountScope(
            PAPER_ACCOUNT_ID, "synthetic"
        ),
        context=context,
        facts=facts,
        decisions=decision_source or PaperDecisionSource(clock),
        policy=paper_policy,
        clock=clock,
        clock_kind="system",
        # Paper account shares one effective cash ceiling between the
        # published suggestion and the confirm-time hold: the suggestion is
        # sized under the account's trial-fraction cap so a 50% intent cannot
        # over-commit the 13,500 trial limit.
        sizing_trial_cap=True,
    )
    app.establish_baseline(request_id=PAPER_BASELINE_REQUEST, cash=baseline_cash)
    app.bind_configuration()
    # Establish the funding partition once so a fresh account shows bound
    # buckets and a real reserve, never the "assignment conflict" placeholder.
    if app.funding_state(app.scope) is None:
        app.bind_funding(app.scope, initial_funding_state(paper_funding, baseline_cash))
    return app
