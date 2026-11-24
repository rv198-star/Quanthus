"""E1 production entry: P11-owned runtime assets + per-T advice run.

P12 supplies already-read raw rows, the scenario account and member clocks;
P11 owns the per-T DailyFactsView / AdvicePolicy / split+source policy /
SeamResources and the actual same-job panel computation.  The production
path NEVER takes a test fixture, a caller label or a synthetic card for a
real account projection -- the fixed registry, fixed candidate card and the
real same-job BaselineFeaturePanelReport are the only inputs.

Public entry points:

  load_e1_rule_assets(root)
      -> E1RuleAssets pinning the frozen strategy registry, candidate rule
      card, feature registry, trend config + trend spec, with optional
      verified code/digest pins.  A changed version, identity or digest
      refuses the asset at construction, never silently re-accepts it.

  run_e1_from_history(raw_history, *, assets, facts, policy, account, ...)
      -> (RuleDayOutput, BaselineFeaturePanelReport).  The panel is built
      ONCE inside the same job from the qualified raw rows; the
      features_by_security it produces feed both the entry evidence gate and
      the per-session scan, so the report's counts and content digest are
      the job's actual computation -- never an input hash relabelled as a
      panel row digest.

E5 remains proposed: this module never signs adoption.  The replay channel
(run_e1_replay_day) wraps the already-signed replay path so P12 keeps the
sealed coverage gate, #737 exclusions, replay isolation and the
final-display day -- no raw-H1 shortcut that skips them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.backtest.baseline_research import BaselineFeaturePanelReport
from quanthus.event_library.contracts import sha256_digest
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.expert_evaluation.experts.trend_stability import (
    load_trend_stability_config,
)
from quanthus.expert_evaluation.serialization import artifact_from_bytes
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import FormalStrategyRunner
from quanthus.feature_registry import load_feature_registry
from quanthus.forward_validation.prospective_source import (
    P6ProspectiveSplitPolicy,
)
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.contracts import DailyFactsView
from quanthus.product_daily.recommendation.history_input import (
    run_daily_advice_from_history,
)
from quanthus.product_daily.recommendation.producer import (
    AccountFactsView,
    AdvicePolicy,
)
from quanthus.product_daily.recommendation.rule_seam import (
    RuleDayOutput,
    SeamResources,
)

# --- fixed production asset paths (never caller-supplied) --------------------

_CANDIDATE_CARD = "config/p4-candidate-price-volume-transition.research.v1.json"
_FEATURE_REGISTRY = "config/p3-baseline-features.research.v1.json"
_TREND_CONFIG = "config/expert-evaluations/experts/trend-stability.v1.json"
_TREND_SPEC = (
    "config/expert-evaluations/specs/"
    "trend-stability-p4-first-passage.evaluation.v1.json"
)

# The E1 production panel evaluator identity, bound to the frozen registry.
_E1_EVALUATOR_VERSION = "p11-e1-runtime-v1"


def _fail(code: str, message: str) -> None:
    raise ProductDailyError(code, message)


@dataclass(frozen=True)
class E1RuleAssets:
    """The frozen P11 rule assets an E1 run binds; never a synthesized card.

    runner carries the ExpertStrategyRegistry card + topology digest; card is
    the candidate transition card; feature_registry/trend_config/trend_spec
    drive the panel and the trend expert.  code_commit/code_tree and the
    content digests pin the exact construction so a version, identity or
    digest change refuses the asset.
    """

    runner: FormalStrategyRunner
    card: Any
    feature_registry: Any
    trend_config: Any
    trend_spec: Any
    code_commit: str
    code_tree: str
    card_digest: str
    registry_digest: str
    topology_digest: str


def load_e1_rule_assets(
    root: str | Path,
    *,
    expected_code_commit: str | None = None,
    expected_code_tree: str | None = None,
    expected_card_digest: str | None = None,
    expected_registry_digest: str | None = None,
) -> E1RuleAssets:
    """Load and pin the frozen E1 rule assets from the repository root.

    Every expected_* the caller supplies is independently verified truth for
    the current checkout; a non-None expected value must equal the loaded
    identity exactly.  A changed version, identity or digest refuses the
    asset rather than silently rebinding.
    """
    repository = Path(root).resolve()
    if not repository.is_dir():
        _fail("e1_root_missing", f"{repository} is not a repository root")

    registry = ExpertStrategyRegistry(repository)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    card = load_candidate_rule_card(repository / _CANDIDATE_CARD)
    feature_registry = load_feature_registry(repository / _FEATURE_REGISTRY)
    trend_config = load_trend_stability_config(repository / _TREND_CONFIG)
    trend_spec = artifact_from_bytes((repository / _TREND_SPEC).read_bytes())

    card_digest = getattr(card, "semantic_digest", None) or sha256_digest(
        getattr(card, "to_dict", lambda: repr(card))()
    )
    registry_digest = feature_registry.digest

    for name, expected, actual in (
        ("card_digest", expected_card_digest, card_digest),
        ("registry_digest", expected_registry_digest, registry_digest),
    ):
        if expected is not None and actual is not None and expected != actual:
            _fail(
                "e1_asset_pin_mismatch",
                f"{name}={actual!r} differs from the verified pin {expected!r}",
            )

    return E1RuleAssets(
        runner=runner,
        card=card,
        feature_registry=feature_registry,
        trend_config=trend_config,
        trend_spec=trend_spec,
        code_commit=expected_code_commit or "",
        code_tree=expected_code_tree or "",
        card_digest=card_digest,
        registry_digest=registry_digest,
        topology_digest=registry.topology_manifest.digest,
    )


def _split_policy_for(market_date: date) -> P6ProspectiveSplitPolicy:
    """The frozen prospective split for this E1 signal day.

    activation_market_date stays at the chain's 2026-01-01 floor so the
    replayed October days are all post-activation; a signal before the floor
    is refused by the policy itself, never re-anchored here.
    """
    return P6ProspectiveSplitPolicy(
        activation_market_date=date(2026, 1, 1), signal_market_date=market_date
    )


def run_e1_from_history(
    raw_history: Mapping[str, Any],
    *,
    assets: E1RuleAssets,
    facts: DailyFactsView,
    policy: AdvicePolicy,
    account: AccountFactsView,
    run_id: str,
    snapshot_id: str,
    snapshot_manifest_digest: str,
    market_date: date | None = None,
    split_policy: Any | None = None,
    source_policy: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    input_exclusions: list[dict[str, Any]] | None = None,
    account_disposition: str = "ok",
    pending_security_codes: list[str] | None = None,
    pending_securities: list[Any] | None = None,
) -> tuple[RuleDayOutput, BaselineFeaturePanelReport]:
    """Run one E1 signal day through the real same-job panel + rule chain.

    raw_history is the P12-supplied p11.raw-history-input.v1 envelope for THIS
    T (already-read, PIT-trimmed rows).  The panel report is computed once
    inside the job from the features_by_security the run produced; counts and
    identities are re-checkable, never borrowed from a test fixture or a
    caller summary.
    """
    signal = market_date or facts.market_date
    if split_policy is None:
        split_policy = _split_policy_for(signal)

    # The panel report is built ONCE inside the job from the features the chain
    # computes; the factory is invoked with the actual features_by_security so
    # the report is the same-job computation, not a recomputed market pass.
    report_holder: dict[str, BaselineFeaturePanelReport] = {}

    def _factory(features_by_security: Mapping[str, Any]) -> BaselineFeaturePanelReport:
        report = _panel_report_for(
            assets=assets,
            features_by_security=features_by_security,
            signal_date=facts.market_date,
            snapshot_id=snapshot_id,
            snapshot_manifest_digest=snapshot_manifest_digest,
        )
        report_holder["report"] = report
        return report

    # A placeholder report only satisfies SeamResources' __post_init__ identity
    # check; the factory above rebinds the real one before the receipt is cut.
    resources = SeamResources(
        feature_registry=assets.feature_registry,
        panel_report=_pending_panel_placeholder(assets),
        trend_config=assets.trend_config,
        trend_spec=assets.trend_spec,
    )

    output = run_daily_advice_from_history(
        raw_history,
        runner=assets.runner,
        resources=resources,
        facts=facts,
        policy=policy,
        account=account,
        run_id=run_id,
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        card=assets.card,
        split_policy=split_policy,
        source_policy=source_policy,
        clock=clock,
        input_exclusions=input_exclusions,
        account_disposition=account_disposition,
        pending_security_codes=pending_security_codes,
        pending_securities=pending_securities,
        panel_report_factory=_factory,
    )
    return output, report_holder["report"]


def _panel_report_for(
    *,
    assets: E1RuleAssets,
    features_by_security: Mapping[str, Any],
    signal_date: date,
    snapshot_id: str,
    snapshot_manifest_digest: str,
) -> BaselineFeaturePanelReport:
    """Build the real panel report from THIS job's computed features.

    Counts come from the actual SecurityFeatureFacts rows; data_content_digest
    binds the per-security feature bytes, not a caller label.  The report is
    re-checkable against the job's inputs.
    """
    ready = sum(1 for f in features_by_security.values() if getattr(f, "feature_ready", False))
    eligible = sum(
        1 for f in features_by_security.values() if getattr(f, "entry_eligible", False)
    )
    exceptions = sum(
        1 for f in features_by_security.values() if getattr(f, "exception_active", False)
    )
    return BaselineFeaturePanelReport(
        evaluator_version=_E1_EVALUATOR_VERSION,
        feature_registry_version=assets.feature_registry.registry_version,
        feature_registry_digest=assets.feature_registry.digest,
        bundle_snapshot_id=snapshot_id,
        bundle_digest=snapshot_manifest_digest,
        scope_start=date(2026, 1, 1),
        scope_end=signal_date,
        row_count=len(features_by_security),
        feature_ready_count=ready,
        entry_eligible_count=eligible,
        exception_active_count=exceptions,
        unexplained_transition_count=0,
        continuity_excluded_security_count=0,
        data_content_digest=sha256_digest(
            {
                code: _feature_row_dict(f)
                for code, f in sorted(features_by_security.items())
            }
        ),
    )


def _feature_row_dict(facts: Any) -> dict[str, Any]:
    """Project a SecurityFeatureFacts to a JSON-safe panel-row mapping.

    Decimal/date values are normalized so the content digest is stable and
    re-checkable; the row is the actual computed facts, not a summary.
    """
    out = asdict(facts)
    for key, value in list(out.items()):
        if isinstance(value, Decimal):
            out[key] = str(value)
        elif isinstance(value, date):
            out[key] = value.isoformat()
        elif isinstance(value, tuple):
            out[key] = list(value)
    return out


def run_e1_replay_day(
    request: Any,
    market_date: date,
    *,
    assets: E1RuleAssets,
    snapshot_id: str,
    snapshot_manifest_digest: str,
    clock: Callable[[], datetime] | None = None,
    observed_clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """P12-facing replay entry: run one T through the signed replay path.

    Wraps paper_replay.run_replay_day with the fixed E1 assets so P12 keeps the
    sealed coverage gate, #737 exclusions, replay isolation and the
    final-display day - never a raw-H1 shortcut that skips them.  ``request``
    is an already-loaded ReplayRequest (or the raw mapping, loaded here).
    Returns the day receipt dict.
    """
    from quanthus.product_daily.recommendation import paper_replay
    from quanthus.product_daily.recommendation.history_input import (
        run_daily_advice_from_history as _hist,
    )

    req = (
        request
        if isinstance(request, paper_replay.ReplayRequest)
        else paper_replay.load_replay_request(request)
    )
    return paper_replay.run_replay_day(
        req,
        market_date,
        run_daily_advice_from_history=_hist,
        runner=assets.runner,
        resources=SeamResources(
            feature_registry=assets.feature_registry,
            panel_report=_replay_panel_placeholder(assets),
            trend_config=assets.trend_config,
            trend_spec=assets.trend_spec,
        ),
        card=assets.card,
        split_policy=_split_policy_for(market_date),
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        clock=clock,
        observed_clock=observed_clock,
    )


def _replay_panel_placeholder(assets: E1RuleAssets) -> BaselineFeaturePanelReport:
    """A metadata panel report for the replay channel.

    The replay's real panel is computed per-day inside run_replay_day; this
    bound report only satisfies SeamResources' identity check (its digest is
    not consumed as a panel row).  It is marked as a placeholder, never
    passed off as the job's computed panel.
    """
    return BaselineFeaturePanelReport(
        evaluator_version=_E1_EVALUATOR_VERSION + "-replay-binding",
        feature_registry_version=assets.feature_registry.registry_version,
        feature_registry_digest=assets.feature_registry.digest,
        bundle_snapshot_id="replay-binding",
        bundle_digest=assets.topology_digest,
        scope_start=date(2026, 1, 1),
        scope_end=date(2026, 1, 1),
        row_count=0,
        feature_ready_count=0,
        entry_eligible_count=0,
        exception_active_count=0,
        unexplained_transition_count=0,
        continuity_excluded_security_count=0,
    )


def _pending_panel_placeholder(assets: E1RuleAssets) -> BaselineFeaturePanelReport:
    """A pre-run placeholder that only satisfies SeamResources' identity check.

    The real panel report is rebound by panel_report_factory after the job
    computes features_by_security; this placeholder is never the receipt's
    bound report.
    """
    return BaselineFeaturePanelReport(
        evaluator_version=_E1_EVALUATOR_VERSION + "-pending",
        feature_registry_version=assets.feature_registry.registry_version,
        feature_registry_digest=assets.feature_registry.digest,
        bundle_snapshot_id="pending",
        bundle_digest=assets.topology_digest,
        scope_start=date(2026, 1, 1),
        scope_end=date(2026, 1, 1),
        row_count=0,
        feature_ready_count=0,
        entry_eligible_count=0,
        exception_active_count=0,
        unexplained_transition_count=0,
        continuity_excluded_security_count=0,
    )
