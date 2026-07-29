"""Dual-lane DWD/DWS candidate, arbitration, and release-set contracts."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quanthus.pipelines.contracts import OdsRelease, verify_ods_release
from quanthus.pipelines.daily_warehouse import (
    DailyWarehouseError,
    build_daily_dwd_revision,
    build_daily_dws_revision,
    check_daily_dwd_revision,
    check_daily_dws_revision,
)


class DualLaneError(RuntimeError):
    """Raised when a dual-lane artifact or transition violates its frozen contract."""


_HEX_OBJECT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_LANES = ("cn01", "hermes")
_REQUIRED_MARKET_DAILY_ARTIFACTS = frozenset(
    {
        "daily_ohlcv_none_delta",
        "daily_status_observation_delta",
        "security_master",
        "stock_list",
        "trading_calendar",
    }
)
_EXACT_ATTEMPT_SELECTION = "exact_ods_current_v1"


TrustedClock = Callable[[], datetime]


def _trusted_now(clock: TrustedClock | None = None) -> datetime:
    value = datetime.now(timezone.utc) if clock is None else clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise DualLaneError("trusted clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _parse_aware_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise DualLaneError(f"{field} must be a timezone-aware ISO-8601 datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DualLaneError(f"{field} must be a timezone-aware ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DualLaneError(f"{field} must be a timezone-aware ISO-8601 datetime")
    return parsed


def _parse_schedule_time(value: object, *, field: str) -> time:
    if not isinstance(value, str):
        raise DualLaneError(f"schedule.{field} must use HH:MM:SS")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise DualLaneError(f"schedule.{field} must use HH:MM:SS") from exc
    if parsed.tzinfo is not None or parsed.microsecond != 0:
        raise DualLaneError(f"schedule.{field} must use local HH:MM:SS")
    return parsed


def _epoch_eligibility(policy: DualLanePolicy, market_date: date) -> dict[str, str]:
    zone = ZoneInfo(policy.timezone)

    def instant(value: time) -> str:
        return datetime.combine(market_date, value, tzinfo=zone).isoformat()

    return {
        "timezone": policy.timezone,
        "epoch_publish_at": instant(policy.epoch_publish_time),
        "candidate_build_at": instant(policy.candidate_build_time),
        "candidate_cutoff_at": instant(policy.arbitration_time),
        "arbitration_at": instant(policy.arbitration_time),
        "completion_deadline_at": instant(policy.completion_deadline),
        "attempt_selection": policy.attempt_selection,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _epoch_input_digest(*digests: str) -> str:
    values: list[str] = []
    for digest in digests:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise DualLaneError("epoch input digest is invalid")
        values.append(digest.removeprefix("sha256:"))
    payload = "".join(values).encode("ascii")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _derive_epoch_id(
    *, market_date: date, code_revision: str, input_digest: str
) -> str:
    if _HEX_OBJECT.fullmatch(code_revision) is None:
        raise DualLaneError("code_revision must be a full lowercase Git object id")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", input_digest) is None:
        raise DualLaneError("epoch input digest is invalid")
    return (
        f"epoch-{market_date.strftime('%Y%m%d')}-{code_revision[:12]}-"
        f"{input_digest.removeprefix('sha256:')[:12]}"
    )


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DualLaneError(f"cannot load JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise DualLaneError(f"JSON value must be an object: {path}")
    return value


def _load_object_with_digest(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DualLaneError(f"cannot load JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise DualLaneError(f"JSON value must be an object: {path}")
    return value, f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o640)
            json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise DualLaneError(f"{key} must be a non-empty string")
    return result


def _safe_relative(value: str, *, field: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.as_posix() == "." or path.is_absolute() or ".." in path.parts:
        raise DualLaneError(f"{field} must be a safe relative path")
    return path


def _resolve_inside(root: Path, value: str, *, field: str) -> Path:
    relative = _safe_relative(value, field=field)
    resolved = root.joinpath(*relative.parts).resolve()
    if root.resolve() not in resolved.parents:
        raise DualLaneError(f"{field} escapes its artifact root")
    return resolved


def _manifest_files(root: Path, *, exclude: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        if path.is_symlink():
            raise DualLaneError(f"symbolic links are forbidden: {relative}")
        files.append(
            {
                "path": relative,
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return files


def _verify_files(root: Path, files: object) -> None:
    if not isinstance(files, list) or not files:
        raise DualLaneError("artifact manifest must contain files")
    seen: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, Mapping):
            raise DualLaneError(f"files[{index}] must be an object")
        value = _required_string(item, "path")
        if value in seen:
            raise DualLaneError(f"duplicate file path: {value}")
        seen.add(value)
        path = _resolve_inside(root, value, field=f"files[{index}].path")
        if path.is_symlink() or not path.is_file():
            raise DualLaneError(f"artifact file is missing or linked: {value}")
        size = item.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise DualLaneError(f"files[{index}].size_bytes is invalid")
        if path.stat().st_size != size or _sha256(path) != item.get("sha256"):
            raise DualLaneError(f"artifact file integrity failed: {value}")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() not in {"manifest.json", "READY"}
    }
    if actual != seen:
        raise DualLaneError(
            "artifact manifest file set differs from disk: "
            f"unlisted={sorted(actual - seen)}, missing={sorted(seen - actual)}"
        )


def _verify_ready(root: Path, *, manifest_name: str = "manifest.json") -> dict[str, Any]:
    manifest_path = root / manifest_name
    ready_path = root / "READY"
    if not manifest_path.is_file() or not ready_path.is_file():
        raise DualLaneError(f"artifact is missing {manifest_name} or READY: {root}")
    if ready_path.read_text(encoding="ascii").strip() != _sha256(manifest_path):
        raise DualLaneError(f"READY does not bind {manifest_name}: {root}")
    return _load_object(manifest_path)


def _make_transfer_readable(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise DualLaneError(f"symbolic links are forbidden: {path.relative_to(root)}")
        path.chmod(0o750 if path.is_dir() else 0o640)
    root.chmod(0o750)


def _seal(staging: Path, output: Path, manifest: Mapping[str, Any]) -> None:
    _write_json(staging / "manifest.json", manifest)
    (staging / "READY").write_text(f"{_sha256(staging / 'manifest.json')}\n", encoding="ascii")
    _make_transfer_readable(staging)
    os.replace(staging, output)


@dataclass(frozen=True)
class DualLanePolicy:
    contract_id: str
    timezone: str
    primary_lane: str
    standby_lane: str
    maximum_consecutive_standby_absence: int
    fallback_failure_reasons: tuple[str, ...]
    operational_ads_required: bool
    delete_enabled: bool
    epoch_publish_time: time
    candidate_build_time: time
    arbitration_time: time
    completion_deadline: time
    attempt_selection: str
    lease_name: str
    lease_duration_seconds: int
    target_publisher_owner: str
    legacy_publisher_owner: str
    cutover_gate_artifact: str
    cutover_environment: str
    recovery_minimum_copies: int
    correction_horizon_days: int
    restore_rpo_seconds: int
    restore_rto_seconds: int
    runtime: Mapping[str, Any]
    path: Path
    digest: str


def load_dual_lane_policy(path: Path) -> DualLanePolicy:
    payload = _load_object(path)
    if payload.get("schema_version") != 1:
        raise DualLaneError("unsupported dual-lane policy schema_version")
    timezone_name = _required_string(payload, "timezone")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise DualLaneError(f"unknown dual-lane timezone: {timezone_name}") from exc
    if timezone_name != "Asia/Shanghai":
        raise DualLaneError("dual-lane policy must use Asia/Shanghai")
    roles = payload.get("roles")
    arbitration = payload.get("arbitration")
    publication = payload.get("publication")
    retention = payload.get("retention")
    runtime = payload.get("runtime")
    schedule = payload.get("schedule")
    qualification = payload.get("qualification")
    ads = payload.get("ads")
    correction = payload.get("correction")
    if not all(
        isinstance(item, Mapping)
        for item in (
            roles,
            arbitration,
            publication,
            retention,
            runtime,
            schedule,
            qualification,
            ads,
            correction,
        )
    ):
        raise DualLaneError(
            "dual-lane roles/arbitration/publication/retention/runtime/schedule/"
            "qualification/ads must be objects"
        )
    assert isinstance(roles, Mapping)
    assert isinstance(arbitration, Mapping)
    assert isinstance(publication, Mapping)
    assert isinstance(retention, Mapping)
    assert isinstance(runtime, Mapping)
    assert isinstance(schedule, Mapping)
    assert isinstance(qualification, Mapping)
    assert isinstance(ads, Mapping)
    assert isinstance(correction, Mapping)
    if roles.get("cn01") != "fixed_primary_candidate_producer":
        raise DualLaneError("CN01 must be the fixed primary candidate producer")
    if roles.get("hermes") != "standby_candidate_producer":
        raise DualLaneError("Hermes must be the standby candidate producer")
    if roles.get("oci") != "epoch_arbitration_ads_release_authority":
        raise DualLaneError("OCI must own epoch, arbitration, ADS, and release publication")
    primary = _required_string(arbitration, "primary_lane")
    standby = _required_string(arbitration, "standby_lane")
    if (primary, standby) != _LANES:
        raise DualLaneError("dual-lane primary/standby order must remain CN01 then Hermes")
    maximum_absence = arbitration.get("maximum_consecutive_standby_absence")
    if (
        not isinstance(maximum_absence, int)
        or isinstance(maximum_absence, bool)
        or maximum_absence < 0
    ):
        raise DualLaneError("maximum_consecutive_standby_absence must be nonnegative")
    reasons = arbitration.get("fallback_failure_reasons")
    if not isinstance(reasons, list) or not reasons or any(not isinstance(x, str) for x in reasons):
        raise DualLaneError("fallback_failure_reasons must be a non-empty string list")
    if arbitration.get("cross_lane_divergence_policy") != "withhold_both":
        raise DualLaneError("cross-lane divergence must withhold both candidates")
    if arbitration.get("row_or_artifact_mixing") is not False:
        raise DualLaneError("row and artifact mixing must remain disabled")
    if publication.get("consumer_pointer") != "release-set_CURRENT.json":
        raise DualLaneError("release-set CURRENT must be the sole consumer pointer")
    if publication.get("operational_ads_required") is not True:
        raise DualLaneError("operational ADS must be required before release publication")
    if publication.get("legacy_dataset_pointers_are_authoritative") is not False:
        raise DualLaneError("legacy dataset pointers must not be target publication authority")
    if publication.get("postgresql_projection_authoritative") is not False:
        raise DualLaneError("PostgreSQL must remain a non-authoritative projection")
    fenced_lease = publication.get("fenced_lease")
    cutover_gate = publication.get("cutover_gate")
    if not isinstance(fenced_lease, Mapping) or not isinstance(cutover_gate, Mapping):
        raise DualLaneError("publication fencing and cutover gate contracts are required")
    if (
        fenced_lease.get("lease_name") != "canonical-publisher"
        or fenced_lease.get("duration_seconds") != 1860
        or fenced_lease.get("pointer_cas") != "sha256-precondition-v1"
        or fenced_lease.get("target_owner") != "dual-lane-target"
        or fenced_lease.get("legacy_owner") != "legacy-oci"
    ):
        raise DualLaneError("canonical publisher fenced lease contract is invalid")
    if dict(cutover_gate) != {
        "artifact": "GO_FOR_CUTOVER.json",
        "environment": "GO_FOR_CUTOVER",
        "live_activation_default": False,
        "required_evidence": [
            "legacy_writer_fenced",
            "retention_gate_passed",
            "restore_drill_passed",
        ],
    }:
        raise DualLaneError("GO_FOR_CUTOVER contract is invalid")
    if retention.get("migration_mode") != "copy_only":
        raise DualLaneError("initial dual-lane migration must be copy_only")
    if retention.get("delete_enabled") is not False:
        raise DualLaneError("initial dual-lane migration must keep deletion disabled")
    for field in (
        "retain_legacy_oci_ods",
        "retain_legacy_dwd_dws",
        "retain_all_migration_candidates",
    ):
        if retention.get(field) is not True:
            raise DualLaneError(f"retention.{field} must remain true during migration")
    accepted_ods_recovery = retention.get("accepted_ods_recovery")
    if not isinstance(accepted_ods_recovery, Mapping) or dict(accepted_ods_recovery) != {
        "minimum_failure_independent_copies": 2,
        "minimum_correction_horizon_days": 90,
        "restore_rpo_seconds": 0,
        "restore_rto_seconds": 3600,
        "purge_requires": [
            "correction_horizon_expired",
            "zero_descendant_references",
            "successful_restore_drill",
            "explicit_inventory_approval",
        ],
    }:
        raise DualLaneError("accepted ODS recovery and retention contract is invalid")
    if dict(correction) != {
        "state_machine": "immutable-correction-v1",
        "approval_required": True,
        "late_equivalent_republishes": False,
        "revoked_generation_publishable": False,
        "rollback_requires_fenced_cas": True,
        "reason_code_required": True,
    }:
        raise DualLaneError("correction and rollback state machine contract is invalid")
    packages = runtime.get("packages")
    if runtime.get("python_series") != "3.10" or not isinstance(packages, Mapping):
        raise DualLaneError("dual-lane runtime must freeze Python 3.10 and package versions")
    required_packages = {"duckdb", "polars", "pyarrow", "quanthus"}
    if set(packages) != required_packages or any(
        not isinstance(name, str) or not isinstance(version, str) or not version
        for name, version in packages.items()
    ):
        raise DualLaneError("dual-lane runtime package contract is incomplete")
    runtime_contract = {
        "python_series": "3.10",
        "packages": dict(sorted(packages.items())),
    }
    expected_schedule = {
        "epoch_publish_time": "16:30:00",
        "candidate_build_time": "19:50:00",
        "arbitration_time": "20:00:00",
        "completion_deadline": "20:30:00",
    }
    if dict(schedule) != expected_schedule:
        raise DualLaneError("dual-lane schedule differs from the frozen v1 window")
    if (
        arbitration.get("eligible_attempt_source") != "lane_ods_current"
        or arbitration.get("attempt_selection") != "exact_only"
    ):
        raise DualLaneError("dual-lane attempts must use the exact lane ODS CURRENT descriptor")
    if dict(qualification) != {
        "calibration_open_sessions": 1,
        "qualification_open_sessions": 2,
        "closed_session_fixtures": 1,
        "threshold_retuning_during_qualification": False,
    }:
        raise DualLaneError("dual-lane qualification contract is invalid")
    if (
        ads.get("operational_profile") != "operational_daily_v1"
        or ads.get("portable_profiles") != ["strategy_replay", "event_library", "ml_training"]
        or ads.get("mandatory_year_partitioning") is not False
        or ads.get("artifact_discovery") != "manifest_tables_parts"
    ):
        raise DualLaneError("dual-lane ADS contract is invalid")
    return DualLanePolicy(
        contract_id=_required_string(payload, "contract_id"),
        timezone=timezone_name,
        primary_lane=primary,
        standby_lane=standby,
        maximum_consecutive_standby_absence=maximum_absence,
        fallback_failure_reasons=tuple(reasons),
        operational_ads_required=True,
        delete_enabled=False,
        epoch_publish_time=_parse_schedule_time(
            schedule["epoch_publish_time"], field="epoch_publish_time"
        ),
        candidate_build_time=_parse_schedule_time(
            schedule["candidate_build_time"], field="candidate_build_time"
        ),
        arbitration_time=_parse_schedule_time(
            schedule["arbitration_time"], field="arbitration_time"
        ),
        completion_deadline=_parse_schedule_time(
            schedule["completion_deadline"], field="completion_deadline"
        ),
        attempt_selection=_EXACT_ATTEMPT_SELECTION,
        lease_name="canonical-publisher",
        lease_duration_seconds=1860,
        target_publisher_owner="dual-lane-target",
        legacy_publisher_owner="legacy-oci",
        cutover_gate_artifact="GO_FOR_CUTOVER.json",
        cutover_environment="GO_FOR_CUTOVER",
        recovery_minimum_copies=2,
        correction_horizon_days=90,
        restore_rpo_seconds=0,
        restore_rto_seconds=3600,
        runtime=runtime_contract,
        path=path.resolve(),
        digest=_sha256(path.resolve()),
    )


@dataclass(frozen=True)
class EpochInput:
    root: Path
    epoch_id: str
    market_date: date
    manifest_sha256: str
    contract_id: str
    policy_sha256: str
    code_revision: str
    source_kind: str
    source_revision: str
    source_manifest_sha256: str
    source_path: Path | None
    universe_policy_path: Path
    code_validity_path: Path
    security_master_path: Path | None
    eligibility: Mapping[str, str] | None


def _verified_epoch_eligibility(
    value: object, *, market_date: date
) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DualLaneError("epoch eligibility must be an object")
    expected_fields = {
        "timezone",
        "epoch_publish_at",
        "candidate_build_at",
        "candidate_cutoff_at",
        "arbitration_at",
        "completion_deadline_at",
        "attempt_selection",
    }
    if set(value) != expected_fields:
        raise DualLaneError("epoch eligibility fields are incomplete")
    if value.get("timezone") != "Asia/Shanghai":
        raise DualLaneError("epoch eligibility timezone must be Asia/Shanghai")
    if value.get("attempt_selection") != _EXACT_ATTEMPT_SELECTION:
        raise DualLaneError("epoch attempt selection must be exact")
    instants = {
        field: _parse_aware_datetime(value[field], field=f"eligibility.{field}")
        for field in (
            "epoch_publish_at",
            "candidate_build_at",
            "candidate_cutoff_at",
            "arbitration_at",
            "completion_deadline_at",
        )
    }
    if any(
        item.astimezone(ZoneInfo("Asia/Shanghai")).date() != market_date
        for item in instants.values()
    ):
        raise DualLaneError("epoch eligibility timestamps must belong to market_date")
    if not (
        instants["epoch_publish_at"]
        < instants["candidate_build_at"]
        < instants["candidate_cutoff_at"]
        == instants["arbitration_at"]
        < instants["completion_deadline_at"]
    ):
        raise DualLaneError("epoch eligibility timestamps are not strictly ordered")
    return {field: str(value[field]) for field in sorted(expected_fields)}


def verify_epoch_input(root: Path) -> EpochInput:
    root = root.resolve()
    manifest = _verify_ready(root)
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "dual_lane_epoch_input":
        raise DualLaneError("unsupported epoch-input manifest")
    _verify_files(root, manifest.get("files"))
    try:
        market_date = date.fromisoformat(_required_string(manifest, "market_date"))
    except ValueError as exc:
        raise DualLaneError("epoch market_date must use YYYY-MM-DD") from exc
    source = manifest.get("canonical_source")
    references = manifest.get("shared_references")
    if not isinstance(source, Mapping) or not isinstance(references, Mapping):
        raise DualLaneError("epoch source and references must be objects")
    source_kind = _required_string(source, "kind")
    if source_kind not in {"daily_partition", "historical_base"}:
        raise DualLaneError("epoch canonical source kind is invalid")
    source_path: Path | None = None
    if source_kind == "daily_partition":
        source_path = _resolve_inside(root, _required_string(source, "path"), field="source.path")
        checked = check_daily_dwd_revision(source_path)
        if checked.revision != source.get("revision"):
            raise DualLaneError("epoch parent revision does not match its manifest")
        if _sha256(source_path / "manifest.json") != source.get("manifest_sha256"):
            raise DualLaneError("epoch parent manifest checksum mismatch")
    else:
        metadata_path = _resolve_inside(
            root,
            _required_string(source, "metadata_path"),
            field="source.metadata_path",
        )
        if _sha256(metadata_path) != source.get("metadata_sha256"):
            raise DualLaneError("epoch historical-base metadata checksum mismatch")
    universe_path = _resolve_inside(
        root,
        _required_string(references, "universe_policy_path"),
        field="shared_references.universe_policy_path",
    )
    validity_path = _resolve_inside(
        root,
        _required_string(references, "code_validity_path"),
        field="shared_references.code_validity_path",
    )
    security_master_value = references.get("security_master_path")
    security_master_digest = references.get("security_master_sha256")
    security_master_path: Path | None = None
    if security_master_value is not None or security_master_digest is not None:
        if not isinstance(security_master_value, str) or not isinstance(
            security_master_digest, str
        ):
            raise DualLaneError("epoch security-master reference is incomplete")
        security_master_path = _resolve_inside(
            root,
            security_master_value,
            field="shared_references.security_master_path",
        )
    if _sha256(universe_path) != references.get("universe_policy_sha256"):
        raise DualLaneError("epoch universe-policy checksum mismatch")
    if _sha256(validity_path) != references.get("code_validity_sha256"):
        raise DualLaneError("epoch code-validity checksum mismatch")
    if security_master_path is not None and _sha256(security_master_path) != security_master_digest:
        raise DualLaneError("epoch security-master checksum mismatch")
    code_revision = _required_string(manifest, "code_revision")
    if _HEX_OBJECT.fullmatch(code_revision) is None:
        raise DualLaneError("epoch code_revision must be a full lowercase Git object id")
    epoch_id = _required_string(manifest, "epoch_id")
    policy_sha256 = _required_string(manifest, "policy_sha256")
    declared_input_digest = manifest.get("input_digest")
    if declared_input_digest is not None:
        if security_master_path is None:
            raise DualLaneError("modern epoch input digest requires a security master")
        input_digest = _epoch_input_digest(
            _required_string(source, "source_pointer_sha256"),
            _required_string(source, "manifest_sha256"),
            policy_sha256,
            _required_string(references, "universe_policy_sha256"),
            _required_string(references, "code_validity_sha256"),
            _required_string(references, "security_master_sha256"),
        )
        if declared_input_digest != input_digest:
            raise DualLaneError("epoch input digest does not match its sealed inputs")
        if epoch_id != _derive_epoch_id(
            market_date=market_date,
            code_revision=code_revision,
            input_digest=input_digest,
        ):
            raise DualLaneError("epoch_id does not match its sealed inputs")
    eligibility = _verified_epoch_eligibility(
        manifest.get("eligibility"), market_date=market_date
    )
    return EpochInput(
        root=root,
        epoch_id=epoch_id,
        market_date=market_date,
        manifest_sha256=_sha256(root / "manifest.json"),
        contract_id=_required_string(manifest, "contract_id"),
        policy_sha256=policy_sha256,
        code_revision=code_revision,
        source_kind=source_kind,
        source_revision=_required_string(source, "revision"),
        source_manifest_sha256=_required_string(source, "manifest_sha256"),
        source_path=source_path,
        universe_policy_path=universe_path,
        code_validity_path=validity_path,
        security_master_path=security_master_path,
        eligibility=eligibility,
    )


def publish_epoch_input(
    *,
    output: Path,
    epoch_id: str,
    market_date: date,
    policy: DualLanePolicy,
    universe_policy_path: Path,
    code_validity_path: Path,
    security_master_path: Path,
    code_revision: str,
    parent_pointer: Path | None = None,
    base_pointer: Path | None = None,
    clock: TrustedClock | None = None,
) -> EpochInput:
    if _SAFE_ID.fullmatch(epoch_id) is None:
        raise DualLaneError("epoch_id contains unsupported characters")
    if _HEX_OBJECT.fullmatch(code_revision) is None:
        raise DualLaneError("code_revision must be a full lowercase Git object id")
    if (parent_pointer is None) == (base_pointer is None):
        raise DualLaneError("epoch publication requires exactly one parent or base pointer")
    eligibility = _epoch_eligibility(policy, market_date)
    output = output.resolve()
    if output.exists():
        existing = verify_epoch_input(output)
        requested_pointer = parent_pointer if parent_pointer is not None else base_pointer
        assert requested_pointer is not None
        existing_manifest = _load_object(existing.root / "manifest.json")
        existing_source = existing_manifest.get("canonical_source")
        expected_source_kind = (
            "daily_partition" if parent_pointer is not None else "historical_base"
        )
        references_match = (
            _sha256(existing.universe_policy_path)
            == _sha256(universe_policy_path.resolve(strict=True))
            and _sha256(existing.code_validity_path)
            == _sha256(code_validity_path.resolve(strict=True))
            and existing.security_master_path is not None
            and _sha256(existing.security_master_path)
            == _sha256(security_master_path.resolve(strict=True))
        )
        if (
            existing.epoch_id != epoch_id
            or existing.market_date != market_date
            or existing.contract_id != policy.contract_id
            or existing.policy_sha256 != policy.digest
            or existing.code_revision != code_revision
            or existing.source_kind != expected_source_kind
            or not isinstance(existing_source, Mapping)
            or existing_source.get("source_pointer_sha256")
            != _sha256(requested_pointer.resolve(strict=True))
            or not references_match
            or existing.eligibility != eligibility
        ):
            raise DualLaneError("existing epoch does not match the request")
        return existing
    now = _trusted_now(clock)
    epoch_publish_at = _parse_aware_datetime(
        eligibility["epoch_publish_at"], field="eligibility.epoch_publish_at"
    ).astimezone(timezone.utc)
    candidate_cutoff_at = _parse_aware_datetime(
        eligibility["candidate_cutoff_at"], field="eligibility.candidate_cutoff_at"
    ).astimezone(timezone.utc)
    if now < epoch_publish_at or now > candidate_cutoff_at:
        raise DualLaneError("trusted clock is outside the epoch publication window")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        references = staging / "references"
        references.mkdir()
        universe_target = references / "p1-universe.json"
        validity_target = references / "security-code-validity.json"
        security_master_target = references / "ref_security_master.parquet"
        shutil.copy2(universe_policy_path.resolve(strict=True), universe_target)
        shutil.copy2(code_validity_path.resolve(strict=True), validity_target)
        shutil.copy2(security_master_path.resolve(strict=True), security_master_target)
        if parent_pointer is not None:
            pointer_path = parent_pointer.resolve(strict=True)
            pointer, pointer_digest = _load_object_with_digest(pointer_path)
            parent_path = Path(_required_string(pointer, "revision_path")).resolve()
            checked = check_daily_dwd_revision(parent_path)
            parent_manifest_sha256 = _sha256(parent_path / "manifest.json")
            if (
                pointer.get("revision") != checked.revision
                or pointer.get("market_date") != checked.market_date.isoformat()
                or pointer.get("manifest_sha256") != parent_manifest_sha256
            ):
                raise DualLaneError("epoch parent pointer does not match its DWD revision")
            if checked.market_date > market_date:
                raise DualLaneError("epoch parent cannot be later than its market date")
            parent_target = staging / "canonical" / checked.revision
            shutil.copytree(parent_path, parent_target)
            copied_parent = check_daily_dwd_revision(parent_target)
            if (
                copied_parent.revision != checked.revision
                or copied_parent.market_date != checked.market_date
                or _sha256(parent_target / "manifest.json") != parent_manifest_sha256
            ):
                raise DualLaneError("copied epoch parent differs from its verified source")
            source = {
                "kind": "daily_partition",
                "revision": checked.revision,
                "market_date": checked.market_date.isoformat(),
                "path": parent_target.relative_to(staging).as_posix(),
                "manifest_sha256": parent_manifest_sha256,
                "source_pointer_sha256": pointer_digest,
            }
        else:
            assert base_pointer is not None
            pointer, pointer_digest = _load_object_with_digest(base_pointer.resolve(strict=True))
            revision = _required_string(pointer, "revision")
            revision_path = Path(_required_string(pointer, "revision_path")).resolve()
            metadata_source = revision_path / "META.json"
            if not metadata_source.is_file():
                raise DualLaneError("historical base is missing META.json")
            metadata_target = staging / "canonical" / revision / "META.json"
            metadata_target.parent.mkdir(parents=True)
            shutil.copy2(metadata_source, metadata_target)
            declared = pointer.get("meta_sha256")
            if declared != _sha256(metadata_target):
                raise DualLaneError("historical-base pointer META checksum mismatch")
            metadata = _load_object(metadata_target)
            if _required_string(metadata, "revision") != revision:
                raise DualLaneError("historical-base pointer does not match its metadata")
            _required_string(metadata, "kind")
            source = {
                "kind": "historical_base",
                "revision": revision,
                "manifest_sha256": str(declared),
                "metadata_path": metadata_target.relative_to(staging).as_posix(),
                "metadata_sha256": _sha256(metadata_target),
                "source_pointer_sha256": pointer_digest,
            }
        input_digest = _epoch_input_digest(
            _required_string(source, "source_pointer_sha256"),
            _required_string(source, "manifest_sha256"),
            policy.digest,
            _sha256(universe_target),
            _sha256(validity_target),
            _sha256(security_master_target),
        )
        expected_epoch_id = _derive_epoch_id(
            market_date=market_date,
            code_revision=code_revision,
            input_digest=input_digest,
        )
        if epoch_id != expected_epoch_id:
            raise DualLaneError("epoch_id does not match the sealed governed inputs")
        if _trusted_now(clock) > candidate_cutoff_at:
            raise DualLaneError("epoch publication completed after its frozen cutoff")
        manifest = {
            "schema_version": 1,
            "kind": "dual_lane_epoch_input",
            "epoch_id": epoch_id,
            "market_date": market_date.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "contract_id": policy.contract_id,
            "policy_sha256": policy.digest,
            "code_revision": code_revision,
            "input_digest": input_digest,
            "eligibility": eligibility,
            "canonical_source": source,
            "shared_references": {
                "universe_policy_path": universe_target.relative_to(staging).as_posix(),
                "universe_policy_sha256": _sha256(universe_target),
                "code_validity_path": validity_target.relative_to(staging).as_posix(),
                "code_validity_sha256": _sha256(validity_target),
                "security_master_path": security_master_target.relative_to(staging).as_posix(),
                "security_master_sha256": _sha256(security_master_target),
            },
            "files": _manifest_files(staging),
        }
        _seal(staging, output, manifest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_epoch_input(output)


def write_epoch_current(epoch: EpochInput, pointer: Path) -> None:
    relative = os.path.relpath(epoch.root, start=pointer.parent.resolve())
    _atomic_json(
        pointer.resolve(),
        {
            "schema_version": 1,
            "market_date": epoch.market_date.isoformat(),
            "epoch_id": epoch.epoch_id,
            "epoch_path": Path(relative).as_posix(),
            "manifest_sha256": epoch.manifest_sha256,
        },
    )


def load_epoch_current(pointer: Path) -> EpochInput:
    value = _load_object(pointer.resolve())
    root = Path(_required_string(value, "epoch_path"))
    if not root.is_absolute():
        root = pointer.resolve().parent / root
    epoch = verify_epoch_input(root)
    if epoch.manifest_sha256 != value.get("manifest_sha256"):
        raise DualLaneError("epoch CURRENT manifest checksum mismatch")
    return epoch


def _json_scalar(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def _table_digest(path: Path, *, excluded_columns: frozenset[str] = frozenset()) -> str:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise DualLaneError("P1 dependencies are required for candidate content hashing") from exc
    # Reading through ParquetFile prevents a parent ``key=value`` directory from
    # being interpreted as a Hive partition and injected into the file schema.
    table = pq.ParquetFile(path).read()
    columns = sorted(name for name in table.column_names if name not in excluded_columns)
    table = table.select(columns)
    sort_keys = [
        (name, "ascending")
        for name in columns
        if name in {"market_date", "security_id", "stock_code"}
    ]
    if sort_keys:
        table = table.sort_by(sort_keys)
    digest = hashlib.sha256()
    digest.update(json.dumps(columns, separators=(",", ":")).encode("ascii"))
    for row in table.to_pylist():
        normalized = {key: _json_scalar(value) for key, value in row.items()}
        digest.update(
            json.dumps(
                normalized,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def _business_content_digest(dwd_root: Path, dws_root: Path, market_date: date) -> str:
    dwd_manifest = _load_object(dwd_root / "manifest.json")
    dws_manifest = _load_object(dws_root / "manifest.json")
    partition = dwd_root / f"market_date={market_date.isoformat()}"
    value = {
        "bars": _table_digest(
            partition / "part-00000.parquet",
            excluded_columns=frozenset({"source_built_at", "ods_release_id", "dwd_revision"}),
        ),
        "status": _table_digest(
            partition / "daily_status_observation.parquet",
            excluded_columns=frozenset({"source_built_at", "ods_release_id", "dwd_revision"}),
        ),
        "validity": _table_digest(dwd_root / "ref_security_code_validity.parquet"),
        "dwd_counts": dwd_manifest.get("counts"),
        "dwd_policy": dwd_manifest.get("policy"),
        "dws_metrics": dws_manifest.get("metrics"),
    }
    return _canonical_digest(value)


def _runtime_identity() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for package in ("duckdb", "pyarrow", "polars", "quanthus"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = "source-tree"
    return {
        "python_series": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": dict(sorted(packages.items())),
    }


def select_market_daily_ods_release(
    date_root: Path,
    *,
    market_date: date,
    preferred_attempt: str | None = None,
) -> Path:
    """Resolve only the exact complete attempt named by the date-local ODS CURRENT."""

    date_root = date_root.resolve()
    pointer_path = date_root / "CURRENT.json"
    pointer, _ = _load_object_with_digest(pointer_path)
    attempt_id = _required_string(pointer, "attempt_id")
    if _SAFE_ID.fullmatch(attempt_id) is None:
        raise DualLaneError("ODS CURRENT attempt id is unsafe")
    if preferred_attempt is not None and preferred_attempt != attempt_id:
        raise DualLaneError("preferred ODS attempt differs from CURRENT")
    attempt = (date_root / attempt_id).resolve()
    if date_root not in attempt.parents or attempt.parent != date_root:
        raise DualLaneError("ODS CURRENT attempt must be an immediate child")
    try:
        verified = verify_ods_release(attempt)
    except (OSError, ValueError, RuntimeError) as exc:
        raise DualLaneError("exact ODS CURRENT attempt is not complete") from exc
    artifact_names = {artifact.name for artifact in verified.release.artifacts}
    if (
        pointer.get("schema_version") != 1
        or pointer.get("schedule") != "daily"
        or pointer.get("market_date") != market_date.isoformat()
        or pointer.get("release_id") != verified.release.release_id
        or pointer.get("manifest_sha256") != verified.manifest_sha256
        or pointer.get("ready_at") != verified.release.created_at.isoformat()
        or verified.release.schedule != "daily"
        or verified.release.market_date != market_date
        or not artifact_names >= _REQUIRED_MARKET_DAILY_ARTIFACTS
    ):
        raise DualLaneError("ODS CURRENT does not bind one eligible market-daily attempt")
    return attempt


def _source_input_lineage(release: OdsRelease) -> list[dict[str, str]]:
    return [
        {
            "name": artifact.name,
            "sha256": artifact.sha256,
            "source_revision": artifact.source_revision,
            "knowledge_cutoff": artifact.knowledge_cutoff.isoformat(),
        }
        for artifact in sorted(release.artifacts, key=lambda item: item.name)
    ]


def _verify_ods_current_selection(
    *, release_dir: Path, attempt_id: str, market_date: date
) -> tuple[dict[str, Any], str]:
    if release_dir.name != attempt_id or release_dir.parent.name != market_date.isoformat():
        raise DualLaneError("candidate attempt must be the exact market-date ODS child")
    pointer_path = release_dir.parent / "CURRENT.json"
    pointer, pointer_digest = _load_object_with_digest(pointer_path)
    selected = select_market_daily_ods_release(
        release_dir.parent,
        market_date=market_date,
        preferred_attempt=attempt_id,
    )
    if selected != release_dir.resolve():
        raise DualLaneError("candidate ODS release differs from exact CURRENT selection")
    return pointer, pointer_digest


def _verify_candidate_governance(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    market_date: date,
    attempt_id: str,
    ods_manifest_sha256: str,
) -> tuple[dict[str, str] | None, tuple[Mapping[str, str], ...], datetime | None]:
    eligibility_value = manifest.get("eligibility")
    selection_value = manifest.get("attempt_selection")
    source_inputs_value = manifest.get("source_inputs")
    present = (
        eligibility_value is not None,
        selection_value is not None,
        source_inputs_value is not None,
    )
    if not any(present):
        return None, (), None
    if not all(present) or not isinstance(selection_value, Mapping):
        raise DualLaneError("candidate governance lineage is incomplete")
    eligibility = _verified_epoch_eligibility(eligibility_value, market_date=market_date)
    assert eligibility is not None
    if selection_value.get("contract") != _EXACT_ATTEMPT_SELECTION:
        raise DualLaneError("candidate attempt selection contract is invalid")
    descriptor_path = _resolve_inside(
        root,
        _required_string(selection_value, "descriptor_path"),
        field="attempt_selection.descriptor_path",
    )
    release_manifest_path = _resolve_inside(
        root,
        _required_string(selection_value, "release_manifest_path"),
        field="attempt_selection.release_manifest_path",
    )
    if _sha256(descriptor_path) != selection_value.get("descriptor_sha256"):
        raise DualLaneError("candidate ODS CURRENT descriptor checksum mismatch")
    if _sha256(release_manifest_path) != ods_manifest_sha256:
        raise DualLaneError("candidate copied ODS manifest checksum mismatch")
    release = OdsRelease.load(release_manifest_path)
    if release.schedule != "daily" or release.market_date != market_date:
        raise DualLaneError("candidate copied ODS manifest is not the requested daily release")
    descriptor = _load_object(descriptor_path)
    if (
        descriptor.get("schema_version") != 1
        or descriptor.get("schedule") != "daily"
        or descriptor.get("market_date") != market_date.isoformat()
        or descriptor.get("attempt_id") != attempt_id
        or descriptor.get("release_id") != release.release_id
        or descriptor.get("manifest_sha256") != ods_manifest_sha256
        or descriptor.get("ready_at") != release.created_at.isoformat()
    ):
        raise DualLaneError("candidate ODS CURRENT descriptor does not bind its release")
    expected_inputs = _source_input_lineage(release)
    if source_inputs_value != expected_inputs:
        raise DualLaneError("candidate per-input PIT lineage differs from its ODS manifest")
    source_cutoffs = [
        _parse_aware_datetime(item["knowledge_cutoff"], field="source_inputs.knowledge_cutoff")
        for item in expected_inputs
    ]
    cutoff_at = _parse_aware_datetime(
        eligibility["candidate_cutoff_at"], field="eligibility.candidate_cutoff_at"
    )
    if any(source_cutoff > cutoff_at for source_cutoff in source_cutoffs):
        raise DualLaneError("candidate source input exceeds its PIT cutoff")
    declared_cutoff = _parse_aware_datetime(
        manifest.get("knowledge_cutoff"), field="knowledge_cutoff"
    )
    if declared_cutoff != max(source_cutoffs):
        raise DualLaneError("candidate knowledge_cutoff is not the maximum source cutoff")
    ready_at = _parse_aware_datetime(
        manifest.get("candidate_ready_at"), field="candidate_ready_at"
    )
    candidate_build_at = _parse_aware_datetime(
        eligibility["candidate_build_at"], field="eligibility.candidate_build_at"
    )
    if ready_at < candidate_build_at or ready_at > cutoff_at:
        raise DualLaneError("candidate readiness is outside its frozen build window")
    if release.created_at > ready_at or release.created_at > cutoff_at:
        raise DualLaneError("candidate ODS release was not sealed by its frozen cutoff")
    return eligibility, tuple(expected_inputs), ready_at


@dataclass(frozen=True)
class LaneCandidate:
    root: Path
    candidate_id: str
    attempt_id: str
    lane_id: str
    market_date: date
    manifest_sha256: str
    candidate_content_digest: str
    business_content_digest: str
    dwd_root: Path
    dws_root: Path
    epoch_manifest_sha256: str
    ods_manifest_sha256: str
    contract_id: str
    policy_sha256: str
    code_revision: str
    runtime: Mapping[str, Any]
    security_master_sha256: str | None
    eligibility: Mapping[str, str] | None
    source_inputs: tuple[Mapping[str, str], ...]
    candidate_ready_at: datetime | None


def verify_lane_candidate(root: Path) -> LaneCandidate:
    root = root.resolve()
    manifest = _verify_ready(root)
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "dual_lane_dwd_dws_candidate":
        raise DualLaneError("unsupported lane-candidate manifest")
    _verify_files(root, manifest.get("files"))
    lane_id = _required_string(manifest, "lane_id")
    if lane_id not in _LANES:
        raise DualLaneError("candidate lane_id is invalid")
    attempt_id = _required_string(manifest, "attempt_id")
    if _SAFE_ID.fullmatch(attempt_id) is None:
        raise DualLaneError("candidate attempt_id contains unsupported characters")
    try:
        market_date = date.fromisoformat(_required_string(manifest, "market_date"))
    except ValueError as exc:
        raise DualLaneError("candidate market_date must use YYYY-MM-DD") from exc
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise DualLaneError("candidate artifacts must be an object")
    dwd = artifacts.get("dwd")
    dws = artifacts.get("dws")
    if not isinstance(dwd, Mapping) or not isinstance(dws, Mapping):
        raise DualLaneError("candidate must bind DWD and DWS")
    dwd_root = _resolve_inside(root, _required_string(dwd, "path"), field="artifacts.dwd.path")
    dws_root = _resolve_inside(root, _required_string(dws, "path"), field="artifacts.dws.path")
    checked_dwd = check_daily_dwd_revision(dwd_root)
    dwd_manifest = _load_object(dwd_root / "manifest.json")
    if _sha256(dwd_root / "manifest.json") != dwd.get("manifest_sha256"):
        raise DualLaneError("candidate DWD manifest checksum mismatch")
    dwd_lineage = dwd_manifest.get("lineage")
    if not isinstance(dwd_lineage, Mapping):
        raise DualLaneError("candidate DWD lineage is missing")
    lineage_security_master = dwd_lineage.get("security_master_sha256")
    manifest_security_master = manifest.get("security_master_sha256")
    security_master_sha256: str | None = None
    if lineage_security_master is not None or manifest_security_master is not None:
        if (
            not isinstance(lineage_security_master, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", lineage_security_master) is None
            or lineage_security_master != manifest_security_master
        ):
            raise DualLaneError("candidate security-master lineage mismatch")
        security_master_sha256 = lineage_security_master
    dws_manifest = _load_object(dws_root / "manifest.json")
    lineage = dws_manifest.get("lineage")
    if not isinstance(lineage, Mapping):
        raise DualLaneError("candidate DWS lineage is missing")
    lineage_path = Path(_required_string(lineage, "dwd_revision_path"))
    if lineage_path.is_absolute():
        raise DualLaneError("candidate DWS lineage must be portable")
    if (dws_root / lineage_path).resolve() != dwd_root:
        raise DualLaneError("candidate DWS lineage escapes its candidate family")
    checked_dws = check_daily_dws_revision(dws_root)
    if _sha256(dws_root / "manifest.json") != dws.get("manifest_sha256"):
        raise DualLaneError("candidate DWS manifest checksum mismatch")
    if checked_dwd.market_date != market_date or checked_dws.market_date != market_date:
        raise DualLaneError("candidate artifact market_date mismatch")
    gates = manifest.get("hard_gates")
    if not isinstance(gates, Mapping) or set(gates.values()) != {"passed"}:
        raise DualLaneError("candidate hard gates are incomplete")
    business_digest = _business_content_digest(dwd_root, dws_root, market_date)
    if business_digest != manifest.get("business_content_digest"):
        raise DualLaneError("candidate business-content digest mismatch")
    code_revision = _required_string(manifest, "code_revision")
    if _HEX_OBJECT.fullmatch(code_revision) is None:
        raise DualLaneError("candidate code_revision must be a full lowercase Git object id")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping) or not isinstance(runtime.get("packages"), Mapping):
        raise DualLaneError("candidate runtime identity is invalid")
    packages = runtime["packages"]
    if any(
        not isinstance(name, str) or not isinstance(version, str) or not version
        for name, version in packages.items()
    ):
        raise DualLaneError("candidate runtime package identity is invalid")
    normalized_runtime = {
        "python_series": _required_string(runtime, "python_series"),
        "packages": dict(sorted(packages.items())),
    }
    ods_manifest_sha256 = _required_string(manifest, "ods_manifest_sha256")
    eligibility, source_inputs, candidate_ready_at = _verify_candidate_governance(
        root=root,
        manifest=manifest,
        market_date=market_date,
        attempt_id=attempt_id,
        ods_manifest_sha256=ods_manifest_sha256,
    )
    content_value: dict[str, Any] = {
        "lane_id": lane_id,
        "market_date": market_date.isoformat(),
        "epoch_manifest_sha256": manifest.get("epoch_manifest_sha256"),
        "ods_manifest_sha256": manifest.get("ods_manifest_sha256"),
        "business_content_digest": business_digest,
        "code_revision": code_revision,
        "runtime": normalized_runtime,
        "contract_id": manifest.get("contract_id"),
        "policy_sha256": manifest.get("policy_sha256"),
    }
    if security_master_sha256 is not None:
        content_value["security_master_sha256"] = security_master_sha256
    if eligibility is not None:
        content_value.update(
            {
                "eligibility": eligibility,
                "attempt_selection": _EXACT_ATTEMPT_SELECTION,
                "source_inputs": list(source_inputs),
                "knowledge_cutoff": manifest.get("knowledge_cutoff"),
            }
        )
    content_digest = _canonical_digest(content_value)
    if content_digest != manifest.get("candidate_content_digest"):
        raise DualLaneError("candidate content digest mismatch")
    return LaneCandidate(
        root=root,
        candidate_id=_required_string(manifest, "candidate_id"),
        attempt_id=attempt_id,
        lane_id=lane_id,
        market_date=market_date,
        manifest_sha256=_sha256(root / "manifest.json"),
        candidate_content_digest=content_digest,
        business_content_digest=business_digest,
        dwd_root=dwd_root,
        dws_root=dws_root,
        epoch_manifest_sha256=_required_string(manifest, "epoch_manifest_sha256"),
        ods_manifest_sha256=ods_manifest_sha256,
        contract_id=_required_string(manifest, "contract_id"),
        policy_sha256=_required_string(manifest, "policy_sha256"),
        code_revision=code_revision,
        runtime=normalized_runtime,
        security_master_sha256=security_master_sha256,
        eligibility=eligibility,
        source_inputs=source_inputs,
        candidate_ready_at=candidate_ready_at,
    )


def build_lane_candidate(
    *,
    output: Path,
    candidate_id: str,
    lane_id: str,
    attempt_id: str,
    market_date: date,
    ods_release_dir: Path,
    epoch_root: Path,
    policy: DualLanePolicy,
    code_revision: str,
    clock: TrustedClock | None = None,
) -> LaneCandidate:
    if lane_id not in _LANES:
        raise DualLaneError("lane_id must be cn01 or hermes")
    for name, value in (("candidate_id", candidate_id), ("attempt_id", attempt_id)):
        if _SAFE_ID.fullmatch(value) is None:
            raise DualLaneError(f"{name} contains unsupported characters")
    if _HEX_OBJECT.fullmatch(code_revision) is None:
        raise DualLaneError("code_revision must be a full lowercase Git object id")
    epoch = verify_epoch_input(epoch_root)
    if epoch.security_master_path is None:
        raise DualLaneError("legacy epoch is audit-only and cannot build a new candidate")
    security_master_path = epoch.security_master_path
    if epoch.market_date != market_date:
        raise DualLaneError("epoch and candidate market_date differ")
    if epoch.contract_id != policy.contract_id or epoch.policy_sha256 != policy.digest:
        raise DualLaneError("epoch input is not bound to the active dual-lane policy")
    if epoch.code_revision != code_revision:
        raise DualLaneError("candidate code revision differs from the frozen epoch")
    expected_eligibility = _epoch_eligibility(policy, market_date)
    if epoch.eligibility is None:
        raise DualLaneError("legacy epoch is audit-only and has no eligibility contract")
    if epoch.eligibility != expected_eligibility:
        raise DualLaneError("epoch eligibility differs from the active dual-lane policy")
    runtime = _runtime_identity()
    if runtime != policy.runtime:
        raise DualLaneError("candidate runtime differs from the frozen dual-lane runtime")
    ods = verify_ods_release(ods_release_dir)
    if ods.release.market_date != market_date or ods.release.schedule != "daily":
        raise DualLaneError("candidate ODS release is not the requested daily market date")
    output = output.resolve()
    if output.exists():
        existing = verify_lane_candidate(output)
        if (
            existing.candidate_id != candidate_id
            or existing.attempt_id != attempt_id
            or existing.lane_id != lane_id
            or existing.market_date != market_date
            or existing.epoch_manifest_sha256 != epoch.manifest_sha256
            or existing.ods_manifest_sha256 != ods.manifest_sha256
            or existing.contract_id != policy.contract_id
            or existing.policy_sha256 != policy.digest
            or existing.security_master_sha256 != _sha256(security_master_path)
            or existing.eligibility != expected_eligibility
        ):
            raise DualLaneError("existing candidate does not match the request")
        return existing
    started_at = _trusted_now(clock)
    candidate_build_at = _parse_aware_datetime(
        expected_eligibility["candidate_build_at"], field="eligibility.candidate_build_at"
    ).astimezone(timezone.utc)
    candidate_cutoff_at = _parse_aware_datetime(
        expected_eligibility["candidate_cutoff_at"], field="eligibility.candidate_cutoff_at"
    ).astimezone(timezone.utc)
    if started_at < candidate_build_at or started_at > candidate_cutoff_at:
        raise DualLaneError("trusted clock is outside the candidate build window")
    _, ods_current_digest = _verify_ods_current_selection(
        release_dir=ods.release_dir,
        attempt_id=attempt_id,
        market_date=market_date,
    )
    source_inputs = _source_input_lineage(ods.release)
    source_cutoffs = [
        _parse_aware_datetime(item["knowledge_cutoff"], field="source_inputs.knowledge_cutoff")
        for item in source_inputs
    ]
    if ods.release.created_at > candidate_cutoff_at or any(
        cutoff > candidate_cutoff_at for cutoff in source_cutoffs
    ):
        raise DualLaneError("candidate ODS input exceeds the frozen PIT cutoff")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        revision_suffix = f"{lane_id}_{market_date.strftime('%Y%m%d')}_{attempt_id}"
        dwd_revision = f"daily_market_dwd_v2_{revision_suffix}"
        dws_revision = f"daily_market_dws_v2_{revision_suffix}"
        dwd_output = staging / "artifacts" / dwd_revision
        dws_output = staging / "artifacts" / dws_revision
        pointer = staging / ".parent-pointer.json"
        if epoch.source_kind == "daily_partition":
            assert epoch.source_path is not None
            _write_json(
                pointer,
                {
                    "market_date": _load_object(epoch.source_path / "manifest.json")["market_date"],
                    "revision": epoch.source_revision,
                    "revision_path": str(epoch.source_path),
                    "manifest_sha256": epoch.source_manifest_sha256,
                },
            )
            parent_pointer = pointer
            base_pointer = None
        else:
            _write_json(
                pointer,
                {
                    "revision": epoch.source_revision,
                    "meta_sha256": epoch.source_manifest_sha256,
                },
            )
            parent_pointer = None
            base_pointer = pointer
        build_daily_dwd_revision(
            release_dir=ods.release_dir,
            output=dwd_output,
            revision=dwd_revision,
            market_date=market_date,
            universe_policy_path=epoch.universe_policy_path,
            code_validity_mapping_path=epoch.code_validity_path,
            code_revision=code_revision,
            security_master_path=security_master_path,
            parent_pointer=parent_pointer,
            base_pointer=base_pointer,
        )
        build_daily_dws_revision(
            dwd_revision_dir=dwd_output,
            output=dws_output,
            revision=dws_revision,
            market_date=market_date,
        )
        pointer.unlink(missing_ok=True)
        ready_at = _trusted_now(clock)
        if ready_at < candidate_build_at or ready_at > candidate_cutoff_at:
            raise DualLaneError("candidate completed outside the frozen build window")
        if ods.release.created_at > ready_at:
            raise DualLaneError("candidate ODS release was sealed after candidate readiness")
        lineage_root = staging / "lineage"
        lineage_root.mkdir()
        ods_release_manifest = lineage_root / "ods-release.json"
        ods_current_manifest = lineage_root / "ods-current.json"
        shutil.copy2(ods.release_dir / "release.json", ods_release_manifest)
        shutil.copy2(ods.release_dir.parent / "CURRENT.json", ods_current_manifest)
        if _sha256(ods_current_manifest) != ods_current_digest:
            raise DualLaneError("copied ODS CURRENT descriptor differs from its selected bytes")
        knowledge_cutoff = max(source_cutoffs).isoformat()
        business_digest = _business_content_digest(dwd_output, dws_output, market_date)
        content_value = {
            "lane_id": lane_id,
            "market_date": market_date.isoformat(),
            "epoch_manifest_sha256": epoch.manifest_sha256,
            "ods_manifest_sha256": ods.manifest_sha256,
            "business_content_digest": business_digest,
            "code_revision": code_revision,
            "runtime": runtime,
            "contract_id": policy.contract_id,
            "policy_sha256": policy.digest,
            "security_master_sha256": _sha256(security_master_path),
            "eligibility": expected_eligibility,
            "attempt_selection": _EXACT_ATTEMPT_SELECTION,
            "source_inputs": source_inputs,
            "knowledge_cutoff": knowledge_cutoff,
        }
        manifest = {
            "schema_version": 1,
            "kind": "dual_lane_dwd_dws_candidate",
            "status": "ready",
            "candidate_id": candidate_id,
            "lane_id": lane_id,
            "attempt_id": attempt_id,
            "market_date": market_date.isoformat(),
            "candidate_ready_at": ready_at.isoformat(),
            "contract_id": policy.contract_id,
            "policy_sha256": policy.digest,
            "code_revision": code_revision,
            "runtime": runtime,
            "epoch_id": epoch.epoch_id,
            "epoch_manifest_sha256": epoch.manifest_sha256,
            "ods_release_id": ods.release.release_id,
            "ods_manifest_sha256": ods.manifest_sha256,
            "security_master_sha256": _sha256(security_master_path),
            "knowledge_cutoff": knowledge_cutoff,
            "eligibility": expected_eligibility,
            "attempt_selection": {
                "contract": _EXACT_ATTEMPT_SELECTION,
                "descriptor_path": ods_current_manifest.relative_to(staging).as_posix(),
                "descriptor_sha256": ods_current_digest,
                "release_manifest_path": ods_release_manifest.relative_to(staging).as_posix(),
            },
            "source_inputs": source_inputs,
            "business_content_digest": business_digest,
            "candidate_content_digest": _canonical_digest(content_value),
            "hard_gates": {
                "dwd_integrity": "passed",
                "dws_integrity": "passed",
                "epoch_identity": "passed",
                "ods_integrity": "passed",
                "attempt_eligibility": "passed",
                "clock": "passed",
                "candidate_cutoff": "passed",
                "pit_cutoff": "passed",
            },
            "artifacts": {
                "dwd": {
                    "revision": dwd_revision,
                    "path": dwd_output.relative_to(staging).as_posix(),
                    "manifest_sha256": _sha256(dwd_output / "manifest.json"),
                },
                "dws": {
                    "revision": dws_revision,
                    "path": dws_output.relative_to(staging).as_posix(),
                    "manifest_sha256": _sha256(dws_output / "manifest.json"),
                },
            },
            "files": _manifest_files(staging),
        }
        _seal(staging, output, manifest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_lane_candidate(output)


def write_candidate_current(candidate: LaneCandidate, pointer: Path) -> None:
    relative = os.path.relpath(candidate.root, start=pointer.parent.resolve())
    _atomic_json(
        pointer.resolve(),
        {
            "schema_version": 1,
            "market_date": candidate.market_date.isoformat(),
            "candidate_id": candidate.candidate_id,
            "candidate_path": Path(relative).as_posix(),
            "manifest_sha256": candidate.manifest_sha256,
        },
    )


def load_candidate_current(pointer: Path) -> LaneCandidate:
    value = _load_object(pointer.resolve())
    root = Path(_required_string(value, "candidate_path"))
    if not root.is_absolute():
        root = pointer.resolve().parent / root
    candidate = verify_lane_candidate(root)
    if candidate.manifest_sha256 != value.get("manifest_sha256"):
        raise DualLaneError("candidate CURRENT manifest checksum mismatch")
    return candidate


def _copy_candidate(candidate: LaneCandidate, destination: Path) -> LaneCandidate:
    if destination.exists():
        existing = verify_lane_candidate(destination)
        if existing.manifest_sha256 != candidate.manifest_sha256:
            raise DualLaneError("existing canonical generation differs from selected candidate")
        return existing
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        shutil.copytree(candidate.root, staging / "candidate")
        os.replace(staging / "candidate", destination)
        staging.rmdir()
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_lane_candidate(destination)


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> str:
    if path.exists():
        existing = _load_object(path)
        if existing != dict(value):
            raise DualLaneError(f"refusing to overwrite immutable record: {path}")
        return _sha256(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o640)
            json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            existing = _load_object(path)
            if existing != dict(value):
                raise DualLaneError(
                    f"refusing to overwrite immutable record: {path}"
                ) from None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)
    return _sha256(path)


def _write_timestamped_immutable_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    timestamp_field: str,
    timestamp: datetime | None = None,
) -> tuple[dict[str, Any], str]:
    if path.exists():
        existing = _load_object(path)
        stable_existing = {key: item for key, item in existing.items() if key != timestamp_field}
        if stable_existing != dict(value):
            raise DualLaneError(f"refusing to overwrite immutable record: {path}")
        timestamp = existing.get(timestamp_field)
        if not isinstance(timestamp, str) or not timestamp:
            raise DualLaneError(f"immutable record is missing {timestamp_field}: {path}")
        return existing, _sha256(path)
    recorded_at = _trusted_now() if timestamp is None else _trusted_now(lambda: timestamp)
    record = {**value, timestamp_field: recorded_at.isoformat()}
    return record, _write_immutable_json(path, record)


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


@dataclass(frozen=True)
class FencedLease:
    root: Path
    lease_name: str
    owner: str
    fencing_token: int
    acquired_at: datetime
    expires_at: datetime
    event_path: Path
    event_sha256: str


def _lease_root(root: Path, policy: DualLanePolicy) -> Path:
    return root.resolve() / "state" / "fenced-leases" / policy.lease_name


def _lease_from_payload(
    root: Path,
    policy: DualLanePolicy,
    payload: Mapping[str, Any],
) -> FencedLease:
    token = payload.get("fencing_token")
    if not isinstance(token, int) or isinstance(token, bool) or token < 1:
        raise DualLaneError("fenced lease token is invalid")
    if payload.get("schema_version") != 1 or payload.get("kind") != "fenced_publisher_lease":
        raise DualLaneError("fenced lease schema is invalid")
    if payload.get("lease_name") != policy.lease_name:
        raise DualLaneError("fenced lease name differs from policy")
    owner = _required_string(payload, "owner")
    if _SAFE_ID.fullmatch(owner) is None:
        raise DualLaneError("fenced lease owner is unsafe")
    acquired_at = _parse_aware_datetime(payload.get("acquired_at"), field="acquired_at")
    expires_at = _parse_aware_datetime(payload.get("expires_at"), field="expires_at")
    if expires_at <= acquired_at:
        raise DualLaneError("fenced lease expiry is invalid")
    if (expires_at - acquired_at).total_seconds() != policy.lease_duration_seconds:
        raise DualLaneError("fenced lease duration differs from policy")
    lease_root = _lease_root(root, policy)
    event_path = _resolve_inside(
        lease_root,
        _required_string(payload, "event_path"),
        field="fenced lease event_path",
    )
    event_sha256 = _required_string(payload, "event_sha256")
    if not event_path.is_file() or _sha256(event_path) != event_sha256:
        raise DualLaneError("fenced lease event integrity failed")
    event = _load_object(event_path)
    if (
        event.get("kind") != "fenced_lease_transition"
        or event.get("lease_name") != policy.lease_name
        or event.get("owner") != owner
        or event.get("fencing_token") != token
        or event.get("expires_at") != expires_at.isoformat()
        or event.get("acquired_at") != acquired_at.isoformat()
        or event.get("policy_sha256") != policy.digest
    ):
        raise DualLaneError("fenced lease event does not bind current lease")
    return FencedLease(
        root=root.resolve(),
        lease_name=policy.lease_name,
        owner=owner,
        fencing_token=token,
        acquired_at=acquired_at,
        expires_at=expires_at,
        event_path=event_path,
        event_sha256=event_sha256,
    )


def _maximum_recorded_fencing_token(
    lease_root: Path, policy: DualLanePolicy
) -> int:
    maximum = 0
    events = lease_root / "events"
    if not events.exists():
        return maximum
    for path in sorted(events.glob("*.json")):
        event = _load_object(path)
        token = event.get("fencing_token")
        if (
            event.get("schema_version") != 1
            or event.get("kind") != "fenced_lease_transition"
            or event.get("lease_name") != policy.lease_name
            or event.get("policy_sha256") != policy.digest
            or not isinstance(token, int)
            or isinstance(token, bool)
            or token < 1
        ):
            raise DualLaneError("fenced lease history is invalid")
        maximum = max(maximum, token)
    return maximum


def acquire_fenced_lease(
    *,
    root: Path,
    policy: DualLanePolicy,
    owner: str,
    clock: TrustedClock | None = None,
) -> FencedLease:
    if _SAFE_ID.fullmatch(owner) is None:
        raise DualLaneError("fenced lease owner is unsafe")
    root = root.resolve()
    lease_root = _lease_root(root, policy)
    current_path = lease_root / "CURRENT.json"
    now = _trusted_now(clock)
    with _exclusive_lock(lease_root / "lease.lock"):
        current = (
            _lease_from_payload(root, policy, _load_object(current_path))
            if current_path.is_file()
            else None
        )
        if current is not None and now < current.expires_at:
            if current.owner != owner:
                raise DualLaneError("canonical publisher lease is held by another owner")
            return current
        token = _maximum_recorded_fencing_token(lease_root, policy) + 1
        if current is None and token == 1:
            reason_code = "lease_initial_acquire"
        elif current is None:
            reason_code = "lease_orphaned_event_recovery"
        elif current.owner == owner:
            reason_code = "lease_expired_reacquire"
        else:
            reason_code = "lease_expired_takeover"
        expires_at = now + timedelta(seconds=policy.lease_duration_seconds)
        event_path = lease_root / "events" / f"{token:020d}.json"
        event, event_digest = _write_timestamped_immutable_json(
            event_path,
            {
                "schema_version": 1,
                "kind": "fenced_lease_transition",
                "lease_name": policy.lease_name,
                "owner": owner,
                "fencing_token": token,
                "previous_fencing_token": current.fencing_token if current else None,
                "expires_at": expires_at.isoformat(),
                "reason_code": reason_code,
                "policy_sha256": policy.digest,
            },
            timestamp_field="acquired_at",
            timestamp=now,
        )
        _atomic_json(
            current_path,
            {
                "schema_version": 1,
                "kind": "fenced_publisher_lease",
                "lease_name": policy.lease_name,
                "owner": owner,
                "fencing_token": token,
                "acquired_at": _required_string(event, "acquired_at"),
                "expires_at": expires_at.isoformat(),
                "event_path": event_path.relative_to(lease_root).as_posix(),
                "event_sha256": event_digest,
            },
        )
        return _lease_from_payload(root, policy, _load_object(current_path))


def _assert_current_lease_locked(
    *,
    root: Path,
    policy: DualLanePolicy,
    lease: FencedLease,
    now: datetime,
) -> None:
    current_path = _lease_root(root, policy) / "CURRENT.json"
    if not current_path.is_file():
        raise DualLaneError("canonical publisher lease is missing")
    current = _lease_from_payload(root, policy, _load_object(current_path))
    if (
        current.owner != lease.owner
        or current.fencing_token != lease.fencing_token
        or current.event_sha256 != lease.event_sha256
    ):
        raise DualLaneError("publisher was fenced by a newer lease")
    if now >= current.expires_at:
        raise DualLaneError("canonical publisher lease expired before commit")


def verify_go_for_cutover(
    *,
    gate_path: Path,
    policy: DualLanePolicy,
    clock: TrustedClock | None = None,
) -> dict[str, Any]:
    gate_path = gate_path.resolve()
    if gate_path.name != policy.cutover_gate_artifact:
        raise DualLaneError("GO_FOR_CUTOVER artifact name differs from policy")
    gate = _load_object(gate_path)
    now = _trusted_now(clock)
    evidence = gate.get("evidence")
    approved_at = _parse_aware_datetime(gate.get("approved_at"), field="approved_at")
    expires_at = _parse_aware_datetime(gate.get("expires_at"), field="expires_at")
    legacy_token = gate.get("legacy_fencing_token")
    if (
        gate.get("schema_version") != 1
        or gate.get("kind") != "go_for_cutover"
        or gate.get("contract_id") != policy.contract_id
        or gate.get("policy_sha256") != policy.digest
        or gate.get("approved") is not True
        or gate.get("live_writer") != policy.target_publisher_owner
        or gate.get("reason_code") != "go_for_cutover_approved"
        or not isinstance(legacy_token, int)
        or isinstance(legacy_token, bool)
        or legacy_token < 1
        or not isinstance(evidence, Mapping)
        or any(
            evidence.get(item) is not True
            for item in (
                "legacy_writer_fenced",
                "retention_gate_passed",
                "restore_drill_passed",
            )
        )
        or approved_at > now
        or now >= expires_at
    ):
        raise DualLaneError("GO_FOR_CUTOVER evidence is absent, stale, or incomplete")
    lease_path = _lease_root(gate_path.parent, policy) / "CURRENT.json"
    if not lease_path.is_file():
        raise DualLaneError("GO_FOR_CUTOVER requires the legacy publisher lease")
    legacy_lease = _lease_from_payload(
        gate_path.parent,
        policy,
        _load_object(lease_path),
    )
    if (
        legacy_lease.owner != policy.legacy_publisher_owner
        or legacy_lease.fencing_token != legacy_token
        or now < legacy_lease.expires_at
    ):
        raise DualLaneError("GO_FOR_CUTOVER does not bind an expired legacy lease")
    return gate


def live_activation_requested(
    policy: DualLanePolicy,
    environment: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environment is None else environment
    value = values.get(policy.cutover_environment)
    if value in (None, "", "0"):
        return False
    if value != "1":
        raise DualLaneError("GO_FOR_CUTOVER environment must be 0, 1, or unset")
    return True


def _build_operational_ads(
    *,
    candidate: LaneCandidate,
    output: Path,
    acceptance_digest: str,
    catalog_id: str,
    catalog_digest: str,
    generation_id: str,
    policy: DualLanePolicy,
) -> tuple[str, str]:
    if output.exists():
        manifest = _verify_ready(output)
        if (
            manifest.get("acceptance_digest") != acceptance_digest
            or manifest.get("catalog_id") != catalog_id
            or manifest.get("catalog_sha256") != catalog_digest
            or manifest.get("generation_ids") != [generation_id]
            or manifest.get("candidate_manifest_sha256") != candidate.manifest_sha256
            or manifest.get("market_date") != candidate.market_date.isoformat()
        ):
            raise DualLaneError("existing operational ADS is bound to different inputs")
        for table in manifest.get("tables", []):
            if not isinstance(table, Mapping):
                raise DualLaneError("operational ADS table manifest is invalid")
            for part in table.get("parts", []):
                if not isinstance(part, Mapping):
                    raise DualLaneError("operational ADS part manifest is invalid")
                path = _resolve_inside(
                    output,
                    _required_string(part, "path"),
                    field="ADS part path",
                )
                if _sha256(path) != part.get("sha256"):
                    raise DualLaneError("operational ADS part checksum mismatch")
        return _sha256(output / "manifest.json"), _required_string(manifest, "bundle_digest")
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise DualLaneError("P1 dependencies are required for operational ADS") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        tables = staging / "tables"
        tables.mkdir()
        partition = candidate.dwd_root / f"market_date={candidate.market_date.isoformat()}"
        sources = {
            "market_daily": partition / "part-00000.parquet",
            "market_status": partition / "daily_status_observation.parquet",
            "market_summary": candidate.dws_root
            / f"market_date={candidate.market_date.isoformat()}"
            / "daily_market_summary.parquet",
        }
        roles = {
            "market_daily": "decision_visible",
            "market_status": "decision_visible",
            "market_summary": "decision_visible",
        }
        table_manifests: list[dict[str, Any]] = []
        for name, source in sorted(sources.items()):
            target = tables / f"{name}.parquet"
            table = pq.ParquetFile(source).read()
            sort_keys = [
                column
                for column in ("market_date", "security_id", "stock_code")
                if column in table.column_names
            ]
            if sort_keys:
                table = table.sort_by([(column, "ascending") for column in sort_keys])
            pq.write_table(table, target, compression="zstd")
            schema_bytes = table.schema.serialize().to_pybytes()
            schema_digest = f"sha256:{hashlib.sha256(schema_bytes).hexdigest()}"
            table_manifests.append(
                {
                    "name": name,
                    "role": roles[name],
                    "table_content_digest": _table_digest(target),
                    "parts": [
                        {
                            "path": target.relative_to(staging).as_posix(),
                            "sha256": _sha256(target),
                            "size_bytes": target.stat().st_size,
                            "row_count": table.num_rows,
                            "schema_sha256": schema_digest,
                            "min_market_date": candidate.market_date.isoformat(),
                            "max_market_date": candidate.market_date.isoformat(),
                            "sort_keys": sort_keys,
                        }
                    ],
                }
            )
        digest_value = {
            "profile": "operational_daily_v1",
            "market_date": candidate.market_date.isoformat(),
            "acceptance_digest": acceptance_digest,
            "catalog_id": catalog_id,
            "catalog_sha256": catalog_digest,
            "generation_ids": [generation_id],
            "candidate_manifest_sha256": candidate.manifest_sha256,
            "tables": table_manifests,
            "policy_sha256": policy.digest,
        }
        manifest = {
            "schema_version": 1,
            "kind": "operational_ads_bundle",
            "profile": "operational_daily_v1",
            "market_date": candidate.market_date.isoformat(),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "research_only": True,
            "execution_authorized": False,
            "acceptance_digest": acceptance_digest,
            "catalog_id": catalog_id,
            "catalog_sha256": catalog_digest,
            "generation_ids": [generation_id],
            "candidate_manifest_sha256": candidate.manifest_sha256,
            "tables": table_manifests,
            "bundle_digest": _canonical_digest(digest_value),
        }
        _seal(staging, output, manifest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    manifest = _verify_ready(output)
    for table in manifest.get("tables", []):
        if not isinstance(table, Mapping):
            raise DualLaneError("operational ADS table manifest is invalid")
        for part in table.get("parts", []):
            if not isinstance(part, Mapping):
                raise DualLaneError("operational ADS part manifest is invalid")
            path = _resolve_inside(output, _required_string(part, "path"), field="ADS part path")
            if _sha256(path) != part.get("sha256"):
                raise DualLaneError("operational ADS part checksum mismatch")
    return _sha256(output / "manifest.json"), _required_string(manifest, "bundle_digest")


@dataclass(frozen=True)
class ArbitrationResult:
    market_date: date
    status: str
    reason: str
    selected_lane: str | None
    release_set_id: str | None
    release_set_pointer: Path | None
    record_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "status": self.status,
            "reason": self.reason,
            "selected_lane": self.selected_lane,
            "release_set_id": self.release_set_id,
            "release_set_pointer": (
                str(self.release_set_pointer) if self.release_set_pointer else None
            ),
            "record_path": str(self.record_path),
        }


def _candidate_or_error(path: Path | None) -> tuple[LaneCandidate | None, str | None]:
    if path is None or not path.exists():
        return None, "missing_candidate"
    try:
        return verify_lane_candidate(path), None
    except (DualLaneError, DailyWarehouseError, OSError, ValueError) as exc:
        return None, f"invalid_candidate:{type(exc).__name__}"


def _verify_release_set(root: Path, release_path: Path) -> dict[str, Any]:
    release = _verify_ready(release_path)
    if release.get("schema_version") != 1 or release.get("kind") != "dual_lane_release_set":
        raise DualLaneError("unsupported release-set manifest")
    references = (
        ("acceptance_path", "acceptance_sha256", False),
        ("catalog_path", "catalog_sha256", False),
        ("operational_ads_path", "operational_ads_manifest_sha256", True),
        ("generation_path", "candidate_manifest_sha256", True),
    )
    for path_field, digest_field, directory in references:
        referenced = _resolve_inside(
            root,
            _required_string(release, path_field),
            field=path_field,
        )
        digest_path = referenced / "manifest.json" if directory else referenced
        if not digest_path.is_file() or _sha256(digest_path) != release.get(digest_field):
            raise DualLaneError(f"release-set reference integrity failed: {path_field}")
    _verify_ready(
        _resolve_inside(
            root,
            _required_string(release, "operational_ads_path"),
            field="operational_ads_path",
        )
    )
    candidate = verify_lane_candidate(
        _resolve_inside(
            root,
            _required_string(release, "generation_path"),
            field="generation_path",
        )
    )
    if candidate.eligibility is not None:
        acceptance_path = _resolve_inside(
            root,
            _required_string(release, "acceptance_path"),
            field="acceptance_path",
        )
        acceptance = _load_object(acceptance_path)
        terminal_path = _resolve_inside(
            root,
            _required_string(acceptance, "terminal_decision_path"),
            field="terminal_decision_path",
        )
        if not terminal_path.is_file() or _sha256(terminal_path) != acceptance.get(
            "terminal_decision_sha256"
        ):
            raise DualLaneError("release-set terminal decision integrity failed")
    return release


def _release_from_pointer(
    root: Path,
    pointer_payload: Mapping[str, Any],
    *,
    allow_revoked: bool = False,
) -> tuple[Path, dict[str, Any]]:
    release_path = _resolve_inside(
        root,
        _required_string(pointer_payload, "release_set_path"),
        field="release_set_path",
    )
    release = _verify_release_set(root, release_path)
    if release.get("release_set_id") != pointer_payload.get("release_set_id") or _sha256(
        release_path / "manifest.json"
    ) != pointer_payload.get("manifest_sha256"):
        raise DualLaneError("release-set CURRENT does not match its immutable release")
    schema_version = pointer_payload.get("schema_version")
    if schema_version not in (1, 2):
        raise DualLaneError("release-set CURRENT schema is unsupported")
    if schema_version == 2:
        event_path = _resolve_inside(
            root,
            _required_string(pointer_payload, "pointer_cas_event_path"),
            field="pointer_cas_event_path",
        )
        if not event_path.is_file() or _sha256(event_path) != pointer_payload.get(
            "pointer_cas_event_sha256"
        ):
            raise DualLaneError("release-set CURRENT CAS evidence integrity failed")
        event = _load_object(event_path)
        if (
            event.get("kind") != "release_set_pointer_cas_attempt"
            or event.get("new_release_set_id") != release.get("release_set_id")
            or event.get("new_release_set_manifest_sha256")
            != pointer_payload.get("manifest_sha256")
            or event.get("fencing_token") != pointer_payload.get("fencing_token")
            or event.get("owner") != pointer_payload.get("lease_owner")
        ):
            raise DualLaneError("release-set CURRENT is not bound to its CAS evidence")
    if not allow_revoked:
        _assert_release_set_not_revoked(root, release)
    return release_path, release


def _release_generation_ids(root: Path, release: Mapping[str, Any]) -> tuple[str, ...]:
    catalog_path = _resolve_inside(
        root,
        _required_string(release, "catalog_path"),
        field="catalog_path",
    )
    catalog = _load_object(catalog_path)
    mappings = catalog.get("market_date_generations")
    if not isinstance(mappings, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in mappings.items()
    ):
        raise DualLaneError("canonical catalog mapping is invalid")
    return tuple(sorted(set(mappings.values())))


def _assert_release_set_not_revoked(root: Path, release: Mapping[str, Any]) -> None:
    for generation_id in _release_generation_ids(root, release):
        if (root / "governance" / "revocations" / f"{generation_id}.json").is_file():
            raise DualLaneError("release set contains a revoked generation")


def compare_and_swap_release_pointer(
    *,
    root: Path,
    policy: DualLanePolicy,
    release_set_root: Path,
    publication_event_path: Path,
    expected_pointer_sha256: str | None,
    lease: FencedLease,
    reason_code: str,
    clock: TrustedClock | None = None,
) -> dict[str, Any]:
    if _SAFE_ID.fullmatch(reason_code) is None:
        raise DualLaneError("pointer CAS reason_code is unsafe")
    root = root.resolve()
    release_set_root = release_set_root.resolve()
    publication_event_path = publication_event_path.resolve()
    if root not in release_set_root.parents or root not in publication_event_path.parents:
        raise DualLaneError("pointer CAS inputs must be inside the publication root")
    release = _verify_release_set(root, release_set_root)
    _assert_release_set_not_revoked(root, release)
    release_manifest_sha256 = _sha256(release_set_root / "manifest.json")
    publication_event, publication_event_sha256 = _load_object_with_digest(
        publication_event_path
    )
    if (
        publication_event.get("kind") != "release_set_publication_intent"
        or publication_event.get("new_release_set_id") != release.get("release_set_id")
        or publication_event.get("new_release_set_manifest_sha256")
        != release_manifest_sha256
    ):
        raise DualLaneError("publication intent does not bind the release set")
    pointer_path = root / "release-set_CURRENT.json"
    now = _trusted_now(clock)
    lease_root = _lease_root(root, policy)
    with _exclusive_lock(lease_root / "lease.lock"):
        _assert_current_lease_locked(
            root=root,
            policy=policy,
            lease=lease,
            now=now,
        )
        current = _load_object(pointer_path) if pointer_path.is_file() else None
        if current is not None and (
            current.get("release_set_id") == release.get("release_set_id")
            and current.get("manifest_sha256") == release_manifest_sha256
        ):
            _release_from_pointer(root, current)
            return current
        actual_digest = _sha256(pointer_path) if pointer_path.is_file() else None
        if actual_digest != expected_pointer_sha256:
            raise DualLaneError("release-set CURRENT CAS precondition failed")
        event_identity = {
            "new_release_set_id": release.get("release_set_id"),
            "new_release_set_manifest_sha256": release_manifest_sha256,
            "expected_pointer_sha256": expected_pointer_sha256,
            "fencing_token": lease.fencing_token,
            "owner": lease.owner,
            "reason_code": reason_code,
        }
        event_id = _canonical_digest(event_identity).removeprefix("sha256:")[:24]
        cas_event_path = root / "pointer-cas-events" / f"cas-{event_id}.json"
        cas_event, cas_event_sha256 = _write_timestamped_immutable_json(
            cas_event_path,
            {
                "schema_version": 1,
                "kind": "release_set_pointer_cas_attempt",
                **event_identity,
                "publication_event_path": publication_event_path.relative_to(root).as_posix(),
                "publication_event_sha256": publication_event_sha256,
                "lease_event_path": lease.event_path.relative_to(root).as_posix(),
                "lease_event_sha256": lease.event_sha256,
            },
            timestamp_field="attempted_at",
            timestamp=now,
        )
        pointer = {
            "schema_version": 2,
            "release_set_id": _required_string(release, "release_set_id"),
            "release_set_path": release_set_root.relative_to(root).as_posix(),
            "manifest_sha256": release_manifest_sha256,
            "publication_event_path": publication_event_path.relative_to(root).as_posix(),
            "publication_event_sha256": publication_event_sha256,
            "published_at": _required_string(publication_event, "published_at"),
            "previous_pointer_sha256": expected_pointer_sha256,
            "fencing_token": lease.fencing_token,
            "lease_owner": lease.owner,
            "reason_code": reason_code,
            "pointer_cas_event_path": cas_event_path.relative_to(root).as_posix(),
            "pointer_cas_event_sha256": cas_event_sha256,
            "switched_at": _required_string(cas_event, "attempted_at"),
        }
        _atomic_json(pointer_path, pointer)
        persisted, _ = _load_object_with_digest(pointer_path)
        if persisted != pointer:
            raise DualLaneError("release-set CURRENT differs after CAS commit")
        return persisted


@dataclass(frozen=True)
class GovernanceTransition:
    path: Path
    digest: str
    action: str
    state: str
    market_date: date
    reason_code: str


_GOVERNANCE_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "late_equivalent": (frozenset({"published", "late_equivalent"}), "late_equivalent"),
    "correction_proposed": (
        frozenset({"published", "late_equivalent", "correction_rejected"}),
        "correction_proposed",
    ),
    "correction_approved": (frozenset({"correction_proposed"}), "correction_approved"),
    "correction_rejected": (
        frozenset({"correction_proposed", "correction_approved"}),
        "correction_rejected",
    ),
    "correction_published": (frozenset({"correction_approved"}), "published"),
    "generation_revoked": (
        frozenset(
            {
                "published",
                "late_equivalent",
                "correction_proposed",
                "correction_approved",
                "correction_rejected",
            }
        ),
        "generation_revoked",
    ),
    "rollback_requested": (
        frozenset({"published", "late_equivalent", "generation_revoked", "rolled_back"}),
        "rollback_requested",
    ),
    "rollback_published": (frozenset({"rollback_requested"}), "rolled_back"),
}


def _governance_transition_from_path(path: Path) -> GovernanceTransition:
    payload, digest = _load_object_with_digest(path)
    try:
        market_date = date.fromisoformat(_required_string(payload, "market_date"))
    except ValueError as exc:
        raise DualLaneError("governance transition market_date is invalid") from exc
    action = _required_string(payload, "action")
    state = _required_string(payload, "state")
    if (
        payload.get("schema_version") != 1
        or payload.get("kind") != "dual_lane_governance_transition"
        or action not in _GOVERNANCE_TRANSITIONS
        or state != _GOVERNANCE_TRANSITIONS[action][1]
    ):
        raise DualLaneError("governance transition schema is invalid")
    return GovernanceTransition(
        path=path.resolve(),
        digest=digest,
        action=action,
        state=state,
        market_date=market_date,
        reason_code=_required_string(payload, "reason_code"),
    )


def _governance_head(
    root: Path, market_date: date
) -> tuple[dict[str, Any] | None, GovernanceTransition | None]:
    head_path = root / "governance" / "market-dates" / market_date.isoformat() / "HEAD.json"
    if not head_path.is_file():
        return None, None
    head = _load_object(head_path)
    event_path = _resolve_inside(
        root,
        _required_string(head, "event_path"),
        field="governance HEAD event_path",
    )
    event = _governance_transition_from_path(event_path)
    if (
        head.get("schema_version") != 1
        or head.get("market_date") != market_date.isoformat()
        or head.get("event_sha256") != event.digest
        or head.get("state") != event.state
    ):
        raise DualLaneError("governance HEAD integrity failed")
    return head, event


def _append_governance_transition(
    *,
    root: Path,
    policy: DualLanePolicy,
    market_date: date,
    action: str,
    actor: str,
    reason_code: str,
    evidence: Mapping[str, Any],
    clock: TrustedClock | None = None,
) -> GovernanceTransition:
    if action not in _GOVERNANCE_TRANSITIONS:
        raise DualLaneError("unsupported governance transition")
    if _SAFE_ID.fullmatch(actor) is None or _SAFE_ID.fullmatch(reason_code) is None:
        raise DualLaneError("governance actor and reason_code must be safe identifiers")
    root = root.resolve()
    governance_root = root / "governance"
    with _exclusive_lock(governance_root / "governance.lock"):
        head, previous = _governance_head(root, market_date)
        previous_state = previous.state if previous is not None else "published"
        allowed, next_state = _GOVERNANCE_TRANSITIONS[action]
        evidence_digest = _canonical_digest(evidence)
        if previous is not None:
            previous_payload = _load_object(previous.path)
            if (
                previous.action == action
                and previous_payload.get("evidence_digest") == evidence_digest
                and previous_payload.get("actor") == actor
                and previous.reason_code == reason_code
            ):
                return previous
        if previous_state not in allowed:
            raise DualLaneError(
                f"governance transition {action} is invalid from {previous_state}"
            )
        identity = {
            "market_date": market_date.isoformat(),
            "action": action,
            "previous_event_sha256": previous.digest if previous else None,
            "evidence_digest": evidence_digest,
            "policy_sha256": policy.digest,
        }
        event_id = _canonical_digest(identity).removeprefix("sha256:")[:24]
        event_path = (
            governance_root
            / "market-dates"
            / market_date.isoformat()
            / "events"
            / f"{event_id}.json"
        )
        _, event_digest = _write_timestamped_immutable_json(
            event_path,
            {
                "schema_version": 1,
                "kind": "dual_lane_governance_transition",
                **identity,
                "previous_state": previous_state,
                "state": next_state,
                "actor": actor,
                "reason_code": reason_code,
                "evidence": dict(evidence),
            },
            timestamp_field="recorded_at",
            timestamp=_trusted_now(clock),
        )
        _atomic_json(
            governance_root / "market-dates" / market_date.isoformat() / "HEAD.json",
            {
                "schema_version": 1,
                "market_date": market_date.isoformat(),
                "state": next_state,
                "event_path": event_path.relative_to(root).as_posix(),
                "event_sha256": event_digest,
                "previous_head_sha256": _canonical_digest(head) if head else None,
            },
        )
        return _governance_transition_from_path(event_path)


def evaluate_late_candidate(
    *,
    root: Path,
    policy: DualLanePolicy,
    active_generation_id: str,
    accepted_candidate_path: Path,
    late_candidate_path: Path,
    actor: str,
    reason_code: str,
    clock: TrustedClock | None = None,
) -> GovernanceTransition:
    if _SAFE_ID.fullmatch(active_generation_id) is None:
        raise DualLaneError("active generation id is unsafe")
    root = root.resolve()
    accepted = verify_lane_candidate(accepted_candidate_path)
    late = verify_lane_candidate(late_candidate_path)
    if accepted.market_date != late.market_date:
        raise DualLaneError("late candidate market_date differs from accepted generation")
    if accepted.epoch_manifest_sha256 != late.epoch_manifest_sha256:
        raise DualLaneError("late candidate belongs to a different arbitration epoch")
    pointer_path = root / "release-set_CURRENT.json"
    if not pointer_path.is_file():
        raise DualLaneError("late candidate review requires a published canonical generation")
    mappings = _load_catalog(root, _load_object(pointer_path))
    expected_path = (
        root
        / "canonical"
        / "generations"
        / accepted.market_date.isoformat()
        / active_generation_id
    ).resolve()
    if (
        mappings.get(accepted.market_date.isoformat()) != active_generation_id
        or accepted.root != expected_path
    ):
        raise DualLaneError("late candidate review does not bind the active catalog generation")
    equivalent = accepted.business_content_digest == late.business_content_digest
    action = "late_equivalent" if equivalent else "correction_proposed"
    evidence = {
        "active_generation_id": active_generation_id,
        "accepted_candidate_manifest_sha256": accepted.manifest_sha256,
        "accepted_business_content_digest": accepted.business_content_digest,
        "late_candidate_manifest_sha256": late.manifest_sha256,
        "late_business_content_digest": late.business_content_digest,
        "diff_digest": _canonical_digest(
            {
                "accepted": accepted.business_content_digest,
                "late": late.business_content_digest,
            }
        ),
        "republication_authorized": False,
    }
    return _append_governance_transition(
        root=root,
        policy=policy,
        market_date=accepted.market_date,
        action=action,
        actor=actor,
        reason_code=reason_code,
        evidence=evidence,
        clock=clock,
    )


def approve_correction(
    *,
    root: Path,
    policy: DualLanePolicy,
    proposal_event_path: Path,
    replacement_generation_id: str,
    impact_digest: str,
    authority: str,
    reason_code: str,
    clock: TrustedClock | None = None,
) -> GovernanceTransition:
    root = root.resolve()
    proposal_path = proposal_event_path.resolve()
    if root not in proposal_path.parents:
        raise DualLaneError("correction proposal must be inside the governance root")
    proposal = _governance_transition_from_path(proposal_path)
    if proposal.action != "correction_proposed":
        raise DualLaneError("correction approval must reference a correction proposal")
    if _SAFE_ID.fullmatch(replacement_generation_id) is None:
        raise DualLaneError("replacement generation id is unsafe")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", impact_digest) is None:
        raise DualLaneError("correction impact digest is invalid")
    return _append_governance_transition(
        root=root,
        policy=policy,
        market_date=proposal.market_date,
        action="correction_approved",
        actor=authority,
        reason_code=reason_code,
        evidence={
            "proposal_event_path": proposal.path.relative_to(root).as_posix(),
            "proposal_event_sha256": proposal.digest,
            "replacement_generation_id": replacement_generation_id,
            "impact_digest": impact_digest,
            "publication_critical_rebuild_required": True,
            "retained_research_is_append_only": True,
        },
        clock=clock,
    )


def record_generation_revocation(
    *,
    root: Path,
    policy: DualLanePolicy,
    generation_path: Path,
    authority: str,
    reason_code: str,
    integrity_evidence_digest: str,
    clock: TrustedClock | None = None,
) -> GovernanceTransition:
    root = root.resolve()
    generation_path = generation_path.resolve()
    if root not in generation_path.parents:
        raise DualLaneError("revoked generation must be inside the publication root")
    generation = verify_lane_candidate(generation_path)
    generation_id = generation_path.name
    if _SAFE_ID.fullmatch(generation_id) is None:
        raise DualLaneError("revoked generation id is unsafe")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", integrity_evidence_digest) is None:
        raise DualLaneError("revocation integrity evidence digest is invalid")
    transition = _append_governance_transition(
        root=root,
        policy=policy,
        market_date=generation.market_date,
        action="generation_revoked",
        actor=authority,
        reason_code=reason_code,
        evidence={
            "generation_id": generation_id,
            "generation_path": generation_path.relative_to(root).as_posix(),
            "candidate_manifest_sha256": generation.manifest_sha256,
            "integrity_evidence_digest": integrity_evidence_digest,
            "descendant_impact_required": True,
        },
        clock=clock,
    )
    _write_immutable_json(
        root / "governance" / "revocations" / f"{generation_id}.json",
        {
            "schema_version": 1,
            "kind": "canonical_generation_revocation",
            "generation_id": generation_id,
            "market_date": generation.market_date.isoformat(),
            "reason_code": reason_code,
            "transition_path": transition.path.relative_to(root).as_posix(),
            "transition_sha256": transition.digest,
            "integrity_evidence_digest": integrity_evidence_digest,
        },
    )
    return transition


def _publication_intent(
    *,
    root: Path,
    release_set_root: Path,
    old_release_set_id: str | None,
    reason_code: str,
    identity_suffix: str,
    clock: TrustedClock | None,
) -> Path:
    release = _verify_release_set(root, release_set_root)
    release_set_id = _required_string(release, "release_set_id")
    manifest_sha256 = _sha256(release_set_root / "manifest.json")
    path = root / "publication-events" / f"{identity_suffix}-{release_set_id}.json"
    _write_timestamped_immutable_json(
        path,
        {
            "schema_version": 1,
            "kind": "release_set_publication_intent",
            "publication_id": f"{identity_suffix}-{release_set_id}",
            "old_release_set_id": old_release_set_id,
            "new_release_set_id": release_set_id,
            "new_release_set_manifest_sha256": manifest_sha256,
            "reason_code": reason_code,
        },
        timestamp_field="published_at",
        timestamp=_trusted_now(clock),
    )
    return path


def _rollback_transition_for_target(
    root: Path, target_release_set_id: str
) -> tuple[GovernanceTransition, dict[str, Any]] | None:
    matches: list[tuple[GovernanceTransition, dict[str, Any]]] = []
    market_dates_root = root / "governance" / "market-dates"
    if not market_dates_root.exists():
        return None
    for head_path in sorted(market_dates_root.glob("*/HEAD.json")):
        try:
            market_date = date.fromisoformat(head_path.parent.name)
        except ValueError as exc:
            raise DualLaneError("governance market-date directory is invalid") from exc
        _, event = _governance_head(root, market_date)
        if event is None or event.action not in {"rollback_requested", "rollback_published"}:
            continue
        payload = _load_object(event.path)
        evidence = payload.get("evidence")
        if (
            isinstance(evidence, Mapping)
            and evidence.get("target_release_set_id") == target_release_set_id
        ):
            matches.append((event, payload))
    if len(matches) > 1:
        raise DualLaneError("multiple rollback transitions target one release set")
    return matches[0] if matches else None


def rollback_release_set(
    *,
    root: Path,
    policy: DualLanePolicy,
    target_release_set_id: str,
    authority: str,
    reason_code: str,
    publisher_owner: str | None = None,
    clock: TrustedClock | None = None,
) -> GovernanceTransition:
    if _SAFE_ID.fullmatch(target_release_set_id) is None:
        raise DualLaneError("rollback release set id is unsafe")
    root = root.resolve()
    pointer_path = root / "release-set_CURRENT.json"
    if not pointer_path.is_file():
        raise DualLaneError("rollback requires an existing release-set CURRENT")
    current_pointer = _load_object(pointer_path)
    current_root, current_release = _release_from_pointer(
        root, current_pointer, allow_revoked=True
    )
    del current_root
    current_id = _required_string(current_release, "release_set_id")
    if current_id == target_release_set_id:
        recorded = _rollback_transition_for_target(root, target_release_set_id)
        if recorded is None or current_pointer.get("reason_code") != "approved_rollback":
            raise DualLaneError("rollback target is already current")
        requested, requested_payload = recorded
        if (
            requested_payload.get("actor") != authority
            or requested_payload.get("reason_code") != reason_code
        ):
            raise DualLaneError("rollback recovery authority differs from its request")
        if requested.action == "rollback_published":
            return requested
        requested_evidence = requested_payload.get("evidence")
        if not isinstance(requested_evidence, Mapping):
            raise DualLaneError("rollback request evidence is invalid")
        return _append_governance_transition(
            root=root,
            policy=policy,
            market_date=requested.market_date,
            action="rollback_published",
            actor=authority,
            reason_code=reason_code,
            evidence={
                "request_event_path": requested.path.relative_to(root).as_posix(),
                "request_event_sha256": requested.digest,
                "from_release_set_id": requested_evidence.get("from_release_set_id"),
                "target_release_set_id": target_release_set_id,
                "pointer_sha256": _sha256(pointer_path),
                "fencing_token": current_pointer.get("fencing_token"),
            },
            clock=clock,
        )
    target_root = root / "release-sets" / target_release_set_id
    target = _verify_release_set(root, target_root)
    _assert_release_set_not_revoked(root, target)
    prior_publication = root / "publication-events" / f"publication-{target_release_set_id}.json"
    if not prior_publication.is_file():
        raise DualLaneError("rollback target was never previously published")
    current_date = date.fromisoformat(_required_string(current_release, "market_date"))
    target_date = date.fromisoformat(_required_string(target, "market_date"))
    if target_date >= current_date:
        raise DualLaneError("rollback target must be an older complete release set")
    requested = _append_governance_transition(
        root=root,
        policy=policy,
        market_date=current_date,
        action="rollback_requested",
        actor=authority,
        reason_code=reason_code,
        evidence={
            "from_release_set_id": current_id,
            "target_release_set_id": target_release_set_id,
            "target_manifest_sha256": _sha256(target_root / "manifest.json"),
        },
        clock=clock,
    )
    now = _trusted_now(clock)
    lease = acquire_fenced_lease(
        root=root,
        policy=policy,
        owner=publisher_owner or policy.target_publisher_owner,
        clock=lambda: now,
    )
    intent = _publication_intent(
        root=root,
        release_set_root=target_root,
        old_release_set_id=current_id,
        reason_code="approved_rollback",
        identity_suffix=f"rollback-{requested.digest.removeprefix('sha256:')[:16]}",
        clock=lambda: now,
    )
    compare_and_swap_release_pointer(
        root=root,
        policy=policy,
        release_set_root=target_root,
        publication_event_path=intent,
        expected_pointer_sha256=_sha256(pointer_path),
        lease=lease,
        reason_code="approved_rollback",
        clock=clock,
    )
    return _append_governance_transition(
        root=root,
        policy=policy,
        market_date=current_date,
        action="rollback_published",
        actor=authority,
        reason_code=reason_code,
        evidence={
            "request_event_path": requested.path.relative_to(root).as_posix(),
            "request_event_sha256": requested.digest,
            "from_release_set_id": current_id,
            "target_release_set_id": target_release_set_id,
            "pointer_sha256": _sha256(pointer_path),
            "fencing_token": lease.fencing_token,
        },
        clock=lambda: now,
    )


def publish_approved_correction(
    *,
    root: Path,
    policy: DualLanePolicy,
    approval_event_path: Path,
    corrected_release_set_id: str,
    authority: str,
    reason_code: str,
    publisher_owner: str | None = None,
    clock: TrustedClock | None = None,
) -> GovernanceTransition:
    root = root.resolve()
    if _SAFE_ID.fullmatch(corrected_release_set_id) is None:
        raise DualLaneError("corrected release set id is unsafe")
    approval_path = approval_event_path.resolve()
    if root not in approval_path.parents:
        raise DualLaneError("correction approval must be inside the governance root")
    approval = _governance_transition_from_path(approval_path)
    approval_payload = _load_object(approval.path)
    if approval.action != "correction_approved":
        raise DualLaneError("correction publication requires an approval transition")
    evidence = approval_payload.get("evidence")
    if not isinstance(evidence, Mapping):
        raise DualLaneError("correction approval evidence is invalid")
    replacement_generation_id = _required_string(evidence, "replacement_generation_id")
    target_root = root / "release-sets" / corrected_release_set_id
    target = _verify_release_set(root, target_root)
    _assert_release_set_not_revoked(root, target)
    catalog_path = _resolve_inside(
        root,
        _required_string(target, "catalog_path"),
        field="catalog_path",
    )
    mappings = _load_object(catalog_path).get("market_date_generations")
    if (
        not isinstance(mappings, Mapping)
        or mappings.get(approval.market_date.isoformat()) != replacement_generation_id
    ):
        raise DualLaneError("corrected release set does not contain the approved generation")
    pointer_path = root / "release-set_CURRENT.json"
    if not pointer_path.is_file():
        raise DualLaneError("correction publication requires an existing CURRENT")
    current = _load_object(pointer_path)
    current_id = _required_string(current, "release_set_id")
    _, current_head = _governance_head(root, approval.market_date)
    if current_id == corrected_release_set_id:
        if current_head is not None and current_head.action == "correction_published":
            completed = _load_object(current_head.path)
            completed_evidence = completed.get("evidence")
            if (
                completed.get("actor") == authority
                and completed.get("reason_code") == reason_code
                and isinstance(completed_evidence, Mapping)
                and completed_evidence.get("approval_event_sha256") == approval.digest
                and completed_evidence.get("corrected_release_set_id")
                == corrected_release_set_id
            ):
                return current_head
        if current_head is None or current_head.digest != approval.digest or current.get(
            "reason_code"
        ) != "approved_correction":
            raise DualLaneError("corrected release is current without its approval HEAD")
        intent_path = (
            root
            / "publication-events"
            / f"correction-{approval.digest.removeprefix('sha256:')[:16]}-"
            f"{corrected_release_set_id}.json"
        )
        intent = _load_object(intent_path)
        return _append_governance_transition(
            root=root,
            policy=policy,
            market_date=approval.market_date,
            action="correction_published",
            actor=authority,
            reason_code=reason_code,
            evidence={
                "approval_event_path": approval.path.relative_to(root).as_posix(),
                "approval_event_sha256": approval.digest,
                "previous_release_set_id": intent.get("old_release_set_id"),
                "corrected_release_set_id": corrected_release_set_id,
                "replacement_generation_id": replacement_generation_id,
                "pointer_sha256": _sha256(pointer_path),
                "fencing_token": current.get("fencing_token"),
            },
            clock=clock,
        )
    if current_head is None or current_head.digest != approval.digest:
        raise DualLaneError("correction approval is no longer the active governance HEAD")
    now = _trusted_now(clock)
    owner = publisher_owner or policy.target_publisher_owner
    if owner != policy.target_publisher_owner:
        raise DualLaneError("correction publication must use the target publisher owner")
    lease = acquire_fenced_lease(
        root=root,
        policy=policy,
        owner=owner,
        clock=lambda: now,
    )
    intent = _publication_intent(
        root=root,
        release_set_root=target_root,
        old_release_set_id=current_id,
        reason_code="approved_correction",
        identity_suffix=f"correction-{approval.digest.removeprefix('sha256:')[:16]}",
        clock=lambda: now,
    )
    compare_and_swap_release_pointer(
        root=root,
        policy=policy,
        release_set_root=target_root,
        publication_event_path=intent,
        expected_pointer_sha256=_sha256(pointer_path),
        lease=lease,
        reason_code="approved_correction",
        clock=clock,
    )
    return _append_governance_transition(
        root=root,
        policy=policy,
        market_date=approval.market_date,
        action="correction_published",
        actor=authority,
        reason_code=reason_code,
        evidence={
            "approval_event_path": approval.path.relative_to(root).as_posix(),
            "approval_event_sha256": approval.digest,
            "previous_release_set_id": current_id,
            "corrected_release_set_id": corrected_release_set_id,
            "replacement_generation_id": replacement_generation_id,
            "pointer_sha256": _sha256(pointer_path),
            "fencing_token": lease.fencing_token,
        },
        clock=lambda: now,
    )


@dataclass(frozen=True)
class AcceptedOdsRecoveryAssessment:
    path: Path
    digest: str
    passed: bool
    reason_code: str
    failure_domains: tuple[str, ...]


def _accepted_ods_root(root: Path, candidate: LaneCandidate) -> Path:
    return (
        root.resolve()
        / "recovery"
        / "accepted-ods"
        / candidate.market_date.isoformat()
        / candidate.ods_manifest_sha256.removeprefix("sha256:")
    )


def record_accepted_ods_copy(
    *,
    root: Path,
    policy: DualLanePolicy,
    accepted_candidate_path: Path,
    ods_release_copy_path: Path,
    copy_id: str,
    location_class: str,
    failure_domain: str,
    retention_until: datetime,
    clock: TrustedClock | None = None,
) -> Path:
    for field, value in (
        ("copy_id", copy_id),
        ("location_class", location_class),
        ("failure_domain", failure_domain),
    ):
        if _SAFE_ID.fullmatch(value) is None:
            raise DualLaneError(f"accepted ODS {field} is unsafe")
    if location_class not in {"source_lane", "independent_recovery"}:
        raise DualLaneError("accepted ODS copy location_class is unsupported")
    retention_until = _parse_aware_datetime(
        retention_until.isoformat(), field="retention_until"
    )
    candidate = verify_lane_candidate(accepted_candidate_path)
    restored = verify_ods_release(ods_release_copy_path)
    if (
        restored.manifest_sha256 != candidate.ods_manifest_sha256
        or restored.release.market_date != candidate.market_date
        or restored.release.schedule != "daily"
    ):
        raise DualLaneError("accepted ODS copy differs from the selected candidate lineage")
    minimum_retention = datetime.combine(
        candidate.market_date + timedelta(days=policy.correction_horizon_days),
        time(0, 0),
        tzinfo=ZoneInfo(policy.timezone),
    )
    if retention_until < minimum_retention:
        raise DualLaneError("accepted ODS copy retention ends before correction horizon")
    recovery_root = _accepted_ods_root(root, candidate)
    receipt_path = recovery_root / "copies" / f"{copy_id}.json"
    _, receipt_digest = _write_timestamped_immutable_json(
        receipt_path,
        {
            "schema_version": 1,
            "kind": "accepted_ods_recovery_copy",
            "market_date": candidate.market_date.isoformat(),
            "copy_id": copy_id,
            "location_class": location_class,
            "failure_domain": failure_domain,
            "ods_release_id": restored.release.release_id,
            "ods_manifest_sha256": restored.manifest_sha256,
            "candidate_manifest_sha256": candidate.manifest_sha256,
            "retention_until": retention_until.isoformat(),
            "reason_code": "accepted_ods_copy_verified",
            "deletion_authorized": False,
        },
        timestamp_field="verified_at",
        timestamp=_trusted_now(clock),
    )
    _write_immutable_json(
        recovery_root / "copy-index" / f"{receipt_digest.removeprefix('sha256:')}.json",
        {
            "schema_version": 1,
            "kind": "accepted_ods_copy_index_entry",
            "copy_id": copy_id,
            "receipt_path": receipt_path.relative_to(root.resolve()).as_posix(),
            "receipt_sha256": receipt_digest,
            "reason_code": "accepted_ods_copy_indexed",
        },
    )
    return receipt_path


def assess_accepted_ods_recovery(
    *,
    root: Path,
    policy: DualLanePolicy,
    accepted_candidate_path: Path,
    clock: TrustedClock | None = None,
) -> AcceptedOdsRecoveryAssessment:
    root = root.resolve()
    candidate = verify_lane_candidate(accepted_candidate_path)
    recovery_root = _accepted_ods_root(root, candidate)
    now = _trusted_now(clock)
    receipts: list[dict[str, Any]] = []
    receipt_digests: list[str] = []
    copies_root = recovery_root / "copies"
    if copies_root.exists():
        for path in sorted(copies_root.glob("*.json")):
            receipt, digest = _load_object_with_digest(path)
            retention_until = _parse_aware_datetime(
                receipt.get("retention_until"), field="retention_until"
            )
            if (
                receipt.get("schema_version") != 1
                or receipt.get("kind") != "accepted_ods_recovery_copy"
                or receipt.get("market_date") != candidate.market_date.isoformat()
                or receipt.get("ods_manifest_sha256") != candidate.ods_manifest_sha256
                or receipt.get("candidate_manifest_sha256") != candidate.manifest_sha256
                or receipt.get("deletion_authorized") is not False
                or retention_until <= now
            ):
                raise DualLaneError("accepted ODS recovery receipt is invalid or expired")
            receipts.append(receipt)
            receipt_digests.append(digest)
    failure_domains = tuple(
        sorted({_required_string(receipt, "failure_domain") for receipt in receipts})
    )
    location_classes = {
        _required_string(receipt, "location_class") for receipt in receipts
    }
    passed = (
        len(failure_domains) >= policy.recovery_minimum_copies
        and {"source_lane", "independent_recovery"} <= location_classes
    )
    reason_code = (
        "accepted_ods_recovery_passed"
        if passed
        else "accepted_ods_failure_independence_insufficient"
    )
    body = {
        "schema_version": 1,
        "kind": "accepted_ods_recovery_assessment",
        "market_date": candidate.market_date.isoformat(),
        "candidate_manifest_sha256": candidate.manifest_sha256,
        "ods_manifest_sha256": candidate.ods_manifest_sha256,
        "receipt_sha256s": sorted(receipt_digests),
        "failure_domains": list(failure_domains),
        "location_classes": sorted(location_classes),
        "minimum_failure_independent_copies": policy.recovery_minimum_copies,
        "passed": passed,
        "reason_code": reason_code,
    }
    assessment_id = _canonical_digest(body).removeprefix("sha256:")[:24]
    path = recovery_root / "assessments" / f"{assessment_id}.json"
    _, digest = _write_timestamped_immutable_json(
        path,
        body,
        timestamp_field="assessed_at",
        timestamp=now,
    )
    return AcceptedOdsRecoveryAssessment(
        path=path,
        digest=digest,
        passed=passed,
        reason_code=reason_code,
        failure_domains=failure_domains,
    )


def record_accepted_ods_restore_drill(
    *,
    root: Path,
    policy: DualLanePolicy,
    accepted_candidate_path: Path,
    restored_ods_release_path: Path,
    rebuilt_candidate_path: Path,
    started_at: datetime,
    actor: str,
    clock: TrustedClock | None = None,
) -> Path:
    if _SAFE_ID.fullmatch(actor) is None:
        raise DualLaneError("restore drill actor is unsafe")
    root = root.resolve()
    accepted = verify_lane_candidate(accepted_candidate_path)
    restored = verify_ods_release(restored_ods_release_path)
    rebuilt = verify_lane_candidate(rebuilt_candidate_path)
    now = _trusted_now(clock)
    started = _parse_aware_datetime(started_at.isoformat(), field="started_at").astimezone(
        timezone.utc
    )
    duration_seconds = (now - started).total_seconds()
    assessment = assess_accepted_ods_recovery(
        root=root,
        policy=policy,
        accepted_candidate_path=accepted_candidate_path,
        clock=lambda: now,
    )
    if not assessment.passed:
        raise DualLaneError("restore drill requires failure-independent accepted ODS copies")
    if (
        duration_seconds < 0
        or duration_seconds > policy.restore_rto_seconds
        or restored.manifest_sha256 != accepted.ods_manifest_sha256
        or rebuilt.ods_manifest_sha256 != accepted.ods_manifest_sha256
        or rebuilt.business_content_digest != accepted.business_content_digest
        or rebuilt.market_date != accepted.market_date
    ):
        raise DualLaneError("accepted ODS restore drill failed RPO, RTO, or content identity")
    recovery_root = _accepted_ods_root(root, accepted)
    drill_body = {
        "schema_version": 1,
        "kind": "accepted_ods_restore_drill",
        "market_date": accepted.market_date.isoformat(),
        "actor": actor,
        "accepted_candidate_manifest_sha256": accepted.manifest_sha256,
        "restored_ods_manifest_sha256": restored.manifest_sha256,
        "rebuilt_candidate_manifest_sha256": rebuilt.manifest_sha256,
        "rebuilt_business_content_digest": rebuilt.business_content_digest,
        "recovery_assessment_path": assessment.path.relative_to(root).as_posix(),
        "recovery_assessment_sha256": assessment.digest,
        "observed_rpo_seconds": 0,
        "observed_rto_seconds": duration_seconds,
        "reason_code": "accepted_ods_restore_drill_passed",
        "passed": True,
    }
    drill_id = _canonical_digest(drill_body).removeprefix("sha256:")[:24]
    path = recovery_root / "restore-drills" / f"{drill_id}.json"
    _write_timestamped_immutable_json(
        path,
        drill_body,
        timestamp_field="completed_at",
        timestamp=now,
    )
    return path


def record_accepted_ods_purge_gate(
    *,
    root: Path,
    policy: DualLanePolicy,
    accepted_candidate_path: Path,
    actor: str,
    clock: TrustedClock | None = None,
) -> Path:
    if _SAFE_ID.fullmatch(actor) is None:
        raise DualLaneError("purge gate actor is unsafe")
    candidate = verify_lane_candidate(accepted_candidate_path)
    body = {
        "schema_version": 1,
        "kind": "accepted_ods_purge_gate",
        "market_date": candidate.market_date.isoformat(),
        "ods_manifest_sha256": candidate.ods_manifest_sha256,
        "actor": actor,
        "eligible": False,
        "reason_code": "purge_disabled_copy_only_migration",
        "delete_enabled": policy.delete_enabled,
    }
    recovery_root = _accepted_ods_root(root, candidate)
    path = (
        recovery_root
        / "purge-gates"
        / f"{_canonical_digest(body).removeprefix('sha256:')[:24]}.json"
    )
    _write_timestamped_immutable_json(
        path,
        body,
        timestamp_field="evaluated_at",
        timestamp=_trusted_now(clock),
    )
    return path


def _load_catalog(root: Path, pointer_payload: Mapping[str, Any] | None) -> dict[str, str]:
    if pointer_payload is None:
        return {}
    _, release = _release_from_pointer(root, pointer_payload)
    catalog_path = _resolve_inside(
        root,
        _required_string(release, "catalog_path"),
        field="catalog_path",
    )
    catalog = _load_object(catalog_path)
    mappings = catalog.get("market_date_generations")
    if not isinstance(mappings, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in mappings.items()
    ):
        raise DualLaneError("canonical catalog mapping is invalid")
    return dict(mappings)


def arbitrate_and_publish(
    *,
    market_date: date,
    primary_candidate_path: Path | None,
    standby_candidate_path: Path | None,
    root: Path,
    policy: DualLanePolicy,
    expected_epoch: EpochInput,
    publisher_lock_path: Path | None = None,
    publisher_owner: str | None = None,
    live_activation: bool = False,
    cutover_gate_path: Path | None = None,
    clock: TrustedClock | None = None,
) -> ArbitrationResult:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if expected_epoch.market_date != market_date:
        raise DualLaneError("trusted OCI epoch market_date does not match arbitration")
    if (
        expected_epoch.contract_id != policy.contract_id
        or expected_epoch.policy_sha256 != policy.digest
    ):
        raise DualLaneError("trusted OCI epoch is not bound to the active policy")
    if expected_epoch.security_master_path is None:
        raise DualLaneError("legacy epoch is audit-only and cannot be arbitrated")
    expected_eligibility = _epoch_eligibility(policy, market_date)
    if expected_epoch.eligibility is None:
        raise DualLaneError("legacy epoch is audit-only and has no eligibility contract")
    if expected_epoch.eligibility != expected_eligibility:
        raise DualLaneError("trusted OCI epoch eligibility differs from the active policy")
    arbitration_at = _parse_aware_datetime(
        expected_eligibility["arbitration_at"], field="eligibility.arbitration_at"
    ).astimezone(timezone.utc)
    completion_deadline_at = _parse_aware_datetime(
        expected_eligibility["completion_deadline_at"],
        field="eligibility.completion_deadline_at",
    ).astimezone(timezone.utc)
    expected_security_master_sha256 = _sha256(expected_epoch.security_master_path)
    lock_path = (
        publisher_lock_path.resolve()
        if publisher_lock_path is not None
        else root / "locks" / "canonical-publisher.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        now = _trusted_now(clock)
        owner = publisher_owner or policy.target_publisher_owner
        if owner != policy.target_publisher_owner:
            raise DualLaneError("dual-lane arbitration must use the target publisher owner")
        if live_activation:
            if cutover_gate_path is None:
                raise DualLaneError("live activation requires GO_FOR_CUTOVER evidence")
            verify_go_for_cutover(
                gate_path=cutover_gate_path,
                policy=policy,
                clock=lambda: now,
            )
        lease = acquire_fenced_lease(
            root=root,
            policy=policy,
            owner=owner,
            clock=lambda: now,
        )
        if now < arbitration_at:
            raise DualLaneError("trusted clock is before the frozen arbitration time")
        deadline_missed = now > completion_deadline_at
        primary, primary_error = _candidate_or_error(primary_candidate_path)
        standby, standby_error = _candidate_or_error(standby_candidate_path)
        for candidate in (primary, standby):
            if candidate is not None and candidate.market_date != market_date:
                raise DualLaneError("candidate market_date does not match arbitration")
        if primary is not None and primary.lane_id != policy.primary_lane:
            raise DualLaneError("primary candidate has the wrong lane id")
        if standby is not None and standby.lane_id != policy.standby_lane:
            raise DualLaneError("standby candidate has the wrong lane id")
        if primary is not None and (
            primary.contract_id != policy.contract_id or primary.policy_sha256 != policy.digest
        ):
            primary = None
            primary_error = "invalid_candidate:policy_mismatch"
        if standby is not None and (
            standby.contract_id != policy.contract_id or standby.policy_sha256 != policy.digest
        ):
            standby = None
            standby_error = "invalid_candidate:policy_mismatch"
        coherence_reason: str | None = None
        for candidate in (primary, standby):
            if candidate is None:
                continue
            if candidate.epoch_manifest_sha256 != expected_epoch.manifest_sha256:
                coherence_reason = "withheld_epoch_mismatch"
            elif candidate.code_revision != expected_epoch.code_revision:
                coherence_reason = "withheld_code_revision_mismatch"
            elif candidate.runtime != policy.runtime:
                coherence_reason = "withheld_runtime_mismatch"
            elif candidate.security_master_sha256 != expected_security_master_sha256:
                coherence_reason = "withheld_security_master_mismatch"
            elif candidate.eligibility != expected_eligibility:
                coherence_reason = "withheld_eligibility_mismatch"
        state_path = root / "state" / "arbitration-state.json"
        state = _load_object(state_path) if state_path.is_file() else {"standby_absence_count": 0}
        absence_value = state.get("standby_absence_count", 0)
        if (
            not isinstance(absence_value, int)
            or isinstance(absence_value, bool)
            or absence_value < 0
        ):
            raise DualLaneError("arbitration state has an invalid standby absence count")
        absence_count = absence_value
        decision_input_digest = _canonical_digest(
            {
                "market_date": market_date.isoformat(),
                "policy_sha256": policy.digest,
                "primary": primary.manifest_sha256 if primary else None,
                "primary_error": primary_error,
                "standby": standby.manifest_sha256 if standby else None,
                "standby_error": standby_error,
            }
        )
        terminal_path = (
            root
            / "state"
            / "terminal-decisions"
            / market_date.isoformat()
            / f"{expected_epoch.epoch_id}.json"
        )
        terminal = _load_object(terminal_path) if terminal_path.is_file() else None
        if terminal is not None and (
            terminal.get("schema_version") != 1
            or terminal.get("kind") != "dual_lane_terminal_decision"
            or terminal.get("market_date") != market_date.isoformat()
            or terminal.get("epoch_id") != expected_epoch.epoch_id
            or terminal.get("epoch_manifest_sha256") != expected_epoch.manifest_sha256
            or terminal.get("policy_sha256") != policy.digest
        ):
            raise DualLaneError("existing terminal decision has invalid identity")
        if terminal is not None and terminal.get("decision_input_digest") != decision_input_digest:
            raise DualLaneError(
                "completed automatic epoch cannot reopen; use an explicit correction decision"
            )
        selected: LaneCandidate | None = None
        same_decision = (
            terminal is not None
            or (
                state.get("last_market_date") == market_date.isoformat()
                and state.get("last_input_digest") == decision_input_digest
            )
        )
        if terminal is not None:
            reason = _required_string(terminal, "reason")
            selected_lane = terminal.get("selected_lane")
            terminal_absence_count = terminal.get("standby_absence_count_after")
            if (
                not isinstance(terminal_absence_count, int)
                or isinstance(terminal_absence_count, bool)
                or terminal_absence_count < 0
            ):
                raise DualLaneError("terminal decision has invalid standby absence state")
            absence_count = terminal_absence_count
            if selected_lane == policy.primary_lane:
                selected = primary
            elif selected_lane == policy.standby_lane:
                selected = standby
            elif selected_lane is not None:
                raise DualLaneError("terminal decision has an invalid selected lane")
            if selected_lane is not None and selected is None:
                raise DualLaneError("terminal decision selection no longer matches its inputs")
        elif deadline_missed:
            selected = None
            reason = "withheld_completion_deadline_missed"
        elif coherence_reason is not None:
            selected = None
            reason = coherence_reason
        elif same_decision:
            reason = _required_string(state, "last_reason")
            selected_lane = state.get("last_selected_lane")
            if selected_lane is not None and selected_lane not in _LANES:
                raise DualLaneError("arbitration state has an invalid selected lane")
            if selected_lane == policy.primary_lane:
                selected = primary
            elif selected_lane == policy.standby_lane:
                selected = standby
            if selected_lane is not None and selected is None:
                raise DualLaneError("arbitration state selection no longer matches its inputs")
        else:
            last_market_date = state.get("last_market_date")
            if (
                isinstance(last_market_date, str)
                and date.fromisoformat(last_market_date) > market_date
            ):
                raise DualLaneError("refusing to arbitrate behind the recorded market date")
            if primary is not None and standby is not None:
                if primary.epoch_manifest_sha256 != standby.epoch_manifest_sha256:
                    reason = "withheld_epoch_mismatch"
                elif primary.business_content_digest != standby.business_content_digest:
                    reason = "withheld_unattributed_divergence"
                else:
                    selected = primary
                    reason = "primary"
                    absence_count = 0
            elif primary is not None:
                if absence_count < policy.maximum_consecutive_standby_absence:
                    selected = primary
                    reason = "primary_without_comparison"
                    absence_count += 1
                else:
                    reason = "withheld_standby_absence_budget_exceeded"
                    absence_count += 1
            elif standby is not None and primary_error is not None:
                primary_reason = primary_error.split(":", 1)[0]
                if primary_reason in policy.fallback_failure_reasons:
                    selected = standby
                    reason = f"primary_failed_over:{primary_reason}"
                    absence_count = 0
                else:
                    reason = f"withheld_unauthorized_failover:{primary_reason}"
            else:
                reason = "both_failed"
        if terminal is None:
            terminal_value = {
                "schema_version": 1,
                "kind": "dual_lane_terminal_decision",
                "market_date": market_date.isoformat(),
                "epoch_id": expected_epoch.epoch_id,
                "epoch_manifest_sha256": expected_epoch.manifest_sha256,
                "policy_sha256": policy.digest,
                "decision_input_digest": decision_input_digest,
                "reason": reason,
                "selected_lane": selected.lane_id if selected else None,
                "standby_absence_count_after": absence_count,
            }
            terminal, terminal_digest = _write_timestamped_immutable_json(
                terminal_path,
                terminal_value,
                timestamp_field="decided_at",
                timestamp=now,
            )
        else:
            terminal_digest = _sha256(terminal_path)
        state_needs_update = not (
            state.get("last_market_date") == market_date.isoformat()
            and state.get("last_input_digest") == decision_input_digest
            and state.get("last_reason") == reason
            and state.get("last_selected_lane") == (selected.lane_id if selected else None)
            and state.get("standby_absence_count") == absence_count
        )
        arbitration_identity = {
            "decision_input_digest": decision_input_digest,
            "reason": reason,
            "selected_lane": selected.lane_id if selected else None,
            "terminal_decision_path": terminal_path.relative_to(root).as_posix(),
            "terminal_decision_sha256": terminal_digest,
        }
        arbitration_id = (
            f"arbitration-{market_date.strftime('%Y%m%d')}-"
            f"{_canonical_digest(arbitration_identity).removeprefix('sha256:')[:20]}"
        )
        arbitration_record = (
            root / "arbitrations" / market_date.isoformat() / f"{arbitration_id}.json"
        )
        evidence = {
            "schema_version": 1,
            "kind": "dual_lane_arbitration",
            "arbitration_id": arbitration_id,
            "market_date": market_date.isoformat(),
            "policy_sha256": policy.digest,
            "primary": primary.manifest_sha256 if primary else None,
            "primary_error": primary_error,
            "standby": standby.manifest_sha256 if standby else None,
            "standby_error": standby_error,
            "reason": reason,
            "selected_lane": selected.lane_id if selected else None,
        }
        if arbitration_record.is_file():
            existing = _load_object(arbitration_record)
            for key, value in evidence.items():
                if existing.get(key) != value:
                    raise DualLaneError("existing arbitration record does not match its inputs")
            release_set_id = existing.get("release_set_id")
            if selected is not None:
                if not isinstance(release_set_id, str) or not release_set_id:
                    raise DualLaneError("published arbitration record has no release set")
                release_root = root / "release-sets" / release_set_id
                _verify_release_set(root, release_root)
                return ArbitrationResult(
                    market_date=market_date,
                    status="published",
                    reason=reason,
                    selected_lane=selected.lane_id,
                    release_set_id=release_set_id,
                    release_set_pointer=root / "release-set_CURRENT.json",
                    record_path=arbitration_record,
                )
            return ArbitrationResult(
                market_date=market_date,
                status="withheld",
                reason=reason,
                selected_lane=None,
                release_set_id=None,
                release_set_pointer=None,
                record_path=arbitration_record,
            )
        pointer_path = root / "release-set_CURRENT.json"
        previous_pointer = _load_object(pointer_path) if pointer_path.is_file() else None
        previous_pointer_sha256 = _sha256(pointer_path) if pointer_path.is_file() else None
        if selected is None:
            if state_needs_update:
                _atomic_json(
                    state_path,
                    {
                        "schema_version": 1,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "last_market_date": market_date.isoformat(),
                        "last_input_digest": decision_input_digest,
                        "last_reason": reason,
                        "last_selected_lane": None,
                        "last_arbitration_id": arbitration_id,
                        "standby_absence_count": absence_count,
                    },
                )
            _write_timestamped_immutable_json(
                arbitration_record,
                evidence,
                timestamp_field="evaluated_at",
            )
            return ArbitrationResult(
                market_date=market_date,
                status="withheld",
                reason=reason,
                selected_lane=None,
                release_set_id=None,
                release_set_pointer=None,
                record_path=arbitration_record,
            )
        generation_id = (
            f"generation-{market_date.strftime('%Y%m%d')}-{selected.lane_id}-"
            f"{selected.candidate_content_digest.removeprefix('sha256:')[:16]}"
        )
        release_set_id = f"release-set-{market_date.strftime('%Y%m%d')}-{generation_id[-16:]}"
        if (
            previous_pointer is not None
            and previous_pointer.get("release_set_id") == release_set_id
        ):
            release_set_root, release_set = _release_from_pointer(root, previous_pointer)
            if (
                release_set.get("generation_id") != generation_id
                or release_set.get("candidate_manifest_sha256") != selected.manifest_sha256
            ):
                raise DualLaneError(
                    "current release-set identity conflicts with selected candidate"
                )
            evidence.update(
                {
                    "generation_id": generation_id,
                    "acceptance_sha256": release_set.get("acceptance_sha256"),
                    "catalog_id": Path(_required_string(release_set, "catalog_path")).parent.name,
                    "operational_ads_bundle_digest": release_set.get(
                        "operational_ads_bundle_digest"
                    ),
                    "release_set_id": release_set_id,
                    "release_set_manifest_sha256": _sha256(release_set_root / "manifest.json"),
                }
            )
            _write_timestamped_immutable_json(
                arbitration_record,
                evidence,
                timestamp_field="evaluated_at",
            )
            return ArbitrationResult(
                market_date=market_date,
                status="published",
                reason=reason,
                selected_lane=selected.lane_id,
                release_set_id=release_set_id,
                release_set_pointer=pointer_path,
                record_path=arbitration_record,
            )
        if state_needs_update:
            _atomic_json(
                state_path,
                {
                    "schema_version": 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "last_market_date": market_date.isoformat(),
                    "last_input_digest": decision_input_digest,
                    "last_reason": reason,
                    "last_selected_lane": selected.lane_id,
                    "last_arbitration_id": arbitration_id,
                    "standby_absence_count": absence_count,
                },
            )
        generation_root = (
            root / "canonical" / "generations" / market_date.isoformat() / generation_id
        )
        canonical_candidate = _copy_candidate(selected, generation_root)
        acceptance = {
            "schema_version": 1,
            "kind": "canonical_acceptance_record",
            "market_date": market_date.isoformat(),
            "generation_id": generation_id,
            "selected_lane": selected.lane_id,
            "reason": reason,
            "arbitration_id": arbitration_id,
            "candidate_manifest_sha256": canonical_candidate.manifest_sha256,
            "candidate_content_digest": canonical_candidate.candidate_content_digest,
            "business_content_digest": canonical_candidate.business_content_digest,
            "epoch_manifest_sha256": canonical_candidate.epoch_manifest_sha256,
            "ods_manifest_sha256": canonical_candidate.ods_manifest_sha256,
            "terminal_decision_path": terminal_path.relative_to(root).as_posix(),
            "terminal_decision_sha256": terminal_digest,
            "generation_path": generation_root.relative_to(root).as_posix(),
        }
        acceptance_path = (
            root / "canonical" / "acceptance" / market_date.isoformat() / f"{generation_id}.json"
        )
        _, acceptance_digest = _write_timestamped_immutable_json(
            acceptance_path,
            acceptance,
            timestamp_field="accepted_at",
        )
        mappings = _load_catalog(root, previous_pointer)
        existing_generation = mappings.get(market_date.isoformat())
        if existing_generation is not None and existing_generation != generation_id:
            raise DualLaneError("correction requires an explicit correction policy")
        mappings[market_date.isoformat()] = generation_id
        catalog_body = {
            "schema_version": 1,
            "kind": "canonical_catalog_snapshot",
            "market_date_generations": dict(sorted(mappings.items())),
        }
        catalog_id = f"catalog-{_canonical_digest(catalog_body).removeprefix('sha256:')[:20]}"
        catalog_path = root / "canonical" / "catalogs" / catalog_id / "catalog.json"
        _, catalog_digest = _write_timestamped_immutable_json(
            catalog_path,
            {**catalog_body, "catalog_id": catalog_id},
            timestamp_field="created_at",
        )
        ads_id = f"ads-operational-{market_date.strftime('%Y%m%d')}-{generation_id[-16:]}"
        ads_root = root / "ads" / "operational" / market_date.isoformat() / ads_id
        ads_manifest_digest, ads_bundle_digest = _build_operational_ads(
            candidate=canonical_candidate,
            output=ads_root,
            acceptance_digest=acceptance_digest,
            catalog_id=catalog_id,
            catalog_digest=catalog_digest,
            generation_id=generation_id,
            policy=policy,
        )
        release_set_root = root / "release-sets" / release_set_id
        if not release_set_root.exists():
            release_set_root.parent.mkdir(parents=True, exist_ok=True)
            release_staging = Path(
                tempfile.mkdtemp(prefix=f".{release_set_id}.", dir=release_set_root.parent)
            )
            release = {
                "schema_version": 1,
                "kind": "dual_lane_release_set",
                "release_set_id": release_set_id,
                "market_date": market_date.isoformat(),
                "generation_id": generation_id,
                "acceptance_path": acceptance_path.relative_to(root).as_posix(),
                "acceptance_sha256": acceptance_digest,
                "catalog_path": catalog_path.relative_to(root).as_posix(),
                "catalog_sha256": catalog_digest,
                "operational_ads_path": ads_root.relative_to(root).as_posix(),
                "operational_ads_manifest_sha256": ads_manifest_digest,
                "operational_ads_bundle_digest": ads_bundle_digest,
                "generation_path": generation_root.relative_to(root).as_posix(),
                "candidate_manifest_sha256": canonical_candidate.manifest_sha256,
            }
            _seal(release_staging, release_set_root, release)
        release_set = _verify_release_set(root, release_set_root)
        release_manifest_digest = _sha256(release_set_root / "manifest.json")
        publication_id = f"publication-{release_set_id}"
        publication_event = root / "publication-events" / f"{publication_id}.json"
        old_release = previous_pointer.get("release_set_id") if previous_pointer else None
        event = {
            "schema_version": 1,
            "kind": "release_set_publication_intent",
            "publication_id": publication_id,
            "old_release_set_id": old_release,
            "new_release_set_id": release_set_id,
            "new_release_set_manifest_sha256": release_manifest_digest,
            "reason_code": "automatic_arbitration",
        }
        _write_timestamped_immutable_json(
            publication_event,
            event,
            timestamp_field="published_at",
        )
        compare_and_swap_release_pointer(
            root=root,
            policy=policy,
            release_set_root=release_set_root,
            publication_event_path=publication_event,
            expected_pointer_sha256=previous_pointer_sha256,
            lease=lease,
            reason_code="automatic_arbitration",
            clock=clock,
        )
        evidence.update(
            {
                "generation_id": generation_id,
                "acceptance_sha256": acceptance_digest,
                "catalog_id": catalog_id,
                "operational_ads_bundle_digest": ads_bundle_digest,
                "release_set_id": release_set_id,
                "release_set_manifest_sha256": release_manifest_digest,
            }
        )
        _write_timestamped_immutable_json(
            arbitration_record,
            evidence,
            timestamp_field="evaluated_at",
        )
        return ArbitrationResult(
            market_date=market_date,
            status="published",
            reason=reason,
            selected_lane=selected.lane_id,
            release_set_id=_required_string(release_set, "release_set_id"),
            release_set_pointer=pointer_path,
            record_path=arbitration_record,
        )
