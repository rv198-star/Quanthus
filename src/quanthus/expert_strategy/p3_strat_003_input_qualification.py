"""P3-STRAT-003-B fixed recent-history input qualification.

This module is deliberately input-only.  It verifies the frozen Base/correction/daily DWD closure,
builds one immutable HistoricalReplayBundle, and publishes outcome-blind P4 candidate identities.
It never evaluates Trend/Position Risk economics or target outcomes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    P6DailyChainPackage,
    P6ProspectiveVisibility,
    _build_bundle,
    _build_candidate_projection,
    _verify_seed,
)
from quanthus.pipelines.contracts import OdsRelease, OdsReleaseError
from quanthus.pipelines.ods_handoff import (
    OdsHandoffError,
    load_ods_source_config,
    resolve_ods_source,
)
from quanthus.pipelines.ods_handoff import (
    _attempt_directories as _ods_attempt_directories,
)
from quanthus.pipelines.ods_handoff import (
    _probe_latest_ready_attempt as _ods_probe_latest_ready_attempt,
)
from quanthus.pipelines.ods_handoff import _remote as _ods_remote
from quanthus.pipelines.ods_handoff import _rsync as _ods_rsync
from quanthus.pipelines.securities_warehouse.daily_domain import (
    DailyDomainBuildError,
    build_daily_domain_increment,
    verify_daily_domain_increment,
)
from quanthus.pipelines.securities_warehouse.daily_ods_catalog import (
    DailyOdsCatalogError,
    build_daily_ods_catalog,
)
from quanthus.policy import find_project_root
from quanthus.warehouse_sync import WarehouseSyncError
from quanthus.warehouse_sync import _remote_path as _warehouse_remote_path
from quanthus.warehouse_sync import _run_rsync as _warehouse_rsync
from quanthus.warehouse_sync import _ssh_command as _warehouse_ssh_command

REQUEST_SCHEMA = "quanthus.p3-strat-003-input-qualification.v1"
REQUEST_SCHEMA_V2 = "quanthus.p3-strat-003-input-qualification.v2"
ARTIFACT_SCHEMA = "quanthus.p3-strat-003-input-artifact.v1"
BUNDLE_SCHEMA = "quanthus.p3-strat-003-sealed-replay-bundle.v1"
CANDIDATE_SCHEMA = "quanthus.p3-strat-003-candidate-input.v1"
PARTITION = "p3-strat-003-recent-history"
_MAX_QUALIFICATION_MANIFEST_BYTES = 512 * 1024
_QUALIFICATION_EVIDENCE_FIELDS = (
    "target_release", "daily_chain", "prior_chain_recovery", "replay_audit",
    "candidate_scan", "deferred_daily_artifact_contract",
)
_SSE_VENUE = "venue_bec59e31eb6bb28111a10d7e6d2c689f"
_SZSE_VENUE = "venue_8c0bd9088a0dc88f28e5e7fa8efdce87"


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
    if (
        raw.get("schema_version") not in {REQUEST_SCHEMA, REQUEST_SCHEMA_V2}
        or raw.get("work_item") != "P3-STRAT-003-B"
    ):
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


@dataclass(frozen=True)
class _MissingPriorReconstruction:
    minimum_market_date: date
    through_market_date: date
    source_id: str
    ods_source_config: Path
    release_config: Path
    revision_allowlist: Path
    revision_allowlist_file_sha256: str
    revision_allowlist_semantic_digest: str
    maximum_prior_candidates: int
    maximum_reconstructed_packages: int
    maximum_calendar_scan_days: int
    maximum_source_attempts_per_lane: int


def _missing_prior_reconstruction(
    raw: Mapping[str, Any],
    *,
    project: Path,
) -> _MissingPriorReconstruction:
    value = raw.get("missing_prior_reconstruction")
    if not isinstance(value, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "missing-prior reconstruction contract is missing",
        )
    try:
        minimum_market_date = date.fromisoformat(str(value["minimum_market_date"]))
        through_market_date = date.fromisoformat(
            str(value["authorized_through_market_date"])
        )
        source_id = str(value["source_id"])
        revision_allowlist_raw = value["revision_allowlist"]
        if not isinstance(revision_allowlist_raw, dict):
            raise TypeError("revision_allowlist must be an object")
        revision_allowlist_file_sha256 = str(
            revision_allowlist_raw["file_sha256"]
        )
        revision_allowlist_semantic_digest = str(
            revision_allowlist_raw["semantic_digest"]
        )
        maximum_prior_candidates = int(value["maximum_prior_candidates"])
        maximum_reconstructed_packages = int(value["maximum_reconstructed_packages"])
        maximum_calendar_scan_days = int(value["maximum_calendar_scan_days"])
        maximum_source_attempts_per_lane = int(value["maximum_source_attempts_per_lane"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "missing-prior reconstruction fields are invalid",
        ) from exc
    if (
        minimum_market_date != date(2026, 8, 1)
        or through_market_date != date(2026, 9, 17)
        or source_id != "cn01"
        or re.fullmatch(r"sha256:[0-9a-f]{64}", revision_allowlist_file_sha256) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", revision_allowlist_semantic_digest) is None
        or not 1 <= maximum_prior_candidates <= 16
        or not 1 <= maximum_reconstructed_packages <= 16
        or not 1 <= maximum_calendar_scan_days <= 31
        or maximum_reconstructed_packages > maximum_calendar_scan_days
        or not 1 <= maximum_source_attempts_per_lane <= 16
        or value.get("job_local_only") is not True
        or value.get("child_binding_required") is not True
        or value.get("mutate_daily_current") is not False
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "missing-prior reconstruction boundary differs",
        )
    ods_source_config = _safe_relative(
        project,
        value.get("ods_source_config"),
        "missing_prior_reconstruction.ods_source_config",
    )
    release_config = _safe_relative(
        project,
        value.get("release_config"),
        "missing_prior_reconstruction.release_config",
    )
    revision_allowlist = _safe_relative(
        project,
        revision_allowlist_raw.get("path"),
        "missing_prior_reconstruction.revision_allowlist.path",
    )
    if (
        not ods_source_config.is_file()
        or not release_config.is_file()
        or not revision_allowlist.is_file()
        or _sha256(revision_allowlist) != revision_allowlist_file_sha256
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "missing-prior reconstruction source config is unavailable",
        )
    return _MissingPriorReconstruction(
        minimum_market_date=minimum_market_date,
        through_market_date=through_market_date,
        source_id=source_id,
        ods_source_config=ods_source_config,
        release_config=release_config,
        revision_allowlist=revision_allowlist,
        revision_allowlist_file_sha256=revision_allowlist_file_sha256,
        revision_allowlist_semantic_digest=revision_allowlist_semantic_digest,
        maximum_prior_candidates=maximum_prior_candidates,
        maximum_reconstructed_packages=maximum_reconstructed_packages,
        maximum_calendar_scan_days=maximum_calendar_scan_days,
        maximum_source_attempts_per_lane=maximum_source_attempts_per_lane,
    )



@dataclass(frozen=True)
class _SemanticEquivalencePrerequisite:
    artifact_root: Path
    code_revision: str
    request_digest: str
    manifest_sha256: str
    result_digest: str
    boundary_market_date: date
    original_package_id: str
    original_manifest_sha256: str
    research_package_root: Path
    research_package_id: str
    research_manifest_sha256: str
    consumer_payload_digest: str

    def receipt(self) -> dict[str, Any]:
        return {
            "artifact_ref": str(self.artifact_root),
            "code_revision": self.code_revision,
            "request_digest": self.request_digest,
            "manifest_sha256": self.manifest_sha256,
            "result_digest": self.result_digest,
            "disposition": "PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE",
            "boundary_market_date": self.boundary_market_date.isoformat(),
            "original_child_binding": {
                "package_id": self.original_package_id,
                "manifest_sha256": self.original_manifest_sha256,
            },
            "research_reconstruction": {
                "package_id": self.research_package_id,
                "manifest_sha256": self.research_manifest_sha256,
                "consumer_payload_digest": self.consumer_payload_digest,
            },
            "original_manifest_claimed_reproduced": False,
        }


def _semantic_equivalence_prerequisite(
    raw: Mapping[str, Any],
) -> _SemanticEquivalencePrerequisite | None:
    from quanthus.expert_strategy.p3_strat_003_semantic_equivalence import (
        _consumer_payload_identity as semantic_consumer_payload_identity,
    )
    from quanthus.expert_strategy.p3_strat_003_semantic_equivalence import (
        verify_semantic_equivalence_probe_artifact,
    )

    schema = raw.get("schema_version")
    value = raw.get("semantic_equivalence_prerequisite")
    if schema == REQUEST_SCHEMA:
        if value is not None:
            raise ExpertEvaluationError(
                "p3_strat_003_input_request_invalid",
                "v1 input qualification cannot bind semantic-equivalence evidence",
            )
        return None
    if schema != REQUEST_SCHEMA_V2 or not isinstance(value, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "v2 input qualification requires semantic-equivalence evidence",
        )
    try:
        artifact_root = Path(str(value["artifact_ref"]))
        code_revision = str(value["code_revision"])
        request_digest = str(value["request_digest"])
        manifest_sha256 = str(value["manifest_sha256"])
        result_digest = str(value["result_digest"])
        disposition = str(value["disposition"])
        boundary_market_date = date.fromisoformat(str(value["boundary_market_date"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "semantic-equivalence binding fields are invalid",
        ) from exc
    if (
        not artifact_root.is_absolute()
        or re.fullmatch(r"[0-9a-f]{40}", code_revision) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", request_digest) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", manifest_sha256) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", result_digest) is None
        or disposition != "PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE"
        or boundary_market_date != date(2026, 8, 27)
        or value.get("reuse_boundary_package") is not True
        or value.get("allow_original_child_semantic_bridge") is not True
        or value.get("original_manifest_claimed_reproduced") is not False
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_input_request_invalid",
            "semantic-equivalence binding boundary differs",
        )

    artifact_root = artifact_root.resolve()
    observed = verify_semantic_equivalence_probe_artifact(
        artifact_root,
        code_revision=code_revision,
        request_digest=request_digest,
    )
    if (
        observed.get("manifest_sha256") != manifest_sha256
        or observed.get("result_digest") != result_digest
        or observed.get("disposition") != disposition
        or observed.get("strategy_result_produced") is not False
        or observed.get("outcome_labels_produced") is not False
        or observed.get("p6_mutation") is not False
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_prerequisite_drift",
            "semantic-equivalence artifact identity differs",
        )

    summary = _object(
        artifact_root / "summary.json",
        "P3-STRAT-003 semantic-equivalence summary",
    )
    boundary = summary.get("missing_boundary")
    if not isinstance(boundary, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_prerequisite_invalid",
            "semantic-equivalence boundary receipt is missing",
        )
    original = boundary.get("original_child_binding")
    research = boundary.get("research_reconstruction")
    if not isinstance(original, dict) or not isinstance(research, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_prerequisite_invalid",
            "semantic-equivalence boundary identities are incomplete",
        )
    original_package_id = str(original.get("package_id") or "")
    original_manifest_sha256 = str(original.get("manifest_sha256") or "")
    research_package_id = str(research.get("package_id") or "")
    research_manifest_sha256 = str(research.get("manifest_sha256") or "")
    consumer_payload_digest = str(research.get("consumer_payload_digest") or "")
    if (
        boundary.get("market_date") != boundary_market_date.isoformat()
        or boundary.get("consumer_semantic_compatibility_precondition_passed") is not True
        or boundary.get("original_manifest_claimed_reproduced") is not False
        or not original_package_id
        or re.fullmatch(r"sha256:[0-9a-f]{64}", original_manifest_sha256) is None
        or not research_package_id
        or re.fullmatch(r"sha256:[0-9a-f]{64}", research_manifest_sha256) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", consumer_payload_digest) is None
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_prerequisite_invalid",
            "semantic-equivalence boundary receipt differs",
        )

    research_package_root = (artifact_root / "missing-boundary" / "package").resolve()
    if not research_package_root.is_relative_to(artifact_root):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_prerequisite_invalid",
            "semantic-equivalence package escapes its artifact root",
        )
    try:
        verified = verify_daily_domain_increment(research_package_root)
    except DailyDomainBuildError as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_prerequisite_invalid",
            "sealed semantic-equivalence package is invalid",
        ) from exc
    if (
        verified.market_date != boundary_market_date
        or verified.package_id != research_package_id
        or verified.manifest_sha256 != research_manifest_sha256
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_prerequisite_drift",
            "sealed semantic-equivalence package identity differs",
        )
    observed_consumer_digest, _ = semantic_consumer_payload_identity(
        research_package_root
    )
    if observed_consumer_digest != consumer_payload_digest:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_prerequisite_drift",
            "sealed semantic-equivalence consumer payload differs",
        )

    return _SemanticEquivalencePrerequisite(
        artifact_root=artifact_root,
        code_revision=code_revision,
        request_digest=request_digest,
        manifest_sha256=manifest_sha256,
        result_digest=result_digest,
        boundary_market_date=boundary_market_date,
        original_package_id=original_package_id,
        original_manifest_sha256=original_manifest_sha256,
        research_package_root=research_package_root,
        research_package_id=research_package_id,
        research_manifest_sha256=research_manifest_sha256,
        consumer_payload_digest=consumer_payload_digest,
    )


def _revision_allowlist(
    reconstruction: _MissingPriorReconstruction,
) -> dict[str, str]:
    raw = _object(
        reconstruction.revision_allowlist,
        "P3-STRAT-003 DWD revision allowlist",
    )
    semantic = raw.get("semantic_digest")
    payload = {key: value for key, value in raw.items() if key != "semantic_digest"}
    mapping = raw.get("prefix_to_revision")
    if (
        raw.get("schema_version")
        != "quanthus.p3-strat-003-dwd-revision-allowlist.v1"
        or raw.get("work_item") != "P3-STRAT-003-B"
        or raw.get("prefix_uniqueness") is not True
        or semantic != reconstruction.revision_allowlist_semantic_digest
        or semantic != digest_value(payload)
        or not isinstance(mapping, dict)
        or raw.get("revision_count") != len(mapping)
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_revision_allowlist_invalid",
            "historical DWD revision allowlist identity differs",
        )
    observed: dict[str, str] = {}
    for prefix, revision in mapping.items():
        if (
            not isinstance(prefix, str)
            or re.fullmatch(r"[0-9a-f]{7}", prefix) is None
            or not isinstance(revision, str)
            or re.fullmatch(r"[0-9a-f]{40}", revision) is None
            or not revision.startswith(prefix)
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_revision_allowlist_invalid",
                "historical DWD revision mapping contains an invalid entry",
            )
        observed[prefix] = revision
    return observed


def _builder_revision_for_package(
    *,
    market_date: date,
    package_id: str,
    reconstruction: _MissingPriorReconstruction,
) -> str:
    matched = re.fullmatch(
        r"dwd-daily-([0-9]{8})-([0-9a-f]{12})-([0-9a-f]{7})",
        package_id,
    )
    if matched is None:
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_not_authorized",
            f"child-bound package id is outside the frozen DWD identity shape: {package_id}",
        )
    try:
        encoded_date = datetime.strptime(matched.group(1), "%Y%m%d").date()
    except ValueError as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_not_authorized",
            f"child-bound package date is invalid: {package_id}",
        ) from exc
    if encoded_date != market_date:
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_not_authorized",
            "child-bound package date differs from its manifest binding",
        )
    prefix = matched.group(3)
    revision = _revision_allowlist(reconstruction).get(prefix)
    if revision is None:
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_revision_not_frozen",
            prefix,
        )
    return revision


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


_RECONSTRUCTION_ODS_LANES: dict[str, tuple[str, str]] = {
    "market_daily_core": ("daily", "hermes-market-daily-core-"),
    "calendar_daily_events": ("daily", "hermes-calendar-daily-events-"),
    "intraday_1m": ("daily/1m", "hermes-intraday-archive-1m-"),
    "intraday_5m": ("daily/5m", "hermes-intraday-archive-5m-"),
}


@dataclass(frozen=True)
class _SelectedOdsRelease:
    lane: str
    schedule_path: str
    attempt_id: str
    release_id: str
    created_at: datetime
    manifest_sha256: str


@dataclass(frozen=True)
class _PriorCandidate:
    root: Path
    market_date: date
    package_id: str
    manifest_sha256: str


def _verified_local_prior_candidates(
    daily_root: Path,
    *,
    before: date,
    limit: int,
) -> tuple[_PriorCandidate, ...]:
    packages_root = daily_root.resolve() / "packages"
    candidates: list[_PriorCandidate] = []
    if not packages_root.is_dir():
        return ()
    for date_root in sorted(packages_root.iterdir(), reverse=True):
        if not date_root.is_dir():
            continue
        try:
            folder_date = date.fromisoformat(date_root.name)
        except ValueError:
            continue
        if folder_date >= before:
            continue
        for package_root in sorted(date_root.iterdir(), reverse=True):
            if not package_root.is_dir():
                continue
            try:
                verified = verify_daily_domain_increment(package_root)
            except DailyDomainBuildError:
                continue
            if verified.market_date != folder_date:
                continue
            candidates.append(
                _PriorCandidate(
                    root=package_root.resolve(),
                    market_date=verified.market_date,
                    package_id=verified.package_id,
                    manifest_sha256=verified.manifest_sha256,
                )
            )
    candidates.sort(
        key=lambda item: (item.market_date, item.package_id),
        reverse=True,
    )
    return tuple(candidates[:limit])


def _resolved_reconstruction_ods_source(
    reconstruction: _MissingPriorReconstruction,
) -> Any:
    config = load_ods_source_config(reconstruction.ods_source_config)
    configured = next(
        (item for item in config.sources if item.source_id == reconstruction.source_id),
        None,
    )
    if configured is None:
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_source_invalid",
            "authorized ODS source is not configured",
        )
    try:
        return resolve_ods_source(configured)
    except OdsHandoffError as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_source_invalid",
            str(exc),
        ) from exc


def _reconstruction_market_dates(
    reconstruction: _MissingPriorReconstruction,
    *,
    anchor_date: date,
    target_date: date,
) -> tuple[date, ...]:
    calendar_days = (target_date - anchor_date).days
    if (
        calendar_days <= 0
        or calendar_days > reconstruction.maximum_calendar_scan_days
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_calendar_budget",
            f"{anchor_date.isoformat()}->{target_date.isoformat()}",
        )
    source = _resolved_reconstruction_ods_source(reconstruction)
    dates: list[date] = []
    for offset in range(1, calendar_days + 1):
        candidate = anchor_date + timedelta(days=offset)
        try:
            attempt = _ods_probe_latest_ready_attempt(
                source,
                schedule="daily",
                market_date=candidate.isoformat(),
            )
        except (OdsHandoffError, subprocess.TimeoutExpired) as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_reconstruction_source_failed",
                f"daily/{candidate.isoformat()}: {exc}",
            ) from exc
        if attempt is not None:
            dates.append(candidate)
    if not dates or dates[-1] != target_date:
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_target_not_ready",
            target_date.isoformat(),
        )
    if len(dates) > reconstruction.maximum_reconstructed_packages:
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_package_budget",
            f"{len(dates)} packages required",
        )
    return tuple(dates)


def _reconstruction_lane(schedule_path: str, release_id: str) -> str | None:
    for lane, (expected_schedule, prefix) in _RECONSTRUCTION_ODS_LANES.items():
        if schedule_path == expected_schedule and release_id.startswith(prefix):
            return lane
    return None


def _probe_reconstruction_release(
    source: Any,
    *,
    schedule_path: str,
    market_date: date,
    attempt_id: str,
    metadata_root: Path,
) -> _SelectedOdsRelease | None:
    market_text = market_date.isoformat()
    target = metadata_root.joinpath(
        *schedule_path.split("/"),
        market_text,
        attempt_id,
    )
    target.mkdir(parents=True, exist_ok=True)
    remote_root = (
        f"{source.release_root}/{schedule_path}/{market_text}/{attempt_id}"
    )
    for filename in ("release.json", "READY"):
        try:
            completed = _ods_rsync(
                source,
                "-a",
                "--timeout=20",
                _ods_remote(source, f"{remote_root}/{filename}"),
                str(target / filename),
                timeout_seconds=30,
            )
        except subprocess.TimeoutExpired:
            return None
        if completed.returncode:
            return None
    manifest = target / "release.json"
    try:
        ready = (target / "READY").read_text(encoding="ascii").strip()
        if ready != _sha256(manifest):
            return None
        release = OdsRelease.load(manifest)
    except (OSError, OdsReleaseError):
        return None
    if release.schedule != "daily" or release.market_date != market_date:
        return None
    lane = _reconstruction_lane(schedule_path, release.release_id)
    if lane is None:
        return None
    return _SelectedOdsRelease(
        lane=lane,
        schedule_path=schedule_path,
        attempt_id=attempt_id,
        release_id=release.release_id,
        created_at=release.created_at,
        manifest_sha256=_sha256(manifest),
    )


def _select_reconstruction_ods_releases(
    reconstruction: _MissingPriorReconstruction,
    *,
    market_date: date,
    metadata_root: Path,
) -> tuple[Any, tuple[_SelectedOdsRelease, ...]]:
    source = _resolved_reconstruction_ods_source(reconstruction)
    selected: dict[str, _SelectedOdsRelease] = {}
    market_text = market_date.isoformat()
    for schedule_path in sorted(
        {value[0] for value in _RECONSTRUCTION_ODS_LANES.values()}
    ):
        try:
            attempts = _ods_attempt_directories(
                source,
                schedule=schedule_path,
                market_date=market_text,
            )
        except (OdsHandoffError, subprocess.TimeoutExpired) as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_reconstruction_source_failed",
                f"{schedule_path}: {exc}",
            ) from exc
        if not attempts:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_reconstruction_source_missing",
                f"no ODS attempts for {schedule_path}/{market_text}",
            )
        if len(attempts) > reconstruction.maximum_source_attempts_per_lane:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_reconstruction_source_budget",
                f"{schedule_path} has {len(attempts)} attempts",
            )
        for attempt_id in attempts:
            candidate = _probe_reconstruction_release(
                source,
                schedule_path=schedule_path,
                market_date=market_date,
                attempt_id=attempt_id,
                metadata_root=metadata_root,
            )
            if candidate is None:
                continue
            current = selected.get(candidate.lane)
            if current is None or (
                candidate.created_at,
                candidate.release_id,
                candidate.attempt_id,
            ) > (
                current.created_at,
                current.release_id,
                current.attempt_id,
            ):
                selected[candidate.lane] = candidate
    missing = set(_RECONSTRUCTION_ODS_LANES) - set(selected)
    if missing:
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_source_missing",
            "missing READY ODS lanes: " + ",".join(sorted(missing)),
        )
    return source, tuple(selected[lane] for lane in sorted(selected))


def _pull_reconstruction_ods(
    reconstruction: _MissingPriorReconstruction,
    *,
    market_date: date,
    output_root: Path,
) -> tuple[Path, tuple[dict[str, Any], ...]]:
    output_root = output_root.resolve()
    source, selected = _select_reconstruction_ods_releases(
        reconstruction,
        market_date=market_date,
        metadata_root=output_root / "ods-metadata",
    )
    release_root = output_root / "ods-releases"
    release_root.mkdir(parents=True, exist_ok=True)
    inventory: list[dict[str, Any]] = []
    market_text = market_date.isoformat()
    for release in selected:
        destination = release_root.joinpath(
            *release.schedule_path.split("/"),
            market_text,
            release.attempt_id,
        )
        destination.mkdir(parents=True, exist_ok=True)
        remote = (
            f"{source.release_root}/{release.schedule_path}/{market_text}/"
            f"{release.attempt_id}/"
        )
        try:
            completed = _ods_rsync(
                source,
                "-a",
                "--checksum",
                "--delay-updates",
                "--partial-dir=.rsync-partial",
                "--timeout=300",
                _ods_remote(source, remote),
                f"{destination}/",
                timeout_seconds=330,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_reconstruction_source_failed",
                f"{release.lane}/{release.attempt_id}: timeout",
            ) from exc
        if completed.returncode:
            detail = " ".join((completed.stderr or completed.stdout).split())
            raise ExpertEvaluationError(
                "p3_strat_003_prior_reconstruction_source_failed",
                f"{release.lane}/{release.attempt_id}: {detail[-900:]}",
            )
        inventory.append(
            {
                "market_date": market_text,
                "lane": release.lane,
                "schedule": release.schedule_path,
                "attempt_id": release.attempt_id,
                "release_id": release.release_id,
                "manifest_sha256": release.manifest_sha256,
                "disposition": "selected_ready_release_copied_job_local",
            }
        )
    return release_root, tuple(inventory)


def _prior_pointer(
    *,
    candidate: _PriorCandidate,
    root: Path,
) -> Path:
    root = root.resolve()
    relative = Path(
        "packages",
        candidate.market_date.isoformat(),
        candidate.package_id,
        "manifest.json",
    )
    manifest = root / relative
    manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate.root / "manifest.json", manifest)
    pointer = root / "CURRENT.json"
    pointer.write_bytes(
        canonical_json_bytes(
            {
                "kind": "cn01_daily_dwd_current",
                "market_date": candidate.market_date.isoformat(),
                "package_id": candidate.package_id,
                "manifest_path": relative.as_posix(),
                "manifest_sha256": candidate.manifest_sha256,
            }
        )
        + b"\n"
    )
    return pointer


def _publish_reconstructed_suffix(
    *,
    cache_root: Path,
    candidates: Sequence[_PriorCandidate],
) -> None:
    for candidate in candidates:
        target = (
            cache_root.resolve()
            / "packages"
            / candidate.market_date.isoformat()
            / candidate.package_id
        ).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            verified = verify_daily_domain_increment(target)
            if (
                verified.market_date != candidate.market_date
                or verified.package_id != candidate.package_id
                or verified.manifest_sha256 != candidate.manifest_sha256
            ):
                raise ExpertEvaluationError(
                    "p3_strat_003_prior_package_drift",
                    "reconstructed suffix conflicts with an existing job-local package",
                )
            continue
        os.replace(candidate.root, target)


def _build_reconstruction_suffix(
    *,
    cache_root: Path,
    anchor: _PriorCandidate,
    target_date: date,
    reconstruction: _MissingPriorReconstruction,
    base_root: Path,
    attempt_root: Path,
    code_revision: str,
) -> tuple[_PriorCandidate, tuple[_PriorCandidate, ...], tuple[dict[str, Any], ...]]:
    market_dates = _reconstruction_market_dates(
        reconstruction,
        anchor_date=anchor.market_date,
        target_date=target_date,
    )
    prior = anchor
    built_candidates: list[_PriorCandidate] = []
    steps: list[dict[str, Any]] = []
    for market_date in market_dates:
        day_root = attempt_root / market_date.isoformat()
        release_root, source_inventory = _pull_reconstruction_ods(
            reconstruction,
            market_date=market_date,
            output_root=day_root,
        )
        catalog_root = day_root / "catalog"
        try:
            catalog = build_daily_ods_catalog(
                release_root=release_root,
                market_date=market_date,
                output=catalog_root,
            )
        except DailyOdsCatalogError as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_reconstruction_catalog_failed",
                f"{market_date.isoformat()}: {exc}",
            ) from exc
        pointer = _prior_pointer(
            candidate=prior,
            root=day_root / "prior",
        )
        output = day_root / "package"
        try:
            built = build_daily_domain_increment(
                catalog_root=catalog_root,
                release_root=release_root,
                base_root=base_root,
                release_config=reconstruction.release_config,
                output=output,
                code_revision=code_revision,
                prior_pointer=pointer,
            )
            verified = verify_daily_domain_increment(output)
        except DailyDomainBuildError as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_reconstruction_build_failed",
                f"{market_date.isoformat()}: {exc}",
            ) from exc
        if (
            built.package_id != verified.package_id
            or built.manifest_sha256 != verified.manifest_sha256
            or verified.market_date != market_date
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_prior_reconstruction_build_drift",
                market_date.isoformat(),
            )
        prior = _PriorCandidate(
            root=output,
            market_date=market_date,
            package_id=verified.package_id,
            manifest_sha256=verified.manifest_sha256,
        )
        built_candidates.append(prior)
        steps.append(
            {
                "market_date": market_date.isoformat(),
                "package_id": verified.package_id,
                "manifest_sha256": verified.manifest_sha256,
                "catalog_digest": catalog.catalog_digest,
                "code_revision": code_revision,
                "source_attempts": list(source_inventory),
            }
        )
    return prior, tuple(built_candidates), tuple(steps)


def _reconstruct_exact_prior_package(
    *,
    cache_root: Path,
    daily_root: Path,
    market_date: date,
    package_id: str,
    manifest_sha256: str,
    reconstruction: _MissingPriorReconstruction,
    base_root: Path,
    transport_failure: str,
) -> tuple[Path, dict[str, Any]]:
    if not (
        reconstruction.minimum_market_date
        <= market_date
        <= reconstruction.through_market_date
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_not_authorized",
            f"{market_date.isoformat()}:{package_id}",
        )
    target_builder_revision = _builder_revision_for_package(
        market_date=market_date,
        package_id=package_id,
        reconstruction=reconstruction,
    )

    cache_root = cache_root.resolve()
    target = cache_root / "packages" / market_date.isoformat() / package_id
    recovery_root = cache_root / "reconstruction" / market_date.isoformat() / package_id
    if target.exists():
        verified = verify_daily_domain_increment(target)
        if (
            verified.package_id != package_id
            or verified.market_date != market_date
            or verified.manifest_sha256 != manifest_sha256
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_prior_package_drift",
                "existing reconstructed package differs from exact child binding",
            )
        return target, {
            "market_date": market_date.isoformat(),
            "package_id": package_id,
            "manifest_sha256": manifest_sha256,
            "disposition": "reused_exact_job_local_reconstruction",
        }
    if recovery_root.exists():
        shutil.rmtree(recovery_root)
    recovery_root.mkdir(parents=True)

    prior_candidates = _verified_local_prior_candidates(
        daily_root,
        before=market_date,
        limit=reconstruction.maximum_prior_candidates,
    )
    if not prior_candidates:
        raise ExpertEvaluationError(
            "p3_strat_003_prior_reconstruction_prior_missing",
            "no verified local package exists before the missing suffix",
        )

    observed_manifest_sha256: list[str] = []
    for index, anchor in enumerate(prior_candidates):
        attempt_root = recovery_root / "suffix-attempts" / f"{index:02d}"
        try:
            final, rebuilt, steps = _build_reconstruction_suffix(
                cache_root=cache_root,
                anchor=anchor,
                target_date=market_date,
                reconstruction=reconstruction,
                base_root=base_root,
                attempt_root=attempt_root,
                code_revision=target_builder_revision,
            )
        except ExpertEvaluationError as exc:
            if exc.reason_code in {
                "p3_strat_003_prior_reconstruction_calendar_budget",
                "p3_strat_003_prior_reconstruction_package_budget",
            }:
                continue
            raise
        if final.package_id != package_id:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_reconstruction_identity_mismatch",
                f"rebuilt suffix produced {final.package_id}, expected {package_id}",
            )
        observed_manifest_sha256.append(final.manifest_sha256)
        if final.manifest_sha256 != manifest_sha256:
            continue
        _publish_reconstructed_suffix(
            cache_root=cache_root,
            candidates=rebuilt,
        )
        target = cache_root / "packages" / market_date.isoformat() / package_id
        return target, {
            "market_date": market_date.isoformat(),
            "package_id": package_id,
            "manifest_sha256": manifest_sha256,
            "disposition": "reconstructed_exact_suffix_from_cn01_ods",
            "source_id": reconstruction.source_id,
            "target_builder_revision": target_builder_revision,
            "anchor_market_date": anchor.market_date.isoformat(),
            "anchor_package_id": anchor.package_id,
            "reconstructed_package_count": len(rebuilt),
            "reconstructed_packages": list(steps),
            "remote_exact_fetch_failure": transport_failure[:800],
        }

    raise ExpertEvaluationError(
        "p3_strat_003_prior_reconstruction_manifest_mismatch",
        (
            f"expected {manifest_sha256}; observed "
            + ",".join(observed_manifest_sha256[: reconstruction.maximum_prior_candidates])
        ),
    )


def _bound_prior_directory_names(market_date: date, package_id: str) -> tuple[str, ...]:
    """Known physical layouts, derived only from the immutable child identity."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}", package_id):
        raise ExpertEvaluationError(
            "p3_strat_003_prior_package_invalid", "bound prior package id is unsafe"
        )
    matched = re.fullmatch(r"dwd-daily-(\d{8})-([0-9a-f]{12})-([0-9a-f]{7})", package_id)
    if matched is None:
        return (package_id,)
    if matched.group(1) != market_date.strftime("%Y%m%d"):
        raise ExpertEvaluationError(
            "p3_strat_003_prior_package_invalid", "bound prior package date differs"
        )
    return package_id, f"{matched.group(2)}-{matched.group(3)}"


def _verify_prior_metadata_paths(package: Path) -> None:
    for name in ("manifest.json", "READY"):
        if not (package / name).resolve().is_relative_to(package.resolve()):
            raise ExpertEvaluationError(
                "p3_strat_003_prior_package_invalid", "prior metadata escapes its package"
            )


def _resolve_exact_local_prior_package(
    *, daily_root: Path, market_date: date, package_id: str, manifest_sha256: str
) -> Path | None:
    """Locate an original package without selecting another retained version."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest_sha256):
        raise ExpertEvaluationError(
            "p3_strat_003_prior_package_invalid", "bound prior manifest digest is invalid"
        )
    daily_root = daily_root.resolve()
    matches: list[Path] = []
    for name in _bound_prior_directory_names(market_date, package_id):
        candidate = (daily_root / "packages" / market_date.isoformat() / name).resolve()
        if not candidate.is_relative_to(daily_root):
            raise ExpertEvaluationError(
                "p3_strat_003_prior_package_invalid", "local prior escapes the governed daily root"
            )
        if not candidate.exists():
            continue
        _verify_prior_metadata_paths(candidate)
        try:
            verified = verify_daily_domain_increment(candidate)
        except (DailyDomainBuildError, OSError, ValueError) as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_package_invalid", "existing OCI prior package is invalid"
            ) from exc
        if (
            verified.market_date != market_date
            or verified.package_id != package_id
            or verified.manifest_sha256 != manifest_sha256
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_prior_package_drift",
                "existing OCI prior package differs from its child binding",
            )
        matches.append(candidate)
    return matches[0] if matches else None


def _fetch_bound_prior_package(
    *,
    cache_root: Path,
    market_date: date,
    package_id: str,
    manifest_sha256: str,
    daily_root: Path | None = None,
    base_root: Path | None = None,
    reconstruction: _MissingPriorReconstruction | None = None,
    semantic_prerequisite: _SemanticEquivalencePrerequisite | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Fetch one exact child-bound prior package into job-local scratch only."""

    directory_names = _bound_prior_directory_names(market_date, package_id)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest_sha256):
        raise ExpertEvaluationError(
            "p3_strat_003_prior_package_invalid",
            "bound prior manifest digest is invalid",
        )
    cache_root = cache_root.resolve()
    target = (cache_root / "packages" / market_date.isoformat() / package_id).resolve()
    if not target.is_relative_to(cache_root):
        raise ExpertEvaluationError(
            "p3_strat_003_prior_package_invalid",
            "bound prior package escapes the job-local cache",
        )
    if target.exists():
        _verify_prior_metadata_paths(target)
        try:
            verified = verify_daily_domain_increment(target)
        except DailyDomainBuildError as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_package_invalid",
                "existing job-local prior package is invalid",
            ) from exc
        if (
            verified.market_date != market_date
            or verified.package_id != package_id
            or verified.manifest_sha256 != manifest_sha256
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_prior_package_drift",
                "existing job-local prior package differs from its child binding",
            )
        return target, {
            "market_date": market_date.isoformat(),
            "package_id": package_id,
            "manifest_sha256": manifest_sha256,
            "disposition": "reused_job_local",
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{package_id}.", dir=target.parent))
    try:
        _, _, remote_user, _, _, ssh = _warehouse_ssh_command()
        remote_host = os.environ["QUANTHUS_CN01_DWD_HOST"]
        remote_names = tuple(reversed(directory_names))
        try:
            for index, name in enumerate(remote_names):
                relative = f"packages/{market_date.isoformat()}/{name}"
                remote_package = (
                    f"{remote_user}@{remote_host}:{_warehouse_remote_path(relative)}/"
                )
                try:
                    _warehouse_rsync(
                        source=remote_package,
                        destination=staging,
                        ssh=ssh,
                        extra=("--exclude=READY",),
                    )
                except WarehouseSyncError as transfer_error:
                    if any(staging.iterdir()):
                        raise ExpertEvaluationError(
                            "p3_strat_003_prior_package_verification_failed",
                            "partial prior package transfer cannot fall back to another layout",
                        ) from transfer_error
                    if index == len(remote_names) - 1:
                        raise
                    shutil.rmtree(staging)
                    staging.mkdir()
                    continue
                try:
                    _warehouse_rsync(
                        source=f"{remote_package}READY",
                        destination=staging / "READY",
                        ssh=ssh,
                    )
                except WarehouseSyncError as ready_error:
                    raise ExpertEvaluationError(
                        "p3_strat_003_prior_package_verification_failed",
                        "fetched prior package READY is unavailable",
                    ) from ready_error
                break
        except WarehouseSyncError as exc:
            detail = " ".join(str(exc).split())
            shutil.rmtree(staging, ignore_errors=True)
            if (
                semantic_prerequisite is not None
                and market_date == semantic_prerequisite.boundary_market_date
                and package_id == semantic_prerequisite.original_package_id
                and manifest_sha256
                == semantic_prerequisite.original_manifest_sha256
            ):
                return semantic_prerequisite.research_package_root, {
                    "market_date": market_date.isoformat(),
                    "package_id": package_id,
                    "manifest_sha256": manifest_sha256,
                    "disposition": "reused_sealed_semantic_equivalence_reconstruction",
                    "semantic_equivalence_result_digest": (
                        semantic_prerequisite.result_digest
                    ),
                    "research_package_id": semantic_prerequisite.research_package_id,
                    "research_manifest_sha256": (
                        semantic_prerequisite.research_manifest_sha256
                    ),
                    "consumer_payload_digest": (
                        semantic_prerequisite.consumer_payload_digest
                    ),
                    "remote_exact_fetch_failure": detail[:800],
                    "original_manifest_claimed_reproduced": False,
                }
            if (
                reconstruction is not None
                and daily_root is not None
                and base_root is not None
                and reconstruction.minimum_market_date
                <= market_date
                <= reconstruction.through_market_date
            ):
                return _reconstruct_exact_prior_package(
                    cache_root=cache_root,
                    daily_root=daily_root,
                    market_date=market_date,
                    package_id=package_id,
                    manifest_sha256=manifest_sha256,
                    reconstruction=reconstruction,
                    base_root=base_root,
                    transport_failure=detail,
                )
            raise ExpertEvaluationError(
                "p3_strat_003_prior_fetch_failed",
                detail[:1200],
            ) from exc
        _verify_prior_metadata_paths(staging)
        try:
            verified = verify_daily_domain_increment(staging)
        except DailyDomainBuildError as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_prior_package_verification_failed",
                f"{market_date.isoformat()}:{package_id}",
            ) from exc
        if (
            verified.market_date != market_date
            or verified.package_id != package_id
            or verified.manifest_sha256 != manifest_sha256
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_prior_package_drift",
                "fetched prior package differs from its child binding",
            )
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target, {
        "market_date": market_date.isoformat(),
        "package_id": package_id,
        "manifest_sha256": manifest_sha256,
        "disposition": "fetched_exact_child_binding",
    }


def _semantic_bridge_allows(
    left: P6DailyChainPackage,
    right: P6DailyChainPackage,
    semantic_prerequisite: _SemanticEquivalencePrerequisite | None,
) -> bool:
    return (
        semantic_prerequisite is not None
        and left.market_date == semantic_prerequisite.boundary_market_date
        and left.package_id == semantic_prerequisite.research_package_id
        and left.manifest_sha256
        == semantic_prerequisite.research_manifest_sha256
        and right.prior_package_id
        == semantic_prerequisite.original_package_id
        and right.prior_manifest_sha256
        == semantic_prerequisite.original_manifest_sha256
        and right.covered_after_date == left.market_date
    )


def _load_bound_daily_chain(
    *,
    config: Any,
    target: ProspectiveDwdRelease,
    cache_root: Path,
    reconstruction: _MissingPriorReconstruction,
    semantic_prerequisite: _SemanticEquivalencePrerequisite | None = None,
) -> tuple[tuple[P6DailyChainPackage, ...], tuple[dict[str, Any], ...]]:
    """Resolve one complete immutable chain without mutating the local daily root."""

    _verify_seed(config)
    daily_root = config.daily_root.resolve()
    if daily_root not in target.package_root.resolve().parents:
        raise ExpertEvaluationError(
            "p3_strat_003_target_release_invalid",
            "target release is outside the governed daily root",
        )
    cursor = target.package_root.resolve()
    observed: list[P6DailyChainPackage] = []
    recovered: list[dict[str, Any]] = []
    seen: set[tuple[date, str]] = set()

    while True:
        try:
            verified = verify_daily_domain_increment(cursor)
        except DailyDomainBuildError as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_daily_chain_invalid",
                f"daily package failed verification: {cursor.name}",
            ) from exc
        raw = _object(cursor / "manifest.json", "daily DWD manifest")
        key = (verified.market_date, verified.package_id)
        if key in seen:
            raise ExpertEvaluationError(
                "p3_strat_003_daily_chain_cycle",
                verified.package_id,
            )
        seen.add(key)
        base = raw.get("base")
        if not isinstance(base, dict) or (
            base.get("package_id") != "dwd-base-202607-v2.1"
            or base.get("manifest_sha256") != config.historical_base_manifest_sha256
            or base.get("content_digest") != config.historical_base_content_digest
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_daily_chain_base_drift",
                verified.package_id,
            )
        code_revision = raw.get("code_revision")
        if not isinstance(code_revision, str) or not re.fullmatch(
            r"[0-9a-f]{40}", code_revision
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_daily_chain_invalid",
                "daily package code revision is invalid",
            )
        try:
            covered_after = date.fromisoformat(str(raw.get("covered_after_date")))
        except ValueError as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_daily_chain_invalid",
                "daily covered-after date is invalid",
            ) from exc

        prior = raw.get("prior")
        prior_id: str | None = None
        prior_sha: str | None = None
        prior_date: date | None = None
        if prior is not None:
            if not isinstance(prior, dict):
                raise ExpertEvaluationError(
                    "p3_strat_003_daily_chain_invalid",
                    "daily prior binding must be an object",
                )
            prior_id = str(prior.get("package_id") or "")
            prior_sha = str(prior.get("manifest_sha256") or "")
            try:
                prior_date = date.fromisoformat(str(prior.get("market_date")))
            except ValueError as exc:
                raise ExpertEvaluationError(
                    "p3_strat_003_daily_chain_invalid",
                    "daily prior market date is invalid",
                ) from exc
            if prior_date >= verified.market_date:
                raise ExpertEvaluationError(
                    "p3_strat_003_daily_chain_invalid",
                    "daily prior date does not precede its child",
                )

        observed.append(
            P6DailyChainPackage(
                root=cursor,
                package_id=verified.package_id,
                market_date=verified.market_date,
                manifest_sha256=verified.manifest_sha256,
                content_digest=verified.content_digest,
                code_revision=code_revision,
                covered_after_date=covered_after,
                prior_package_id=prior_id,
                prior_manifest_sha256=prior_sha,
            )
        )
        if prior is None:
            if covered_after != config.historical_base_through_date:
                raise ExpertEvaluationError(
                    "p3_strat_003_daily_chain_gap",
                    "first daily package does not advance the governed Base",
                )
            break

        assert prior_date is not None and prior_id is not None and prior_sha is not None
        local_prior = _resolve_exact_local_prior_package(
            daily_root=daily_root,
            market_date=prior_date,
            package_id=prior_id,
            manifest_sha256=prior_sha,
        )
        if local_prior is not None:
            cursor = local_prior
            continue

        cursor, recovery = _fetch_bound_prior_package(
            cache_root=cache_root,
            daily_root=daily_root,
            base_root=config.historical_base_root,
            market_date=prior_date,
            package_id=prior_id,
            manifest_sha256=prior_sha,
            reconstruction=reconstruction,
            semantic_prerequisite=semantic_prerequisite,
        )
        recovered.append(recovery)

    ordered = tuple(reversed(observed))
    if not ordered or ordered[-1].manifest_sha256 != target.manifest_sha256:
        raise ExpertEvaluationError(
            "p3_strat_003_daily_chain_invalid",
            "target package identity differs after chain resolution",
        )
    for left, right in zip(ordered, ordered[1:], strict=False):
        exact_edge = (
            right.prior_package_id == left.package_id
            and right.prior_manifest_sha256 == left.manifest_sha256
            and right.covered_after_date == left.market_date
        )
        semantic_bridge = _semantic_bridge_allows(
            left,
            right,
            semantic_prerequisite,
        )
        if not exact_edge and not semantic_bridge:
            raise ExpertEvaluationError(
                "p3_strat_003_daily_chain_gap",
                f"{left.package_id}->{right.package_id}",
            )
    return ordered, tuple(recovered)


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


def _qualification_evidence(summary: Mapping[str, Any]) -> dict[str, Any]:
    if any(key not in summary for key in _QUALIFICATION_EVIDENCE_FIELDS):
        raise ExpertEvaluationError(
            "p3_strat_003_input_artifact_invalid", "qualification evidence summary is incomplete"
        )
    evidence = {key: summary[key] for key in _QUALIFICATION_EVIDENCE_FIELDS}
    if "semantic_equivalence_prerequisite" in summary:
        evidence["semantic_equivalence_prerequisite"] = summary["semantic_equivalence_prerequisite"]
    return evidence


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
    reconstruction = _missing_prior_reconstruction(raw, project=project)
    semantic_prerequisite = _semantic_equivalence_prerequisite(raw)
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
    if output.exists():
        return verify_p3_strat_003_input_artifact(
            output,
            code_revision=code_revision,
            request_digest=digest_value(raw),
        )

    target = _target_release(runtime.daily_root, target_date)
    warmup_dates = _warmup_dates(runtime.historical_base_root, decision_start, warmup_count)
    history_start = warmup_dates[0]
    chain_cache = output.parent / f".{output.name}.prior-chain"
    shutil.rmtree(chain_cache, ignore_errors=True)
    try:
        chain, recovered_prior_packages = _load_bound_daily_chain(
            config=runtime,
            target=target,
            cache_root=chain_cache,
            reconstruction=reconstruction,
            semantic_prerequisite=semantic_prerequisite,
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
    finally:
        shutil.rmtree(chain_cache, ignore_errors=True)
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
                "covered_after_date": item.covered_after_date.isoformat(),
                "prior_package_id": item.prior_package_id,
                "prior_manifest_sha256": item.prior_manifest_sha256,
            }
            for item in chain
        ],
        "prior_chain_recovery": {
            "mutated_local_daily_root": False,
            "remote_source_authority": "cn01_rrsync_forced_read_only",
            "recovered_package_count": len(recovered_prior_packages),
            "recovered_packages": list(recovered_prior_packages),
        },
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
    if semantic_prerequisite is not None:
        summary["semantic_equivalence_prerequisite"] = (
            semantic_prerequisite.receipt()
        )
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
        "qualification_evidence": _qualification_evidence(summary),
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
    if semantic_prerequisite is not None:
        manifest_payload["semantic_equivalence_result_digest"] = (
            semantic_prerequisite.result_digest
        )
    manifest = {
        **manifest_payload,
        "result_digest": digest_value(manifest_payload),
    }
    manifest_path = output / "manifest.json"
    manifest_bytes = canonical_json_bytes(manifest) + b"\n"
    if len(manifest_bytes) > _MAX_QUALIFICATION_MANIFEST_BYTES:
        raise ExpertEvaluationError(
            "p3_strat_003_input_artifact_invalid",
            "complete qualification manifest exceeds the 512 KiB evidence limit",
        )
    manifest_path.write_bytes(manifest_bytes)
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
    if (root / "manifest.json").stat().st_size > _MAX_QUALIFICATION_MANIFEST_BYTES:
        raise ExpertEvaluationError(
            "p3_strat_003_input_artifact_invalid",
            "complete qualification manifest exceeds the 512 KiB evidence limit",
        )
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
    if manifest.get("qualification_evidence") != _qualification_evidence(summary):
        raise ExpertEvaluationError(
            "p3_strat_003_input_artifact_drift",
            "qualification evidence projection differs from its bound summary",
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
    semantic_result_digest = manifest.get("semantic_equivalence_result_digest")
    semantic_summary = summary.get("semantic_equivalence_prerequisite")
    if semantic_result_digest is None:
        if semantic_summary is not None:
            raise ExpertEvaluationError(
                "p3_strat_003_input_artifact_invalid",
                "semantic-equivalence summary is unbound",
            )
    elif (
        not isinstance(semantic_summary, dict)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", str(semantic_result_digest))
        is None
        or semantic_summary.get("result_digest") != semantic_result_digest
        or semantic_summary.get("disposition")
        != "PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE"
        or semantic_summary.get("original_manifest_claimed_reproduced") is not False
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_input_artifact_invalid",
            "semantic-equivalence binding differs",
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
    result = {
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
    if semantic_result_digest is not None:
        result["semantic_equivalence_result_digest"] = str(
            semantic_result_digest
        )
    return result


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
