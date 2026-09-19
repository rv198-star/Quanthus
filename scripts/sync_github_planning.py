#!/usr/bin/env python3
"""Synchronize Quanthus design work with GitHub planning objects.

The repository documents are the source of truth. This command manages only labels,
milestones, and issues whose titles start with a Quanthus stable work-item id.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEFAULT_REPOSITORY = "rv198-star/Quanthus"
MANAGED_LABEL_PREFIXES = ("type:", "status:", "priority:", "phase:", "area:")


@dataclass(frozen=True)
class LabelSpec:
    name: str
    color: str
    description: str


@dataclass(frozen=True)
class ChapterSpec:
    number: str
    path: str
    phase: str
    area: str
    milestone: str
    status: str


@dataclass(frozen=True)
class WorkItem:
    stable_id: str
    title: str
    dependencies: str
    evidence: str
    phase: str
    area: str
    status: str
    priority: str
    milestone: str | None
    design_path: str
    issue_type: str


@dataclass(frozen=True)
class IssueImplementationSpec:
    design_path: str
    approach: str
    owned_paths: tuple[str, ...]
    decision_gates: tuple[str, ...]
    verification: tuple[str, ...]


LABELS = (
    LabelSpec("type:epic", "5319E7", "Design chapter or delivery epic"),
    LabelSpec("type:task", "1D76DB", "Executable work item"),
    LabelSpec("status:ready", "0E8A16", "Ready under the current release gate"),
    LabelSpec("status:closed", "6E7781", "Accepted or completed with linked evidence"),
    LabelSpec("status:blocked", "B60205", "Blocked by an explicit predecessor gate"),
    LabelSpec("status:triage", "FBCA04", "Defined but not released for implementation"),
    LabelSpec("priority:critical", "D73A4A", "Release-blocking critical work"),
    LabelSpec("priority:high", "E99695", "High-priority work in the released phase"),
    LabelSpec("priority:normal", "C5DEF5", "Normal planned work"),
    *(LabelSpec(f"phase:P{phase}", "0052CC", f"Delivery phase P{phase}") for phase in range(1, 8)),
    LabelSpec("phase:PK", "0B7285", "Orthogonal Platform Kernel engineering milestone"),
    LabelSpec("phase:meta", "6F42C1", "Cross-phase project governance"),
    LabelSpec("area:data", "BFDADC", "Point-in-time data foundation"),
    LabelSpec("area:backtest", "D4C5F9", "Backtest engine and accounting"),
    LabelSpec("area:labels", "FEF2C0", "Labels and feature contracts"),
    LabelSpec("area:experts", "F9D0C4", "Experts and aggregation"),
    LabelSpec("area:positions", "C2E0C6", "Position and execution lifecycle"),
    LabelSpec("area:events", "FDE2A7", "Event library and datasets"),
    LabelSpec("area:ml", "D8B4FE", "Research scoring and models"),
    LabelSpec("area:governance", "E4E669", "Validation and change governance"),
    LabelSpec("area:roadmap", "D4C5F9", "Delivery roadmap and gates"),
    LabelSpec("area:observability", "BFD4F2", "Monitoring and attribution"),
    LabelSpec("area:platform", "9ED9CC", "Platform identity, API, jobs, Web, and operations"),
    LabelSpec("area:docs", "0075CA", "Product and design documentation"),
)

PLATFORM_KERNEL_MILESTONE = "Platform Kernel Extraction"
WORKER_RUNTIME_MILESTONE = "PostgreSQL Worker Runtime"
EXPERT_STRATEGY_MILESTONE = "Expert Strategy Layer MVP"
EXPERT_STRATEGY_DESIGN_PATH = "docs/design/lld/04-experts-aggregator/expert-strategy-layer.md"
EXPERT_STRATEGY_IMPLEMENTATION_ROOT = "docs/design/lld/04-experts-aggregator/expert-strategy-layer"
EXPERT_STRATEGY_REVIEW_PATH = (
    f"{EXPERT_STRATEGY_IMPLEMENTATION_ROOT}/implementation-readiness-review.md"
)
EXPERT_STRATEGY_CHILD_IDS = (
    "P3-EXPERT-001",
    "P3-EXPERT-002",
    "P3-EXPERT-003",
    "P3-STRAT-002",
    "P3-ATTR-001",
    "P3-EXPERT-004",
    "P3-EXPERT-005",
)
EXPERT_STRATEGY_IDS = frozenset({"P3-EXPERT-000", *EXPERT_STRATEGY_CHILD_IDS})
EXPERT_STRATEGY_IMPLEMENTATION_SPECS = {
    "P3-EXPERT-000": IssueImplementationSpec(
        design_path=EXPERT_STRATEGY_DESIGN_PATH,
        approach=(
            "Coordinate the seven dependency-gated children as one bounded lifecycle loop; the "
            "Epic closes only from child acceptance evidence, not from issue or document creation."
        ),
        owned_paths=(
            EXPERT_STRATEGY_DESIGN_PATH,
            f"{EXPERT_STRATEGY_IMPLEMENTATION_ROOT}/README.md",
            EXPERT_STRATEGY_REVIEW_PATH,
        ),
        decision_gates=(
            "Preserve the target, strategy-card, and lifecycle decisions as explicit human gates.",
            "Keep arbitration, permanent-blind, P6, notification, and execution authority closed.",
        ),
        verification=(
            "All seven blocking children are accepted with linked immutable evidence.",
            "One lifecycle decision and compatible enabled/disabled Shadow disposition are "
            "recorded with zero sealed access.",
        ),
    ),
    "P3-EXPERT-001": IssueImplementationSpec(
        design_path=f"{EXPERT_STRATEGY_IMPLEMENTATION_ROOT}/evaluation-protocol-v2.md",
        approach=(
            "Add a domain-only v2 sidecar package with seven exact artifacts, canonical "
            "identities, "
            "graph validation, lifecycle/Shadow compatibility, and a hard byte-compatibility "
            "boundary around accepted v1 Runs."
        ),
        owned_paths=(
            "src/quanthus/expert_evaluation/{__init__,contracts,serialization,validation}.py",
            "tests/expert_evaluation_fixtures.py",
            "tests/unit/test_expert_evaluation_contracts.py",
            "tests/contract/test_expert_evaluation_compatibility.py",
            "tests/fixtures/golden/expert_evaluation_v2/",
        ),
        decision_gates=(
            "No new semantic human decision; target, expert, Worker, and UI choices remain out "
            "of scope.",
        ),
        verification=(
            "Golden round trips and typed negative cases cover every v2 artifact, including "
            "lifecycle/Shadow compatibility.",
            "Future settlement and policy remapping cannot affect observation identity; accepted "
            "v1 bytes remain unchanged.",
        ),
    ),
    "P3-EXPERT-002": IssueImplementationSpec(
        design_path=f"{EXPERT_STRATEGY_IMPLEMENTATION_ROOT}/worker-evaluation-pipeline.md",
        approach=(
            "Admit `quanthus.expert-evaluation.v1` through the existing PostgreSQL Worker, reuse "
            "static concurrency-one dispatch, accept a canonical non-empty evaluation-spec set, "
            "and publish immutable semantic Run artifacts plus one summary row."
        ),
        owned_paths=(
            "src/quanthus/platform/{jobs,worker,worker_handlers,models,persistence,migrations}.py",
            "src/quanthus/expert_evaluation/{runner,cli}.py",
            "tests/platform/test_expert_evaluation_worker.py",
        ),
        decision_gates=(
            "Do not add a queue, daemon, registry, cache, or opinion-row table without a measured "
            "trigger.",
        ),
        verification=(
            "Lease loss, cancellation, retry, stale fencing, partial publication, and two-Worker "
            "competition pass.",
            "Identical semantic inputs publish the same content Run and byte-identical canonical "
            "artifacts while operational attempt fields may differ; candidate replay remains "
            "compatible.",
        ),
    ),
    "P3-EXPERT-003": IssueImplementationSpec(
        design_path=f"{EXPERT_STRATEGY_IMPLEMENTATION_ROOT}/first-target-resolver-expert.md",
        approach=(
            "Reference the accepted P4 outcome card when semantics match, then implement one "
            "outcome-blind technical assessment expert, independent resolver, closed sample "
            "ledger, and development scorecard through the accepted Worker."
        ),
        owned_paths=(
            "config/expert-evaluations/{targets,experts,specs}/",
            "src/quanthus/expert_evaluation/{target_cards,resolver,scoring}.py",
            "src/quanthus/expert_evaluation/experts/trend_stability.py",
        ),
        decision_gates=(
            "A human freezes target, population, horizon, metric, baseline, slices, and stop "
            "conditions before result access.",
            "If P4-LABEL-001 cannot be reused, record an outcome-blind non-reuse decision before "
            "creating a card.",
        ),
        verification=(
            "PIT leakage, resolver independence, horizon edges, suspension/delisting, censoring, "
            "and ledger closure pass.",
            "The accepted Worker produces the development scorecard with coverage, abstention, "
            "dependence-aware uncertainty, and limitations.",
        ),
    ),
    "P3-STRAT-002": IssueImplementationSpec(
        design_path=f"{EXPERT_STRATEGY_IMPLEMENTATION_ROOT}/first-formal-topology.md",
        approach=(
            "Freeze and implement exactly two active strategy experts: the accepted assessment "
            "expert and one policy-only position-risk expert, composed through existing "
            "P2/aggregator ports and bound to one exact product candidate version."
        ),
        owned_paths=(
            "config/expert-strategies/",
            "src/quanthus/expert_strategy/{contracts,registry,position_risk,runner}.py",
            "src/quanthus/expert_strategy/attribution_adapter.py",
        ),
        decision_gates=(
            "A human freezes the complete strategy card, expected changes, resource budget, and "
            "rollback before results.",
            "The assessment evidence population must match the strategy applicability population.",
            "The card admits exactly two single fallbacks and their required joint fallback; no "
            "search is allowed.",
        ),
        verification=(
            "Two-expert topology closure, guardian separation, legal fallbacks, audit "
            "completeness, and deterministic sentinel replay pass.",
            "The frozen P3 fixture and all v1 Run digests remain unchanged.",
        ),
    ),
    "P3-ATTR-001": IssueImplementationSpec(
        design_path="docs/design/lld/10-monitoring-attribution/formal-strategy-attribution.md",
        approach=(
            "Bind the formal strategy to the accepted attribution planner/Pipeline, reproduce its "
            "baseline, and run both single fallbacks plus their required joint fallback through "
            "full P2 replay."
        ),
        owned_paths=(
            "sealed src/quanthus/expert_strategy/attribution_adapter.py (read-only input)",
            "src/quanthus/strategy_attribution_{planning,pipeline}.py only for a proven "
            "generic gap",
            "tests/unit/test_formal_expert_attribution_adapter.py",
            "tests/acceptance/test_formal_expert_attribution_pipeline.py",
        ),
        decision_gates=(
            "Persist the three outcome-blind questions and metric/materiality rules before result "
            "access.",
            "Explicitly resume the deferred gate after P3-STRAT-002 acceptance.",
        ),
        verification=(
            "Baseline reproduction and four complete P2 Runs pass deep evidence verification.",
            "Effects remain non-additive and the published release exposes interaction and "
            "simulator limitations.",
        ),
    ),
    "P3-EXPERT-004": IssueImplementationSpec(
        design_path=f"{EXPERT_STRATEGY_IMPLEMENTATION_ROOT}/candidate-detail-evidence.md",
        approach=(
            "Project trusted topology, scorecard, attribution, disagreement, limitation, and "
            "optional lifecycle artifacts through an exact product-candidate evidence package "
            "into the existing candidate detail response and page without adding mutations."
        ),
        owned_paths=(
            "src/quanthus/expert_evaluation/product_view.py",
            "config/product-expert-evidence.v1.json",
            "src/quanthus/platform/{product_query,api}.py",
            "product-web/src/views/CandidateDetail.vue",
        ),
        decision_gates=(
            "No UI decision gate: lifecycle decisions are immutable domain artifacts and existing "
            "approval axes stay unchanged.",
        ),
        verification=(
            "API digest resolution and invalid/partial/stale artifact paths fail closed without "
            "fallback mixing.",
            "Desktop/mobile E2E proves the section is read-only and cannot change governance or "
            "execution authority.",
        ),
    ),
    "P3-EXPERT-005": IssueImplementationSpec(
        design_path=f"{EXPERT_STRATEGY_IMPLEMENTATION_ROOT}/mvp-shadow-handoff.md",
        approach=(
            "Bind and independently reproduce the full development package, record one append-only "
            "human lifecycle decision, then either enable bounded background forward Shadow or "
            "prove the current version remains disabled."
        ),
        owned_paths=(
            "src/quanthus/expert_evaluation/{lifecycle,cli}.py",
            "config/product-expert-evidence.v1.json (decision-bound serving advance)",
            "config/expert-evaluations/shadow/",
            "tests/acceptance/test_expert_mvp_acceptance.py",
        ),
        decision_gates=(
            "A human selects keep, narrow, remap, merge, retire, or inconclusive from the complete "
            "evidence package.",
            "The decision records a compatible enabled/disabled Shadow disposition; replacement "
            "and retire decisions disable the current version.",
            "Forward Shadow has no notification, promotion, Order, Fill, or execution edge.",
        ),
        verification=(
            "Independent semantic rerun, lifecycle append, decided-page binding, enabled Shadow "
            "idempotency/recovery or disabled submission rejection, schedule rollback, cost, and "
            "zero sealed access pass.",
            "One acceptance record binds every artifact, limitation, decision, and runtime "
            "revision.",
        ),
    ),
}

P3_STRAT_003_MILESTONE = "P3-STRAT-003 | 多专家策略增量价值与近期历史验证"
P3_STRAT_003_DESIGN_PATH = "docs/work-items/p3-strat-003.md"
P3_STRAT_003_CHILD_IDS = (
    "P3-STRAT-003-A",
    "P3-STRAT-003-B",
    "P3-STRAT-003-C",
    "P3-STRAT-003-C-R1",
    "P3-STRAT-003-D",
    "P3-STRAT-003-E",
)
P3_STRAT_003_IDS = frozenset({"P3-STRAT-003", *P3_STRAT_003_CHILD_IDS})
P3_STRAT_003_IMPLEMENTATION_SPECS = {
    "P3-STRAT-003": IssueImplementationSpec(
        design_path=P3_STRAT_003_DESIGN_PATH,
        approach=(
            "Coordinate one bounded recent-history factorial study that compares the existing "
            "Trend and Position Risk effects with a non-empty deterministic candidate baseline "
            "while P6-v1 continues independently."
        ),
        owned_paths=(
            P3_STRAT_003_DESIGN_PATH,
            "config/research/p3-strat-003/",
            "docs/acceptance/p3-strat-003-*.md",
        ),
        decision_gates=(
            "A freezes result-independent data authority, variants, metrics, thresholds and "
            "resource limits before B/C or any result-bearing access.",
            "P6-v1 is parallel evidence and is not a predecessor or mutable input.",
        ),
        verification=(
            "All declared children close in dependency order with immutable evidence and no silent "
            "window roll-forward.",
            "Negative, harmful or inconclusive economics remain terminally reviewable outcomes.",
        ),
    ),
    "P3-STRAT-003-A": IssueImplementationSpec(
        design_path=P3_STRAT_003_DESIGN_PATH,
        approach=(
            "Freeze B/T/R/TR, the deterministic pass-through control, 2025 one-time validation "
            "authority, the fixed 2026 cutoff, headline/diagnostic comparisons, uncertainty, "
            "resource budget and stop conditions before recent-history strategy results are read."
        ),
        owned_paths=(
            "config/research/p3-strat-003/request.v1.json",
            P3_STRAT_003_DESIGN_PATH,
            "docs/acceptance/p3-strat-003-a-preregistration-20260920.md",
            "tests/contract/test_p3_strat_003_preregistration.py",
        ),
        decision_gates=(
            "The human owner authorizes 2025 only for this bounded major-strategy validation; "
            "first D result access consumes its untouched status for successor research.",
            "General 2022-2024 result access stays closed; warm-up is input-only and "
            "exactly bounded.",
            "No 2025/2026 strategy result is read before A acceptance.",
        ),
        verification=(
            "Request digest, frozen source bindings, variants, data roles, metrics, thresholds, "
            "resource limits and P6 non-mutation are contract-tested.",
            "Planning sync recognizes the parent/children without overwriting the "
            "detailed v2 issue bodies.",
        ),
    ),
    "P3-STRAT-003-B": IssueImplementationSpec(
        design_path=P3_STRAT_003_DESIGN_PATH,
        approach=(
            "Resolve the exact warm-up dates and fixed 2025-2026 input closure, verify PIT, "
            "coverage, corporate-action/tradability requirements and immutable run isolation, "
            "then publish one input manifest without producing strategy economics."
        ),
        owned_paths=(
            "config/research/p3-strat-003/input-manifest.v1.json",
            "docs/acceptance/p3-strat-003-b-input-qualification-*.md",
        ),
        decision_gates=(
            "Input gaps block or are explicitly ledgered; the window may not shrink "
            "based on results.",
            "No strategy/economic result production is authorized in B.",
        ),
        verification=(
            "Every input identity, cutoff, exception, missing count, warm-up row and "
            "output root is auditable.",
            "P6 Worker, ledger, budget and mutable pointers remain untouched.",
        ),
    ),
    "P3-STRAT-003-C": IssueImplementationSpec(
        design_path=P3_STRAT_003_DESIGN_PATH,
        approach=(
            "Add the deterministic pass-through research control and four-variant composite runner "
            "as an additive path over the accepted expert/aggregator/P2 interfaces, using only "
            "synthetic or already-consumed golden evidence before D."
        ),
        owned_paths=(
            "src/quanthus/expert_strategy/",
            "config/research/p3-strat-003/",
            "tests/unit/",
            "tests/contract/",
        ),
        decision_gates=(
            "No new alpha expert, threshold search, alternate matcher or weakened "
            "guardian is admitted.",
            "TR must preserve the accepted v1 strategy semantics on applicable "
            "consumed-golden scope.",
        ),
        verification=(
            "B/T/R/TR differ only on the two declared factors and all share one "
            "data/capital/P2 closure.",
            "One composite Job builds expensive deterministic prerequisites once and "
            "reports stage telemetry.",
        ),
    ),
    "P3-STRAT-003-C-R1": IssueImplementationSpec(
        design_path=P3_STRAT_003_DESIGN_PATH,
        approach=(
            "Decouple the recent-history population from the legacy P4 Event+Outcome-Label outer "
            "Run by binding B's outcome-blind candidate manifest, while preserving the accepted "
            "P3-ATTR-001 parent-Run path unchanged."
        ),
        owned_paths=(
            "src/quanthus/expert_strategy/formal_p2_replay.py",
            "src/quanthus/expert_strategy/p3_strat_003.py",
            "src/quanthus/expert_strategy/p3_strat_003_cli.py",
            "config/research/p3-strat-003/runtime.v1.1.json",
            "tests/unit/test_p3_strat_003_candidate_input.py",
        ),
        decision_gates=(
            "Exactly one population source is allowed: legacy Event Run for compatibility or "
            "B's candidate input manifest for recent-history D.",
            "No 2025/2026 outcome label may be read before D.",
        ),
        verification=(
            "Candidate input checksum, scope, industry mapping and replay-bundle identity fail closed.",
            "Existing formal attribution and C runner regressions remain green with no P6 mutation.",
        ),
    ),
    "P3-STRAT-003-D": IssueImplementationSpec(
        design_path=P3_STRAT_003_DESIGN_PATH,
        approach=(
            "Bind exact A request + B input/candidate manifest + C-R1 code revision, then run one primary "
            "four-variant composite batch and one independent cold reproduction with no "
            "cross-batch intermediate reuse."
        ),
        owned_paths=(
            "immutable P3-STRAT-003 run roots",
            "docs/acceptance/p3-strat-003-d-recent-history-*.md",
        ),
        decision_gates=(
            "D is the first result-bearing gate and may not alter the frozen request "
            "after any result is visible.",
            "Unmatured targets and open terminal positions are reported honestly "
            "rather than waiting for future data.",
        ),
        verification=(
            "Complete decisions/orders/fills/costs/equity/ledger and preregistered "
            "comparisons are published.",
            "The second batch independently rebuilds semantic artifacts and matches "
            "the declared reproduction contract.",
        ),
    ),
    "P3-STRAT-003-E": IssueImplementationSpec(
        design_path=P3_STRAT_003_DESIGN_PATH,
        approach=(
            "Read the frozen D evidence without rerunning or tuning, classify return/risk "
            "increment, factor diagnostics and regime slices, and select at most one bounded "
            "successor question."
        ),
        owned_paths=(
            "docs/reviews/p3-strat-003-*.md",
            "docs/acceptance/p3-strat-003-e-*.md",
        ),
        decision_gates=(
            "Support, harm, no support and inconclusive are all valid terminal research "
            "dispositions.",
            "No review result automatically adds an expert, changes P6, promotes to P7 "
            "or opens live authority.",
        ),
        verification=(
            "Every conclusion links to the frozen request and immutable D artifacts.",
            "Review provenance distinguishes self-review, simulation, cold reproduction "
            "and genuinely independent review.",
        ),
    ),
}

P6_DESIGN_PATH = "docs/design/lld/08-validation-governance/p6-forward-validation.md"
P6_CHILD_IDS = (
    "P6-GOV-001",
    "P6-DATA-001",
    "P6-SCORE-001",
    "P6-GOV-002",
    "P6-PAPER-001",
    "P6-WEB-001",
    "P6-ACTIVATE-001",
    "P6-REVIEW-001",
)
P6_IDS = frozenset({"P6-000", *P6_CHILD_IDS})
P6_IMPLEMENTATION_SPECS = {
    "P6-000": IssueImplementationSpec(
        design_path=P6_DESIGN_PATH,
        approach=(
            "Coordinate one bounded prospective-validation program. Keep engineering readiness, "
            "activation authority, evidence maturity, and human review as separate gates; close "
            "the Epic only when every child is accepted."
        ),
        owned_paths=(P6_DESIGN_PATH, "docs/work-items/index.md"),
        decision_gates=(
            "P5 remains optional and is not a predecessor.",
            "No child opens notification, broker, or live-capital authority.",
        ),
        verification=(
            "All eight dependency-gated children have linked acceptance evidence.",
            "The final review binds a genuinely prospective sequence and closed paper ledger.",
        ),
    ),
    "P6-GOV-001": IssueImplementationSpec(
        design_path=P6_DESIGN_PATH,
        approach=(
            "Publish one immutable entry proposal binding the frozen strategy/evaluation/data "
            "closure, finite observation budget, expected behavior, controls, stop rules, and "
            "rollback before result-bearing P6 reads."
        ),
        owned_paths=(
            "src/quanthus/forward_validation/{contracts,serialization,validation}.py",
            "config/forward-validation/entry/",
            "tests/unit/test_forward_validation_entry.py",
        ),
        decision_gates=(
            "A human selects the exact candidate, budget unit/value, trigger independence rule, "
            "activation-boundary rule, and longest maturity tail.",
            "The proposal explicitly excludes P5, sealed-partition access, notification, broker, "
            "and live capital.",
        ),
        verification=(
            "Canonical identity, invalid budget, policy drift, and incompatible lifecycle cases "
            "fail closed.",
            "The proposal is published before any result-bearing P6 data access.",
        ),
    ),
    "P6-DATA-001": IssueImplementationSpec(
        design_path=P6_DESIGN_PATH,
        approach=(
            "Enforce the first-post-activation prospective boundary, deep DWD effective-view "
            "identity, knowledge cutoff, append-only access ledger, and versioned late-correction "
            "disposition without rewriting observations."
        ),
        owned_paths=(
            "src/quanthus/forward_validation/{data_boundary,access_ledger}.py",
            "config/forward-validation/data/",
            "tests/unit/test_forward_validation_data_boundary.py",
        ),
        decision_gates=(
            "Pre-activation 2026 history is engineering rehearsal only and never consumes the "
            "official budget.",
            "Arbitration and permanent blind remain sealed until separately authorized review.",
        ),
        verification=(
            "Boundary, cutoff, late/missing release, correction, retry, and sealed-access negative "
            "cases pass.",
            "Every admitted cycle binds one verified DWD identity and auditable access record.",
        ),
    ),
    "P6-SCORE-001": IssueImplementationSpec(
        design_path=P6_DESIGN_PATH,
        approach=(
            "Freeze role-specific expert, veto, constraint, aggregator, complete-strategy, and "
            "paper-operation scorecards plus loss attribution before official observations."
        ),
        owned_paths=(
            "src/quanthus/forward_validation/{scorecards,loss_attribution}.py",
            "config/forward-validation/scorecards/",
            "tests/unit/test_forward_validation_scorecards.py",
        ),
        decision_gates=(
            "A human freezes primary metrics, guardrails, slices, and material-loss threshold.",
            "No universal expert accuracy score or result-driven metric edit is admitted.",
        ),
        verification=(
            "Scorecards distinguish roles, abstention, dependence-aware uncertainty, and coverage.",
            "Unclassified material loss blocks review readiness.",
        ),
    ),
    "P6-GOV-002": IssueImplementationSpec(
        design_path=P6_DESIGN_PATH,
        approach=(
            "Admit one `quanthus.p6-forward-cycle.v1` composite Job to the existing PostgreSQL "
            "Replay Worker and `queue=replay`; build shared DWD prerequisites once, then append "
            "Shadow, settlement, scorecard, and bounded Run artifacts."
        ),
        owned_paths=(
            "src/quanthus/platform/{jobs,worker,worker_handlers}.py",
            "src/quanthus/forward_validation/{runner,cli}.py",
            "tests/platform/test_forward_validation_worker.py",
        ),
        decision_gates=(
            "Do not add Redis, Celery, a Worker registry, a new queue, or a new transport without "
            "a measured trigger.",
            "Activation remains disabled and is not implied by successful implementation.",
        ),
        verification=(
            "Idempotency, lease loss, retry, cancellation, stale fencing, partial publication, "
            "resource bounds, and rollback rehearsal pass.",
            "API/Web processes perform no DWD analysis and one Job reuses one prerequisite build.",
        ),
    ),
    "P6-PAPER-001": IssueImplementationSpec(
        design_path=P6_DESIGN_PATH,
        approach=(
            "Consume the sealed aggregate decision through P2's accepted Signal-to-Fill contracts "
            "in an isolated paper authority, with balanced cash/position state and non-compounding "
            "fixed-capital-unit accounting."
        ),
        owned_paths=(
            "src/quanthus/forward_validation/{paper_ledger,paper_projection}.py",
            "tests/unit/test_forward_validation_paper_ledger.py",
            "tests/acceptance/test_forward_validation_paper_round_trip.py",
        ),
        decision_gates=(
            "Paper ids and authority cannot be promoted to actual execution records.",
            "The initial profile is exactly `close-to-next-open-v1`; two-window behavior remains "
            "out of scope.",
        ),
        verification=(
            "Pending intents, fills, costs, rejections, carry, positions, and cash reconcile.",
            "The paper lane matches honest P2 replay under identical frozen inputs and "
            "assumptions.",
        ),
    ),
    "P6-WEB-001": IssueImplementationSpec(
        design_path=P6_DESIGN_PATH,
        approach=(
            "Add one read-only Forward Validation section to candidate detail, projecting bounded "
            "PostgreSQL references to admission/evidence/Job/review state, scorecards, and paper "
            "ledger without creating another dashboard product."
        ),
        owned_paths=(
            "src/quanthus/forward_validation/product_view.py",
            "src/quanthus/platform/{product_query,api}.py",
            "product-web/src/views/CandidateDetail.vue",
            "tests/e2e/forward-validation.spec.ts",
        ),
        decision_gates=(
            "The page cannot activate, tune, promote, reveal sealed data, or run DWD analysis.",
        ),
        verification=(
            "Invalid, partial, stale, and mismatched artifact references fail closed.",
            "Desktop/mobile acceptance proves complete read-only evidence and no authority leak.",
        ),
    ),
    "P6-ACTIVATE-001": IssueImplementationSpec(
        design_path=P6_DESIGN_PATH,
        approach=(
            "After every engineering predecessor passes, publish a signed enabled binding, run "
            "the first official post-activation DWD cycle, and independently reproduce its sealed "
            "outputs."
        ),
        owned_paths=(
            "config/forward-validation/activation/",
            "docs/acceptance/p6-forward-activation-*.md",
            "tests/acceptance/test_forward_validation_activation.py",
        ),
        decision_gates=(
            "A human authorizes the exact deployment, proposal digest, activation boundary, and "
            "rollback after a clean disabled rehearsal.",
            "Historical rehearsal cannot satisfy this prospective gate.",
        ),
        verification=(
            "The first official cycle is post-activation, byte-verifiable, and independently "
            "reproduced.",
            "Pause/disable rollback prevents new admission without deleting evidence.",
        ),
    ),
    "P6-REVIEW-001": IssueImplementationSpec(
        design_path=P6_DESIGN_PATH,
        approach=(
            "Wait for the declared real-calendar budget and maturity tail, close all eligible "
            "settlements, reconcile the paper account, and record one append-only human review "
            "decision with any separately authorized arbitration reveal."
        ),
        owned_paths=(
            "src/quanthus/forward_validation/review.py",
            "docs/acceptance/p6-forward-review-*.md",
            "tests/acceptance/test_forward_validation_review.py",
        ),
        decision_gates=(
            "A human chooses continue, narrow, remap, retire, or inconclusive.",
            "Arbitration reveal is separate and permanent blind remains sealed; no result "
            "automatically authorizes P7.",
        ),
        verification=(
            "Budget, maturity, ledger closure, incident attribution, scorecards, and paper "
            "reconciliation are complete.",
            "The signed review preserves negative/inconclusive outcomes and zero live authority.",
        ),
    ),
}
IMPLEMENTATION_SPECS = {
    **EXPERT_STRATEGY_IMPLEMENTATION_SPECS,
    **P3_STRAT_003_IMPLEMENTATION_SPECS,
    **P6_IMPLEMENTATION_SPECS,
}
PROGRAM_EPIC_IDS = frozenset({
    "PK-WORKER-000", "P3-EXPERT-000", "P3-STRAT-003", "P3-STRUCT-004", "P3-DICT-002",
    "P6-000", "P3-STAGE-001",
})

MILESTONES = (
    "P1 Data Foundation",
    "P2 Backtest Engine",
    "P3 Rule Vertical Slice",
    "P4 Event Library",
    "P5 Research Scoring",
    "P6 Simulation Governance",
    "P7 Live Validation",
    PLATFORM_KERNEL_MILESTONE,
    WORKER_RUNTIME_MILESTONE,
    EXPERT_STRATEGY_MILESTONE,
    P3_STRAT_003_MILESTONE,
)

CHAPTERS = (
    ChapterSpec(
        "01", "docs/design/lld/01-data-foundation.md", "P1", "data", MILESTONES[0], "ready"
    ),
    ChapterSpec(
        "02", "docs/design/lld/02-backtest-engine.md", "P2", "backtest", MILESTONES[1], "ready"
    ),
    ChapterSpec(
        "03", "docs/design/lld/03-label-feature-system.md", "P3", "labels", MILESTONES[2], "closed"
    ),
    ChapterSpec(
        "04", "docs/design/lld/04-experts-aggregator.md", "P3", "experts", MILESTONES[2], "closed"
    ),
    ChapterSpec(
        "05",
        "docs/design/lld/05-position-execution.md",
        "P3",
        "positions",
        MILESTONES[2],
        "closed",
    ),
    ChapterSpec(
        "06", "docs/design/lld/06-event-library.md", "P4", "events", MILESTONES[3], "closed"
    ),
    ChapterSpec(
        "07", "docs/design/lld/07-research-scoring.md", "P5", "ml", MILESTONES[4], "blocked"
    ),
    ChapterSpec(
        "08",
        "docs/design/lld/08-validation-governance.md",
        "P6",
        "governance",
        MILESTONES[5],
        "ready",
    ),
    ChapterSpec("09", "docs/design/lld/09-roadmap.md", "meta", "roadmap", MILESTONES[0], "ready"),
    ChapterSpec(
        "10",
        "docs/design/lld/10-monitoring-attribution.md",
        "P2",
        "observability",
        MILESTONES[1],
        "ready",
    ),
    ChapterSpec(
        "11",
        "docs/design/lld/11-platform-kernel.md",
        "PK",
        "platform",
        PLATFORM_KERNEL_MILESTONE,
        "closed",
    ),
)

PHASE_GATES = {
    "meta": "Active project governance; changes must preserve document and issue traceability.",
    "P1": (
        "Conditionally accepted under p1-c1-conditional-20260725-v1; 4,173 C1 factor "
        "visibility exceptions remain fail-closed."
    ),
    "P2": (
        "Conditionally accepted for close-to-next-open research; target two-window activation "
        "remains blocked by config/p2-gates.v1.json."
    ),
    "P3": (
        "Accepted as a rule-only historical capability slice under config/p3-gates.v1.json; "
        "the frozen fixture is not a strategy lineage."
    ),
    "P4": (
        "Accepted for the development Event Library, outcome corpus, and daily comparison report "
        "under frozen P4 authorities."
    ),
    "P5": "Optional research is deferred and is not a predecessor of P6.",
    "P6": (
        "Prospective validation implementation is open at P6-GOV-001. Later work remains "
        "dependency-gated, and full acceptance waits for real-calendar evidence maturity."
    ),
    "P7": "Blocked until frozen simulation supports an explicit live/no-live decision.",
    "PK": (
        "Platform Kernel is orthogonal to P1-P7. PK1-PK4, the ADR 0010 PostgreSQL Worker "
        "Runtime, and PK6 cutover/recovery are accepted; first-cutover PK5 is not applicable "
        "under config/platform-kernel-gates.v1.json."
    ),
}

CRITICAL_IDS = {"C1", "C2", "S1"}
HIGH_IDS = {
    "P1-DATA-001",
    "P1-SNAPSHOT-001",
    "P1-ACCEPT-001",
    "P2-ENG-001",
    "P2-MATCH-001",
    "P2-LEDGER-001",
    "P2-ACCEPT-001",
    "P3-EXPERT-000",
    "P3-EXPERT-001",
    "P3-STRAT-002",
    "P3-STRAT-003",
    "P3-STRAT-003-A",
    "P6-000",
    "P6-GOV-001",
}


def parse_table(lines: list[str], start: int) -> list[dict[str, str]]:
    header = [cell.strip() for cell in lines[start].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells, strict=True)))
    return rows


def id_tables(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if line.startswith("| ID |"):
            rows.extend(parse_table(lines, index))
    return rows


def section(path: Path, name: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start: int | None = None
    level = 0
    for index, line in enumerate(lines):
        if re.match(rf"^##+ (?:\d+\. )?{re.escape(name)}$", line, re.IGNORECASE):
            start = index + 1
            level = len(line) - len(line.lstrip("#"))
            break
    if start is None:
        return ""
    selected: list[str] = []
    for line in lines[start:]:
        if line.startswith("#"):
            next_level = len(line) - len(line.lstrip("#"))
            if next_level <= level:
                break
        selected.append(line)
    return "\n".join(selected).strip()


def document_title(path: Path) -> str:
    first = path.read_text(encoding="utf-8").splitlines()[0]
    return re.sub(r"^# LLD \d+:\s*", "", first).strip()


def first_paragraph(content: str) -> str:
    paragraphs = [part.strip().replace("\n", " ") for part in content.split("\n\n")]
    return next((part for part in paragraphs if part and not part.startswith("|")), "")


def chapter_rows(
    root: Path,
) -> tuple[dict[str, tuple[ChapterSpec, dict[str, str]]], dict[str, list[dict[str, str]]]]:
    owners: dict[str, tuple[ChapterSpec, dict[str, str]]] = {}
    by_chapter: dict[str, list[dict[str, str]]] = {}
    for chapter in CHAPTERS:
        rows = id_tables(root / chapter.path)
        by_chapter[chapter.number] = rows
        for row in rows:
            stable_id = row["ID"]
            if stable_id in owners:
                previous = owners[stable_id][0]
                raise ValueError(
                    f"{stable_id} is owned by both LLD-{previous.number} and LLD-{chapter.number}"
                )
            owners[stable_id] = (chapter, row)
    return owners, by_chapter


def index_rows(root: Path) -> list[dict[str, str]]:
    path = root / "docs/work-items/index.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    wanted_sections = {
        "Meta": "meta",
        "P1 data foundation": "P1",
        "P2 backtest and observability": "P2",
        "P3 rule-only vertical slice": "P3",
        "P4-P7": "explicit",
        "Platform Kernel": "PK",
    }
    phase: str | None = None
    rows: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if line.startswith("## "):
            phase = wanted_sections.get(line[3:])
        if phase is not None and line.startswith("| ID |"):
            for row in parse_table(lines, index):
                if phase != "explicit":
                    row["Phase"] = phase
                rows.append(row)
            phase = None
    return rows


def work_items(root: Path) -> list[WorkItem]:
    owners, _ = chapter_rows(root)
    items: list[WorkItem] = []
    for row in index_rows(root):
        stable_id = row["ID"]
        if stable_id not in owners:
            raise ValueError(f"{stable_id} has no governing LLD work-item row")
        chapter, design_row = owners[stable_id]
        phase = row["Phase"]
        dependencies = design_row.get("Dependency") or row.get("Depends on") or "None"
        evidence = design_row.get("Acceptance evidence", "Linked implementation evidence")
        state = row.get("State", "")
        disposition = row.get("Disposition", "")
        if phase == "meta":
            status = "ready" if state == "active" else "triage"
        elif disposition.startswith(("accepted", "closed")):
            status = "closed"
        elif disposition.startswith(("blocked", "deferred")):
            status = "blocked"
        elif disposition in {"open", "ready", "open epic"}:
            status = "ready"
        elif phase == "PK":
            status = "blocked"
        elif phase == "P4":
            status = "closed"
        else:
            status = "ready" if phase in {"P1", "P2", "P3"} else "blocked"
        if stable_id in CRITICAL_IDS:
            priority = "critical"
        elif stable_id in HIGH_IDS:
            priority = "high"
        else:
            priority = "normal"
        if phase == "meta":
            milestone = None
        elif stable_id in P3_STRAT_003_IDS:
            milestone = P3_STRAT_003_MILESTONE
        elif stable_id in EXPERT_STRATEGY_IDS:
            milestone = EXPERT_STRATEGY_MILESTONE
        elif phase == "PK":
            milestone = (
                WORKER_RUNTIME_MILESTONE
                if stable_id.startswith("PK-WORKER-")
                else PLATFORM_KERNEL_MILESTONE
            )
        else:
            milestone = MILESTONES[int(phase[1:]) - 1]
        items.append(
            WorkItem(
                stable_id=stable_id,
                title=row["Title"],
                dependencies=dependencies,
                evidence=evidence,
                phase=phase,
                area=chapter.area,
                status=status,
                priority=priority,
                milestone=milestone,
                design_path=(
                    IMPLEMENTATION_SPECS[stable_id].design_path
                    if stable_id in IMPLEMENTATION_SPECS
                    else chapter.path
                ),
                issue_type="epic" if stable_id in PROGRAM_EPIC_IDS else "task",
            )
        )
    return items


def repo_url(repository: str, path: str) -> str:
    return f"https://github.com/{repository}/blob/main/{path}"


def task_body(item: WorkItem, repository: str) -> str:
    blocked_note = ""
    worker_decision = ""
    expert_design = ""
    authority_note = ""
    implementation_contract = ""
    product_requirements = (
        f"- [Product requirements]({repo_url(repository, 'docs/product/prd.md')})\n"
    )
    if item.stable_id.startswith("PK-WORKER-"):
        worker_decision = (
            "- [Worker Runtime decision]("
            f"{repo_url(repository, 'docs/architecture/adr-0010-postgresql-worker-runtime.md')}"
            ")\n"
        )
    if item.stable_id in EXPERT_STRATEGY_IDS:
        if item.design_path != EXPERT_STRATEGY_DESIGN_PATH:
            expert_design = (
                "- [Expert Strategy Layer MVP]("
                f"{repo_url(repository, EXPERT_STRATEGY_DESIGN_PATH)})\n"
            )
        authority_note = (
            "\n\nThis is a bounded post-P3 development increment. It does not reopen P3 "
            "acceptance or authorize arbitration, permanent-blind, P6 promotion, notification, "
            "or execution access."
        )
    elif item.stable_id in P6_IDS:
        authority_note = (
            "\n\nThis is a bounded prospective-validation increment. It does not depend on P5 "
            "or authorize notification, broker connection, actual fills, or live capital."
        )
    if item.stable_id in IMPLEMENTATION_SPECS:
        implementation = IMPLEMENTATION_SPECS[item.stable_id]
        owned_paths = "\n".join(f"- `{path}`" for path in implementation.owned_paths)
        decision_gates = "\n".join(f"- {gate}" for gate in implementation.decision_gates)
        verification = "\n".join(f"- {check}" for check in implementation.verification)
        implementation_contract = f"""

## Implementation contract

{implementation.approach}

### Owned paths

{owned_paths}

## Decision gates

{decision_gates}

## Verification

{verification}
""".rstrip()
    governing_links = (
        f"- [Detailed design]({repo_url(repository, item.design_path)})\n"
        f"{expert_design}{worker_decision}{product_requirements}"
        f"- [High-level design]({repo_url(repository, 'docs/design/hld.md')})"
    )
    return f"""<!-- quanthus-planning:v1 id={item.stable_id} kind={item.issue_type} -->
## Objective

{item.title}.

## Governing design

{governing_links}

## Phase and gate

{PHASE_GATES[item.phase]}{blocked_note}{authority_note}

## Dependencies

{item.dependencies}{implementation_contract}

## Acceptance evidence

{item.evidence}

## Traceability

- Stable work-item id: `{item.stable_id}`
- [Work-item index]({repo_url(repository, "docs/work-items/index.md")})
- Policy source: [`config/p0-policy.v1.json`]({repo_url(repository, "config/p0-policy.v1.json")})

## Completion checklist

- [ ] Implementation and configuration are versioned.
- [ ] Tests cover the governing contract and failure behavior.
- [ ] Acceptance evidence is linked from this issue.
- [ ] Documentation and traceability are updated.
- [ ] `make check` passes.
"""


def expert_strategy_epic_body(item: WorkItem, repository: str, task_urls: dict[str, str]) -> str:
    checklist = "\n".join(
        f"- [ ] [{stable_id}]({task_urls[stable_id]})" for stable_id in EXPERT_STRATEGY_CHILD_IDS
    )
    return task_body(item, repository).replace(
        "## Acceptance evidence\n",
        f"## Blocking work items\n\n{checklist}\n\n## Acceptance evidence\n",
        1,
    )


def p3_strat_003_epic_body(item: WorkItem, repository: str, task_urls: dict[str, str]) -> str:
    checklist = "\n".join(
        f"- [ ] [{stable_id}]({task_urls[stable_id]})" for stable_id in P3_STRAT_003_CHILD_IDS
    )
    return task_body(item, repository).replace(
        "## Acceptance evidence\n",
        f"## Blocking work items\n\n{checklist}\n\n## Acceptance evidence\n",
        1,
    )


def p6_epic_body(item: WorkItem, repository: str, task_urls: dict[str, str]) -> str:
    checklist = "\n".join(
        f"- [ ] [{stable_id}]({task_urls[stable_id]})" for stable_id in P6_CHILD_IDS
    )
    return task_body(item, repository).replace(
        "## Acceptance evidence\n",
        f"## Blocking work items\n\n{checklist}\n\n## Acceptance evidence\n",
        1,
    )


def epic_body(
    root: Path,
    chapter: ChapterSpec,
    rows: list[dict[str, str]],
    task_urls: dict[str, str],
    repository: str,
) -> str:
    path = root / chapter.path
    goal = section(path, "Goal") or section(path, "Role") or section(path, "Build order")
    acceptance = section(path, "Acceptance")
    if not acceptance:
        acceptance = (
            "- [ ] The chapter's milestones and release mechanics are represented by linked issues."
        )
    checklist = []
    for row in rows:
        stable_id = row["ID"]
        title = row.get("Work", "")
        if stable_id in task_urls:
            checklist.append(f"- [ ] [{stable_id}]({task_urls[stable_id]}) - {title}")
        else:
            checklist.append(f"- [ ] `{stable_id}` - {title}")
    checklist_text = "\n".join(checklist) or "- [ ] No work items have been declared."
    return f"""<!-- quanthus-planning:v1 id=LLD-{chapter.number} kind=epic -->
## Objective

{first_paragraph(goal)}

## Governing documents

- [LLD {chapter.number}: {document_title(path)}]({repo_url(repository, chapter.path)})
- [Product requirements]({repo_url(repository, "docs/product/prd.md")})
- [High-level design]({repo_url(repository, "docs/design/hld.md")})

## Phase and gate

{PHASE_GATES[chapter.phase]}

## Work items

{checklist_text}

## Acceptance

{acceptance}
"""


class GitHub:
    def __init__(self, repository: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("repository must use OWNER/NAME form")
        self.repository = repository

    def api(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        command = ["gh", "api", "--method", method, endpoint]
        input_text = None
        if payload is not None:
            command.extend(["--input", "-"])
            input_text = json.dumps(payload)
        result = subprocess.run(
            command,
            check=False,
            input=input_text,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "unknown GitHub CLI error"
            raise RuntimeError(f"GitHub API {method} {endpoint} failed: {detail}")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def pages(self, endpoint: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        separator = "&" if "?" in endpoint else "?"
        for page in range(1, 100):
            batch = self.api("GET", f"{endpoint}{separator}per_page=100&page={page}")
            results.extend(batch)
            if len(batch) < 100:
                return results
        raise RuntimeError(f"pagination limit exceeded for {endpoint}")

    def labels(self) -> list[dict[str, Any]]:
        return self.pages(f"/repos/{self.repository}/labels")

    def milestones(self) -> list[dict[str, Any]]:
        return self.pages(f"/repos/{self.repository}/milestones?state=all")

    def issues(self) -> list[dict[str, Any]]:
        issues = self.pages(f"/repos/{self.repository}/issues?state=all")
        if issues:
            listed_numbers = {int(issue["number"]) for issue in issues}
            missing_numbers = sorted(set(range(1, max(listed_numbers) + 1)) - listed_numbers)
            if len(missing_numbers) > 100:
                raise RuntimeError(
                    "too many issue-number gaps to safely reconcile GitHub list consistency"
                )
            for number in missing_numbers:
                try:
                    issues.append(self.api("GET", f"/repos/{self.repository}/issues/{number}"))
                except RuntimeError as error:
                    if "HTTP 404" not in str(error):
                        raise
        return [issue for issue in issues if "pull_request" not in issue]

    def upsert_label(self, spec: LabelSpec, current: dict[str, dict[str, Any]]) -> str:
        existing = current.get(spec.name.casefold())
        payload = {"new_name": spec.name, "color": spec.color, "description": spec.description}
        if existing is None:
            created = self.api(
                "POST",
                f"/repos/{self.repository}/labels",
                {"name": spec.name, "color": spec.color, "description": spec.description},
            )
            current[spec.name.casefold()] = created
            return "created"
        if (
            existing.get("color", "").casefold() == spec.color.casefold()
            and existing.get("description", "") == spec.description
        ):
            return "reused"
        updated = self.api(
            "PATCH",
            f"/repos/{self.repository}/labels/{quote(existing['name'], safe='')}",
            payload,
        )
        current[spec.name.casefold()] = updated
        return "updated"

    def upsert_milestone(self, title: str, current: dict[str, dict[str, Any]]) -> tuple[int, str]:
        existing = current.get(title)
        if existing is not None:
            return int(existing["number"]), "reused"
        created = self.api(
            "POST",
            f"/repos/{self.repository}/milestones",
            {"title": title, "description": f"Quanthus {title} release gate."},
        )
        current[title] = created
        return int(created["number"]), "created"

    def upsert_issue(
        self,
        existing: dict[str, Any] | None,
        title: str,
        body: str,
        labels: list[str],
        milestone: int | None,
    ) -> tuple[dict[str, Any], str]:
        existing_labels = [] if existing is None else [item["name"] for item in existing["labels"]]
        unmanaged = [
            name for name in existing_labels if not name.startswith(MANAGED_LABEL_PREFIXES)
        ]
        desired_labels = sorted(set(labels + unmanaged))
        should_close = "status:closed" in desired_labels
        existing_body = None if existing is None else existing.get("body")
        desired_body = body
        if (
            isinstance(existing_body, str)
            and "<!-- quanthus-planning:v2 " in existing_body
            and "<!-- quanthus-planning:v1 " in body
        ):
            desired_body = existing_body
        payload: dict[str, Any] = {
            "title": title,
            "body": desired_body,
            "labels": desired_labels,
            "milestone": milestone,
        }
        if existing is None:
            created = self.api("POST", f"/repos/{self.repository}/issues", payload)
            if should_close:
                created = self.api(
                    "PATCH",
                    f"/repos/{self.repository}/issues/{created['number']}",
                    {"state": "closed"},
                )
            return created, "created"
        current_milestone = existing.get("milestone")
        current_milestone_number = (
            None if current_milestone is None else current_milestone["number"]
        )
        unchanged = (
            existing["title"] == title
            and existing_body == desired_body
            and sorted(existing_labels) == desired_labels
            and current_milestone_number == milestone
            and (not should_close or existing.get("state", "").casefold() == "closed")
        )
        if unchanged:
            return existing, "reused"
        if should_close:
            payload["state"] = "closed"
        endpoint = f"/repos/{self.repository}/issues/{existing['number']}"
        return self.api("PATCH", endpoint, payload), "updated"


def issue_by_stable_id(
    issues: Iterable[dict[str, Any]], targets: set[str]
) -> dict[str, dict[str, Any]]:
    found: dict[str, list[dict[str, Any]]] = {stable_id: [] for stable_id in targets}
    for issue in issues:
        match = re.match(r"^\[([^]]+)]", issue["title"])
        if match and match.group(1) in found:
            found[match.group(1)].append(issue)
    duplicates = {key: value for key, value in found.items() if len(value) > 1}
    if duplicates:
        summary = ", ".join(
            f"{key}: {[item['number'] for item in value]}" for key, value in duplicates.items()
        )
        raise RuntimeError(f"duplicate stable ids detected: {summary}")
    return {key: values[0] for key, values in found.items() if values}


def synchronize(root: Path, repository: str) -> dict[str, Any]:
    items = work_items(root)
    _, rows_by_chapter = chapter_rows(root)
    target_ids = {item.stable_id for item in items} | {f"LLD-{item.number}" for item in CHAPTERS}
    closed_ids = {item.stable_id for item in items if item.status == "closed"} | {
        f"LLD-{chapter.number}" for chapter in CHAPTERS if chapter.status == "closed"
    }
    github = GitHub(repository)

    existing_issues = github.issues()
    issues_by_id = issue_by_stable_id(existing_issues, target_ids)

    label_state = {item["name"].casefold(): item for item in github.labels()}
    label_results = {"created": 0, "updated": 0, "reused": 0}
    for spec in LABELS:
        label_results[github.upsert_label(spec, label_state)] += 1

    milestone_state = {item["title"]: item for item in github.milestones()}
    milestone_numbers: dict[str, int] = {}
    milestone_results = {"created": 0, "reused": 0}
    for title in MILESTONES:
        number, result = github.upsert_milestone(title, milestone_state)
        milestone_numbers[title] = number
        milestone_results[result] += 1

    task_results = {"created": 0, "updated": 0, "reused": 0}
    task_urls: dict[str, str] = {}
    deferred_epics = {"P3-EXPERT-000", "P3-STRAT-003", "P6-000"}
    ordered_items = [item for item in items if item.stable_id not in deferred_epics]
    ordered_items.extend(item for item in items if item.stable_id in deferred_epics)
    for item in ordered_items:
        labels = [
            f"type:{item.issue_type}",
            f"status:{item.status}",
            f"priority:{item.priority}",
            f"phase:{item.phase}",
            f"area:{item.area}",
        ]
        milestone = None if item.milestone is None else milestone_numbers[item.milestone]
        if item.stable_id == "P3-EXPERT-000":
            body = expert_strategy_epic_body(item, repository, task_urls)
        elif item.stable_id == "P3-STRAT-003":
            body = p3_strat_003_epic_body(item, repository, task_urls)
        elif item.stable_id == "P6-000":
            body = p6_epic_body(item, repository, task_urls)
        else:
            body = task_body(item, repository)
        issue, result = github.upsert_issue(
            issues_by_id.get(item.stable_id),
            f"[{item.stable_id}] {item.title}",
            body,
            labels,
            milestone,
        )
        task_results[result] += 1
        task_urls[item.stable_id] = issue["html_url"]
        issues_by_id[item.stable_id] = issue

    epic_results = {"created": 0, "updated": 0, "reused": 0}
    epic_urls: dict[str, str] = {}
    for chapter in CHAPTERS:
        stable_id = f"LLD-{chapter.number}"
        labels = [
            "type:epic",
            f"status:{chapter.status}",
            f"phase:{chapter.phase}",
            f"area:{chapter.area}",
        ]
        issue, result = github.upsert_issue(
            issues_by_id.get(stable_id),
            f"[{stable_id}] {document_title(root / chapter.path)}",
            epic_body(root, chapter, rows_by_chapter[chapter.number], task_urls, repository),
            labels,
            milestone_numbers[chapter.milestone],
        )
        epic_results[result] += 1
        epic_urls[stable_id] = issue["html_url"]
        issues_by_id[stable_id] = issue

    for stable_id in sorted(target_ids):
        verification_issue = issues_by_id.get(stable_id)
        if verification_issue is None:
            raise RuntimeError(f"GitHub verification failed; missing stable id: {stable_id}")
        remote_issue = github.api(
            "GET", f"/repos/{repository}/issues/{verification_issue['number']}"
        )
        if not remote_issue["title"].startswith(f"[{stable_id}]"):
            raise RuntimeError(
                f"GitHub verification failed for {stable_id}: issue #{verification_issue['number']}"
            )
        if stable_id in closed_ids and remote_issue.get("state", "").casefold() != "closed":
            raise RuntimeError(
                f"GitHub verification failed for {stable_id}: expected closed issue "
                f"#{verification_issue['number']}"
            )

    issue_by_stable_id(github.issues(), target_ids)

    return {
        "repository": f"https://github.com/{repository}",
        "labels": {"total": len(LABELS), **label_results},
        "milestones": {"total": len(MILESTONES), **milestone_results},
        "epics": {"total": len(CHAPTERS), **epic_results, "urls": epic_urls},
        "tasks": {"total": len(items), **task_results},
        "critical": {stable_id: task_urls[stable_id] for stable_id in sorted(CRITICAL_IDS)},
        "duplicate_stable_ids": 0,
    }


def dry_run(root: Path, repository: str) -> dict[str, Any]:
    items = work_items(root)
    owners, rows_by_chapter = chapter_rows(root)
    return {
        "repository": repository,
        "labels": len(LABELS),
        "milestones": len(MILESTONES),
        "epics": len(CHAPTERS),
        "tasks": len(items),
        "critical_ids": sorted(CRITICAL_IDS),
        "work_items_by_chapter": {
            f"LLD-{chapter.number}": len(rows_by_chapter[chapter.number]) for chapter in CHAPTERS
        },
        "owned_stable_ids": len(owners),
    }


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", default=DEFAULT_REPOSITORY, help="GitHub repository as OWNER/NAME"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply the plan through the authenticated gh CLI; otherwise perform an offline dry run"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = (
            synchronize(project_root(), args.repo)
            if args.apply
            else dry_run(project_root(), args.repo)
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
