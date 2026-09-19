"""P3-STRAT-003-B fixed recent-history input qualification.

This module is deliberately input-only.  It verifies the frozen Base/correction/daily DWD closure,
builds one immutable HistoricalReplayBundle, and publishes outcome-blind P4 candidate identities.
It never evaluates Trend/Position Risk economics or target outcomes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.backtest.historical_bundle import (
    REQUIRED_HISTORICAL_REPLAY_DATASETS,
    HistoricalReplayBundle,
)
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.event_library.contracts import SplitAssignment
from quanthus.event_library.physical_scan import iter_projected_candidate_series
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import scan_forward_candidate_series
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)
from quanthus.expert_strategy.p3_strat_003 import (
    REQUEST_DIGEST,
    P3Strat003CandidateInput,
)
from quanthus.forward_validation.activation import load_prospective_runtime_config
from quanthus.forward_validation.controller import _visibility_from_sync_receipt
from quanthus.forward_validation.data_boundary import (
    ProspectiveDwdRelease,
    load_visibility_receipt,
    verify_prospective_dwd_release,
)
from quanthus.forward_validation.prospective_source import (
    P6ProspectiveVisibility,
    _build_bundle,
    _build_candidate_projection,
    load_daily_chain,
)
from quanthus.pipelines.securities_warehouse.daily_domain import (
    DailyDomainBuildError,
    verify_daily_domain_increment,
)
from quanthus.policy import find_project_root

REQUEST_SCHEMA = "quanthus.p3-strat-003-input-qualification.v1"
ARTIFACT_SCHEMA = "quanthus.p3-strat-003-input-artifact.v1"
BUNDLE_SCHEMA = "quanthus.p3-strat-003-sealed-replay-bundle.v1"
CANDIDATE_SCHEMA = "quanthus.p3-strat-003-candidate-input.v1"
PARTITION = "p3-strat-003-recent-history"
_SSE_VENUE = "venue_bec59e31eb6bb28111a10d7e6d2c689f"
_SZSE_VENUE = "venue_8c0bd9088f28e5e7fa8efdce87"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_input_unreadable",
            f"{label} is unreadable",
        ) from exc
    if not isinstance(value, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_input_invalid",
            f"{label} must be an object",
        )
    return value


def _safe_relative(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith("/"):
        raise ExpertEvaluationError(
            "p3_strat_003_input_invalid",
            f"{label} path must be relative",
        )
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ExpertEvaluationError(
            "p3_strat_003_input_invalid",
            f"{label} path escapes artifact root",
        )
    return path


def _request(path: Path) -> dict[str, Any]:
    raw = _object(path, "P3-STRAT-003-B request")
    if raw.get("schema_version") != REQUEST_SCHEMA or raw.get("work_item") != "P3-STRAT-003-B":
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "P3-STRAT-003-B request schema differs",
        )
    project = find_project_root(path)
    frozen = raw.get("request")
    source_runtime = raw.get("source_runtime")
    if not isinstance(frozen, dict) or not isinstance(source_runtime, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "input qualification bindings are incomplete",
        )
    request_path = project / str(frozen.get("path"))
    if (
        frozen.get("semantic_digest") != REQUEST_DIGEST
        or frozen.get("file_sha256") != _sha256(request_path)
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_drift",
            "frozen P3-STRAT-003 request differs",
        )
    runtime_path = project / str(source_runtime.get("path"))
    if source_runtime.get("file_sha256") != _sha256(runtime_path):
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_drift",
            "bound prospective runtime source differs",
        )
    if source_runtime.get("p6_authority_inherited") is not False:
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "B must not inherit P6 authority",
        )
    return raw


def _scope(raw: Mapping[str, Any]) -> tuple[date, date, date, date, int]:
    value = raw.get("scope")
    if not isinstance(value, dict):
        raise ExpertEvaluationError("p3_strat_003_input_request_invalid", "scope is missing")
    try:
        decision_start = date.fromisoformat(str(value["decision_start"]))
        decision_end = date.fromisoformat(str(value["decision_end"]))
        execution_end = date.fromisoformat(str(value["execution_end"]))
        target = date.fromisoformat(str(value["target_daily_market_date"]))
        warmup = int(value["warmup_open_sessions"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "scope fields are invalid",
        ) from exc
    if (
        (decision_start, decision_end, execution_end, target)
        != (date(2025, 1, 1), date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 18))
        or warmup != 60
        or value.get("roll_forward_authorized") is not False
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_input_scope_drift",
            "B scope differs from the frozen A request",
        )
    return decision_start, decision_end, execution_end, target, warmup


def _target_release(daily_root: Path, market_date: date) -> ProspectiveDwdRelease:
    packages = daily_root / "packages" / market_date.isoformat()
    candidates: list[ProspectiveDwdRelease] = []
    package_roots = (
        sorted(path for path in packages.iterdir() if path.is_dir())
        if packages.is_dir()
        else ()
    )
    for root in package_roots:
        try:
            verified = verify_daily_domain_increment(root)
            if verified.market_date != market_date:
                continue
            pointer = {
                "market_date": verified.market_date.isoformat(),
                "package_id": verified.package_id,
                "manifest_sha256": verified.manifest_sha256,
                "content_digest": verified.content_digest,
            }
            visibility = load_visibility_receipt(_visibility_from_sync_receipt(daily_root, pointer))
            candidates.append(verify_prospective_dwd_release(root, visibility))
        except (DailyDomainBuildError, ExpertEvaluationError, ValueError):
            continue
    if len(candidates) != 1:
        raise ExpertEvaluationError(
            "p3_strat_003_target_release_not_exact",
            f"expected one verified {market_date.isoformat()} daily release, got {len(candidates)}",
        )
    return candidates[0]


def _warmup_dates(base_root: Path, before: date, count: int) -> tuple[date, ...]:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ExpertEvaluationError("dependency_missing", "duckdb") from exc
    paths = sorted(
        (base_root / "payload/tradability_state").glob(
            "dwd_trading_calendar/**/*.parquet"
        )
    )
    if not paths:
        raise ExpertEvaluationError(
            "p3_strat_003_base_calendar_missing",
            "Base trading calendar is unavailable",
        )
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            """
            SELECT CAST(trade_date AS DATE) AS market_date
            FROM read_parquet(?, hive_partitioning=true)
            WHERE knowledge_to IS NULL
              AND is_open
              AND venue_id IN (?, ?)
              AND CAST(trade_date AS DATE) < ?
            GROUP BY market_date
            HAVING count(DISTINCT venue_id) = 2
            ORDER BY market_date DESC
            LIMIT ?
            """,
            [[str(path) for path in paths], _SSE_VENUE, _SZSE_VENUE, before, count],
        ).fetchall()
    finally:
        connection.close()
    dates = tuple(reversed([row[0] for row in rows]))
    if len(dates) != count or tuple(sorted(set(dates))) != dates:
        raise ExpertEvaluationError(
            "p3_strat_003_warmup_calendar_incomplete",
            "exact warm-up trading sessions could not be resolved",
        )
    return dates


@dataclass(frozen=True)
class _RecentVisibilityPolicy:
    history_start: date
    decision_start: date
    decision_end: date
    execution_end: date
    semantic_digest: str
    historical_visibility: P6ProspectiveVisibility = P6ProspectiveVisibility(
        projection_id="p3-strat-003-recent-history-effective-view-v1"
    )

    def require_stage_b_authority(self) -> None:
        return None

    def require_scan_date(self, value: date) -> None:
        if value < self.history_start or value > self.execution_end:
            raise ExpertEvaluationError(
                "p3_strat_003_scan_date_outside_scope",
                value.isoformat(),
            )

    def require_development_date(self, value: date) -> None:
        self.require_scan_date(value)

    def candidate_signal_date_allowed(self, value: date) -> bool:
        return self.decision_start <= value <= self.decision_end


@dataclass(frozen=True)
class _RecentSplitPolicy:
    history_start: date
    decision_start: date
    decision_end: date
    execution_end: date
    policy_id: str = "p3-strat-003-recent-history-split"
    policy_version: str = "1.0.0"

    @property
    def semantic_digest(self) -> str:
        return digest_value(
            {
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "history_start": self.history_start.isoformat(),
                "decision_start": self.decision_start.isoformat(),
                "decision_end": self.decision_end.isoformat(),
                "execution_end": self.execution_end.isoformat(),
                "2025_assignment": SplitAssignment.PERMANENT_BLIND.value,
                "2026_assignment": SplitAssignment.PROSPECTIVE.value,
            }
        )

    def require_stage_b_authority(self) -> None:
        return None

    def assign_event_split(
        self,
        *,
        signal_date: date,
        feature_start: date,
        outcome_end: date,
        trading_dates: Sequence[date],
    ) -> SplitAssignment:
        ordered = tuple(trading_dates)
        if tuple(sorted(set(ordered))) != ordered:
            raise ExpertEvaluationError(
                "p3_strat_003_split_calendar_invalid",
                "trading dates must be sorted and unique",
            )
        dates = set(ordered)
        if any(value not in dates for value in (feature_start, signal_date, outcome_end)):
            raise ExpertEvaluationError(
                "p3_strat_003_split_calendar_invalid",
                "event coordinate is not an admitted open session",
            )
        if not (
            self.history_start <= feature_start <= signal_date < outcome_end <= self.execution_end
            and self.decision_start <= signal_date <= self.decision_end
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_split_scope_invalid",
                "candidate event falls outside frozen scope",
            )
        return (
            SplitAssignment.PERMANENT_BLIND
            if signal_date.year == 2025
            else SplitAssignment.PROSPECTIVE
        )


def _bundle_manifest(bundle: Any, *, request_digest: str) -> dict[str, Any]:
    payload = {
        "schema_version": BUNDLE_SCHEMA,
        "work_item": "P3-STRAT-003-B",
        "request_digest": request_digest,
        "partition": PARTITION,
        "snapshot": {
            "market_date": bundle.snapshot.market_date.isoformat(),
            "knowledge_cutoff": bundle.snapshot.knowledge_cutoff.isoformat(),
            "source_revision": bundle.snapshot.source_revision,
            "snapshot_id": bundle.snapshot.snapshot_id,
        },
        "universe_policy_version": bundle.universe_policy_version,
        "datasets": [item.to_dict() for item in bundle.datasets],
        "bundle_digest": bundle.digest,
    }
    return {**payload, "semantic_digest": digest_value(payload)}


def _candidate_manifest(
    *,
    root: Path,
    request_digest: str,
    bundle_digest: str,
    decision_start: date,
    decision_end: date,
    execution_end: date,
    events_path: Path,
    industry_path: Path,
    event_count: int,
) -> dict[str, Any]:
    payload = {
        "schema_version": CANDIDATE_SCHEMA,
        "work_item": "P3-STRAT-003-B",
        "request_digest": request_digest,
        "scope": {
            "decision_start": decision_start.isoformat(),
            "decision_end": decision_end.isoformat(),
            "execution_end": execution_end.isoformat(),
        },
        "replay_bundle_digest": bundle_digest,
        "events": {
            "path": events_path.relative_to(root).as_posix(),
            "sha256": _sha256(events_path),
            "count": event_count,
        },
        "industry_mapping": {
            "path": industry_path.relative_to(root).as_posix(),
            "sha256": _sha256(industry_path),
        },
    }
    return {**payload, "semantic_digest": digest_value(payload)}


def build_input_artifact(
    *,
    request_path: Path,
    output: Path,
    code_revision: str,
) -> dict[str, Any]:
    request_path = request_path.resolve()
    project = find_project_root(request_path)
    raw = _request(request_path)
    decision_start, decision_end, execution_end, target_date, warmup_count = _scope(raw)
    runtime_path = project / str(raw["source_runtime"]["path"])
    runtime = load_prospective_runtime_config(runtime_path, root=project)
    output = output.resolve()
    for source_root in (
        runtime.historical_base_root,
        runtime.correction_root,
        runtime.daily_root,
    ):
        if output == source_root.resolve() or output.is_relative_to(source_root.resolve()):
            raise ExpertEvaluationError(
                "p3_strat_003_input_isolation_invalid",
                "qualification output must be outside every read-only input root",
            )
    target = _target_release(runtime.daily_root, target_date)
    chain = load_daily_chain(config=runtime, target=target)
    warmup_dates = _warmup_dates(runtime.historical_base_root, decision_start, warmup_count)
    history_start = warmup_dates[0]

    if output.exists():
        return verify_p3_strat_003_input_artifact(
            output,
            code_revision=code_revision,
            request_digest=digest_value(raw),
        )
    output.mkdir(parents=True)
    replay_root = output / "replay"
    bundle = _build_bundle(
        config=runtime,
        target=target,
        chain=chain,
        history_start=history_start,
        output_root=replay_root,
    )
    bundle_manifest = _bundle_manifest(bundle, request_digest=REQUEST_DIGEST)
    (replay_root / "bundle.json").write_bytes(canonical_json_bytes(bundle_manifest) + b"\n")

    with HistoricalReplayFrameReader(bundle) as reader:
        audit = reader.audit(decision_start, decision_end)
    if (
        audit.first_decision_date < decision_start
        or audit.last_decision_date > decision_end
        or audit.last_execution_date != execution_end
        or not audit.ready_for_strategy_replay
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_replay_audit_failed",
            "recent-history replay audit does not match the frozen scope",
        )

    candidate_daily = output / "candidate-daily.parquet"
    trading_dates, industry = _build_candidate_projection(
        config=runtime,
        bundle=bundle,
        history_start=history_start,
        target_date=execution_end,
        output=candidate_daily,
    )
    source_policy = _RecentVisibilityPolicy(
        history_start,
        decision_start,
        decision_end,
        execution_end,
        digest_value(
            {
                "request_digest": REQUEST_DIGEST,
                "bundle_digest": bundle.digest,
                "history_start": history_start.isoformat(),
                "decision_start": decision_start.isoformat(),
                "decision_end": decision_end.isoformat(),
                "execution_end": execution_end.isoformat(),
            }
        ),
    )
    split_policy = _RecentSplitPolicy(
        history_start,
        decision_start,
        decision_end,
        execution_end,
    )
    card = load_candidate_rule_card(
        project / "config/p4-candidate-price-volume-transition.research.v1.json"
    )

    class _Projection:
        def __init__(self, path: Path) -> None:
            self.candidate_path = path

    scan = scan_forward_candidate_series(
        iter_projected_candidate_series(
            _Projection(candidate_daily),  # type: ignore[arg-type]
            source_policy=source_policy,  # type: ignore[arg-type]
            industry_mapping=industry,
        ),
        trading_dates=trading_dates,
        snapshot_id=bundle.snapshot.snapshot_id,
        snapshot_manifest_digest=target.manifest_sha256,
        card=card,
        split_policy=split_policy,  # type: ignore[arg-type]
        source_policy=source_policy,  # type: ignore[arg-type]
    )
    missing_industry = int(scan.counters.reason_counts.get("static_industry_missing", 0))
    if missing_industry:
        raise ExpertEvaluationError(
            "p3_strat_003_industry_mapping_incomplete",
            str(missing_industry),
        )

    candidate_root = output / "candidate-input"
    candidate_root.mkdir()
    events_path = candidate_root / "events.jsonl"
    with events_path.open("wb") as stream:
        for item in scan.candidates:
            stream.write(
                canonical_json_bytes(
                    {
                        "event": item.record.to_dict(),
                        "listing_code": item.listing_code,
                        "static_level1_industry_id": item.static_level1_industry_id,
                    }
                )
                + b"\n"
            )
    industry_path = candidate_root / "static_industry_mapping.json"
    shutil.copy2(industry.path, industry_path)
    candidate_manifest = _candidate_manifest(
        root=candidate_root,
        request_digest=REQUEST_DIGEST,
        bundle_digest=bundle.digest,
        decision_start=decision_start,
        decision_end=decision_end,
        execution_end=execution_end,
        events_path=events_path,
        industry_path=industry_path,
        event_count=len(scan.candidates),
    )
    candidate_manifest_path = candidate_root / "manifest.json"
    candidate_manifest_path.write_bytes(canonical_json_bytes(candidate_manifest) + b"\n")

    summary = {
        "schema_version": "quanthus.p3-strat-003-input-summary.v1",
        "work_item": "P3-STRAT-003-B",
        "request_digest": REQUEST_DIGEST,
        "code_revision": code_revision,
        "scope": {
            "warmup_start": history_start.isoformat(),
            "warmup_sessions": len(warmup_dates),
            "warmup_dates": [value.isoformat() for value in warmup_dates],
            "decision_start": decision_start.isoformat(),
            "decision_end": decision_end.isoformat(),
            "execution_end": execution_end.isoformat(),
        },
        "target_release": {
            "market_date": target.market_date.isoformat(),
            "package_id": target.package_id,
            "manifest_sha256": target.manifest_sha256,
            "content_digest": target.content_digest,
            "visibility_receipt_digest": target.visibility.receipt_digest,
        },
        "daily_chain": [
            {
                "market_date": item.market_date.isoformat(),
                "package_id": item.package_id,
                "manifest_sha256": item.manifest_sha256,
                "content_digest": item.content_digest,
                "code_revision": item.code_revision,
            }
            for item in chain
        ],
        "deferred_daily_artifact_contract": {
            "successful_ready_package_requires_deferred_daily_artifacts_empty": True,
            "daily_package_count": len(chain),
        },
        "replay_audit": audit.to_dict(),
        "candidate_scan": {
            "evaluated_rows": scan.counters.evaluated_rows,
            "raw_triggers": scan.counters.raw_triggers,
            "isolated_candidates": scan.counters.isolated_candidates,
            "concentration_retained": scan.counters.concentration_retained,
            "candidate_events": scan.counters.candidate_events,
            "reason_counts": dict(sorted(scan.counters.reason_counts.items())),
        },
        "bundle_digest": bundle.digest,
        "bundle_manifest_digest": bundle_manifest["semantic_digest"],
        "candidate_manifest_digest": candidate_manifest["semantic_digest"],
        "strategy_or_economic_result_count": 0,
        "outcome_label_count": 0,
        "p2_probe_count": 0,
        "p2_fill_count": 0,
        "p6_mutation": False,
    }
    summary_path = output / "summary.json"
    summary_path.write_bytes(canonical_json_bytes(summary) + b"\n")

    files = {
        "replay_bundle": "replay/bundle.json",
        "candidate_manifest": "candidate-input/manifest.json",
        "summary": "summary.json",
    }
    manifest_payload = {
        "schema_version": ARTIFACT_SCHEMA,
        "work_item": "P3-STRAT-003-B",
        "request_digest": digest_value(raw),
        "frozen_research_request_digest": REQUEST_DIGEST,
        "code_revision": code_revision,
        "partition": PARTITION,
        "scope": summary["scope"],
        "files": {
            name: {"path": relative, "sha256": _sha256(output / relative)}
            for name, relative in sorted(files.items())
        },
        "bundle_digest": bundle.digest,
        "candidate_manifest_digest": candidate_manifest["semantic_digest"],
        "row_count": audit.decision_observation_count,
        "candidate_event_count": scan.counters.candidate_events,
        "execution_authorized": False,
        "strategy_result_produced": False,
        "outcome_labels_produced": False,
        "p6_mutation": False,
    }
    manifest = {
        **manifest_payload,
        "result_digest": digest_value(manifest_payload),
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
    (output / "READY").write_text(_sha256(manifest_path) + "\n", encoding="ascii")
    return verify_p3_strat_003_input_artifact(
        output,
        code_revision=code_revision,
        request_digest=digest_value(raw),
    )


def _verify_replay_bundle(
    root: Path,
    *,
    request_digest: str,
) -> HistoricalReplayBundle:
    root = root.resolve()
    manifest = _object(root / "bundle.json", "P3-STRAT-003-B replay bundle")
    if (
        manifest.get("schema_version") != BUNDLE_SCHEMA
        or manifest.get("work_item") != "P3-STRAT-003-B"
        or manifest.get("request_digest") != request_digest
        or manifest.get("partition") != PARTITION
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_replay_bundle_invalid",
            "replay bundle identity differs",
        )
    payload = {key: value for key, value in manifest.items() if key != "semantic_digest"}
    if manifest.get("semantic_digest") != digest_value(payload):
        raise ExpertEvaluationError(
            "p3_strat_003_replay_bundle_drift",
            "replay bundle manifest digest differs",
        )
    snapshot = manifest.get("snapshot")
    datasets_raw = manifest.get("datasets")
    if not isinstance(snapshot, dict) or not isinstance(datasets_raw, list):
        raise ExpertEvaluationError(
            "p3_strat_003_replay_bundle_invalid",
            "replay bundle closure is incomplete",
        )
    try:
        snapshot_ref = SnapshotRef(
            market_date=date.fromisoformat(str(snapshot["market_date"])),
            knowledge_cutoff=datetime.fromisoformat(str(snapshot["knowledge_cutoff"])),
            source_revision=str(snapshot["source_revision"]),
            snapshot_id=str(snapshot["snapshot_id"]),
        )
        datasets = tuple(DatasetManifest.from_dict(item) for item in datasets_raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_replay_bundle_invalid",
            "replay bundle closure cannot be decoded",
        ) from exc
    if {item.dataset for item in datasets} != REQUIRED_HISTORICAL_REPLAY_DATASETS:
        raise ExpertEvaluationError(
            "p3_strat_003_replay_bundle_invalid",
            "replay bundle dataset closure differs",
        )
    for dataset in datasets:
        if set(dataset.partitions) != set(dataset.content_checksums):
            raise ExpertEvaluationError(
                "p3_strat_003_replay_bundle_invalid",
                f"dataset partition bindings differ: {dataset.dataset}",
            )
        for relative, expected in dataset.content_checksums.items():
            path = _safe_relative(root, relative, dataset.dataset)
            if not path.is_file() or _sha256(path) != expected:
                raise ExpertEvaluationError(
                    "p3_strat_003_replay_bundle_drift",
                    f"dataset checksum differs: {dataset.dataset}",
                )
    bundle = HistoricalReplayBundle(
        root=root,
        snapshot=snapshot_ref,
        universe_policy_version=str(manifest.get("universe_policy_version") or ""),
        datasets=datasets,
    )
    if manifest.get("bundle_digest") != bundle.digest:
        raise ExpertEvaluationError(
            "p3_strat_003_replay_bundle_drift",
            "replay bundle semantic digest differs",
        )
    return bundle


def verify_p3_strat_003_input_artifact(
    root: Path,
    *,
    code_revision: str,
    request_digest: str,
) -> dict[str, Any]:
    root = root.resolve()
    manifest = _object(root / "manifest.json", "P3-STRAT-003-B manifest")
    summary = _object(root / "summary.json", "P3-STRAT-003-B summary")
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_input_unreadable",
            "P3-STRAT-003-B READY is unreadable",
        ) from exc
    if (
        ready != _sha256(root / "manifest.json")
        or manifest.get("schema_version") != ARTIFACT_SCHEMA
        or manifest.get("work_item") != "P3-STRAT-003-B"
        or manifest.get("request_digest") != request_digest
        or manifest.get("code_revision") != code_revision
        or manifest.get("partition") != PARTITION
        or manifest.get("strategy_result_produced") is not False
        or manifest.get("outcome_labels_produced") is not False
        or manifest.get("p6_mutation") is not False
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_input_artifact_invalid",
            "P3-STRAT-003-B manifest boundary differs",
        )
    payload = {key: value for key, value in manifest.items() if key != "result_digest"}
    if manifest.get("result_digest") != digest_value(payload):
        raise ExpertEvaluationError(
            "p3_strat_003_input_artifact_drift",
            "P3-STRAT-003-B result digest differs",
        )
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_input_artifact_invalid",
            "P3-STRAT-003-B file bindings are missing",
        )
    for label, binding in files.items():
        if not isinstance(binding, dict):
            raise ExpertEvaluationError(
                "p3_strat_003_input_artifact_invalid",
                f"{label} binding is invalid",
            )
        path = _safe_relative(root, binding.get("path"), label)
        if _sha256(path) != binding.get("sha256"):
            raise ExpertEvaluationError(
                "p3_strat_003_input_artifact_drift",
                f"{label} checksum differs",
            )
    replay_bundle = _verify_replay_bundle(
        root / "replay",
        request_digest=REQUEST_DIGEST,
    )
    candidate_input = P3Strat003CandidateInput.load(
        root / "candidate-input/manifest.json",
        request_digest=REQUEST_DIGEST,
        decision_start=date(2025, 1, 1),
        decision_end=date(2026, 9, 17),
        execution_end=date(2026, 9, 18),
    )
    if (
        summary.get("strategy_or_economic_result_count") != 0
        or summary.get("outcome_label_count") != 0
        or summary.get("p6_mutation") is not False
        or summary.get("candidate_manifest_digest") != manifest.get("candidate_manifest_digest")
        or summary.get("bundle_digest") != manifest.get("bundle_digest")
        or replay_bundle.digest != manifest.get("bundle_digest")
        or candidate_input.manifest_digest != manifest.get("candidate_manifest_digest")
        or candidate_input.replay_bundle_digest != replay_bundle.digest
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_input_artifact_invalid",
            "input-only summary boundary differs",
        )
    return {
        "artifact_ref": str(root),
        "manifest_sha256": _sha256(root / "manifest.json"),
        "result_digest": str(manifest["result_digest"]),
        "request_digest": request_digest,
        "bundle_digest": str(manifest["bundle_digest"]),
        "candidate_manifest_digest": str(manifest["candidate_manifest_digest"]),
        "candidate_manifest_path": str(
            _safe_relative(root, files["candidate_manifest"]["path"], "candidate_manifest")
        ),
        "row_count": int(manifest["row_count"]),
        "candidate_event_count": int(manifest["candidate_event_count"]),
        "p2_probe_count": 0,
        "p2_fill_count": 0,
        "execution_authorized": False,
        "strategy_result_produced": False,
        "outcome_labels_produced": False,
        "p6_mutation": False,
    }


def _parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Qualify P3-STRAT-003 fixed recent-history input")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if len(args.code_revision) != 40 or any(
        ch not in "0123456789abcdef" for ch in args.code_revision
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_input_code_revision_invalid",
            "code revision must be a full lowercase Git object id",
        )
    result = build_input_artifact(
        request_path=args.request,
        output=args.output,
        code_revision=args.code_revision,
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
