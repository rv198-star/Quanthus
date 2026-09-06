"""Bounded real-DWD qualification runner for Continuous Structure Facts v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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
from quanthus.backtest.contracts import BacktestRunSpec, BacktestSignal, EventType, OrderSide
from quanthus.backtest.data_provider import (
    ReplayDataProvider,
    ReplayDataProviderConfig,
    load_replay_data_provider_config,
)
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.policy import load_execution_policy
from quanthus.expert_protocols import ExpertApplicability, ExpertContext, run_scoring_expert
from quanthus.policy import find_project_root
from quanthus.structure_facts import (
    COMPATIBILITY_EXPERT_ID,
    COMPATIBILITY_EXPERT_VERSION,
    FACTS_ADM_ID,
    FACTS_ADM_VERSION,
    ContinuousStructureFactRow,
    ContinuousStructureFactsAdm,
    ContinuousStructureFactsCompatibilityExpert,
    build_continuous_structure_facts,
    continuous_structure_feature_facts,
    render_coarse_structure_state,
)
from quanthus.structure_mvp_run import (
    _base_constraint,
    _load_dwd_identity_map,
    _source_rows_from_session,
    _system_veto,
)

RUN_SCHEMA = "quanthus.structure-facts-run.v1"
RESULT_SCHEMA = "quanthus.structure-facts-result.v1"
MANIFEST_SCHEMA = "quanthus.structure-facts-artifact-manifest.v1"
READY_SCHEMA = "quanthus.structure-facts-ready.v1"
DEFAULT_PROVIDER = "config/replay-data-provider.development.v2.json"
DEFAULT_FACTS_POLICY = "config/structure-continuous-facts.research.v1.json"
DEFAULT_EXECUTION_POLICY = "config/p2-execution-policy.research.v1.json"
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
class StructureFactsRunRequest:
    provider_config: str
    facts_policy: str
    execution_policy: str
    scan_start: date
    report_start: date
    report_end: date
    security_codes: tuple[str, ...]
    maximum_p2_probes: int
    p2_probe_quantity: int
    consumer_expert_id: str
    consumer_expert_version: str
    consumer_mapping: str

    def __post_init__(self) -> None:
        if self.provider_config != DEFAULT_PROVIDER:
            raise ValueError("structure facts provider config is not admitted")
        if self.facts_policy != DEFAULT_FACTS_POLICY:
            raise ValueError("structure facts policy is not admitted")
        if self.execution_policy != DEFAULT_EXECUTION_POLICY:
            raise ValueError("structure facts execution policy is not admitted")
        if not self.scan_start < self.report_start <= self.report_end:
            raise ValueError("structure facts date scope is invalid")
        if not 1 <= len(self.security_codes) <= 10:
            raise ValueError("structure facts requires 1-10 security codes")
        if len(self.security_codes) != len(set(self.security_codes)):
            raise ValueError("structure facts security codes must be unique")
        if any(len(code) != 6 or not code.isdigit() for code in self.security_codes):
            raise ValueError("structure facts security codes must be six digits")
        if not 0 <= self.maximum_p2_probes <= 20:
            raise ValueError("structure facts maximum_p2_probes must be between zero and twenty")
        if self.p2_probe_quantity <= 0 or self.p2_probe_quantity % 100:
            raise ValueError("structure facts P2 probe quantity must be a positive 100-share lot")
        if (self.consumer_expert_id, self.consumer_expert_version) != (
            COMPATIBILITY_EXPERT_ID,
            COMPATIBILITY_EXPERT_VERSION,
        ):
            raise ValueError("structure facts consumer expert differs")
        if self.consumer_mapping != "active_breakout_episode_uses_bounded_breakout_margin_directly":
            raise ValueError("structure facts consumer mapping differs")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUN_SCHEMA,
            "provider_config": self.provider_config,
            "facts_policy": self.facts_policy,
            "execution_policy": self.execution_policy,
            "scan_start": self.scan_start.isoformat(),
            "report_start": self.report_start.isoformat(),
            "report_end": self.report_end.isoformat(),
            "security_codes": list(self.security_codes),
            "maximum_p2_probes": self.maximum_p2_probes,
            "p2_probe_quantity": self.p2_probe_quantity,
            "consumer": {
                "expert_id": self.consumer_expert_id,
                "expert_version": self.consumer_expert_version,
                "mapping": self.consumer_mapping,
                "economic_claim": False,
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StructureFactsRunRequest:
        expected = {
            "schema_version",
            "provider_config",
            "facts_policy",
            "execution_policy",
            "scan_start",
            "report_start",
            "report_end",
            "security_codes",
            "maximum_p2_probes",
            "p2_probe_quantity",
            "consumer",
        }
        if set(value) != expected or value.get("schema_version") != RUN_SCHEMA:
            raise ValueError("structure facts run request fields differ")
        codes = value.get("security_codes")
        consumer = value.get("consumer")
        if not isinstance(codes, list) or any(not isinstance(item, str) for item in codes):
            raise ValueError("structure facts security_codes are invalid")
        if not isinstance(consumer, dict) or consumer.get("economic_claim") is not False:
            raise ValueError("structure facts consumer contract is invalid")
        try:
            return cls(
                provider_config=str(value["provider_config"]),
                facts_policy=str(value["facts_policy"]),
                execution_policy=str(value["execution_policy"]),
                scan_start=date.fromisoformat(str(value["scan_start"])),
                report_start=date.fromisoformat(str(value["report_start"])),
                report_end=date.fromisoformat(str(value["report_end"])),
                security_codes=tuple(codes),
                maximum_p2_probes=int(value["maximum_p2_probes"]),
                p2_probe_quantity=int(value["p2_probe_quantity"]),
                consumer_expert_id=str(consumer.get("expert_id")),
                consumer_expert_version=str(consumer.get("expert_version")),
                consumer_mapping=str(consumer.get("mapping")),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("structure facts run request is invalid") from exc


def load_structure_facts_run_request(path: Path) -> StructureFactsRunRequest:
    return StructureFactsRunRequest.from_dict(_load_json(path, "structure facts run request"))


def _facts_policy_digest(path: Path) -> str:
    raw = _load_json(path, "continuous structure facts policy")
    if (
        raw.get("schema_version") != FACTS_ADM_VERSION
        or raw.get("adm_id") != FACTS_ADM_ID
        or raw.get("adm_version") != FACTS_ADM_VERSION
        or raw.get("status") != "development_only"
        or raw.get("research_only") is not True
        or raw.get("execution_authorized") is not False
    ):
        raise ValueError("continuous structure facts policy is not admitted")
    episode = raw.get("episode_policy")
    if not isinstance(episode, dict) or episode != {
        "creation": "t_close_strictly_above_prior_rolling_resistance20",
        "volume_role": "independent_fact_not_episode_gate",
        "frozen_breakout_level": True,
        "frozen_attempt_support_level": True,
        "hold": "later_close_at_or_above_frozen_breakout_level",
        "termination": "later_close_below_frozen_breakout_level",
        "same_row_restart_after_termination": False,
    }:
        raise ValueError("continuous structure episode policy differs")
    return _digest(raw)


@dataclass(frozen=True)
class StructureFactsDecision:
    market_date: date
    security_code: str
    episode_id: str
    breakout_level: Decimal
    breakout_margin: Decimal
    score: Decimal
    final_action: GovernedAction

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "security_code": self.security_code,
            "episode_id": self.episode_id,
            "breakout_level": str(self.breakout_level),
            "breakout_margin": str(self.breakout_margin),
            "score": str(self.score),
            "final_action": self.final_action.value,
        }


@dataclass(frozen=True)
class StructureFactsProbe:
    market_date: date
    security_code: str
    episode_id: str
    aggregate_score: Decimal
    execution_date: date
    fill_count: int
    result_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "security_code": self.security_code,
            "episode_id": self.episode_id,
            "aggregate_score": str(self.aggregate_score),
            "execution_date": self.execution_date.isoformat(),
            "fill_count": self.fill_count,
            "result_digest": self.result_digest,
        }


@dataclass(frozen=True)
class StructureFactsRunResult:
    request_digest: str
    provider_identity: Mapping[str, Any]
    adm: ContinuousStructureFactsAdm
    unavailable_counts: Mapping[str, int]
    fact_counts: Mapping[str, int]
    renderer_counts: Mapping[str, int]
    consumer_counts: Mapping[str, int]
    decisions: tuple[StructureFactsDecision, ...]
    probes: tuple[StructureFactsProbe, ...]

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RESULT_SCHEMA,
            "research_only": True,
            "execution_authorized": False,
            "economic_acceptance_claim": False,
            "coarse_renderer_authority": "explanation_only",
            "consumer_authority": "compatibility_proof_only",
            "request_digest": self.request_digest,
            "provider_identity": dict(self.provider_identity),
            "adm_manifest": self.adm.manifest,
            "unavailable_counts": dict(sorted(self.unavailable_counts.items())),
            "fact_counts": dict(sorted(self.fact_counts.items())),
            "renderer_counts": dict(sorted(self.renderer_counts.items())),
            "consumer_counts": dict(sorted(self.consumer_counts.items())),
            "decisions": [item.to_dict() for item in self.decisions],
            "p2_probe_semantics": "execution compatibility only; no portfolio/economic claim",
            "probes": [item.to_dict() for item in self.probes],
        }


def _decision_context(
    adm: ContinuousStructureFactsAdm,
    row: ContinuousStructureFactRow,
) -> ExpertContext:
    return ExpertContext(
        security_code=row.security_code,
        snapshot_id=row.snapshot_id,
        knowledge_cutoff=row.knowledge_cutoff,
        position_state="empty",
        feature_facts=continuous_structure_feature_facts(adm, row),
        label_results=(),
    )


def _evaluate_consumer(
    adm: ContinuousStructureFactsAdm,
) -> tuple[tuple[StructureFactsDecision, ...], dict[str, int]]:
    expert = ContinuousStructureFactsCompatibilityExpert()
    score_policy = ScoreActionPolicy(
        policy_version="structure-facts-compatibility@1.0.0",
        minimum_entry_score=Decimal("0"),
    )
    decisions: list[StructureFactsDecision] = []
    abstained = 0
    for row in adm.rows:
        context = _decision_context(adm, row)
        score = run_scoring_expert(expert, context)
        if score.applicability is ExpertApplicability.ABSTAINED:
            abstained += 1
            continue
        governance = aggregate_governance(
            context,
            (_system_veto(context),),
            (_base_constraint(context),),
        )
        action = aggregate_score_and_map_action(
            context,
            governance,
            (score,),
            score_policy,
        )
        if (
            row.episode_id is None
            or row.breakout_level is None
            or row.breakout_margin is None
        ):
            raise RuntimeError("applicable structure facts consumer lacks active episode facts")
        decisions.append(
            StructureFactsDecision(
                market_date=row.market_date,
                security_code=row.security_code,
                episode_id=row.episode_id,
                breakout_level=row.breakout_level,
                breakout_margin=row.breakout_margin,
                score=action.aggregate_score,
                final_action=action.final_action,
            )
        )
    counts = {
        "evaluated_rows": len(adm.rows),
        "applicable": len(decisions),
        "abstained": abstained,
        "trial_entry": sum(
            item.final_action is GovernedAction.TRIAL_ENTRY for item in decisions
        ),
    }
    return tuple(decisions), counts


def _probe_candidates(
    request: StructureFactsRunRequest,
    decisions: tuple[StructureFactsDecision, ...],
) -> tuple[StructureFactsDecision, ...]:
    buckets = {
        code: [
            item
            for item in decisions
            if item.security_code == code and item.final_action is GovernedAction.TRIAL_ENTRY
        ]
        for code in request.security_codes
    }
    selected: list[StructureFactsDecision] = []
    index = 0
    while len(selected) < request.maximum_p2_probes:
        added = False
        for code in request.security_codes:
            bucket = buckets[code]
            if index < len(bucket):
                selected.append(bucket[index])
                added = True
                if len(selected) >= request.maximum_p2_probes:
                    break
        if not added:
            break
        index += 1
    return tuple(selected)


def _run_p2_probes(
    *,
    request: StructureFactsRunRequest,
    adm: ContinuousStructureFactsAdm,
    frames_by_date: Mapping[date, Any],
    decisions: tuple[StructureFactsDecision, ...],
    repository_root: Path,
) -> tuple[StructureFactsProbe, ...]:
    if request.maximum_p2_probes == 0:
        return ()
    execution_policy = load_execution_policy(repository_root / request.execution_policy)
    engine = BacktestEngine(execution_policy)
    probes: list[StructureFactsProbe] = []
    rows_by_key = {(row.market_date, row.security_code): row for row in adm.rows}
    for index, decision in enumerate(_probe_candidates(request, decisions), start=1):
        row = rows_by_key[(decision.market_date, decision.security_code)]
        frame = frames_by_date.get(row.market_date)
        if frame is None:
            raise RuntimeError("structure facts P2 probe frame is missing")
        signal = BacktestSignal(
            signal_id=f"structure-facts-probe-{index:03d}",
            snapshot=frame.snapshot,
            decision_at=frame.decision_at,
            profile_id="close-to-next-open-v1",
            window_id=None,
            security_code=row.security_code,
            side=OrderSide.BUY,
            unit_id=f"structure-facts-unit-{index:03d}",
            quantity=request.p2_probe_quantity,
            aggregate_score=decision.score,
            reason_code="structure_facts_compatibility_probe",
            decision_version="structure-facts-compatibility@1.0.0",
        )
        run_spec = BacktestRunSpec(
            code_version="structure-facts-compatibility-probe",
            parameter_hash=request.digest,
            data_snapshot_id=row.snapshot_id,
            feature_version=f"{FACTS_ADM_ID}@{FACTS_ADM_VERSION}",
            rule_version="structure-facts-compatibility@1.0.0",
            run_id=f"structure-facts-probe-{index:03d}",
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
            StructureFactsProbe(
                market_date=row.market_date,
                security_code=row.security_code,
                episode_id=decision.episode_id,
                aggregate_score=decision.score,
                execution_date=frame.execution_date,
                fill_count=fill_count,
                result_digest=_digest(result.to_dict()),
            )
        )
    return tuple(probes)


def run_structure_facts(
    request: StructureFactsRunRequest,
    *,
    repository_root: Path | None = None,
    provider: ReplayDataProvider | None = None,
    identity_map: Mapping[str, str] | None = None,
) -> StructureFactsRunResult:
    root = (repository_root or find_project_root()).resolve()
    config: ReplayDataProviderConfig = load_replay_data_provider_config(
        root / request.provider_config
    )
    if config.scope.partition != "development":
        raise RuntimeError("structure facts may read development only")
    config.scope.authorize(request.scan_start, request.report_end)
    policy_digest = _facts_policy_digest(root / request.facts_policy)
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
            raise RuntimeError("structure facts governed identity map is incomplete")
        source_rows, frames_by_date, unavailable = _source_rows_from_session(
            session=session,
            request=request,  # type: ignore[arg-type]
            identity_map=mapping,
        )
        full_adm = build_continuous_structure_facts(
            source_rows,
            upstream_identity=str(session.identity["provider_lineage_digest"]),
            policy_digest=policy_digest,
        )
        published_rows = tuple(
            row
            for row in full_adm.rows
            if request.report_start <= row.market_date <= request.report_end
        )
        if not published_rows:
            raise RuntimeError("structure facts produced no report-scope rows")
        adm = ContinuousStructureFactsAdm(
            upstream_identity=full_adm.upstream_identity,
            policy_digest=full_adm.policy_digest,
            rows=published_rows,
        )
        renderer_counts: dict[str, int] = {}
        for row in adm.rows:
            rendered = render_coarse_structure_state(row).value
            renderer_counts[rendered] = renderer_counts.get(rendered, 0) + 1
        decisions, consumer_counts = _evaluate_consumer(adm)
        probes = _run_p2_probes(
            request=request,
            adm=adm,
            frames_by_date=frames_by_date,
            decisions=decisions,
            repository_root=root,
        )
        fact_counts = {
            "rows": len(adm.rows),
            "available": sum(row.available for row in adm.rows),
            "unavailable": sum(not row.available for row in adm.rows),
            "active_episode_rows": sum(row.episode_status == "active" for row in adm.rows),
            "ended_episode_rows": sum(row.episode_status == "ended" for row in adm.rows),
            "episode_attempt_rows": sum(
                row.episode_status == "active" and row.episode_age_sessions == 0
                for row in adm.rows
            ),
            "history_reset_rows": sum(row.history_reset for row in adm.rows),
        }
        provider_identity = {
            "provider_id": session.identity.get("provider_id"),
            "provider_version": session.identity.get("provider_version"),
            "provider_config_digest": session.identity.get("provider_config_digest"),
            "provider_lineage_digest": session.identity.get("provider_lineage_digest"),
            "materialized_content_digest": session.identity.get("materialized_content_digest"),
            "point_in_time": session.identity.get("point_in_time"),
        }
        return StructureFactsRunResult(
            request_digest=request.digest,
            provider_identity=provider_identity,
            adm=adm,
            unavailable_counts=unavailable,
            fact_counts=fact_counts,
            renderer_counts=renderer_counts,
            consumer_counts=consumer_counts,
            decisions=decisions,
            probes=probes,
        )


def write_structure_facts_result(
    result: StructureFactsRunResult,
    output_root: Path,
    *,
    code_revision: str,
) -> dict[str, Any]:
    if _CODE_REVISION.fullmatch(code_revision) is None:
        raise ValueError("structure facts code revision must be a 40-character git sha")
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    rows_payload = b"".join(_canonical_bytes(row.to_dict()) + b"\n" for row in result.adm.rows)
    rows_path = root / "structure-facts.jsonl"
    rows_path.write_bytes(rows_payload)
    summary = result.to_dict()
    summary["result_digest"] = result.digest
    summary["code_revision"] = code_revision
    summary_path = root / "summary.json"
    summary_path.write_bytes(_canonical_bytes(summary) + b"\n")
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "research_only": True,
        "execution_authorized": False,
        "code_revision": code_revision,
        "request_digest": result.request_digest,
        "files": {
            "structure-facts.jsonl": f"sha256:{hashlib.sha256(rows_payload).hexdigest()}",
            "summary.json": f"sha256:{hashlib.sha256(summary_path.read_bytes()).hexdigest()}",
        },
        "result_digest": result.digest,
        "adm_manifest_digest": result.adm.manifest_digest,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(_canonical_bytes(manifest) + b"\n")
    ready = {
        "schema_version": READY_SCHEMA,
        "manifest_sha256": f"sha256:{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}",
        "result_digest": result.digest,
    }
    (root / "READY").write_bytes(_canonical_bytes(ready) + b"\n")
    return {**manifest, "ready": ready, "artifact_ref": str(root)}


def verify_structure_facts_result(
    root: Path,
    *,
    expected_code_revision: str,
    expected_request_digest: str,
) -> dict[str, Any]:
    if _CODE_REVISION.fullmatch(expected_code_revision) is None:
        raise ValueError("structure facts expected code revision is invalid")
    resolved = root.resolve()
    manifest_path = resolved / "manifest.json"
    ready_path = resolved / "READY"
    summary_path = resolved / "summary.json"
    rows_path = resolved / "structure-facts.jsonl"
    manifest = _load_json(manifest_path, "structure facts manifest")
    ready = _load_json(ready_path, "structure facts READY")
    summary = _load_json(summary_path, "structure facts summary")
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("structure facts manifest schema differs")
    if (
        manifest.get("research_only") is not True
        or manifest.get("execution_authorized") is not False
    ):
        raise ValueError("structure facts manifest authority differs")
    if manifest.get("code_revision") != expected_code_revision:
        raise ValueError("structure facts code revision differs")
    if manifest.get("request_digest") != expected_request_digest:
        raise ValueError("structure facts request digest differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {"structure-facts.jsonl", "summary.json"}:
        raise ValueError("structure facts manifest files differ")
    for name, path in (("structure-facts.jsonl", rows_path), ("summary.json", summary_path)):
        payload = path.read_bytes()
        observed = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        if files.get(name) != observed:
            raise ValueError(f"structure facts file digest differs: {name}")
    manifest_sha = f"sha256:{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}"
    if (
        ready.get("schema_version") != READY_SCHEMA
        or ready.get("manifest_sha256") != manifest_sha
        or ready.get("result_digest") != manifest.get("result_digest")
    ):
        raise ValueError("structure facts READY differs")
    if (
        summary.get("schema_version") != RESULT_SCHEMA
        or summary.get("code_revision") != expected_code_revision
        or summary.get("request_digest") != expected_request_digest
        or summary.get("result_digest") != manifest.get("result_digest")
        or summary.get("economic_acceptance_claim") is not False
        or summary.get("coarse_renderer_authority") != "explanation_only"
    ):
        raise ValueError("structure facts summary identity differs")
    semantic_summary = dict(summary)
    semantic_summary.pop("result_digest", None)
    semantic_summary.pop("code_revision", None)
    if _digest(semantic_summary) != manifest.get("result_digest"):
        raise ValueError("structure facts semantic result digest differs")
    adm_manifest = summary.get("adm_manifest")
    if (
        not isinstance(adm_manifest, dict)
        or _digest(adm_manifest) != manifest.get("adm_manifest_digest")
    ):
        raise ValueError("structure facts ADM manifest digest differs")
    row_values = [
        json.loads(line)
        for line in rows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if (
        len(row_values) != int(adm_manifest.get("row_count", -1))
        or _digest(row_values) != adm_manifest.get("content_digest")
    ):
        raise ValueError("structure facts ADM semantic content differs")
    return {
        "artifact_ref": str(resolved),
        "manifest_sha256": manifest_sha,
        "result_digest": str(manifest["result_digest"]),
        "adm_manifest_digest": str(manifest["adm_manifest_digest"]),
        "row_count": int(adm_manifest["row_count"]),
        "fact_counts": dict(summary["fact_counts"]),
        "renderer_counts": dict(summary["renderer_counts"]),
        "consumer_counts": dict(summary["consumer_counts"]),
        "p2_probe_count": len(summary["probes"]),
        "p2_fill_count": sum(int(item["fill_count"]) for item in summary["probes"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Continuous Structure Facts v1 qualification")
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
    request = load_structure_facts_run_request(request_path)
    result = run_structure_facts(request, repository_root=root)
    publication = write_structure_facts_result(
        result,
        Path(args.output),
        code_revision=str(args.code_revision),
    )
    verified = verify_structure_facts_result(
        Path(args.output),
        expected_code_revision=str(args.code_revision),
        expected_request_digest=request.digest,
    )
    print(json.dumps({**publication, "verified": verified}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
