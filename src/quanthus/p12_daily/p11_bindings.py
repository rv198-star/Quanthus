"""Assemble the injected P11 objects for the E1 real-history runner.

Issue #718 / 8792-phase1.  REFERENCE.md (e1-runtime-intake @ 7d45f0b2) fixes
HOW each object is built: the runner is a FormalStrategyRunner bound to the
ExpertStrategyRegistry card + topology digest, the candidate card loads from
p4-candidate-price-volume-transition, and resources bind the three fixed
config files plus a panel_report produced by THAT run's real panel evaluator.

The October engineering replay fixes the logical decision instant at T-day
21:30 +08 (not the engine default 22:30).  This module only ASSEMBLES - the
panel_report, account facts and source/split clocks are injected by the
caller because they bind this run's actual computation and account, never a
P12-synthesized value.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .p11_history_runner import P11Bindings

SHANGHAI = ZoneInfo("Asia/Shanghai")

# Frozen per REFERENCE.md / coordinator: the October replay decides at 21:30 +08.
REPLAY_DECISION_TIME = time(21, 30)

# Fixed P11 config paths (relative to the repository root).
CANDIDATE_CARD_PATH = "config/p4-candidate-price-volume-transition.research.v1.json"
FEATURE_REGISTRY_PATH = "config/p3-baseline-features.research.v1.json"
TREND_CONFIG_PATH = "config/expert-evaluations/experts/trend-stability.v1.json"
TREND_SPEC_PATH = (
    "config/expert-evaluations/specs/"
    "trend-stability-p4-first-passage.evaluation.v1.json"
)

# P11 fixed producer / policy identities (frozen with the P11 card).
ADVICE_POLICY_ID = "p11-d1b"
ADVICE_POLICY_VERSION = "1.0.0"
TRUSTED_PRODUCER_ID = "p11.fixed-rule-day"
RULE_DAY_PRODUCER_VERSION = "1.1.0"


@dataclass(frozen=True)
class P11Static:
    """The run-invariant P11 objects loaded once from the fixed config files."""

    runner: Any
    card: Any
    feature_registry: Any
    trend_config: Any
    trend_spec: Any


def load_p11_static(root: Path) -> P11Static:
    """Load runner + card + resource configs from the fixed tree (REFERENCE.md)."""
    from quanthus.event_library.rule_card import load_candidate_rule_card
    from quanthus.expert_evaluation.experts.trend_stability import (
        load_trend_stability_config,
    )
    from quanthus.expert_evaluation.serialization import artifact_from_bytes
    from quanthus.expert_strategy.registry import ExpertStrategyRegistry
    from quanthus.expert_strategy.runner import FormalStrategyRunner
    from quanthus.feature_registry import load_feature_registry

    registry = ExpertStrategyRegistry(root)
    runner = FormalStrategyRunner(
        registry.card, registry.topology_manifest.digest
    )
    return P11Static(
        runner=runner,
        card=load_candidate_rule_card(root / CANDIDATE_CARD_PATH),
        feature_registry=load_feature_registry(root / FEATURE_REGISTRY_PATH),
        trend_config=load_trend_stability_config(root / TREND_CONFIG_PATH),
        trend_spec=artifact_from_bytes((root / TREND_SPEC_PATH).read_bytes()),
    )


def replay_decision_cutoff(market_date: date) -> datetime:
    """The fixed logical decision instant for one replay market_date: T 21:30 +08."""
    return datetime.combine(market_date, REPLAY_DECISION_TIME, SHANGHAI)


def build_p11_bindings(
    *,
    static: P11Static,
    panel_report: Any,
    facts: Any,
    policy: Any,
    account: Any,
    split_policy: Any,
    source_policy: Any,
    snapshot_id: str,
    snapshot_manifest_digest: str,
    clock: Callable[[], datetime] | None = None,
    anchor_mode: str = "equivalent_day",
) -> P11Bindings:
    """Assemble P11Bindings; every run-bound object is caller-injected.

    panel_report binds this run's real feature-panel computation (snapshot,
    counts, content digest) - it is never synthesized here.  facts/policy/
    account carry the P12-projected market/account truth for this run.
    """
    from quanthus.product_daily.recommendation.rule_seam import SeamResources

    resources = SeamResources(
        feature_registry=static.feature_registry,
        panel_report=panel_report,
        trend_config=static.trend_config,
        trend_spec=static.trend_spec,
    )
    return P11Bindings(
        runner=static.runner,
        resources=resources,
        facts=facts,
        policy=policy,
        account=account,
        card=static.card,
        split_policy=split_policy,
        source_policy=source_policy,
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        clock=clock,
        anchor_mode=anchor_mode,
    )
