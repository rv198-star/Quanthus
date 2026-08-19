"""Batch runner that binds candidate signals to Quanthus DWD/PIT and P2 replay."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from quanthus.backtest.contracts import BacktestRunSpec
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.backtest.strategy_replay import run_strategy_replay
from quanthus.candidate_strategy.contract import CandidateManifest
from quanthus.candidate_strategy.quantdinger_adapter import build_quantdinger_adapter


@dataclass(frozen=True)
class CandidateReplayEvidence:
    payload: Mapping[str, Any]

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload, "evidence_digest": self.digest}


def run_candidate_replay(
    *,
    reader: HistoricalReplayFrameReader,
    engine: BacktestEngine,
    manifest: CandidateManifest,
    scope_start: date,
    scope_end: date,
    initial_cash: Decimal,
    code_version: str,
) -> CandidateReplayEvidence:
    if manifest.execution_authorized:
        raise ValueError("candidate replay cannot carry execution authority")
    frame_audit = reader.audit(scope_start, scope_end)
    strategy = build_quantdinger_adapter(manifest)
    run = BacktestRunSpec(
        code_version=code_version,
        parameter_hash=manifest.digest,
        data_snapshot_id=reader.bundle.snapshot.snapshot_id,
        data_content_digest=reader.data_content_digest,
        data_manifest_sha256=reader.data_manifest_sha256,
        feature_version=manifest.adapter_contract,
        rule_version=manifest.candidate_version,
        run_id=f"candidate-{manifest.candidate_id}-{scope_start}-{scope_end}",
        execution_policy_version=engine.policy.policy_version,
        execution_profile_id="close-to-next-open-v1",
        schedule_version=engine.policy.profile("close-to-next-open-v1").schedule_version,
        official=False,
    )
    result = run_strategy_replay(
        engine,
        strategy,
        reader.build_replay_spec(
            run=run,
            unit_id="candidate-unit-001",
            initial_cash=initial_cash,
            scope_start=scope_start,
            scope_end=scope_end,
        ),
    )
    decision_counts: dict[str, int] = {}
    for decision in result.decisions:
        decision_counts[decision.action.value] = decision_counts.get(decision.action.value, 0) + 1
    payload = {
        "schema_version": "quanthus.candidate-replay-evidence.v1",
        "candidate": {
            "candidate_id": manifest.candidate_id,
            "candidate_version": manifest.candidate_version,
            "manifest_digest": manifest.digest,
            "source_repository": manifest.source_repository,
            "source_commit": manifest.source_commit,
            "source_template_key": manifest.source_template_key,
            "source_symbol": manifest.source_symbol,
            "quanthus_security_id": manifest.security_code,
        },
        "pipeline": {
            "state": "replayed",
            "governance_decision": "pending-review",
            "research_only": True,
            "execution_authorized": False,
            "quantdinger_runtime_used": False,
            "quantdinger_fill_model_used": False,
            "quanthus_p2_referee_used": True,
        },
        "scope": {"start": scope_start.isoformat(), "end": scope_end.isoformat()},
        "data": {
            "bundle_snapshot_id": frame_audit.bundle_snapshot_id,
            "bundle_digest": frame_audit.bundle_digest,
            "frame_audit_digest": frame_audit.digest,
            "point_in_time": True,
        },
        "result": {
            "run_id": result.backtest.run.run_id,
            "result_digest": f"sha256:{hashlib.sha256(result.canonical_bytes()).hexdigest()}",
            "decision_counts": decision_counts,
            "closed_trades": result.performance.closed_trades,
            "total_return": str(result.performance.total_return),
            "maximum_drawdown": str(result.performance.maximum_drawdown),
            "fills": result.backtest.result_summary.get("fills", 0),
            "pending_exit": result.pending_exit,
        },
        "replay_artifact": result.to_dict(),
    }
    return CandidateReplayEvidence(payload)
