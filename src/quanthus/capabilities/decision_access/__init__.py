"""Decision access capability: typed, PIT-checked market decisions consumed by P12.

The product never re-scores a market decision.  A decision carries the identity of the
component that produced it, its allowed use (action vs. explanation), the knowledge cutoff
and named evidence.  Concrete sources (adopted daily packages or a sealed synthetic fixture)
implement the application's ``DecisionPackageSource`` port.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum

from quanthus.shared.fact_contracts import FactError
from quanthus.shared.values import ADMITTED_MODES, VersionRef

KNOWLEDGE_CUTOFF_BREACH = "KNOWLEDGE_CUTOFF_BREACH"
POLICY_NOT_ADOPTED = "POLICY_NOT_ADOPTED"
STALE_MARKET_INPUT = "STALE_MARKET_INPUT"
EXECUTION_WINDOW_INVALID = "EXECUTION_WINDOW_INVALID"
CLOSE_TO_NEXT_OPEN = "close-to-next-open-v1"
# Session bounds of the active daily profile: signals after the T close, first execution at
# the T+1 open (AGENTS.md invariants, config/p2-gates.v1.json accepted_profile).
_EXCHANGE_TZ = timezone(timedelta(hours=8))
_SESSION_OPEN = time(9, 30)
_SESSION_CLOSE = time(15, 0)


class OpportunityStage(str, Enum):
    ENTRY_PLAN = "entry_plan"
    WAITING = "waiting"
    HOLDING = "holding"
    EXIT_PLAN = "exit_plan"
    INPUT_BLOCKED = "input_blocked"
    ENDED = "ended"


class ExplanationUse(str, Enum):
    ACTION = "action"
    EXPLANATION = "explanation"


@dataclass(frozen=True)
class PriceBar:
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        prices = (self.open, self.high, self.low, self.close)
        if any(not isinstance(item, Decimal) or item <= 0 for item in prices):
            raise FactError("INVALID_DECISION", "bar prices must be positive Decimals")
        if self.high < max(self.open, self.close, self.low) or self.low > min(
            self.open, self.close
        ):
            raise FactError("INVALID_DECISION", "bar high/low bound violated")
        if not isinstance(self.volume, int) or isinstance(self.volume, bool) or self.volume < 0:
            raise FactError("INVALID_DECISION", "bar volume must be a non-negative integer")


@dataclass(frozen=True)
class ChartEvidence:
    price_basis: str
    as_of: datetime
    bars: tuple[PriceBar, ...]
    support: Decimal | None
    volume_baseline: int | None
    volume_baseline_window: int | None


@dataclass(frozen=True)
class Explanation:
    code: str
    use: ExplanationUse
    component_ref: str
    text: str


@dataclass(frozen=True)
class EntryIntent:
    """Strategy-side buy intent; quantity is solved later by the risk capability."""

    fraction: Decimal
    price_bound: Decimal
    mark_price: Decimal
    industry_id: str
    eligible_at: datetime
    expires_at: datetime
    window_ref: str
    invalidations: tuple[str, ...]


@dataclass(frozen=True)
class MarketDecision:
    opportunity_id: str
    security_code: str
    security_name: str
    stage: OpportunityStage
    reason_codes: tuple[str, ...]
    explanations: tuple[Explanation, ...]
    next_observation: str | None
    chart: ChartEvidence | None
    entry: EntryIntent | None
    component_ref: VersionRef
    # p12.decision-set.v2 (issue #723): an EXIT_PLAN decision declares its
    # exit kind ("full" | "reduce") and, for a reduce, the target position
    # fraction it leaves behind.  Both are optional so v1 packages still
    # validate unchanged; on v1 they stay None.
    exit_kind: str | None = None
    target_position_fraction: Decimal | None = None


@dataclass(frozen=True)
class MarketDecisionSet:
    """Market-side output for one T close; account applicability is added by the application."""

    source_ref: VersionRef
    mode: str
    synthetic: bool
    market_date: date
    knowledge_cutoff: datetime
    published_at: datetime
    next_session: date
    decisions: tuple[MarketDecision, ...]


def validate_decision_set(value: MarketDecisionSet) -> MarketDecisionSet:
    """Fail closed on PIT, mode and identity breaches before any consumer sees the set."""

    if value.mode not in ADMITTED_MODES:
        raise FactError(POLICY_NOT_ADOPTED, "unknown decision mode")
    if value.synthetic != (value.mode == "synthetic"):
        raise FactError(POLICY_NOT_ADOPTED, "synthetic identity and mode differ")
    for stamp in (value.knowledge_cutoff, value.published_at):
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise FactError("INVALID_DECISION", "decision timestamps must be aware")
    if value.published_at < value.knowledge_cutoff:
        raise FactError(KNOWLEDGE_CUTOFF_BREACH, "package published before its knowledge cutoff")
    if value.knowledge_cutoff.date() != value.market_date:
        raise FactError(KNOWLEDGE_CUTOFF_BREACH, "knowledge cutoff must fall on the market date")
    if value.next_session <= value.market_date:
        raise FactError(KNOWLEDGE_CUTOFF_BREACH, "next session must follow the market date")
    identities = [item.opportunity_id for item in value.decisions]
    if len(identities) != len(set(identities)):
        raise FactError("INVALID_DECISION", "opportunity ids must be unique")
    securities = [
        item.security_code for item in value.decisions if item.stage is not OpportunityStage.ENDED
    ]
    if len(securities) != len(set(securities)):
        raise FactError("INVALID_DECISION", "one live opportunity per security")
    for item in value.decisions:
        if item.chart is not None:
            if item.chart.as_of > value.knowledge_cutoff:
                raise FactError(KNOWLEDGE_CUTOFF_BREACH, "chart evidence is after the cutoff")
            if any(bar.session > value.market_date for bar in item.chart.bars):
                raise FactError(KNOWLEDGE_CUTOFF_BREACH, "bar after the market date")
            sessions = [bar.session for bar in item.chart.bars]
            if sessions != sorted(set(sessions)):
                raise FactError("INVALID_DECISION", "bars must be unique and ordered")
        if (item.stage is OpportunityStage.ENTRY_PLAN) != (item.entry is not None):
            raise FactError("INVALID_DECISION", "entry intent belongs only to entry plans")
        if item.entry is not None:
            entry = item.entry
            if entry.eligible_at <= value.knowledge_cutoff:
                raise FactError(
                    KNOWLEDGE_CUTOFF_BREACH, "action may first execute after the T close"
                )
            if entry.expires_at <= entry.eligible_at:
                raise FactError("INVALID_DECISION", "entry window must be non-empty")
            if not (Decimal("0") < entry.fraction <= Decimal("1")):
                raise FactError("INVALID_DECISION", "entry fraction must be in (0, 1]")
            if not any(x.use is ExplanationUse.ACTION for x in item.explanations):
                raise FactError(
                    "INVALID_DECISION", "an action requires an action-use explanation source"
                )
        if (
            item.stage is OpportunityStage.INPUT_BLOCKED
            and STALE_MARKET_INPUT not in (item.reason_codes)
            and "INPUT_UNAVAILABLE" not in item.reason_codes
        ):
            raise FactError("INVALID_DECISION", "blocked input requires a named input reason")
        # p12.decision-set.v2 exit-kind integrity.  exit_kind only has meaning on
        # an EXIT_PLAN; carrying it on any other stage is a version breach.
        if item.exit_kind is not None:
            if item.exit_kind not in ("full", "reduce"):
                raise FactError(
                    "INVALID_DECISION", "exit_kind must be full or reduce"
                )
            if item.stage is not OpportunityStage.EXIT_PLAN:
                raise FactError(
                    "INVALID_DECISION",
                    "exit_kind may only accompany an exit_plan decision",
                )
        if item.target_position_fraction is not None:
            if item.exit_kind != "reduce":
                raise FactError(
                    "INVALID_DECISION",
                    "target_position_fraction is only set on a reduce exit",
                )
            if not (Decimal("0") <= item.target_position_fraction < Decimal("1")):
                raise FactError(
                    "INVALID_DECISION",
                    "target_position_fraction must be in [0, 1)",
                )
    return value


def execution_window_issue(
    profile: str, decisions: MarketDecisionSet, entry: EntryIntent
) -> str | None:
    """Profile-bound window check; a non-admitted window yields a reason, never an action.

    Under close-to-next-open-v1 an entry may only be actionable inside the next trading
    session (exchange time) after the T close; a same-day evening window is rejected.
    """

    if profile != CLOSE_TO_NEXT_OPEN:
        return POLICY_NOT_ADOPTED
    start = entry.eligible_at.astimezone(_EXCHANGE_TZ)
    end = entry.expires_at.astimezone(_EXCHANGE_TZ)
    if (
        start.date() != decisions.next_session
        or end.date() != decisions.next_session
        or start.time() < _SESSION_OPEN
        or end.time() > _SESSION_CLOSE
    ):
        return EXECUTION_WINDOW_INVALID
    return None


__all__ = [
    "CLOSE_TO_NEXT_OPEN",
    "EXECUTION_WINDOW_INVALID",
    "KNOWLEDGE_CUTOFF_BREACH",
    "POLICY_NOT_ADOPTED",
    "STALE_MARKET_INPUT",
    "ChartEvidence",
    "EntryIntent",
    "Explanation",
    "ExplanationUse",
    "MarketDecision",
    "MarketDecisionSet",
    "OpportunityStage",
    "PriceBar",
    "execution_window_issue",
    "validate_decision_set",
]
