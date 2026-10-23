"""Five preregistered retrospective controls; no production strategy selection.

Only the cap-only cell needs new policy wiring. All scoring, missing-data guards,
base constraints, held rules, allocation and corrected P2 accounting are reused.
The retained T/TR anchors must reproduce before the three new cells may run.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_protocols import ConstraintOutput, ConstraintScope, ExpertApplicability
from quanthus.expert_strategy import formal_p2_accounting_v2 as accounting
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_component_checkpoint as checkpoint
from quanthus.expert_strategy import p3_retrospective_accounting_v2 as corrected
from quanthus.expert_strategy.p3_retrospective_analysis import path_metrics
from quanthus.expert_strategy.position_risk import EntryRiskDecision, PositionRiskPolicy
from quanthus.expert_strategy.runner import FormalStrategyDecision, FormalStrategyRunner

VERSION = "p3-retrospective-risk-components-v1"
REQUEST_PATH = "config/research/p3-strat-003/risk-components.v1.json"
ACTIVATION = "risk-components-execution.v1.json"
VARIANTS = {
    "T": {"entry_filter": False, "entry_cap": False, "held_risk": False},
    "TC": {"entry_filter": False, "entry_cap": True, "held_risk": False},
    "TE": {"entry_filter": True, "entry_cap": True, "held_risk": False},
    "TH": {"entry_filter": False, "entry_cap": False, "held_risk": True},
    "TR": {"entry_filter": True, "entry_cap": True, "held_risk": True},
}
CONTRASTS = {
    "cap_at_no_filter_no_held": {"TC": 1, "T": -1},
    "filter_at_capped_no_held": {"TE": 1, "TC": -1},
    "held_at_original_entry": {"TH": 1, "T": -1},
    "held_at_full_entry": {"TR": 1, "TE": -1},
    "entry_held_interaction": {"TR": 1, "TE": -1, "TH": -1, "T": 1},
    "full_risk_gap": {"TR": 1, "T": -1},
}
ORDER = ("T", "TR", "TC", "TE", "TH")


def load_request(repository: Path) -> dict[str, Any]:
    request = native._json(repository / REQUEST_PATH)
    if (
        request.get("experiment_id") != VERSION
        or request.get("schema_version") != "quanthus.p3-risk-components.v1"
        or request.get("variants") != VARIANTS
        or request.get("contrasts") != CONTRASTS
        or request.get("execution_order") != list(ORDER)
        or request.get("scope") != ["2025-01-01", "2026-09-17", "2026-09-18"]
        or request.get("information_cutoff") != "2026-10-23T11:21:45.250143+00:00"
        or request.get("already_observed_window") is not True
        or any(
            request.get(key) is not False
            for key in (
                "parameter_search",
                "original_request_replaced",
                "original_B_accepted",
                "original_D_activated",
                "historical_availability_claim",
                "P6_mutation_authorized",
                "production_authorized",
                "step3_started",
            )
        )
        or request.get("analysis", {}).get("paired_block_sessions") != 22
        or request.get("analysis", {}).get("resamples") != 2000
        or request.get("original_request_sha256")
        != native._sha(repository / "config/research/p3-strat-003/request.v1.json")
        or request.get("strategy_card_sha256")
        != native._sha(repository / "config/expert-strategies/p4-trend-risk.v1.0.1.json")
    ):
        raise native.InputError("COMPONENT_REQUEST_BINDING")
    if any(type(x) is not bool for v in request["variants"].values() for x in v.values()):
        raise native.InputError("COMPONENT_REQUEST_BOOL")
    anchors = request.get("anchor_artifact_sha256", {})
    if set(anchors) != {"T", "TR"} or any(
        set(files) != {"equity.json", "daily-returns.json", "final-ledgers.json"}
        or any(not isinstance(s, str) or not native._SHA.fullmatch(s) for s in files.values())
        for files in anchors.values()
    ):
        raise native.InputError("COMPONENT_ANCHOR_BINDING")
    decisions = request.get("anchor_decision_sha256", {})
    if set(decisions) != {"T", "TR"} or any(
        not isinstance(s, str) or not native._SHA.fullmatch(s) for s in decisions.values()
    ):
        raise native.InputError("COMPONENT_DECISION_ANCHOR_BINDING")
    if request.get("anchor_snapshot_id") != (
        "p3-base24-retrospective-comparison-v1/"
        "28a3aede1da691a1799284bad37e2b628bc253a062c7d8001faaa0564a37aece"
    ):
        raise native.InputError("COMPONENT_ANCHOR_SNAPSHOT_BINDING")
    return request


def ledger_anchor_digest(payload: bytes, *, current_snapshot: str, old_snapshot: str) -> str:
    """Map only the exact declared lineage ID; preserve every business field."""
    rows = json.loads(payload)
    if not isinstance(rows, list):
        raise native.InputError("COMPONENT_LEDGER_ANCHOR_SCHEMA")
    for row in rows:
        if not isinstance(row, dict) or "last_mark_snapshot_id" not in row:
            raise native.InputError("COMPONENT_LEDGER_ANCHOR_SCHEMA")
        value = row["last_mark_snapshot_id"]
        if value is not None:
            if value != current_snapshot:
                raise native.InputError("COMPONENT_LEDGER_ANCHOR_SNAPSHOT")
            row["last_mark_snapshot_id"] = old_snapshot
    return "sha256:" + hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def decision_anchor_digest(
    rows: Sequence[Mapping[str, Any]], coordinates: Mapping[str, str]
) -> str:
    """Keep every recorded business field, including no-action/no-fill decisions.

    Event keys are mapped bijectively to security/date. Only the formal digest,
    which binds the changed physical preparation and experiment identity, is
    omitted. Native governance is separately checked before recording entries;
    held governance uses the unchanged native implementation.
    """
    if len(set(coordinates.values())) != len(coordinates):
        raise native.InputError("COMPONENT_ANCHOR_COORDINATES")
    normalized = []
    seen = set()
    fields = {
        "aggregate_score",
        "allocated",
        "decision_key",
        "filled",
        "final_action",
        "formal_decision_digest",
        "market_date",
        "reason_codes",
        "surface",
        "target_maximum_position",
        "unit_id",
    }
    for source in rows:
        if set(source) != fields or source["surface"] not in {"entry", "held"}:
            raise native.InputError("COMPONENT_ANCHOR_SCHEMA")
        row = dict(source)
        if row["surface"] == "entry":
            if row["decision_key"] not in coordinates:
                raise native.InputError("COMPONENT_ANCHOR_COORDINATE_MISSING")
            row["decision_key"] = coordinates[row["decision_key"]]
        key = (row["market_date"], row["decision_key"])
        if key in seen:
            raise native.InputError("COMPONENT_ANCHOR_DUPLICATE")
        seen.add(key)
        del row["formal_decision_digest"]
        normalized.append(row)
    return digest_value(sorted(normalized, key=lambda r: (r["market_date"], r["decision_key"])))


@dataclass(frozen=True)
class FixedCapPolicy(PositionRiskPolicy):
    """A separately identified 20% strategy constraint, never a changed system cap."""

    def evaluate_entry(self, context: Any, *, fallback: bool = False) -> EntryRiskDecision:
        native_decision = super().evaluate_entry(context, fallback=fallback)
        if fallback or any(f.value is None for f in context.feature_facts):
            return native_decision
        abstained = super().evaluate_entry(context, fallback=True)
        return EntryRiskDecision(
            abstained.veto,
            ConstraintOutput(
                expert_id="control.p3-fixed-entry-cap",
                expert_version="1.0.0",
                applicability=ExpertApplicability.APPLICABLE,
                applicability_reason="preregistered_cap_only_control",
                reason_code="component_fixed_entry_cap",
                evidence=native_decision.veto.evidence,
                minimum_position=self.entry_minimum_position,
                maximum_position=self.entry_maximum_position,
                stop_rule="base_constraint_stop_preserved",
                hold_rule="base_holding_policy_preserved",
                scope=ConstraintScope.UNIT,
            ),
        )


class ComponentEntryRunner(FormalStrategyRunner):
    def __init__(self, runner: FormalStrategyRunner, variant: str) -> None:
        if variant not in VARIANTS:
            raise native.InputError("COMPONENT_VARIANT")
        super().__init__(runner.card, runner.topology_digest)
        self.component_variant = variant
        if variant == "TC":
            self.position_policy = FixedCapPolicy(**asdict(self.position_policy))

    def evaluate_component(self, **kwargs: Any) -> FormalStrategyDecision:
        decision = self.evaluate_entry(
            **kwargs,
            intervention_id=(
                "intervention.position-risk-base-policy"
                if self.component_variant in {"T", "TH"}
                else None
            ),
        )
        return replace(decision, intervention_id=VERSION + ":" + self.component_variant)


class ComponentEngine(corrected.CorrectedRetrospectiveEngine):
    runtime_version = VERSION


class RiskComponentRuntime(corrected.CorrectedRetrospectiveRuntime):
    runtime_version = VERSION
    activation_name = ACTIVATION
    engine_type = ComponentEngine

    def __init__(self, repository: Path, *args: Any, **kwargs: Any) -> None:
        self.component_request = load_request(repository)
        self.component_request_digest = digest_value(self.component_request)
        self.component_runners: dict[str, ComponentEntryRunner] = {}
        receipt = kwargs.get("consumer_admission", {})
        self.index_checkpoint = receipt.get("session_index_checkpoint")
        self.result_role = receipt.get("result_role", "synthetic")
        super().__init__(repository, *args, **kwargs)

    def _adapter_binding(self) -> dict[str, Any]:
        binding = {
            "runtime_version": VERSION,
            "accounting_version": accounting.VERSION,
            "code_config_digest": digest_value(self.r2_plan["code_config_members"]),
            "experiment_id": VERSION,
            "experiment_request_digest": self.component_request_digest,
        }
        if self.index_checkpoint is not None:
            binding["session_index_checkpoint"] = self.index_checkpoint
        return binding

    def _build_session_index(self) -> dict[date, tuple[Any, Any]]:
        if self.index_checkpoint is None:
            return super()._build_session_index()
        # Charge the failed attempt against the original runtime budget.
        elapsed = self.index_checkpoint.get("prior_runtime_seconds")
        if type(elapsed) is not int or not 0 <= elapsed < 3600:
            raise native.InputError("COMPONENT_CHECKPOINT_PRIOR_BUDGET")
        self.started -= elapsed
        self._guard()
        return checkpoint.resume_index(
            self.prepared,
            self.workspace,
            self.r2_plan,
            self.snapshot_id,
            self.index_checkpoint,
            self.result_role,
            self._guard,
        )

    @staticmethod
    def token_for(variant_id: str) -> str:
        if variant_id not in VARIANTS:
            raise native.InputError("COMPONENT_VARIANT")
        return VERSION + ":" + variant_id

    @staticmethod
    def variant_for(token: str | None) -> str:
        for name in VARIANTS:
            if token == VERSION + ":" + name:
                return name
        raise native.InputError("COMPONENT_VARIANT")

    @staticmethod
    def _variant_id(intervention_id: str | None) -> str:
        return "variant." + RiskComponentRuntime.variant_for(intervention_id)

    def _baseline_variant_directory(self) -> str:
        return "variant.T"

    def _metrics_intervention_id(self, intervention_id: str | None) -> str | None:
        return None if self.variant_for(intervention_id) == "T" else intervention_id

    def _held_intervention_id(self, intervention_id: str | None) -> str | None:
        return (
            None
            if VARIANTS[self.variant_for(intervention_id)]["held_risk"]
            else "intervention.position-risk-base-policy"
        )

    def _parameter_payload(self, intervention_id: str | None) -> dict[str, Any]:
        variant = self.variant_for(intervention_id)
        return {
            **self._adapter_binding(),
            "original_request_digest": self.request.digest,
            "variant_id": variant,
            "components": VARIANTS[variant],
            "scope": self.r2_plan["scope"],
            "information_cutoff": self.r2_plan["information_cutoff"],
        }

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._manifest_metadata(intervention_id),
            "result_namespace": VERSION + "/" + self.variant_for(intervention_id),
            "components": VARIANTS[self.variant_for(intervention_id)],
            "successor_strategy_selected": False,
        }

    def _evaluate_entry_decision(
        self, runner: FormalStrategyRunner, *, intervention_id: str | None, **kwargs: Any
    ) -> FormalStrategyDecision:
        variant = self.variant_for(intervention_id)
        if variant not in self.component_runners:
            self.component_runners[variant] = ComponentEntryRunner(runner, variant)
        decision = self.component_runners[variant].evaluate_component(**kwargs)
        if variant in {"T", "TR"}:
            expected = runner.evaluate_entry(
                **kwargs,
                intervention_id="intervention.position-risk-base-policy"
                if variant == "T"
                else None,
            )
            if decision != replace(expected, intervention_id=self.token_for(variant)):
                raise native.InputError("COMPONENT_NATIVE_ENTRY_GOVERNANCE_MISMATCH")
        return decision

    def _verify_anchor(self, target: Path, variant: str) -> None:
        for name, sha in self.component_request["anchor_artifact_sha256"][variant].items():
            observed = (
                ledger_anchor_digest(
                    (target / name).read_bytes(),
                    current_snapshot=self.snapshot_id,
                    old_snapshot=self.component_request["anchor_snapshot_id"],
                )
                if name == "final-ledgers.json"
                else native._sha(target / name)
            )
            if observed != sha:
                raise native.InputError("COMPONENT_OLD_ANCHOR_MISMATCH")
        coordinates = {
            "entry:" + e.record.event_id: e.record.identity.security_id
            + ":"
            + e.record.identity.signal_date.isoformat()
            for e in self.events
        }
        observed = decision_anchor_digest(
            json.loads((target / "decisions.json").read_bytes()), coordinates
        )
        if observed != self.component_request["anchor_decision_sha256"][variant]:
            raise native.InputError("COMPONENT_OLD_DECISION_ANCHOR_MISMATCH")

    def execute_all(self) -> dict[str, Any]:
        results = {}
        for variant in ORDER:
            self._guard()
            target = self.workspace / ("variant." + variant)
            target.mkdir()
            try:
                evidence, artifacts = self.run_research_variant(variant, target)
                for name, payload in artifacts.items():
                    (target / name).write_bytes(payload)
                if not self.synthetic and variant in {"T", "TR"}:
                    self._verify_anchor(target, variant)
                results[variant] = evidence.replay_digest
            except Exception as exc:
                (self.workspace / "FAILED.json").write_bytes(
                    canonical_json_bytes(
                        {
                            **self._adapter_binding(),
                            "failed_variant": variant,
                            "failure_type": type(exc).__name__,
                            "completed_variants": list(results),
                            "complete": False,
                            "outputs_are_partial": True,
                        }
                    )
                )
                raise
        report = {
            **self._adapter_binding(),
            "variants": results,
            "mode": "SYNTHETIC_ONLY" if self.synthetic else "ADMITTED_RETROSPECTIVE_COMPONENTS",
            "provider_builds": 1,
            "feature_panel_builds": 0,
            "session_index_builds": 1 if self.index_checkpoint is None else 0,
            "session_index_checkpoint_reuses": 0 if self.index_checkpoint is None else 1,
            "real_comparisons_executed": 0 if self.synthetic else len(results),
            "information_cutoff": self.info.isoformat(),
            "old_business_anchors_verified": not self.synthetic,
            "production_authorized": False,
        }
        self._guard()
        (self.workspace / "COMPLETE.json").write_bytes(canonical_json_bytes(report))
        return report


def analyze_components(
    request: Mapping[str, Any],
    series: Mapping[str, Sequence[Mapping[str, str]]],
    points: Mapping[str, Mapping[str, Decimal]],
) -> dict[str, Any]:
    """Fixed conditional contrasts; descriptive intervals never select a strategy."""
    if set(series) != set(VARIANTS) or set(points) != set(VARIANTS):
        raise ValueError("COMPONENT_ANALYSIS_VARIANTS")
    if (
        request.get("contrasts") != CONTRASTS
        or request.get("analysis", {}).get("paired_block_sessions") != 22
        or request.get("analysis", {}).get("resamples") != 2000
    ):
        raise ValueError("COMPONENT_ANALYSIS_CONTRASTS")
    dates = [r["market_date"] for r in series["T"]]
    if (
        dates != sorted(set(dates))
        or any([r["market_date"] for r in rows] != dates for rows in series.values())
        or any(not date(2025, 1, 1) <= date.fromisoformat(d) <= date(2026, 9, 18) for d in dates)
    ):
        raise ValueError("COMPONENT_ANALYSIS_DATES")
    values = {v: [Decimal(r["return"]) for r in rows] for v, rows in series.items()}
    for v in VARIANTS:
        path_metrics(values[v])
        if any(not n.is_finite() for n in points[v].values()):
            raise ValueError("COMPONENT_ANALYSIS_METRICS")
    count = len(dates) // 22
    if not count:
        raise ValueError("COMPONENT_ANALYSIS_NO_BLOCK")
    blocks = {v: [rs[i * 22 : (i + 1) * 22] for i in range(count)] for v, rs in values.items()}
    result = {}
    for name, weights in CONTRASTS.items():
        seed = int(hashlib.sha256(f"{digest_value(dict(request))}:{name}".encode()).hexdigest(), 16)
        generator = random.Random(seed)
        draws: dict[str, list[Decimal]] = {"net_total_return": [], "maximum_drawdown": []}
        for _ in range(2000):
            sample = [generator.randrange(count) for _ in range(count)]
            metrics = {v: path_metrics([r for i in sample for r in blocks[v][i]]) for v in weights}
            for key in draws:
                draws[key].append(
                    sum((metrics[v][key] * w for v, w in weights.items()), Decimal(0))
                )
        result[name] = {
            "role": "DESCRIPTIVE_ONLY",
            "seed": str(seed),
            "point_delta": {
                k: str(sum((points[v][k] * w for v, w in weights.items()), Decimal(0)))
                for k in draws
            },
            "paired_block_95_interval": {
                k: {
                    "lower": str(sorted(xs)[math.floor(0.025 * 1999)]),
                    "upper": str(sorted(xs)[math.floor(0.975 * 1999)]),
                }
                for k, xs in draws.items()
            },
        }
    return {
        "experiment_request_digest": digest_value(dict(request)),
        "contrasts": result,
        "full_window_sessions": len(dates),
        "complete_blocks": count,
        "incomplete_block_dates_excluded_only_from_resampling": dates[count * 22 :],
        "successor_strategy_selected": False,
        "confirmatory_acceptance": False,
    }
