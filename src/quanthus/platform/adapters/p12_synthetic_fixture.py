"""Sealed synthetic P12 desktop fixture: decision source, policy bundle and account specs.

The fixture is a trusted local configuration file.  Its digest is part of every exposed
version reference, so any edit changes the visible identity.  It implements the
application's ``DecisionPackageSource`` port for synthetic accounts only and refuses every
other mode with POLICY_NOT_ADOPTED instead of returning an empty package.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.capabilities.decision_access import (
    ChartEvidence,
    EntryIntent,
    Explanation,
    ExplanationUse,
    MarketDecision,
    MarketDecisionSet,
    OpportunityStage,
    PriceBar,
    validate_decision_set,
)
from quanthus.capabilities.funding import FixedBucketPolicy
from quanthus.capabilities.risk import AccountRiskPolicy
from quanthus.product_daily.application.desktop import AdoptionBinding, DesktopPolicyBundle
from quanthus.shared.fact_contracts import FactError, digest
from quanthus.shared.values import AccountScope, VersionRef, parse_decimal_text

FIXTURE_SCHEMA = "quanthus.p12.synthetic-desktop-fixture.v1"
DEFAULT_FIXTURE = Path("config/p12/synthetic-desktop.v1.json")


def _aware(value: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FactError("INVALID_FIXTURE", f"{field} must be timezone-aware")
    return parsed


def _dec(value: object, field: str, places: int = 4) -> Decimal:
    return parse_decimal_text(value, field, places=places)


@dataclass(frozen=True)
class SyntheticAccountSpec:
    account_id: str
    display_name: str
    baseline_request_id: str
    baseline_cash: Decimal
    as_of: datetime
    source_id: str
    coverage_id: str

    @property
    def scope(self) -> AccountScope:
        return AccountScope(self.account_id, "synthetic")


@dataclass(frozen=True)
class SyntheticDesktopFixture:
    fixture_ref: VersionRef
    clock: datetime
    accounts: tuple[SyntheticAccountSpec, ...]
    policy: DesktopPolicyBundle
    decisions: tuple[MarketDecisionSet, ...]

    def account(self, account_id: str) -> SyntheticAccountSpec:
        for item in self.accounts:
            if item.account_id == account_id:
                return item
        raise FactError("UNAUTHORIZED_CONTEXT", "account is not part of the synthetic fixture")


class FixtureDecisionSource:
    """DecisionPackageSource for sealed synthetic accounts.

    The fixture may carry several dated packages; the source returns the
    latest one whose ``published_at`` has already passed on the shared
    clock, so a packaged T+1 release only becomes visible once it is due.
    """

    def __init__(
        self,
        fixture: SyntheticDesktopFixture,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._fixture = fixture
        self._accounts = {item.account_id for item in fixture.accounts}
        self._clock = clock or (lambda: fixture.clock)

    def current(self, scope: AccountScope) -> MarketDecisionSet:
        if scope.mode != "synthetic" or scope.account_id not in self._accounts:
            raise FactError("POLICY_NOT_ADOPTED", "no adopted decision source for this account")
        now = self._clock()
        published = [
            item for item in self._fixture.decisions if now >= item.published_at
        ]
        if not published:
            # Before the first package is due, surface the earliest one so the
            # account binds and shows it as not yet published.
            return min(self._fixture.decisions, key=lambda item: item.published_at)
        return max(published, key=lambda item: item.published_at)

    def history(self, scope: AccountScope) -> tuple[MarketDecisionSet, ...]:
        return (self.current(scope),)


def _decision(raw: dict[str, Any]) -> MarketDecision:
    chart_raw = raw.get("chart")
    chart = None
    if chart_raw is not None:
        chart = ChartEvidence(
            price_basis=str(chart_raw["price_basis"]),
            as_of=_aware(chart_raw["as_of"], "chart.as_of"),
            bars=tuple(
                PriceBar(
                    session=date.fromisoformat(bar["session"]),
                    open=_dec(bar["open"], "bar.open", 2),
                    high=_dec(bar["high"], "bar.high", 2),
                    low=_dec(bar["low"], "bar.low", 2),
                    close=_dec(bar["close"], "bar.close", 2),
                    volume=int(bar["volume"]),
                )
                for bar in chart_raw["bars"]
            ),
            support=(
                None
                if chart_raw.get("support") is None
                else _dec(chart_raw["support"], "support", 2)
            ),
            volume_baseline=chart_raw.get("volume_baseline"),
            volume_baseline_window=chart_raw.get("volume_baseline_window"),
        )
    entry_raw = raw.get("entry")
    entry = None
    if entry_raw is not None:
        entry = EntryIntent(
            fraction=_dec(entry_raw["fraction"], "entry.fraction"),
            price_bound=_dec(entry_raw["price_bound"], "entry.price_bound", 2),
            mark_price=_dec(entry_raw["mark_price"], "entry.mark_price", 2),
            industry_id=str(entry_raw["industry_id"]),
            eligible_at=_aware(entry_raw["eligible_at"], "entry.eligible_at"),
            expires_at=_aware(entry_raw["expires_at"], "entry.expires_at"),
            window_ref=str(entry_raw["window_ref"]),
            invalidations=tuple(str(x) for x in entry_raw["invalidations"]),
        )
    component = raw["component"]
    return MarketDecision(
        opportunity_id=str(raw["opportunity_id"]),
        security_code=str(raw["security_code"]),
        security_name=str(raw["security_name"]),
        stage=OpportunityStage(raw["stage"]),
        reason_codes=tuple(str(x) for x in raw["reason_codes"]),
        explanations=tuple(
            Explanation(
                str(item["code"]),
                ExplanationUse(item["use"]),
                str(item["component_ref"]),
                str(item["text"]),
            )
            for item in raw["explanations"]
        ),
        next_observation=raw.get("next_observation"),
        chart=chart,
        entry=entry,
        component_ref=VersionRef(str(component["ref_id"]), str(component["version"]), digest(raw)),
    )


def load_synthetic_fixture(path: Path) -> SyntheticDesktopFixture:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != FIXTURE_SCHEMA:
        raise FactError("INVALID_FIXTURE", "unexpected synthetic fixture schema")
    if raw.get("synthetic") is not True or raw.get("mode") != "synthetic":
        raise FactError("INVALID_FIXTURE", "fixture must be explicitly synthetic")
    fixture_digest = digest(raw)
    fixture_ref = VersionRef(str(raw["fixture_id"]), str(raw["version"]), fixture_digest)
    accounts = tuple(
        SyntheticAccountSpec(
            account_id=str(item["account_id"]),
            display_name=str(item["display_name"]),
            baseline_request_id=str(item["baseline_request_id"]),
            baseline_cash=_dec(item["baseline_cash"], "baseline_cash", 2),
            as_of=_aware(item["as_of"], "account.as_of"),
            source_id=str(item["source_id"]),
            coverage_id=str(item["coverage_id"]),
        )
        for item in raw["accounts"]
    )
    if not accounts or any(not item.account_id.startswith("SYN-") for item in accounts):
        raise FactError("INVALID_FIXTURE", "synthetic accounts must use the SYN- namespace")
    policy_raw = raw["policy"]
    funding_raw = policy_raw["funding"]
    risk_raw = policy_raw["risk"]
    if funding_raw["mode"] != "fixed_bucket":
        raise FactError("INVALID_FIXTURE", "D1 admits only the fixed bucket funding mode")
    funding = FixedBucketPolicy(
        policy_ref=VersionRef(
            str(funding_raw["policy_id"]), str(funding_raw["version"]), digest(funding_raw)
        ),
        unit_count=int(funding_raw["unit_count"]),
        bucket_budget=_dec(funding_raw["bucket_budget"], "bucket_budget", 2),
        initial_reserve=_dec(funding_raw["initial_reserve"], "initial_reserve", 2),
        reserve_cap=(
            None
            if funding_raw.get("reserve_cap") is None
            else _dec(funding_raw["reserve_cap"], "reserve_cap", 2)
        ),
    )
    risk = AccountRiskPolicy(
        policy_ref=VersionRef(
            str(risk_raw["policy_id"]), str(risk_raw["version"]), digest(risk_raw)
        ),
        stock_limit=_dec(risk_raw["stock_limit"], "stock_limit"),
        industry_limit=_dec(risk_raw["industry_limit"], "industry_limit"),
        total_limit=_dec(risk_raw["total_limit"], "total_limit"),
        lot_size=int(risk_raw["lot_size"]),
        commission_rate=_dec(risk_raw["commission_rate"], "commission_rate", 6),
        minimum_commission=_dec(risk_raw["minimum_commission"], "minimum_commission", 2),
    )
    catalog = raw["binding_catalog"]
    adoption = tuple(
        AdoptionBinding(
            binding_id=str(item["binding_id"]),
            title=str(catalog[item["binding_id"]]["title"]),
            consumer=str(catalog[item["binding_id"]]["consumer"]),
            mode=str(item["mode"]),
            version=str(item["version"]),
            digest=item.get("digest"),
            source_ref=str(catalog[item["binding_id"]]["source_ref"]),
            allowed_uses=tuple(str(x) for x in item["allowed_uses"]),
            dependencies=tuple(str(x) for x in item["dependencies"]),
            status=str(item["status"]),
            missing_fields=tuple(str(x) for x in item["missing_fields"]),
            owner=str(catalog[item["binding_id"]]["owner"]),
            acceptance_ref=item.get("acceptance_ref"),
        )
        for item in raw["adoption"]
    )
    composition = VersionRef(
        str(policy_raw["composition_id"]),
        str(policy_raw["composition_version"]),
        digest(policy_raw),
    )
    bundle = DesktopPolicyBundle(
        composition_ref=composition,
        funding=funding,
        risk=risk,
        execution_profile=str(policy_raw["execution_profile"]),
        adoption=adoption,
    )
    decision_raws = raw.get("decision_sets") or [raw["decision_set"]]
    def _one_set(decision_raw):
        return validate_decision_set(
        MarketDecisionSet(
            source_ref=VersionRef(
                str(decision_raw["source_id"]), str(decision_raw["version"]), digest(decision_raw)
            ),
            mode="synthetic",
            synthetic=True,
            market_date=date.fromisoformat(decision_raw["market_date"]),
            knowledge_cutoff=_aware(decision_raw["knowledge_cutoff"], "knowledge_cutoff"),
            published_at=_aware(decision_raw["published_at"], "published_at"),
            next_session=date.fromisoformat(decision_raw["next_session"]),
            decisions=tuple(_decision(item) for item in decision_raw["decisions"]),
        )
    )

    decisions = tuple(sorted(
        (_one_set(item) for item in decision_raws),
        key=lambda item: item.published_at,
    ))
    return SyntheticDesktopFixture(
        fixture_ref=fixture_ref,
        clock=_aware(raw["synthetic_clock"], "synthetic_clock"),
        accounts=accounts,
        policy=bundle,
        decisions=decisions,
    )
