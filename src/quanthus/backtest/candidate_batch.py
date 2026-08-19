"""Run the governed, baseline, and external candidate families in one replay session."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus import __version__
from quanthus.backtest.baseline_research import (
    BaselineFeaturePanel,
    load_baseline_strategy_catalogue,
    run_baseline_strategy_suite,
)
from quanthus.backtest.data_provider import ReplayDataProvider
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.policy import load_execution_policy
from quanthus.backtest.portfolio_replay import run_governed_portfolio_replay
from quanthus.candidate_strategy import load_candidate_manifest, run_candidate_replay
from quanthus.feature_registry import load_feature_registry
from quanthus.governed_strategy import load_governed_feature_registry
from quanthus.label_registry import load_label_registry
from quanthus.policy import find_project_root, load_policy
from quanthus.strategy_policy import load_governed_strategy_policy

CANDIDATE_BATCH_SCHEMA = "quanthus.candidate-replay-batch.v1"
CANDIDATE_BATCH_RUNNER_VERSION = "1.0.0"
PROGRESS_ERROR_MESSAGE_LIMIT = 500


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _write_evidence(
    root: Path,
    candidate_id: str,
    candidate_version: str,
    result_digest: str,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=True)
    relative = f"{candidate_id}-{candidate_version}.json"
    path = root / relative
    encoded = (
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.write_bytes(encoded)
    return {
        "candidate_id": candidate_id,
        "candidate_version": candidate_version,
        "result_digest": result_digest,
        "artifact": f"evidence/{relative}",
        "artifact_sha256": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
    }


@dataclass(frozen=True)
class CandidateBatchResult:
    payload: Mapping[str, Any]

    @property
    def digest(self) -> str:
        return _digest(self.payload)

    @property
    def succeeded(self) -> bool:
        return self.payload.get("status") == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload, "batch_digest": self.digest}


def _failure(candidate_id: str, version: str, runner: str, exc: Exception) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "candidate_version": version,
        "runner": runner,
        "status": "failed",
        "pipeline_state": "validation-failed",
        "governance_decision": "pending-review",
        "execution_authorized": False,
        "error": {"type": type(exc).__name__, "message": str(exc)},
    }


def _failure_progress(exc: Exception) -> dict[str, str]:
    message = str(exc).strip()
    return {
        "error_type": type(exc).__name__,
        "error_message": message[:PROGRESS_ERROR_MESSAGE_LIMIT],
    }


def _baseline_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = []
    for strategy in payload["strategies"]:
        performance = strategy["performance"]
        items.append(
            {
                "candidate_id": strategy["strategy_id"],
                "candidate_version": strategy["strategy_version"],
                "runner": "baseline-suite",
                "status": "succeeded",
                "pipeline_state": "replayed",
                "governance_decision": "pending-review",
                "execution_authorized": False,
                "result_digest": strategy["result_digest"],
                "summary": {
                    "operations": strategy["operation_count"],
                    "closed_trades": strategy["closed_trade_count"],
                    "total_return": performance["total_return"],
                    "maximum_drawdown": performance["maximum_drawdown"],
                },
            }
        )
    return items


def _governed_item(payload: Mapping[str, Any]) -> dict[str, Any]:
    performance = payload["performance"]
    return {
        "candidate_id": payload["strategy_id"],
        "candidate_version": payload["strategy_version"],
        "runner": "governed-portfolio",
        "status": "succeeded",
        "pipeline_state": "replayed",
        "governance_decision": "pending-review",
        "execution_authorized": False,
        "result_digest": payload["digest"],
        "summary": {
            "operations": performance["operation_count"],
            "closed_trades": performance["closed_trades"],
            "total_return": performance["total_return"],
            "maximum_drawdown": performance["maximum_drawdown"],
        },
    }


def _external_item(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidate = payload["candidate"]
    result = payload["result"]
    return {
        "candidate_id": candidate["candidate_id"],
        "candidate_version": candidate["candidate_version"],
        "runner": "external-candidate-adapter",
        "status": "succeeded",
        "pipeline_state": "replayed",
        "governance_decision": "pending-review",
        "execution_authorized": False,
        "result_digest": result["result_digest"],
        "summary": {
            "operations": result["fills"],
            "closed_trades": result["closed_trades"],
            "total_return": result["total_return"],
            "maximum_drawdown": result["maximum_drawdown"],
        },
    }


def run_candidate_batch(
    provider: ReplayDataProvider,
    *,
    scope_start: date,
    scope_end: date,
    feature_panel: Path | None = None,
    evidence_dir: Path | None = None,
    project_root: Path | None = None,
    now: Callable[[], datetime] | None = None,
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> CandidateBatchResult:
    def report(stage: str, **details: Any) -> None:
        if progress is not None:
            progress(stage, details)

    root = (project_root or find_project_root()).resolve()
    report("loading-contracts")
    baseline_catalogue = load_baseline_strategy_catalogue(
        root / "config" / "p3-baseline-strategies.research.v2.json"
    )
    baseline_registry = load_feature_registry(
        root / "config" / "p3-baseline-features.research.v1.json"
    )
    governed_registry = load_governed_feature_registry(
        root / "config" / "p3-governed-feature-extension.research.v1.json"
    )
    label_registry = load_label_registry(
        root / "config" / "p3-governed-labels.research.v1.json"
    )
    governed_policy = load_governed_strategy_policy(
        root / "config" / "p3-governed-strategy.research.v1.json"
    )
    external_manifest = load_candidate_manifest(
        root / "config" / "quantdinger-candidates" / "bullish-three-lines.v1.json"
    )
    engine = BacktestEngine(
        load_execution_policy(root / "config" / "p2-execution-policy.research.v1.json")
    )
    expected = [
        (item.strategy_id, item.strategy_version) for item in baseline_catalogue.strategies
    ] + [
        (governed_policy.strategy_id, governed_policy.strategy_version),
        (external_manifest.candidate_id, external_manifest.candidate_version),
    ]
    report("contracts-loaded", candidate_count=len(expected))
    results: dict[str, dict[str, Any]] = {}
    evidence_index: dict[str, dict[str, str]] = {}
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if feature_panel is None:
        temporary = tempfile.TemporaryDirectory(prefix="quanthus-candidate-batch-")
        panel_path = Path(temporary.name) / "shared-features.parquet"
    else:
        panel_path = feature_panel.resolve()
        panel_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        report("opening-data-provider", provider_id=provider.config.provider_id)
        with provider.open(
            scope_start=scope_start,
            scope_end=scope_end,
            progress=lambda stage, details: report(stage, **details),
        ) as session:
            report(
                "data-provider-opened",
                provider_id=session.identity["provider_id"],
                mode=session.identity.get("mode", "shared-replay-projection"),
            )
            panel_scope_end = session.reader.frames(scope_end, scope_end)[0].execution_date
            report("building-shared-feature-panel")
            with BaselineFeaturePanel.build(
                session.reader.bundle,
                baseline_registry,
                scope_start=scope_start,
                scope_end=panel_scope_end,
                output=panel_path,
                data_content_digest=session.reader.data_content_digest,
                data_manifest_sha256=session.reader.data_manifest_sha256,
            ) as panel:
                report("shared-feature-panel-ready", row_count=panel.report.row_count)
                benchmark = session.reader.load_benchmark()
                industry = session.reader.load_industry_mapping(
                    expected_version=governed_policy.bindings.industry_mapping_version
                )
                try:
                    baseline = run_baseline_strategy_suite(
                        session.reader,
                        engine,
                        baseline_catalogue,
                        baseline_registry,
                        panel,
                        benchmark,
                        scope_start=scope_start,
                        scope_end=scope_end,
                    )
                    for item in _baseline_items(baseline.to_dict()):
                        results[item["candidate_id"]] = item
                    if evidence_dir is not None:
                        for strategy in baseline.strategies:
                            evidence_index[strategy.strategy_id] = _write_evidence(
                                evidence_dir,
                                strategy.strategy_id,
                                strategy.strategy_version,
                                results[strategy.strategy_id]["result_digest"],
                                strategy.to_dict(),
                            )
                    report("baseline-suite-complete", candidate_count=3)
                except Exception as exc:
                    for definition in baseline_catalogue.strategies:
                        results[definition.strategy_id] = _failure(
                            definition.strategy_id,
                            definition.strategy_version,
                            "baseline-suite",
                            exc,
                        )
                    report("baseline-suite-failed", **_failure_progress(exc))
                try:
                    governed = run_governed_portfolio_replay(
                        session.reader,
                        engine,
                        panel,
                        governed_registry,
                        label_registry,
                        governed_policy,
                        load_policy(root / "config" / "p0-policy.v1.json"),
                        industry,
                        benchmark,
                        scope_start=scope_start,
                        scope_end=scope_end,
                    )
                    item = _governed_item(governed.to_dict())
                    results[item["candidate_id"]] = item
                    if evidence_dir is not None:
                        evidence_index[item["candidate_id"]] = _write_evidence(
                            evidence_dir,
                            item["candidate_id"],
                            item["candidate_version"],
                            item["result_digest"],
                            governed.to_dict(),
                        )
                    report("governed-candidate-complete", candidate_count=1)
                except Exception as exc:
                    results[governed_policy.strategy_id] = _failure(
                        governed_policy.strategy_id,
                        governed_policy.strategy_version,
                        "governed-portfolio",
                        exc,
                    )
                    report("governed-candidate-failed", **_failure_progress(exc))
                try:
                    external = run_candidate_replay(
                        reader=session.reader,
                        engine=engine,
                        manifest=external_manifest,
                        scope_start=scope_start,
                        scope_end=scope_end,
                        initial_cash=Decimal("1000000.00"),
                        code_version=__version__,
                    )
                    item = _external_item(external.to_dict())
                    results[item["candidate_id"]] = item
                    if evidence_dir is not None:
                        evidence_index[item["candidate_id"]] = _write_evidence(
                            evidence_dir,
                            item["candidate_id"],
                            item["candidate_version"],
                            item["result_digest"],
                            external.to_dict(),
                        )
                    report("external-candidate-complete", candidate_count=1)
                except Exception as exc:
                    results[external_manifest.candidate_id] = _failure(
                        external_manifest.candidate_id,
                        external_manifest.candidate_version,
                        "external-candidate-adapter",
                        exc,
                    )
                    report("external-candidate-failed", **_failure_progress(exc))
            ordered = [results[candidate_id] for candidate_id, _version in expected]
            failed = sum(item["status"] == "failed" for item in ordered)
            created_at = (now or (lambda: datetime.now(timezone.utc)))().isoformat()
            request = {
                "scope": {"start": scope_start.isoformat(), "end": scope_end.isoformat()},
                "candidate_versions": [
                    {"candidate_id": candidate_id, "candidate_version": version}
                    for candidate_id, version in expected
                ],
                "provider_config_digest": provider.config.digest,
                "runner_version": CANDIDATE_BATCH_RUNNER_VERSION,
            }
            payload = {
                "schema_version": CANDIDATE_BATCH_SCHEMA,
                "batch_id": f"candidate-batch-{_digest(request).split(':', 1)[1][:16]}",
                "created_at": created_at,
                "status": "succeeded" if failed == 0 else "failed",
                "research_only": True,
                "execution_authorized": False,
                "governance_transition_applied": False,
                "runner_version": CANDIDATE_BATCH_RUNNER_VERSION,
                "scope": request["scope"],
                "data_provider": dict(session.identity),
                "candidate_count": len(ordered),
                "succeeded_count": len(ordered) - failed,
                "failed_count": failed,
                "candidates": ordered,
                "evidence_index": [
                    evidence_index[candidate_id]
                    for candidate_id, _version in expected
                    if candidate_id in evidence_index
                ],
            }
            if evidence_dir is not None and len(payload["evidence_index"]) != len(ordered):
                payload["status"] = "failed"
                payload["failed_count"] = max(1, int(payload["failed_count"]))
            report(
                "batch-complete",
                status=payload["status"],
                succeeded_count=payload["succeeded_count"],
                failed_count=payload["failed_count"],
            )
            return CandidateBatchResult(payload)
    finally:
        if temporary is not None:
            temporary.cleanup()
