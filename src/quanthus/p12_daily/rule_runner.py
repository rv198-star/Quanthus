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

import json
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
# The synthetic batch-A producer identity; bound only to synthetic accounts
# in the publish-gate config, never to a live/simulation account.
SYNTHETIC_PRODUCER_ID = "p12-synthetic-rule-producer"


def _candidate_sort_key(item: dict) -> str:
    '''Deterministic identity ordering for a decision/candidate record -
    never compares dicts positionally (audit R9).'''

    return json.dumps(
        {
            "opportunity_id": item.get("opportunity_id"),
            "security_code": item.get("security_code"),
        },
        sort_keys=True,
    )


@dataclass(frozen=True)
class HeldPositionFact:
    "The account facts P12 supplies for one open position (M2 F9)."

    security_code: str
    quantity: int
    holding_sessions: int | None = None
    marked_return: str | None = None
    prior_strategy_reduction_count: int | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class TrendObservation:
    "A P11 evaluate_trend_stability result bound to its event (M2 F10)."

    opportunity_id: str
    observation_digest: str
    applicability: str
    raw_value: str | None = None


@dataclass(frozen=True)
class HistoryGateResult:
    "One of the three independent history floors (M2 section 3)."

    gate_id: str
    satisfied: bool
    observed_sessions: int | None
    required_sessions: int
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class RunnerRequest:
    '''E1 port input: the raw rows P12 read - the qualified daily-core snapshot
    (daily bars, corporate actions, calendar, listing facts) plus the raw
    account holding facts.  P12 never constructs CandidateDailyInput or
    SecurityFeatureFacts; those are the P11 adapter's job (issue #719 seam).'''

    snapshot: DailyCoreSnapshot
    next_session: date
    # Account holdings (M2 F9): the rule binds these into HeldPositionFacts
    # and must not double-enter a held security.
    holdings: tuple[HeldPositionFact, ...]
    published_at: datetime
    # The run identity the P12 job mints for this attempt (audit R10): the
    # runner echoes it inside its evaluation receipt so a receipt minted by a
    # different run can never pass the publish gate.
    run_id: str | None = None
    # The configured decision cutoff (tz-aware): the package's knowledge_cutoff
    # is this value, not the 15:00 close anchor or the job's completed_at.
    decision_cutoff: datetime = None


@dataclass(frozen=True)
class SecurityExclusion:
    '''One security dropped from new-candidate evaluation, with the P11-side
    evidence carried verbatim (frozen single-security drop path).

    reason_code maps to the existing INPUT_UNAVAILABLE transport reason; the
    P12 receipt adds no new reason codes of its own.'''

    security_code: str
    reason_code: str
    detail: str
    metric: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    regime_version: str | None = None


# P11 internal evidence codes P12 must transport verbatim on the receipt; they
# are not P12 reason codes and never widen the P12 reason-code contract.
_EXCLUSION_EVIDENCE_KEYS = ("metric", "window_start", "window_end", "regime_version")

VOLUME_REGIME_BOUNDARY = "VOLUME_REGIME_BOUNDARY"
HOLD_AND_ALERT = "HOLD_AND_ALERT"
EXIT_SIGNAL = "EXIT_SIGNAL"


@dataclass(frozen=True)
class HoldingDirective:
    '''The rule's disposition for one open position (frozen cross-regime rule).

    HOLD_AND_ALERT is reserved for a holding whose evaluation depends on the
    missing volume/amount input.  When a qualified volume-independent exit or
    reduction signal already exists (price or risk evidence, base exit), the
    rule emits EXIT instead; the volume-boundary reason is still recorded on
    the receipt, it never rewrites a qualified signal into a hold.'''

    security_code: str
    action: str  # HOLD_AND_ALERT | EXIT_SIGNAL | HOLD
    reason_code: str
    detail: str


@dataclass(frozen=True)
class RunnerOutput:
    decision_set: MarketDecisionSet
    exclusions: tuple[SecurityExclusion, ...]
    # Publishable flag and its reason (issue #723 comment-7114642978, audit
    # R11): the runner itself declares whether this run may publish.  When
    # publishable is False the publish gate blocks the day and the receipt
    # carries disposition/diagnostics verbatim.
    publishable: bool = True
    disposition: str | None = None
    diagnostics: dict | None = None
    # Run-seam contract (issue #719 seam note): the evaluation coverage receipt
    # and the source closure are produced by the runner inside this same run.
    # P12 transports and validates them, never constructs, parses or rewrites
    # them - the publish gate rejects a missing or self-minted receipt.
    evaluation_receipt: dict | None = None
    source_closure: dict | None = None
    # Per-holding dispositions (volume-boundary HOLD_AND_ALERT vs qualified
    # exits); recorded on the run receipt, never inside the published package.
    holding_directives: tuple[HoldingDirective, ...] = ()
    # M2 F10: P11-bound trend observations carried verbatim, abstentions kept.
    trend_observations: tuple[TrendObservation, ...] = ()
    # M2 section 3: the three independent history floors evaluated for this run.
    history_gates: tuple[HistoryGateResult, ...] = ()


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
        # 15:00 is the T-close observation anchor only; the package
        # knowledge_cutoff is the configured decision cutoff (e.g. 22:30+08).
        observation_anchor = datetime.combine(
            snapshot.market_date, SESSION_CLOSE, tzinfo=EXCHANGE_TZ
        )
        cutoff = request.decision_cutoff or observation_anchor
        eligible = datetime.combine(request.next_session, SESSION_OPEN, tzinfo=EXCHANGE_TZ)
        expires = datetime.combine(request.next_session, SESSION_CLOSE, tzinfo=EXCHANGE_TZ)
        held = {fact.security_code for fact in request.holdings if fact.quantity > 0}
        decisions: list[MarketDecision] = []
        exclusions: list[SecurityExclusion] = []
        trend_observations: list[TrendObservation] = []
        holding_directives: list[HoldingDirective] = []
        # Audit R5: the port consumes the real listed-securities facts read
        # from the verified 1d payload; a candidate whose security is not in
        # the input's listing set is dropped with its reason recorded, never
        # evaluated from configuration alone.
        listed = set(snapshot.listed_securities or ())
        for item in self._candidates:
            code = str(item["security_code"])
            if listed and code not in listed:
                exclusions.append(
                    SecurityExclusion(
                        security_code=code,
                        reason_code="INPUT_UNAVAILABLE",
                        detail=(
                            "security not present in the verified 1d listed facts"
                        ),
                    )
                )
                continue
            drop_reason = item.get("unqualified_reason")
            if drop_reason:
                # Single-security input defect: drop this security's new
                # candidates and record the reason; the package still publishes.
                exclusions.append(
                    SecurityExclusion(
                        security_code=code,
                        reason_code=str(drop_reason.get("reason_code", "INPUT_UNAVAILABLE")),
                        detail=str(drop_reason.get("detail", "candidate input not qualified")),
                        **{
                            key: (
                                None
                                if drop_reason.get(key) is None
                                else str(drop_reason[key])
                            )
                            for key in _EXCLUSION_EVIDENCE_KEYS
                        },
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
        # M2 F10: a candidate may carry the P11 evaluate_trend_stability
        # observation identity; P12 transports it verbatim, abstentions kept.
        for item in self._candidates:
            obs = item.get("trend_observation")
            if not isinstance(obs, dict):
                continue
            if not obs.get("observation_digest"):
                continue
            trend_observations.append(
                TrendObservation(
                    opportunity_id=str(
                        obs.get("opportunity_id")
                        or ("OPP-D1B-{}-{}".format(
                            snapshot.market_date.strftime("%Y%m%d"),
                            str(item["security_code"]),
                        ))
                    ),
                    observation_digest=str(obs["observation_digest"]),
                    applicability=str(obs.get("applicability", "ABSTAINED")),
                    raw_value=None if obs.get("raw_value") is None else str(obs["raw_value"]),
                )
            )
        # Frozen cross-regime rule (issue #719 comment-7112561800): a holding
        # whose evaluation depends on the missing volume/amount input yields
        # HOLD_AND_ALERT.  A qualified volume-independent exit or reduction
        # signal (price/risk evidence, base exit) still emits EXIT - the
        # boundary reason is recorded, it never rewrites the signal into a hold.
        volume_boundary = {
            item.security_code
            for item in exclusions
            if item.reason_code == VOLUME_REGIME_BOUNDARY
        }
        holding_signals = {
            str(item["security_code"]): item
            for item in self._candidates
            if item.get("holding_signal")
        }
        for fact in request.holdings:
            if fact.quantity <= 0:
                continue
            signal = holding_signals.get(fact.security_code)
            if signal is not None:
                action = str(signal.get("action", EXIT_SIGNAL))
                if action == "EXIT":
                    action = EXIT_SIGNAL
                holding_directives.append(
                    HoldingDirective(
                        security_code=fact.security_code,
                        action=action,
                        reason_code=str(signal.get("reason_code", EXIT_SIGNAL)),
                        detail=str(
                            signal.get(
                                "detail",
                                "qualified volume-independent exit/reduction signal",
                            )
                        ),
                    )
                )
                if action == EXIT_SIGNAL:
                    # A qualified volume-independent exit/reduction signal
                    # still publishes; the boundary reason is recorded on the
                    # receipt, it never rewrites the signal into a hold.
                    decisions.append(
                        MarketDecision(
                            opportunity_id="OPP-D1B-{}-{}".format(
                                snapshot.market_date.strftime("%Y%m%d"),
                                fact.security_code,
                            ),
                            security_code=fact.security_code,
                            security_name=str(
                                signal.get("security_name", fact.security_code)
                            ),
                            stage=OpportunityStage.EXIT_PLAN,
                            reason_codes=(EXIT_SIGNAL,),
                            explanations=(
                                Explanation(
                                    EXIT_SIGNAL,
                                    ExplanationUse.ACTION,
                                    f"{self._ref.ref_id}@{self._ref.version}",
                                    str(
                                        signal.get(
                                            "detail",
                                            "qualified volume-independent "
                                            "exit/reduction signal",
                                        )
                                    ),
                                ),
                            ),
                            next_observation="next session after close",
                            chart=None,
                            entry=None,
                            component_ref=self._ref,
                        )
                    )
            elif fact.security_code in volume_boundary:
                holding_directives.append(
                    HoldingDirective(
                        security_code=fact.security_code,
                        action=HOLD_AND_ALERT,
                        reason_code=VOLUME_REGIME_BOUNDARY,
                        detail=(
                            "volume/amount input crosses a trading-rule regime "
                            "boundary; the holding evaluation that depends on "
                            "it is deferred, the position is kept"
                        ),
                    )
                )
            elif fact.unavailable_reason:
                # Audit R11: a holding whose own account facts are incomplete
                # cannot be evaluated - HOLD_AND_ALERT, position stays held.
                holding_directives.append(
                    HoldingDirective(
                        security_code=fact.security_code,
                        action=HOLD_AND_ALERT,
                        reason_code="INPUT_UNAVAILABLE",
                        detail=(
                            "holding evidence incomplete: "
                            + str(fact.unavailable_reason)
                        ),
                    )
                )
                decisions.append(
                    MarketDecision(
                        opportunity_id="OPP-D1B-{}-{}".format(
                            snapshot.market_date.strftime("%Y%m%d"),
                            fact.security_code,
                        ),
                        security_code=fact.security_code,
                        security_name=fact.security_code,
                        stage=OpportunityStage.HOLDING,
                        reason_codes=(HOLD_AND_ALERT,),
                        explanations=(
                            Explanation(
                                HOLD_AND_ALERT,
                                ExplanationUse.EXPLANATION,
                                f"{self._ref.ref_id}@{self._ref.version}",
                                "holding evidence incomplete; the position "
                                "stays held and alerts",
                            ),
                        ),
                        next_observation="next qualified daily input",
                        chart=None,
                        entry=None,
                        component_ref=self._ref,
                    )
                )
        decisions.extend(
            MarketDecision(
                opportunity_id="OPP-D1B-{}-{}".format(
                    snapshot.market_date.strftime("%Y%m%d"), fact.security_code
                ),
                security_code=fact.security_code,
                security_name=fact.security_code,
                stage=OpportunityStage.HOLDING,
                reason_codes=(HOLD_AND_ALERT,),
                explanations=(
                    Explanation(
                        HOLD_AND_ALERT,
                        ExplanationUse.EXPLANATION,
                        f"{self._ref.ref_id}@{self._ref.version}",
                        "volume regime boundary defers the holding evaluation; "
                        "the position stays held and alerts",
                    ),
                ),
                next_observation="next qualified daily input",
                chart=None,
                entry=None,
                component_ref=self._ref,
            )
            for fact in request.holdings
            if fact.quantity > 0
            and fact.security_code in volume_boundary
            and fact.security_code not in holding_signals
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
                published_at=max(request.published_at, cutoff),
                next_session=request.next_session,
                decisions=tuple(decisions),
            )
        )
        # Run-seam: the synthetic runner mints its own evaluation receipt and
        # source closure inside this same run.  producer_id is the synthetic
        # identity (bound only to synthetic accounts); coverage_digest is
        # recomputed over this run's producer identity, run id and evaluated
        # universe; evaluated_candidates carries the per-decision identity the
        # publish gate matches one-to-one against the emitted decisions.
        evaluated_universe = sorted(
            {
                str(item["security_code"])
                for item in self._candidates
                if item.get("security_code")
            }
            | {
                fact.security_code
                for fact in request.holdings
                if fact.quantity > 0
            }
        )
        run_id = digest(
            {
                "run": self._ref.ref_id,
                "market_date": snapshot.market_date.isoformat(),
                "manifest": snapshot.core_manifest_sha256,
                "published_at": request.published_at.isoformat(),
            }
        )
        # The receipt echoes the job-minted run identity when the job supplies
        # one (audit R10); bound into the coverage digest so a receipt minted
        # by a prior or different-manifest run can never pass the gate.
        if request.run_id:
            run_id = request.run_id
        evaluated_candidates = sorted(
            ({
                "opportunity_id": d.opportunity_id,
                "security_code": d.security_code,
            }
            for d in decision_set.decisions),
            key=_candidate_sort_key,
        )
        coverage_payload = {
            "producer_id": SYNTHETIC_PRODUCER_ID,
            "producer_version": self._ref.version,
            "run_id": run_id,
            "evaluated_universe": evaluated_universe,
            # Bind this exact input: a receipt minted over another manifest
            # digest or scope recomputes a different coverage (audit R10).
            "manifest_sha256": snapshot.core_manifest_sha256,
            "package_id": snapshot.package_id,
        }
        evaluation_receipt = {
            "producer_id": SYNTHETIC_PRODUCER_ID,
            "producer_version": self._ref.version,
            "run_id": run_id,
            "completed_at": request.published_at.isoformat(),
            "coverage_digest": digest(coverage_payload),
            "evaluated_count": len(evaluated_universe),
            "qualified_count": sum(
                1 for d in decision_set.decisions if d.entry is not None
            ),
            "evaluated_universe": evaluated_universe,
            "evaluated_candidates": evaluated_candidates,
        }
        source_closure = {
            "package_id": snapshot.package_id,
            "market_date": snapshot.market_date.isoformat(),
            "core_manifest_sha256": snapshot.core_manifest_sha256,
            "index_sha256": snapshot.index_sha256,
            "base_manifest_sha256": snapshot.base_manifest_sha256,
            "content_digest": snapshot.content_digest,
            "row_counts": dict(snapshot.row_counts),
            "runner": {
                "ref_id": self._ref.ref_id,
                "version": self._ref.version,
                "digest": self._ref.digest,
            },
        }
        return RunnerOutput(
            decision_set=decision_set,
            exclusions=tuple(exclusions),
            evaluation_receipt=evaluation_receipt,
            source_closure=source_closure,
            holding_directives=tuple(holding_directives),
            trend_observations=tuple(trend_observations),
            history_gates=tuple(snapshot.history_gates),
        )
