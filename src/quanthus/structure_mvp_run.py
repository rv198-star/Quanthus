"""Bounded real-DWD plumbing run for the Structure Intelligence MVP.

The run is deliberately not an investment strategy.  It proves one read-only path:

    ReplayDataProvider -> thin Structure Research ADM -> expert protocol -> aggregator -> P2 probe

The P2 probe is independent per accepted structure observation and does not maintain a portfolio,
report performance, or authorize execution.  All published artifacts are research-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.aggregator import (
    GovernedAction,
    ScoreActionPolicy,
    aggregate_governance,
    aggregate_score_and_map_action,
)
from quanthus.backtest.baseline_research import BaselineFeaturePanel, BaselineFeatureRow
from quanthus.backtest.contracts import BacktestRunSpec, BacktestSignal, EventType, OrderSide
from quanthus.backtest.data_provider import (
    ReplayDataProvider,
    ReplayDataProviderConfig,
    load_replay_data_provider_config,
)
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.policy import load_execution_policy
from quanthus.expert_evaluation.experts.trend_stability import (
    TrendStabilityConfig,
    classify_trend_stability,
    load_trend_stability_config,
)
from quanthus.expert_protocols import (
    ConstraintOutput,
    ConstraintScope,
    EvidenceKind,
    ExpertApplicability,
    ExpertContext,
    RequiredAction,
    ScoringOutput,
    VetoAuthority,
    VetoOutput,
    run_scoring_expert,
)
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.feature_registry import (
    FeatureCutoffRule,
    FeatureLineage,
    FeatureSourceEvidence,
    load_feature_registry,
)
from quanthus.label_registry import FeatureFact
from quanthus.policy import find_project_root
from quanthus.structure_intelligence import (
    StructureAdmRow,
    StructureMvpScoringExpert,
    StructureMvpState,
    StructureResearchAdm,
    StructureSourceRow,
    build_structure_research_adm,
    structure_feature_fact,
)

RUN_SCHEMA = "quanthus.structure-mvp-run.v1"
RESULT_SCHEMA = "quanthus.structure-mvp-result.v1"
DEFAULT_PROVIDER = "config/replay-data-provider.development.v2.json"
DEFAULT_ADM_POLICY = "config/structure-research-adm.mvp.v1.json"
DEFAULT_EXECUTION_POLICY = "config/p2-execution-policy.research.v1.json"
BASELINE_FEATURE_REGISTRY = "config/p3-baseline-features.research.v1.json"
TREND_EXPERT_CONFIG = "config/expert-evaluations/experts/trend-stability.v1.json"
TREND_ADAPTER_ID = "expert.trend-stability-structure-mvp"
TREND_ADAPTER_VERSION = "0.1.0"
TREND_ADAPTER_FEATURE_ID = "trend.mvp.existing-state"
TREND_ADAPTER_FEATURE_VERSION = "0.1.0"
TECHNICAL_PROBE_SCORE_THRESHOLD = Decimal("0.40")
_SHA256_PREFIX = "sha256:"
_CODE_REVISION = re.compile(r"^[0-9a-f]{40}$")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


@dataclass(frozen=True)
class StructureMvpRunRequest:
    provider_config: str
    adm_policy: str
    execution_policy: str
    scan_start: date
    report_start: date
    report_end: date
    security_codes: tuple[str, ...]
    maximum_p2_probes: int
    p2_probe_quantity: int

    def __post_init__(self) -> None:
        if self.provider_config != DEFAULT_PROVIDER:
            raise ValueError("structure MVP provider config is not admitted")
        if self.adm_policy != DEFAULT_ADM_POLICY:
            raise ValueError("structure MVP ADM policy is not admitted")
        if self.execution_policy != DEFAULT_EXECUTION_POLICY:
            raise ValueError("structure MVP execution policy is not admitted")
        if not self.scan_start < self.report_start <= self.report_end:
            raise ValueError("structure MVP date scope is invalid")
        if not 1 <= len(self.security_codes) <= 10:
            raise ValueError("structure MVP requires 1-10 security codes")
        if len(self.security_codes) != len(set(self.security_codes)):
            raise ValueError("structure MVP security codes must be unique")
        if any(len(code) != 6 or not code.isdigit() for code in self.security_codes):
            raise ValueError("structure MVP security codes must be six digits")
        if not 0 <= self.maximum_p2_probes <= 20:
            raise ValueError("structure MVP maximum_p2_probes must be between zero and twenty")
        if self.p2_probe_quantity <= 0 or self.p2_probe_quantity % 100:
            raise ValueError("structure MVP P2 probe quantity must be a positive 100-share lot")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUN_SCHEMA,
            "provider_config": self.provider_config,
            "adm_policy": self.adm_policy,
            "execution_policy": self.execution_policy,
            "scan_start": self.scan_start.isoformat(),
            "report_start": self.report_start.isoformat(),
            "report_end": self.report_end.isoformat(),
            "security_codes": list(self.security_codes),
            "maximum_p2_probes": self.maximum_p2_probes,
            "p2_probe_quantity": self.p2_probe_quantity,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StructureMvpRunRequest:
        expected = {
            "schema_version",
            "provider_config",
            "adm_policy",
            "execution_policy",
            "scan_start",
            "report_start",
            "report_end",
            "security_codes",
            "maximum_p2_probes",
            "p2_probe_quantity",
        }
        if set(value) != expected or value.get("schema_version") != RUN_SCHEMA:
            raise ValueError("structure MVP run request fields differ")
        codes = value.get("security_codes")
        if not isinstance(codes, list) or any(not isinstance(item, str) for item in codes):
            raise ValueError("structure MVP security_codes are invalid")
        try:
            return cls(
                provider_config=str(value["provider_config"]),
                adm_policy=str(value["adm_policy"]),
                execution_policy=str(value["execution_policy"]),
                scan_start=date.fromisoformat(str(value["scan_start"])),
                report_start=date.fromisoformat(str(value["report_start"])),
                report_end=date.fromisoformat(str(value["report_end"])),
                security_codes=tuple(codes),
                maximum_p2_probes=int(value["maximum_p2_probes"]),
                p2_probe_quantity=int(value["p2_probe_quantity"]),
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("structure MVP run request is invalid") from exc


@dataclass(frozen=True)
class StructureMvpDecision:
    market_date: date
    security_code: str
    state: StructureMvpState
    trend_state: str
    baseline_score: Decimal
    baseline_action: GovernedAction
    assisted_score: Decimal
    assisted_action: GovernedAction

    @property
    def changed(self) -> bool:
        return self.baseline_action is not self.assisted_action

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "security_code": self.security_code,
            "state": self.state.value,
            "trend_state": self.trend_state,
            "baseline_score": str(self.baseline_score),
            "baseline_action": self.baseline_action.value,
            "assisted_score": str(self.assisted_score),
            "assisted_action": self.assisted_action.value,
            "changed": self.changed,
        }


@dataclass(frozen=True)
class StructureMvpProbe:
    market_date: date
    security_code: str
    state: StructureMvpState
    trend_state: str
    baseline_action: GovernedAction
    aggregate_score: Decimal
    final_action: GovernedAction
    execution_date: date
    fill_count: int
    result_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "security_code": self.security_code,
            "state": self.state.value,
            "trend_state": self.trend_state,
            "baseline_action": self.baseline_action.value,
            "aggregate_score": str(self.aggregate_score),
            "final_action": self.final_action.value,
            "execution_date": self.execution_date.isoformat(),
            "fill_count": self.fill_count,
            "result_digest": self.result_digest,
        }


@dataclass(frozen=True)
class StructureMvpRunResult:
    request_digest: str
    provider_identity: Mapping[str, Any]
    adm: StructureResearchAdm
    state_counts: Mapping[str, int]
    unavailable_counts: Mapping[str, int]
    trend_semantics: Mapping[str, str]
    decision_counts: Mapping[str, int]
    decisions: tuple[StructureMvpDecision, ...]
    probes: tuple[StructureMvpProbe, ...]

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RESULT_SCHEMA,
            "research_only": True,
            "execution_authorized": False,
            "economic_acceptance_claim": False,
            "request_digest": self.request_digest,
            "provider_identity": dict(self.provider_identity),
            "adm_manifest": self.adm.manifest,
            "state_counts": dict(sorted(self.state_counts.items())),
            "unavailable_counts": dict(sorted(self.unavailable_counts.items())),
            "trend_semantics": dict(sorted(self.trend_semantics.items())),
            "decision_counts": dict(sorted(self.decision_counts.items())),
            "decisions": [item.to_dict() for item in self.decisions],
            "p2_probe_semantics": (
                "independent execution-compatibility probes; no portfolio or performance claim"
            ),
            "probes": [item.to_dict() for item in self.probes],
        }


def load_structure_mvp_run_request(path: Path) -> StructureMvpRunRequest:
    return StructureMvpRunRequest.from_dict(_load_json(path, "structure MVP run request"))


def _adm_policy_digest(path: Path) -> str:
    raw = _load_json(path, "structure MVP ADM policy")
    if (
        raw.get("schema_version") != "0.1.0"
        or raw.get("adm_id") != "structure-research-adm"
        or raw.get("adm_version") != "0.1.0"
        or raw.get("status") != "development_only"
        or raw.get("research_only") is not True
        or raw.get("execution_authorized") is not False
    ):
        raise ValueError("structure MVP ADM policy is not admitted")
    return _digest(raw)


def _load_dwd_identity_map(bundle_root: Path, security_codes: tuple[str, ...]) -> dict[str, str]:
    """Read only the governed identity bridge from the verified job-local DWD projection."""

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise RuntimeError("structure MVP real-DWD run requires duckdb") from exc
    path = bundle_root / "tables" / "daily_universe.parquet"
    if not path.is_file():
        raise RuntimeError("structure MVP daily_universe projection is missing")
    connection = duckdb.connect(":memory:")
    try:
        placeholders = ",".join("?" for _ in security_codes)
        rows = connection.execute(
            f"""
            SELECT security_id, dwd_security_id
            FROM read_parquet(?)
            WHERE security_id IN ({placeholders})
            GROUP BY security_id, dwd_security_id
            ORDER BY security_id, dwd_security_id
            """,
            [str(path.resolve()), *security_codes],
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[str, set[str]] = {code: set() for code in security_codes}
    for code, dwd_security_id in rows:
        grouped.setdefault(str(code), set()).add(str(dwd_security_id))
    invalid = {code: sorted(values) for code, values in grouped.items() if len(values) != 1}
    if invalid:
        raise RuntimeError(f"structure MVP governed identity is not unique: {invalid}")
    return {code: next(iter(grouped[code])) for code in security_codes}


def _source_rows_from_session(
    *,
    session: Any,
    request: StructureMvpRunRequest,
    identity_map: Mapping[str, str],
) -> tuple[tuple[StructureSourceRow, ...], dict[date, Any], dict[str, int]]:
    rows: list[StructureSourceRow] = []
    frames_by_date: dict[date, Any] = {}
    unavailable: dict[str, int] = {}
    provider_revision = str(session.identity.get("provider_lineage_digest") or "")
    source_artifact = str(session.identity.get("materialized_content_digest") or "")
    reset_pending = {code: False for code in request.security_codes}
    if not provider_revision.startswith(_SHA256_PREFIX) or not source_artifact.startswith(
        _SHA256_PREFIX
    ):
        raise RuntimeError("structure MVP provider lineage is incomplete")

    for frame in session.reader.frames(request.scan_start, request.report_end):
        frames_by_date[frame.snapshot.market_date] = frame
        if frame.decision_view is None:
            raise RuntimeError("structure MVP requires the accepted PIT decision view")
        for code in request.security_codes:
            try:
                bar = frame.decision_view.bar(code)
            except KeyError:
                unavailable["security_outside_decision_universe"] = (
                    unavailable.get("security_outside_decision_universe", 0) + 1
                )
                reset_pending[code] = True
                continue
            if (
                not bar.data_valid
                or bar.high_price is None
                or bar.low_price is None
                or bar.close_price is None
            ):
                unavailable["invalid_or_missing_daily_bar"] = (
                    unavailable.get("invalid_or_missing_daily_bar", 0) + 1
                )
                reset_pending[code] = True
                continue
            rows.append(
                StructureSourceRow(
                    market_date=frame.snapshot.market_date,
                    security_code=code,
                    dwd_security_id=identity_map[code],
                    snapshot_id=frame.snapshot.snapshot_id,
                    knowledge_cutoff=frame.snapshot.knowledge_cutoff,
                    high=bar.high_price,
                    low=bar.low_price,
                    close=bar.close_price,
                    volume=bar.volume,
                    source_revision=provider_revision,
                    source_artifact_sha256=source_artifact,
                    history_reset=reset_pending[code],
                )
            )
            reset_pending[code] = False
    return tuple(rows), frames_by_date, unavailable


def _load_trend_semantics(
    repository_root: Path,
) -> tuple[TrendStabilityConfig, dict[str, Any], dict[str, str]]:
    config = load_trend_stability_config(repository_root / TREND_EXPERT_CONFIG)
    registry = ExpertStrategyRegistry(repository_root)
    trend = registry.card.experts[0]
    mapping = trend["policy_mapping"]
    if (
        trend["expert_id"] != "expert.trend-stability"
        or trend["expert_version"] != "1.0.0"
        or mapping["mapping_id"] != "mapping.trend-stability-formal-entry"
        or mapping["mapping_version"] != "1.0.0"
    ):
        raise RuntimeError("structure MVP accepted trend semantics differ")
    effects = mapping["state_effects"]
    if set(effects) != {"fragile", "neutral", "stable", "abstained"}:
        raise RuntimeError("structure MVP accepted trend mapping differs")
    semantics = {
        "source_expert_id": str(trend["expert_id"]),
        "source_expert_version": str(trend["expert_version"]),
        "source_assessment_digest": str(trend["assessment_digest"]),
        "source_mapping_digest": str(mapping["mapping_digest"]),
        "adapter_id": TREND_ADAPTER_ID,
        "adapter_version": TREND_ADAPTER_VERSION,
        "population_disposition": (
            "new_security_day_research_adapter_not_original_acceptance_population"
        ),
    }
    return config, dict(mapping), semantics


def _trend_adapter_fact(
    *,
    structure_row: StructureAdmRow,
    state: str | None,
    reason: str,
    source_identity: Mapping[str, str],
    config: TrendStabilityConfig,
    mapping_digest: str,
) -> FeatureFact:
    definition_digest = _digest(
        {
            "feature_id": TREND_ADAPTER_FEATURE_ID,
            "feature_version": TREND_ADAPTER_FEATURE_VERSION,
            "source_expert_semantic_digest": config.semantic_digest,
            "source_policy_mapping_digest": mapping_digest,
            "population": "structure-mvp-security-day",
        }
    )
    lineage = FeatureLineage(
        feature_id=TREND_ADAPTER_FEATURE_ID,
        feature_version=TREND_ADAPTER_FEATURE_VERSION,
        definition_digest=definition_digest,
        snapshot_id=structure_row.snapshot_id,
        market_date=structure_row.market_date,
        knowledge_cutoff=structure_row.knowledge_cutoff,
        source_revision=source_identity["source_revision"],
        universe_policy_version=source_identity["universe_policy_version"],
        sources=(
            FeatureSourceEvidence(
                dataset="p3-baseline-feature-panel",
                column="trend_stability_state",
                dataset_version=source_identity["dataset_version"],
                manifest_id=source_identity["manifest_id"],
                knowledge_cutoff=structure_row.knowledge_cutoff,
            ),
        ),
        history_sessions=60,
        cross_section_complete=None,
        cutoff_rule=FeatureCutoffRule.SNAPSHOT_OR_EARLIER,
    )
    return FeatureFact(
        security_code=structure_row.security_code,
        feature_id=TREND_ADAPTER_FEATURE_ID,
        feature_version=TREND_ADAPTER_FEATURE_VERSION,
        lineage=lineage,
        value=state,
        unavailable_reason=None if state is not None else reason,
    )


class _TrendSemanticsMvpScorer:
    expert_id = TREND_ADAPTER_ID
    expert_version = TREND_ADAPTER_VERSION

    def __init__(self, mapping: Mapping[str, Any]) -> None:
        self.mapping = mapping

    def evaluate(self, context: ExpertContext) -> ScoringOutput:
        matches = [
            fact
            for fact in context.feature_facts
            if (fact.feature_id, fact.feature_version)
            == (TREND_ADAPTER_FEATURE_ID, TREND_ADAPTER_FEATURE_VERSION)
        ]
        if len(matches) != 1:
            raise RuntimeError("structure MVP trend adapter requires one trend-state fact")
        fact = matches[0]
        evidence = tuple(
            item
            for item in context.evidence_refs
            if item.kind is EvidenceKind.FEATURE
            and item.evidence_id == TREND_ADAPTER_FEATURE_ID
        )
        if len(evidence) != 1:
            raise RuntimeError("structure MVP trend adapter evidence is missing")
        if fact.value is None:
            return ScoringOutput(
                expert_id=self.expert_id,
                expert_version=self.expert_version,
                applicability=ExpertApplicability.ABSTAINED,
                applicability_reason="source_trend_semantics_unavailable",
                reason_code="source_trend_semantics_unavailable",
                evidence=evidence,
                signal=None,
                confidence=None,
            )
        state = str(fact.value)
        effects = self.mapping["state_effects"]
        if state not in {"fragile", "neutral", "stable"} or state not in effects:
            raise RuntimeError("structure MVP trend state is not admitted")
        effect = effects[state]
        return ScoringOutput(
            expert_id=self.expert_id,
            expert_version=self.expert_version,
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="reused_accepted_trend_semantics",
            reason_code=f"trend_{state}",
            evidence=evidence,
            signal=Decimal(str(effect["signal"])),
            confidence=Decimal(str(effect["confidence"])),
        )


def _trend_feature_facts(
    *,
    session: Any,
    request: StructureMvpRunRequest,
    adm: StructureResearchAdm,
    repository_root: Path,
    injected_features: Mapping[tuple[date, str], BaselineFeatureRow | None] | None,
) -> tuple[dict[tuple[date, str], FeatureFact], dict[str, Any], dict[str, str]]:
    config, mapping, semantics = _load_trend_semantics(repository_root)
    facts: dict[tuple[date, str], FeatureFact] = {}
    if injected_features is not None:
        source_identity = {
            "source_revision": _digest(
                {"mode": "injected-test", "count": len(injected_features)}
            ),
            "universe_policy_version": "structure-mvp-injected-test",
            "dataset_version": "injected-test",
            "manifest_id": _digest({"injected-test": True}),
        }
        for row in adm.rows:
            feature = injected_features.get((row.market_date, row.security_code))
            state, reason = classify_trend_stability(feature, config)
            facts[(row.market_date, row.security_code)] = _trend_adapter_fact(
                structure_row=row,
                state=state,
                reason=reason,
                source_identity=source_identity,
                config=config,
                mapping_digest=str(mapping["mapping_digest"]),
            )
        return facts, mapping, semantics

    registry = load_feature_registry(repository_root / BASELINE_FEATURE_REGISTRY)
    with tempfile.TemporaryDirectory(prefix="quanthus-structure-mvp-features-") as temporary:
        panel = BaselineFeaturePanel.build(
            session.reader.bundle,
            registry,
            scope_start=request.scan_start,
            scope_end=request.report_end,
            output=Path(temporary) / "feature-panel.parquet",
            data_content_digest=str(session.identity.get("materialized_content_digest") or ""),
            data_manifest_sha256=str(session.identity.get("provider_lineage_digest") or ""),
            allowed_security_ids=request.security_codes,
        )
        try:
            source_identity = {
                "source_revision": (
                    panel.report.data_content_digest or panel.report.bundle_digest
                ),
                "universe_policy_version": panel.report.feature_registry_version,
                "dataset_version": panel.report.evaluator_version,
                "manifest_id": panel.report.digest,
            }
            for row in adm.rows:
                feature = panel.feature(row.market_date, row.security_code)
                state, reason = classify_trend_stability(feature, config)
                facts[(row.market_date, row.security_code)] = _trend_adapter_fact(
                    structure_row=row,
                    state=state,
                    reason=reason,
                    source_identity=source_identity,
                    config=config,
                    mapping_digest=str(mapping["mapping_digest"]),
                )
        finally:
            panel.close()
    return facts, mapping, semantics


def _system_veto(context: ExpertContext) -> VetoOutput:
    snapshot = next(item for item in context.evidence_refs if item.kind is EvidenceKind.SNAPSHOT)
    return VetoOutput(
        expert_id="system.structure-mvp-guardian",
        expert_version="0.1.0",
        applicability=ExpertApplicability.APPLICABLE,
        applicability_reason="technical_probe",
        reason_code="technical_probe_allowed",
        evidence=(snapshot,),
        authority=VetoAuthority.SYSTEM,
        allow=True,
        required_action=RequiredAction.NONE,
    )


def _base_constraint(context: ExpertContext) -> ConstraintOutput:
    snapshot = next(item for item in context.evidence_refs if item.kind is EvidenceKind.SNAPSHOT)
    return ConstraintOutput(
        expert_id="system.structure-mvp-position",
        expert_version="0.1.0",
        applicability=ExpertApplicability.APPLICABLE,
        applicability_reason="technical_probe",
        reason_code="technical_probe_position_bound",
        evidence=(snapshot,),
        minimum_position=Decimal("0"),
        maximum_position=Decimal("0.10"),
        stop_rule="technical_probe_no_strategy_stop",
        hold_rule="technical_probe_no_portfolio_hold",
        scope=ConstraintScope.UNIT,
    )


def _decision_context(
    adm: StructureResearchAdm,
    row: StructureAdmRow,
    trend_fact: FeatureFact,
) -> ExpertContext:
    return ExpertContext(
        security_code=row.security_code,
        snapshot_id=row.snapshot_id,
        knowledge_cutoff=row.knowledge_cutoff,
        position_state="empty",
        feature_facts=(trend_fact, structure_feature_fact(adm, row)),
        label_results=(),
    )


def _evaluate_decisions(
    *,
    adm: StructureResearchAdm,
    trend_facts: Mapping[tuple[date, str], FeatureFact],
    trend_mapping: Mapping[str, Any],
) -> tuple[tuple[StructureMvpDecision, ...], dict[str, int]]:
    trend_expert = _TrendSemanticsMvpScorer(trend_mapping)
    structure_expert = StructureMvpScoringExpert()
    score_policy = ScoreActionPolicy(
        policy_version="structure-mvp-technical-probe@0.2.0",
        minimum_entry_score=TECHNICAL_PROBE_SCORE_THRESHOLD,
    )
    decisions: list[StructureMvpDecision] = []
    trend_unavailable = 0
    for row in adm.rows:
        trend_fact = trend_facts[(row.market_date, row.security_code)]
        context = _decision_context(adm, row, trend_fact)
        trend_output = run_scoring_expert(trend_expert, context)
        if trend_output.applicability is ExpertApplicability.ABSTAINED:
            trend_unavailable += 1
            continue
        structure_output = run_scoring_expert(structure_expert, context)
        governance = aggregate_governance(
            context,
            [_system_veto(context)],
            [_base_constraint(context)],
        )
        baseline = aggregate_score_and_map_action(
            context,
            governance,
            [trend_output],
            score_policy,
        )
        assisted_outputs = [trend_output]
        if structure_output.applicability is ExpertApplicability.APPLICABLE:
            assisted_outputs.append(structure_output)
        assisted = aggregate_score_and_map_action(
            context,
            governance,
            assisted_outputs,
            score_policy,
        )
        decisions.append(
            StructureMvpDecision(
                market_date=row.market_date,
                security_code=row.security_code,
                state=row.state,
                trend_state=str(trend_fact.value),
                baseline_score=baseline.aggregate_score,
                baseline_action=baseline.final_action,
                assisted_score=assisted.aggregate_score,
                assisted_action=assisted.final_action,
            )
        )
    counts = {
        "evaluated": len(decisions),
        "trend_unavailable": trend_unavailable,
        "changed": sum(item.changed for item in decisions),
        "baseline_trial_entry": sum(
            item.baseline_action is GovernedAction.TRIAL_ENTRY for item in decisions
        ),
        "assisted_trial_entry": sum(
            item.assisted_action is GovernedAction.TRIAL_ENTRY for item in decisions
        ),
        "assisted_new_entry": sum(
            item.baseline_action is GovernedAction.NO_ACTION
            and item.assisted_action is GovernedAction.TRIAL_ENTRY
            for item in decisions
        ),
        "assisted_removed_entry": sum(
            item.baseline_action is GovernedAction.TRIAL_ENTRY
            and item.assisted_action is GovernedAction.NO_ACTION
            for item in decisions
        ),
    }
    return tuple(decisions), counts


def _probe_candidates(
    request: StructureMvpRunRequest,
    decisions: tuple[StructureMvpDecision, ...],
) -> tuple[StructureMvpDecision, ...]:
    buckets = {
        code: sorted(
            (
                item
                for item in decisions
                if item.security_code == code
                and item.assisted_action is GovernedAction.TRIAL_ENTRY
            ),
            key=lambda item: (not item.changed, item.market_date),
        )
        for code in request.security_codes
    }
    selected: list[StructureMvpDecision] = []
    index = 0
    while len(selected) < request.maximum_p2_probes:
        added = False
        for code in request.security_codes:
            values = buckets[code]
            if index < len(values):
                selected.append(values[index])
                added = True
                if len(selected) >= request.maximum_p2_probes:
                    break
        if not added:
            break
        index += 1
    return tuple(selected)


def _run_p2_probes(
    *,
    request: StructureMvpRunRequest,
    adm: StructureResearchAdm,
    frames_by_date: Mapping[date, Any],
    decisions: tuple[StructureMvpDecision, ...],
    repository_root: Path,
) -> tuple[StructureMvpProbe, ...]:
    if request.maximum_p2_probes == 0:
        return ()
    execution_policy = load_execution_policy(repository_root / request.execution_policy)
    engine = BacktestEngine(execution_policy)
    probes: list[StructureMvpProbe] = []
    rows_by_key = {(row.market_date, row.security_code): row for row in adm.rows}
    for index, decision in enumerate(_probe_candidates(request, decisions), start=1):
        row = rows_by_key[(decision.market_date, decision.security_code)]
        frame = frames_by_date.get(row.market_date)
        if frame is None:
            raise RuntimeError("structure MVP P2 probe frame is missing")
        signal = BacktestSignal(
            signal_id=f"structure-mvp-probe-{index:03d}",
            snapshot=frame.snapshot,
            decision_at=frame.decision_at,
            profile_id="close-to-next-open-v1",
            window_id=None,
            security_code=row.security_code,
            side=OrderSide.BUY,
            unit_id=f"structure-mvp-unit-{index:03d}",
            quantity=request.p2_probe_quantity,
            aggregate_score=decision.assisted_score,
            reason_code="structure_mvp_technical_probe",
            decision_version="structure-mvp-technical-probe@0.2.0",
        )
        run_spec = BacktestRunSpec(
            code_version="structure-mvp-technical-probe",
            parameter_hash=request.digest,
            data_snapshot_id=row.snapshot_id,
            feature_version="structure.mvp.state@0.1.0",
            rule_version="structure-mvp-technical-probe@0.2.0",
            run_id=f"structure-mvp-probe-{index:03d}",
            execution_policy_version=execution_policy.policy_version,
            execution_profile_id="close-to-next-open-v1",
            schedule_version=execution_policy.active_profile.schedule_version,
            official=False,
            data_content_digest=adm.content_digest,
            data_manifest_sha256=adm.manifest_digest,
        )
        result = engine.run_session(
            run=run_spec,
            signal=signal,
            session=frame.session(row.security_code),
            initial_cash=Decimal("1000000"),
        )
        fill_count = sum(1 for item in result.events if item.event_type is EventType.FILL)
        probes.append(
            StructureMvpProbe(
                market_date=row.market_date,
                security_code=row.security_code,
                state=row.state,
                trend_state=decision.trend_state,
                baseline_action=decision.baseline_action,
                aggregate_score=decision.assisted_score,
                final_action=decision.assisted_action,
                execution_date=frame.execution_date,
                fill_count=fill_count,
                result_digest=_digest(result.to_dict()),
            )
        )
    return tuple(probes)


def run_structure_mvp(
    request: StructureMvpRunRequest,
    *,
    repository_root: Path | None = None,
    provider: ReplayDataProvider | None = None,
    identity_map: Mapping[str, str] | None = None,
    trend_features: Mapping[tuple[date, str], BaselineFeatureRow | None] | None = None,
) -> StructureMvpRunResult:
    """Execute the bounded technical proof without opening any sealed partition."""

    root = (repository_root or find_project_root()).resolve()
    provider_config_path = root / request.provider_config
    config: ReplayDataProviderConfig = load_replay_data_provider_config(provider_config_path)
    if config.scope.partition != "development":
        raise RuntimeError("structure MVP may read development only")
    config.scope.authorize(request.scan_start, request.report_end)
    adm_policy_digest = _adm_policy_digest(root / request.adm_policy)
    active_provider = provider or ReplayDataProvider(config)
    with active_provider.open(
        scope_start=request.scan_start,
        scope_end=request.report_end,
    ) as session:
        mapping = (
            dict(identity_map)
            if identity_map is not None
            else _load_dwd_identity_map(session.reader.bundle.root, request.security_codes)
        )
        if set(mapping) != set(request.security_codes):
            raise RuntimeError("structure MVP governed identity map is incomplete")
        source_rows, frames_by_date, unavailable = _source_rows_from_session(
            session=session,
            request=request,
            identity_map=mapping,
        )
        full_adm = build_structure_research_adm(
            source_rows,
            upstream_identity=str(session.identity["provider_lineage_digest"]),
            policy_digest=adm_policy_digest,
        )
        published_rows = tuple(
            row
            for row in full_adm.rows
            if request.report_start <= row.market_date <= request.report_end
        )
        if not published_rows:
            raise RuntimeError("structure MVP produced no report-scope rows")
        adm = StructureResearchAdm(
            upstream_identity=full_adm.upstream_identity,
            policy_digest=full_adm.policy_digest,
            rows=published_rows,
        )
        state_counts: dict[str, int] = {}
        for row in adm.rows:
            state_counts[row.state.value] = state_counts.get(row.state.value, 0) + 1
        trend_facts, trend_mapping, trend_semantics = _trend_feature_facts(
            session=session,
            request=request,
            adm=adm,
            repository_root=root,
            injected_features=trend_features,
        )
        decisions, decision_counts = _evaluate_decisions(
            adm=adm,
            trend_facts=trend_facts,
            trend_mapping=trend_mapping,
        )
        probes = _run_p2_probes(
            request=request,
            adm=adm,
            frames_by_date=frames_by_date,
            decisions=decisions,
            repository_root=root,
        )
        provider_identity = {
            "provider_id": session.identity.get("provider_id"),
            "provider_version": session.identity.get("provider_version"),
            "provider_config_digest": session.identity.get("provider_config_digest"),
            "provider_lineage_digest": session.identity.get("provider_lineage_digest"),
            "materialized_content_digest": session.identity.get("materialized_content_digest"),
            "point_in_time": session.identity.get("point_in_time"),
        }
        return StructureMvpRunResult(
            request_digest=request.digest,
            provider_identity=provider_identity,
            adm=adm,
            state_counts=state_counts,
            unavailable_counts=unavailable,
            trend_semantics=trend_semantics,
            decision_counts=decision_counts,
            decisions=decisions,
            probes=probes,
        )


def write_structure_mvp_result(
    result: StructureMvpRunResult,
    output_root: Path,
    *,
    code_revision: str,
) -> dict[str, Any]:
    """Publish one deterministic research artifact set below a caller-owned staging root."""

    if _CODE_REVISION.fullmatch(code_revision) is None:
        raise ValueError("structure MVP code revision must be a 40-character git sha")
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    rows_path = root / "structure-adm.jsonl"
    rows_payload = b"".join(
        _canonical_bytes(row.to_dict()) + b"\n" for row in result.adm.rows
    )
    rows_path.write_bytes(rows_payload)
    summary = result.to_dict()
    summary["result_digest"] = result.digest
    summary["code_revision"] = code_revision
    summary_path = root / "summary.json"
    summary_path.write_bytes(_canonical_bytes(summary) + b"\n")
    manifest = {
        "schema_version": "quanthus.structure-mvp-artifact-manifest.v1",
        "research_only": True,
        "execution_authorized": False,
        "code_revision": code_revision,
        "request_digest": result.request_digest,
        "files": {
            "structure-adm.jsonl": f"sha256:{hashlib.sha256(rows_payload).hexdigest()}",
            "summary.json": f"sha256:{hashlib.sha256(summary_path.read_bytes()).hexdigest()}",
        },
        "result_digest": result.digest,
        "adm_manifest_digest": result.adm.manifest_digest,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(_canonical_bytes(manifest) + b"\n")
    ready = {
        "schema_version": "quanthus.structure-mvp-ready.v1",
        "manifest_sha256": (
            f"sha256:{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}"
        ),
        "result_digest": result.digest,
    }
    (root / "READY").write_bytes(_canonical_bytes(ready) + b"\n")
    return {**manifest, "ready": ready, "artifact_ref": str(root)}


def verify_structure_mvp_result(
    root: Path,
    *,
    expected_code_revision: str,
    expected_request_digest: str,
) -> dict[str, Any]:
    """Verify one sealed Structure MVP package without recomputing analytical results."""

    if _CODE_REVISION.fullmatch(expected_code_revision) is None:
        raise ValueError("structure MVP expected code revision is invalid")
    resolved = root.resolve()
    manifest_path = resolved / "manifest.json"
    ready_path = resolved / "READY"
    summary_path = resolved / "summary.json"
    rows_path = resolved / "structure-adm.jsonl"
    manifest = _load_json(manifest_path, "structure MVP manifest")
    ready = _load_json(ready_path, "structure MVP READY")
    summary = _load_json(summary_path, "structure MVP summary")
    if manifest.get("schema_version") != "quanthus.structure-mvp-artifact-manifest.v1":
        raise ValueError("structure MVP manifest schema differs")
    if (
        manifest.get("research_only") is not True
        or manifest.get("execution_authorized") is not False
    ):
        raise ValueError("structure MVP manifest authority differs")
    if manifest.get("code_revision") != expected_code_revision:
        raise ValueError("structure MVP code revision differs")
    if manifest.get("request_digest") != expected_request_digest:
        raise ValueError("structure MVP request digest differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {"structure-adm.jsonl", "summary.json"}:
        raise ValueError("structure MVP manifest files differ")
    for name, path in (("structure-adm.jsonl", rows_path), ("summary.json", summary_path)):
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"structure MVP file is unreadable: {name}") from exc
        observed = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        if files.get(name) != observed:
            raise ValueError(f"structure MVP file digest differs: {name}")
    manifest_sha = f"sha256:{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}"
    if (
        ready.get("schema_version") != "quanthus.structure-mvp-ready.v1"
        or ready.get("manifest_sha256") != manifest_sha
        or ready.get("result_digest") != manifest.get("result_digest")
    ):
        raise ValueError("structure MVP READY differs")
    if (
        summary.get("schema_version") != RESULT_SCHEMA
        or summary.get("code_revision") != expected_code_revision
        or summary.get("request_digest") != expected_request_digest
        or summary.get("result_digest") != manifest.get("result_digest")
    ):
        raise ValueError("structure MVP summary identity differs")
    semantic_summary = dict(summary)
    semantic_summary.pop("result_digest", None)
    semantic_summary.pop("code_revision", None)
    if _digest(semantic_summary) != manifest.get("result_digest"):
        raise ValueError("structure MVP semantic result digest differs")
    adm_manifest = summary.get("adm_manifest")
    if not isinstance(adm_manifest, dict):
        raise ValueError("structure MVP ADM manifest is invalid")
    if _digest(adm_manifest) != manifest.get("adm_manifest_digest"):
        raise ValueError("structure MVP ADM manifest digest differs")
    try:
        row_values = [
            json.loads(line)
            for line in rows_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("structure MVP ADM rows are invalid") from exc
    if (
        len(row_values) != int(adm_manifest.get("row_count", -1))
        or _digest(row_values) != adm_manifest.get("content_digest")
    ):
        raise ValueError("structure MVP ADM semantic content differs")
    return {
        "artifact_ref": str(resolved),
        "manifest_sha256": manifest_sha,
        "result_digest": str(manifest["result_digest"]),
        "adm_manifest_digest": str(manifest["adm_manifest_digest"]),
        "row_count": int(summary["adm_manifest"]["row_count"]),
        "state_counts": dict(summary["state_counts"]),
        "p2_probe_count": len(summary["probes"]),
        "p2_fill_count": sum(int(item["fill_count"]) for item in summary["probes"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the bounded Quanthus Structure MVP")
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--code-revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = find_project_root()
    request_path = Path(args.request)
    if not request_path.is_absolute():
        request_path = root / request_path
    request = load_structure_mvp_run_request(request_path)
    result = run_structure_mvp(request, repository_root=root)
    publication = write_structure_mvp_result(
        result,
        Path(args.output),
        code_revision=str(args.code_revision),
    )
    verified = verify_structure_mvp_result(
        Path(args.output),
        expected_code_revision=str(args.code_revision),
        expected_request_digest=request.digest,
    )
    print(json.dumps({**publication, "verified": verified}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
