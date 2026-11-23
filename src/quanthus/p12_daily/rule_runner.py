'''Strategy rule execution port for the P12 daily advice runtime.

P11 owns research-time rules and ships a fixed version: a code entry point and
commit hash, a strategy card with sha256, its input manifest, its usage receipts
(A01/A02) and machine-readable invalidation conditions plus position caps.
P12 owns the daily run: this port takes one qualified daily-core snapshot plus
the account's current holdings and returns a validated MarketDecisionSet whose
wire form matches the P11 G1 decision_set dict (see decision_loader).

The batch-A runner is explicitly synthetic - it replays a fixed replay rule so
the end-to-end path (input -> rule -> package -> workbench) is exercisable
before the P11 version lands.  It never copies or rewrites the P11 formula;
the FormalStrategyRunner hook keeps the same signature so the swap is a
configuration change, not a refactor.

Frozen semantics (2026-11-24):
* A qualified run with no entry candidate emits the single G1 no-op decision:
  opportunity_id "no-qualified-candidate", security "000000", stage ended,
  explanation code NO_QUALIFIED_ENTRY; the package audit then reads
  admission_disposition "empty_advice".  An empty decision list is never
  published - that shape is reserved for the run-receipt-only blocked day.
* A whole-day input block publishes nothing; only a run receipt is written.
* A single-security input defect drops that security's new candidates, records
  the reason in the run receipt, and the rest of the package still publishes.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Protocol

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
from quanthus.p12_daily.daily_core_input import DailyCoreSnapshot
from quanthus.shared.fact_contracts import digest
from quanthus.shared.values import VersionRef

EXCHANGE_TZ = timezone(timedelta(hours=8))
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(15, 0)

RUNNER_REF = "p12.synthetic-replay-rule"
RUNNER_VERSION = "v1"
SOURCE_REF_ID = "p12-daily-advice"

NO_QUALIFIED_CANDIDATE_ID = "no-qualified-candidate"
NO_QUALIFIED_PLACEHOLDER = "000000"
NO_QUALIFIED_ENTRY = "NO_QUALIFIED_ENTRY"

RULE_VERSION_REJECTED = "RULE_VERSION_REJECTED"


@dataclass(frozen=True)
class RunnerRequest:
    snapshot: DailyCoreSnapshot
    next_session: date
    # Account holdings the rule must not double-enter: (security_code, quantity).
    holdings: tuple[tuple[str, int], ...]
    published_at: datetime


@dataclass(frozen=True)
class SecurityExclusion:
    security_code: str
    reason_code: str
    detail: str


@dataclass(frozen=True)
class RunnerOutput:
    decision_set: MarketDecisionSet
    exclusions: tuple[SecurityExclusion, ...]


class StrategyRuleRunner(Protocol):
    @property
    def runner_ref(self) -> VersionRef: ...

    def run(self, request: RunnerRequest) -> RunnerOutput: ...


def _no_op_decision(component_ref: VersionRef, market_date: date) -> MarketDecision:
    return MarketDecision(
        opportunity_id=NO_QUALIFIED_CANDIDATE_ID,
        security_code=NO_QUALIFIED_PLACEHOLDER,
        security_name="no qualified candidate",
        stage=OpportunityStage.ENDED,
        reason_codes=(),
        explanations=(
            Explanation(
                NO_QUALIFIED_ENTRY,
                ExplanationUse.EXPLANATION,
                f"{component_ref.ref_id}@{component_ref.version}",
                "no qualified entry candidate for this daily package",
            ),
        ),
        next_observation="next session after close",
        chart=None,
        entry=None,
        component_ref=component_ref,
    )


class SyntheticRuleRunner:
    '''Batch-A synthetic runner: a fixed, labelled replay rule over the snapshot.

    A candidate may carry an unqualified_reason to model the single-security
    drop path; dropped candidates land in RunnerOutput.exclusions, never in the
    package.  It declares itself synthetic so its packages can never masquerade
    as the adopted daily strategy.
    '''

    def __init__(
        self,
        *,
        candidates: tuple[dict, ...],
        runner_ref: VersionRef | None = None,
    ) -> None:
        self._candidates = candidates
        self._ref = runner_ref or VersionRef(
            RUNNER_REF, RUNNER_VERSION, digest({"ref": RUNNER_REF, "v": RUNNER_VERSION})
        )

    @property
    def runner_ref(self) -> VersionRef:
        return self._ref

    def run(self, request: RunnerRequest) -> RunnerOutput:
        snapshot = request.snapshot
        cutoff = datetime.combine(snapshot.market_date, SESSION_CLOSE, tzinfo=EXCHANGE_TZ)
        eligible = datetime.combine(request.next_session, SESSION_OPEN, tzinfo=EXCHANGE_TZ)
        expires = datetime.combine(request.next_session, SESSION_CLOSE, tzinfo=EXCHANGE_TZ)
        held = {code for code, qty in request.holdings if qty > 0}
        decisions: list[MarketDecision] = []
        exclusions: list[SecurityExclusion] = []
        for item in self._candidates:
            code = str(item["security_code"])
            drop_reason = item.get("unqualified_reason")
            if drop_reason:
                # Single-security input defect: drop this security's new
                # candidates and record the reason; the package still publishes.
                exclusions.append(
                    SecurityExclusion(
                        security_code=code,
                        reason_code=str(drop_reason.get("reason_code", "INPUT_UNAVAILABLE")),
                        detail=str(drop_reason.get("detail", "candidate input not qualified")),
                    )
                )
                continue
            if code in held:
                # A held security is managed by the holding/exit path, never
                # re-entered by a fresh entry plan.
                continue
            mark = Decimal(str(item["mark_price"]))
            bound = Decimal(str(item["price_bound"]))
            fraction = Decimal(str(item.get("fraction", "0.5")))
            bars = tuple(
                PriceBar(
                    session=date.fromisoformat(bar["session"]),
                    open=Decimal(str(bar["open"])),
                    high=Decimal(str(bar["high"])),
                    low=Decimal(str(bar["low"])),
                    close=Decimal(str(bar["close"])),
                    volume=int(bar["volume"]),
                )
                for bar in item.get("bars", ())
            )
            chart = None
            if bars:
                chart = ChartEvidence(
                    price_basis=str(item.get("price_basis", "none")),
                    as_of=cutoff,
                    bars=bars,
                    support=(
                        Decimal(str(item["support"])) if item.get("support") else None
                    ),
                    volume_baseline=item.get("volume_baseline"),
                    volume_baseline_window=item.get("volume_baseline_window"),
                )
            entry = EntryIntent(
                fraction=fraction,
                price_bound=bound,
                mark_price=mark,
                industry_id=str(item.get("industry_id", "SYN-IND")),
                eligible_at=eligible,
                expires_at=expires,
                window_ref=f"close-to-next-open:{request.next_session.isoformat()}",
                invalidations=tuple(str(x) for x in item.get("invalidations", ())),
            )
            decisions.append(
                MarketDecision(
                    opportunity_id="OPP-D1B-{}-{}".format(
                        snapshot.market_date.strftime("%Y%m%d"), code
                    ),
                    security_code=code,
                    security_name=str(item.get("security_name", code)),
                    stage=OpportunityStage.ENTRY_PLAN,
                    reason_codes=("NEW_ENTRY_SIGNAL",),
                    explanations=(
                        Explanation(
                            "SYNTHETIC_RULE_ENTRY",
                            ExplanationUse.ACTION,
                            f"{self._ref.ref_id}@{self._ref.version}",
                            "[synthetic] batch-A replay rule emitted this entry on the "
                            "qualified daily-core snapshot; account sizing is solved by "
                            "the P12 risk path before confirmation",
                        ),
                    ),
                    next_observation=None,
                    chart=chart,
                    entry=entry,
                    component_ref=self._ref,
                )
            )
        if not decisions:
            decisions.append(_no_op_decision(self._ref, snapshot.market_date))
        decision_set = validate_decision_set(
            MarketDecisionSet(
                source_ref=VersionRef(
                    SOURCE_REF_ID,
                    "v1",
                    digest(
                        {
                            "src": SOURCE_REF_ID,
                            "market_date": snapshot.market_date.isoformat(),
                            "manifest": snapshot.core_manifest_sha256,
                            "runner": self._ref.digest,
                        }
                    ),
                ),
                mode="synthetic",
                synthetic=True,
                market_date=snapshot.market_date,
                knowledge_cutoff=cutoff,
                published_at=request.published_at,
                next_session=request.next_session,
                decisions=tuple(decisions),
            )
        )
        return RunnerOutput(decision_set=decision_set, exclusions=tuple(exclusions))
