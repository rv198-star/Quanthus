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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quanthus.pipelines.contracts import verify_ods_release
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
    security_master_path: Path


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
    security_master_path = _resolve_inside(
        root,
        _required_string(references, "security_master_path"),
        field="shared_references.security_master_path",
    )
    if _sha256(universe_path) != references.get("universe_policy_sha256"):
        raise DualLaneError("epoch universe-policy checksum mismatch")
    if _sha256(validity_path) != references.get("code_validity_sha256"):
        raise DualLaneError("epoch code-validity checksum mismatch")
    if _sha256(security_master_path) != references.get("security_master_sha256"):
        raise DualLaneError("epoch security-master checksum mismatch")
    code_revision = _required_string(manifest, "code_revision")
    if _HEX_OBJECT.fullmatch(code_revision) is None:
        raise DualLaneError("epoch code_revision must be a full lowercase Git object id")
    epoch_id = _required_string(manifest, "epoch_id")
    policy_sha256 = _required_string(manifest, "policy_sha256")
    declared_input_digest = manifest.get("input_digest")
    if declared_input_digest is not None:
        input_digest = _epoch_input_digest(
            _required_string(source, "source_pointer_sha256"),
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
) -> EpochInput:
    if _SAFE_ID.fullmatch(epoch_id) is None:
        raise DualLaneError("epoch_id contains unsupported characters")
    if _HEX_OBJECT.fullmatch(code_revision) is None:
        raise DualLaneError("code_revision must be a full lowercase Git object id")
    if (parent_pointer is None) == (base_pointer is None):
        raise DualLaneError("epoch publication requires exactly one parent or base pointer")
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
        ):
            raise DualLaneError("existing epoch does not match the request")
        return existing
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
    """Resolve one complete market-daily attempt without trusting a shared CURRENT pointer."""

    date_root = date_root.resolve()
    attempts: list[Path] = []
    if preferred_attempt is not None:
        if _SAFE_ID.fullmatch(preferred_attempt) is None:
            raise DualLaneError("preferred ODS attempt id is unsafe")
        attempts.append(date_root / preferred_attempt)
    if date_root.is_dir():
        attempts.extend(
            sorted(
                (
                    item
                    for item in date_root.iterdir()
                    if item.is_dir()
                    and not item.is_symlink()
                    and _SAFE_ID.fullmatch(item.name) is not None
                ),
                key=lambda item: item.name,
                reverse=True,
            )
        )
    seen: set[Path] = set()
    for attempt in attempts:
        resolved = attempt.resolve()
        if resolved in seen or date_root not in resolved.parents:
            continue
        seen.add(resolved)
        try:
            verified = verify_ods_release(resolved)
        except (OSError, ValueError, RuntimeError):
            continue
        artifact_names = {artifact.name for artifact in verified.release.artifacts}
        if (
            verified.release.schedule == "daily"
            and verified.release.market_date == market_date
            and artifact_names >= _REQUIRED_MARKET_DAILY_ARTIFACTS
        ):
            return resolved
    raise DualLaneError(f"no complete market-daily ODS release found under {date_root}")


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
    security_master_sha256: str


def verify_lane_candidate(root: Path) -> LaneCandidate:
    root = root.resolve()
    manifest = _verify_ready(root)
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "dual_lane_dwd_dws_candidate":
        raise DualLaneError("unsupported lane-candidate manifest")
    _verify_files(root, manifest.get("files"))
    lane_id = _required_string(manifest, "lane_id")
    if lane_id not in _LANES:
        raise DualLaneError("candidate lane_id is invalid")
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
    security_master_sha256 = _required_string(dwd_lineage, "security_master_sha256")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", security_master_sha256) is None:
        raise DualLaneError("candidate security-master digest is invalid")
    if security_master_sha256 != manifest.get("security_master_sha256"):
        raise DualLaneError("candidate security-master lineage mismatch")
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
    content_value = {
        "lane_id": lane_id,
        "market_date": market_date.isoformat(),
        "epoch_manifest_sha256": manifest.get("epoch_manifest_sha256"),
        "ods_manifest_sha256": manifest.get("ods_manifest_sha256"),
        "business_content_digest": business_digest,
        "code_revision": code_revision,
        "runtime": normalized_runtime,
        "contract_id": manifest.get("contract_id"),
        "policy_sha256": manifest.get("policy_sha256"),
        "security_master_sha256": security_master_sha256,
    }
    content_digest = _canonical_digest(content_value)
    if content_digest != manifest.get("candidate_content_digest"):
        raise DualLaneError("candidate content digest mismatch")
    attempt_id = _required_string(manifest, "attempt_id")
    if _SAFE_ID.fullmatch(attempt_id) is None:
        raise DualLaneError("candidate attempt_id contains unsupported characters")
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
        ods_manifest_sha256=_required_string(manifest, "ods_manifest_sha256"),
        contract_id=_required_string(manifest, "contract_id"),
        policy_sha256=_required_string(manifest, "policy_sha256"),
        code_revision=code_revision,
        runtime=normalized_runtime,
        security_master_sha256=security_master_sha256,
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
) -> LaneCandidate:
    if lane_id not in _LANES:
        raise DualLaneError("lane_id must be cn01 or hermes")
    for name, value in (("candidate_id", candidate_id), ("attempt_id", attempt_id)):
        if _SAFE_ID.fullmatch(value) is None:
            raise DualLaneError(f"{name} contains unsupported characters")
    if _HEX_OBJECT.fullmatch(code_revision) is None:
        raise DualLaneError("code_revision must be a full lowercase Git object id")
    epoch = verify_epoch_input(epoch_root)
    if epoch.market_date != market_date:
        raise DualLaneError("epoch and candidate market_date differ")
    if epoch.contract_id != policy.contract_id or epoch.policy_sha256 != policy.digest:
        raise DualLaneError("epoch input is not bound to the active dual-lane policy")
    if epoch.code_revision != code_revision:
        raise DualLaneError("candidate code revision differs from the frozen epoch")
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
            or existing.security_master_sha256 != _sha256(epoch.security_master_path)
        ):
            raise DualLaneError("existing candidate does not match the request")
        return existing
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
            security_master_path=epoch.security_master_path,
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
            "security_master_sha256": _sha256(epoch.security_master_path),
        }
        manifest = {
            "schema_version": 1,
            "kind": "dual_lane_dwd_dws_candidate",
            "status": "ready",
            "candidate_id": candidate_id,
            "lane_id": lane_id,
            "attempt_id": attempt_id,
            "market_date": market_date.isoformat(),
            "candidate_ready_at": datetime.now(timezone.utc).isoformat(),
            "contract_id": policy.contract_id,
            "policy_sha256": policy.digest,
            "code_revision": code_revision,
            "runtime": runtime,
            "epoch_id": epoch.epoch_id,
            "epoch_manifest_sha256": epoch.manifest_sha256,
            "ods_release_id": ods.release.release_id,
            "ods_manifest_sha256": ods.manifest_sha256,
            "security_master_sha256": _sha256(epoch.security_master_path),
            "knowledge_cutoff": ods.release.created_at.isoformat(),
            "business_content_digest": business_digest,
            "candidate_content_digest": _canonical_digest(content_value),
            "hard_gates": {
                "dwd_integrity": "passed",
                "dws_integrity": "passed",
                "epoch_identity": "passed",
                "ods_integrity": "passed",
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
    _write_json(path, value)
    return _sha256(path)


def _write_timestamped_immutable_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    timestamp_field: str,
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
    record = {**value, timestamp_field: datetime.now(timezone.utc).isoformat()}
    return record, _write_immutable_json(path, record)


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
    verify_lane_candidate(
        _resolve_inside(
            root,
            _required_string(release, "generation_path"),
            field="generation_path",
        )
    )
    return release


def _release_from_pointer(
    root: Path, pointer_payload: Mapping[str, Any]
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
    return release_path, release


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
    lock_path = (
        publisher_lock_path.resolve()
        if publisher_lock_path is not None
        else root / "locks" / "canonical-publisher.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
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
            elif candidate.security_master_sha256 != _sha256(expected_epoch.security_master_path):
                coherence_reason = "withheld_security_master_mismatch"
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
        selected: LaneCandidate | None = None
        same_decision = (
            state.get("last_market_date") == market_date.isoformat()
            and state.get("last_input_digest") == decision_input_digest
        )
        if coherence_reason is not None:
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
        arbitration_identity = {
            "decision_input_digest": decision_input_digest,
            "reason": reason,
            "selected_lane": selected.lane_id if selected else None,
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
        if selected is None:
            if not same_decision:
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
        if not same_decision:
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
        }
        event, event_digest = _write_timestamped_immutable_json(
            publication_event,
            event,
            timestamp_field="published_at",
        )
        published_at = _required_string(event, "published_at")
        _atomic_json(
            pointer_path,
            {
                "schema_version": 1,
                "release_set_id": release_set_id,
                "release_set_path": release_set_root.relative_to(root).as_posix(),
                "manifest_sha256": release_manifest_digest,
                "publication_event_path": publication_event.relative_to(root).as_posix(),
                "publication_event_sha256": event_digest,
                "published_at": published_at,
            },
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
