"""Trusted local assembly for the sealed synthetic P12 desktop runtime.

This is the only place that turns the synthetic fixture into account bindings.  The
production ``platform_kernel_from_env`` never calls it, so a deployed kernel exposes the
P12 routes with RBAC and answers P12_RUNTIME_NOT_CONFIGURED until an adopted runtime exists.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from quanthus.capabilities.execution import SyntheticContext
from quanthus.platform.adapters.p12_synthetic_fixture import (
    DEFAULT_FIXTURE,
    FixtureDecisionSource,
    SyntheticDesktopFixture,
    load_synthetic_fixture,
)
from quanthus.platform.adapters.product_daily_facts_sqlite import build_synthetic_fact_service
from quanthus.platform.p12.http import P12AccountBinding, P12Runtime
from quanthus.policy import find_project_root
from quanthus.product_daily.application.desktop import P12DesktopApplication
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization

STORE_NAME = "p12-synthetic-facts.sqlite"


def synthetic_applications(
    *,
    state_dir: Path,
    fixture: SyntheticDesktopFixture,
    clock: Callable[[], datetime] | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, P12DesktopApplication]:
    """One application per fixture account, all sharing one synthetic SQLite store."""

    state_dir.mkdir(parents=True, exist_ok=True)
    effective_clock = clock or (lambda: fixture.clock)
    clock_kind = "synthetic_fixed" if clock is None else "system"
    authorization = SyntheticRuntimeAuthorization(
        f"local-synthetic:{fixture.fixture_ref.digest}",
        frozenset(item.account_id for item in fixture.accounts),
    )
    source = FixtureDecisionSource(fixture)
    applications: dict[str, P12DesktopApplication] = {}
    for spec in fixture.accounts:
        context = SyntheticContext(
            spec.account_id, "synthetic", spec.source_id, spec.as_of, spec.coverage_id
        )
        facts = build_synthetic_fact_service(
            state_dir / STORE_NAME,
            context=context,
            authorization=authorization,
            clock=effective_clock,
            fault_hook=fault_hook,
        )
        application = P12DesktopApplication(
            scope=spec.scope,
            context=context,
            facts=facts,
            decisions=source,
            policy=fixture.policy,
            clock=effective_clock,
            clock_kind=clock_kind,
        )
        application.establish_baseline(request_id=spec.baseline_request_id, cash=spec.baseline_cash)
        applications[spec.account_id] = application
    return applications


def build_synthetic_runtime(
    *,
    state_dir: Path,
    account_users: Mapping[str, frozenset[str]],
    fixture_path: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> P12Runtime:
    fixture = load_synthetic_fixture(fixture_path or find_project_root() / DEFAULT_FIXTURE)
    applications = synthetic_applications(state_dir=state_dir, fixture=fixture, clock=clock)
    bindings = {
        account_id: P12AccountBinding(
            application=application,
            display_name=fixture.account(account_id).display_name,
            allowed_usernames=frozenset(account_users.get(account_id, frozenset())),
            synthetic=True,
        )
        for account_id, application in applications.items()
    }
    return P12Runtime(
        runtime_ref=f"{fixture.fixture_ref.ref_id}@{fixture.fixture_ref.version}", bindings=bindings
    )
