"""Wire the replay account: desktop app + paper service over the isolated
state dir (issue #748).

Reuses the production builders - ``build_paper_application`` establishes
the baseline + funding partition and binds the PublishedPackageSource,
``build_paper_service`` owns the shared order store - on the replay's own
state dir and replay clock.  The builders receive the engine's injected
decision source (a PublishedPackageSource over the replay store) rather
than the always-empty paper source, so confirmed plans trace back to the
package the replay actually published.
"""

from __future__ import annotations

from pathlib import Path

from quanthus.p12_daily.package_source import PublishedPackageSource
from quanthus.platform.p12.paper import build_paper_service
from quanthus.platform.p12.paper_fixture import (
    build_paper_application,
    build_quote_live_marks,
)
from quanthus.platform.p12.assembly import build_synthetic_runtime
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization


def build_replay_app(
    *,
    state_dir: Path,
    clock,
    decision_source: PublishedPackageSource,
    account_id: str = "SYN-PAPER-A",
    policy=None,
    fixture_path: Path | None = None,
):
    """The replay account's desktop application on the replay clock."""

    # Reuse the demo policy bundle (fixed buckets + risk) - the paper
    # account keeps its own sizing discipline; the replay only swaps the
    # decision source and the clock.
    if policy is None:
        runtime = build_synthetic_runtime(
            state_dir=state_dir,
            account_users={"SYN-P12-A": frozenset({"replay"})},
            fixture_path=fixture_path,
            clock=clock,
        )
        policy = runtime.bindings["SYN-P12-A"].application.policy
    return build_paper_application(
        state_dir=state_dir,
        policy=policy,
        authorization=SyntheticRuntimeAuthorization(
            "local-paper:v1", frozenset({account_id})
        ),
        now=clock,
        decision_source=decision_source,
        live_marks=None,
        live_valuation_ref="replay-1d",
    )


def build_replay_paper(*, state_dir: Path, applications: dict, quotes, now):
    """The replay paper service over the session-scoped 1d quote source.

    Kept for the quote/kline read paths; the engine's fill path posts
    ExecutionReports directly through app.paper_submit_report, so the
    order-book matcher is not used in phase 1."""

    service = build_paper_service(
        state_dir=state_dir,
        applications=applications,
        quotes=quotes,
        intraday=None,
        daily_kline=None,
        now=now,
    )
    # The replay's compressed step never wants the 5s background matcher
    # racing the day boundary - matching runs through explicit ticks.
    service.stop()
    return service
