"""Immutable monthly-complete DWD/DWS Base package contracts."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.dual_lane import (
    EpochInput,
    LaneCandidate,
    load_candidate_current,
    verify_epoch_input,
    verify_lane_candidate,
)
from quanthus.pipelines.dual_lane_history import (
    HistoryBaseBundle,
    HistoryBasePolicy,
    verify_history_base_bundle,
)


class DwdDwsBaseError(RuntimeError):
    """Raised when a DWD/DWS Base input or package violates its contract."""


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_DWS_METRICS = (
    "security_count",
    "tradable_security_count",
    "suspended_security_count",
    "st_security_count",
    "bar_security_count",
    "advanced_security_count",
    "declined_security_count",
    "flat_security_count",
    "missing_pct_change_security_count",
    "total_volume",
    "total_amount",
)
_PACKAGE_CONTENT_FIELDS = (
    "contract_id",
    "policy_sha256",
    "phase",
    "through_date",
    "code_revision",
    "runtime",
    "transformation",
    "input_ledger",
    "upstream_ods_base",
    "source_lineage",
    "datasets",
    "quality",
    "authority",
)
_PARQUET_FACT_FIELDS = (
    "path",
    "sha256",
    "size_bytes",
    "row_count",
    "key_count",
    "duplicate_key_count",
    "key_null_count",
    "business_key",
    "date_column",
    "date_start",
    "date_end",
    "schema",
    "schema_sha256",
    "null_counts",
)


@dataclass(frozen=True)
class DwdDwsBasePolicy:
    contract_id: str
    phase: str
    through_date: date
    open_sessions: tuple[date, ...]
    dwd_package_id: str
    dws_package_id: str
    history_bundle_id: str
    history_manifest_sha256: str
    history_content_digest: str
    canonical_catalog_id: str
    canonical_catalog_sha256: str
    calendar_id: str
    calendar_sha256: str
    projection_contract: str
    upstream_ods_base: Mapping[str, Any]
    path: Path
    digest: str


@dataclass(frozen=True)
class BaseInputLedger:
    path: Path
    digest: str
    through_date: date
    value: Mapping[str, Any]


@dataclass(frozen=True)
class LayerBasePackage:
    root: Path
    package_id: str
    layer: str
    manifest_sha256: str
    content_digest: str
    through_date: date
    input_ledger_digest: str
    value: Mapping[str, Any]


@dataclass(frozen=True)
class DwdDwsBasePair:
    dwd: LayerBasePackage
    dws: LayerBasePackage


@dataclass(frozen=True)
class BaseLaneHistoryCatalog:
    root: Path
    catalog_id: str
    lane_id: str
    manifest_sha256: str
    content_digest: str
    pair: DwdDwsBasePair
    candidates: tuple[LaneCandidate, ...]
    through_date: date


@dataclass(frozen=True)
class _ResolvedGeneration:
    candidate: LaneCandidate
    epoch: EpochInput
    acceptance: Mapping[str, Any]
    acceptance_sha256: str


@dataclass(frozen=True)
class _ResolvedInputs:
    history: HistoryBaseBundle
    generations: tuple[_ResolvedGeneration, ...]
    ledger_value: Mapping[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DwdDwsBaseError(f"cannot load JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise DwdDwsBaseError(f"JSON value must be an object: {path}")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise DwdDwsBaseError(f"{key} must be a non-empty string")
    return item


def _digest(value: Mapping[str, Any], key: str) -> str:
    item = _text(value, key)
    if _SHA256.fullmatch(item) is None:
        raise DwdDwsBaseError(f"{key} must be a SHA-256 digest")
    return item


def _parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise DwdDwsBaseError(f"{field} must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DwdDwsBaseError(f"{field} must use YYYY-MM-DD") from exc


def _safe_relative(value: str, field: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() == "." or ".." in path.parts:
        raise DwdDwsBaseError(f"{field} must be a safe relative path")
    return path


def _inside(root: Path, value: str, field: str) -> Path:
    root = root.resolve()
    relative = _safe_relative(value, field)
    resolved = root.joinpath(*relative.parts).resolve()
    if root not in resolved.parents:
        raise DwdDwsBaseError(f"{field} escapes its package root")
    return resolved


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path = path.resolve()
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


def _runtime_identity() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in ("duckdb", "pyarrow", "quanthus"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "source-tree"
    return {
        "python_series": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": packages,
    }


def _manifest_files(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "READY"}
    ]


def _verify_files(root: Path, value: object) -> None:
    if not isinstance(value, list):
        raise DwdDwsBaseError("package files must be an array")
    expected: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise DwdDwsBaseError("package file entry must be an object")
        relative = _safe_relative(_text(item, "path"), "files.path").as_posix()
        if relative in expected:
            raise DwdDwsBaseError(f"duplicate package file entry: {relative}")
        expected.add(relative)
        path = _inside(root, relative, "files.path")
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != item.get("size_bytes")
            or _sha256(path) != item.get("sha256")
        ):
            raise DwdDwsBaseError(f"package file integrity failed: {relative}")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "READY"}
    }
    if actual != expected:
        raise DwdDwsBaseError("package manifest file set differs from disk")


def _seal(staging: Path, output: Path, manifest: Mapping[str, Any]) -> None:
    _write_json(staging / "manifest.json", manifest)
    (staging / "READY").write_text(
        f"{_sha256(staging / 'manifest.json')}\n",
        encoding="ascii",
    )
    for path in sorted(staging.rglob("*")):
        if path.is_symlink():
            raise DwdDwsBaseError("symbolic links are forbidden in Base packages")
        path.chmod(0o750 if path.is_dir() else 0o640)
    staging.chmod(0o750)
    os.replace(staging, output)


def _apply_permissions(root: Path, *, owner_uid: int, group_gid: int) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise DwdDwsBaseError("symbolic links are forbidden in Base package trees")
        path.chmod(0o750 if path.is_dir() else 0o640)
        os.chown(path, owner_uid, group_gid)
    root.chmod(0o750)
    os.chown(root, owner_uid, group_gid)


def _ready_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest = root / "manifest.json"
    ready = root / "READY"
    if not manifest.is_file() or not ready.is_file():
        raise DwdDwsBaseError(f"Base package is missing manifest.json or READY: {root}")
    if ready.read_text(encoding="ascii").strip() != _sha256(manifest):
        raise DwdDwsBaseError(f"READY does not bind manifest.json: {root}")
    return _object(manifest)


def load_dwd_dws_base_policy(path: Path) -> DwdDwsBasePolicy:
    path = path.resolve()
    value = _object(path)
    packages = value.get("packages")
    history = value.get("historical_seed")
    catalog = value.get("canonical_catalog")
    calendar = value.get("calendar")
    projection = value.get("projection")
    authority = value.get("authority")
    retention = value.get("retention")
    upstream = value.get("upstream_ods_base")
    if value.get("schema_version") != 1 or not all(
        isinstance(item, Mapping)
        for item in (
            packages,
            history,
            catalog,
            calendar,
            projection,
            authority,
            retention,
            upstream,
        )
    ):
        raise DwdDwsBaseError("unsupported DWD/DWS Base policy")
    assert isinstance(packages, Mapping)
    assert isinstance(history, Mapping)
    assert isinstance(catalog, Mapping)
    assert isinstance(calendar, Mapping)
    assert isinstance(projection, Mapping)
    assert isinstance(authority, Mapping)
    assert isinstance(retention, Mapping)
    assert isinstance(upstream, Mapping)
    phase = _text(value, "phase")
    sessions_value = value.get("open_sessions")
    if not isinstance(sessions_value, list) or not sessions_value:
        raise DwdDwsBaseError("open_sessions must be a non-empty array")
    sessions = tuple(_parse_date(item, "open_sessions") for item in sessions_value)
    through_date = _parse_date(value.get("through_date"), "through_date")
    if (
        phase != "provisional"
        or sessions != tuple(sorted(set(sessions)))
        or sessions[-1] != through_date
        or authority.get("historical_seed") != "legacy_accepted_disclosed"
        or authority.get("daily_generations") != "canonical_acceptance_only"
        or authority.get("row_or_lane_mixing") is not False
        or packages.get("layout") != "layer-manifest-members-v1"
        or packages.get("ready_written_last") is not True
        or projection.get("contract") != "dws-from-exact-dwd-members-v1"
        or projection.get("source_equivalence") != "exact_rows"
        or retention.get("migration_mode") != "copy_only"
        or retention.get("delete_enabled") is not False
        or retention.get("absorbed_sources_retained") is not True
        or upstream.get("binding_state") not in {"pending_issue_47", "bound"}
    ):
        raise DwdDwsBaseError("DWD/DWS Base policy invariants are invalid")
    if upstream.get("binding_state") == "pending_issue_47" and (
        upstream.get("issue") != 47 or upstream.get("required_for_final") is not True
    ):
        raise DwdDwsBaseError("pending ODS Base binding must identify the Issue #47 dependency")
    if upstream.get("binding_state") == "bound":
        if _SAFE_ID.fullmatch(_text(upstream, "package_id")) is None:
            raise DwdDwsBaseError("bound ODS Base package id is unsafe")
        _digest(upstream, "manifest_sha256")
        _digest(upstream, "content_digest")
    dwd_id = _text(packages, "dwd")
    dws_id = _text(packages, "dws")
    identifiers = (
        _text(value, "contract_id"),
        dwd_id,
        dws_id,
        _text(history, "bundle_id"),
        _text(catalog, "catalog_id"),
        _text(calendar, "calendar_id"),
        _text(projection, "contract"),
    )
    if any(_SAFE_ID.fullmatch(item) is None for item in identifiers):
        raise DwdDwsBaseError("DWD/DWS Base policy contains an unsafe identifier")
    for section, key in (
        (history, "manifest_sha256"),
        (history, "content_digest"),
        (catalog, "sha256"),
        (calendar, "sha256"),
    ):
        _digest(section, key)
    return DwdDwsBasePolicy(
        contract_id=_text(value, "contract_id"),
        phase=phase,
        through_date=through_date,
        open_sessions=sessions,
        dwd_package_id=dwd_id,
        dws_package_id=dws_id,
        history_bundle_id=_text(history, "bundle_id"),
        history_manifest_sha256=_digest(history, "manifest_sha256"),
        history_content_digest=_digest(history, "content_digest"),
        canonical_catalog_id=_text(catalog, "catalog_id"),
        canonical_catalog_sha256=_digest(catalog, "sha256"),
        calendar_id=_text(calendar, "calendar_id"),
        calendar_sha256=_digest(calendar, "sha256"),
        projection_contract=_text(projection, "contract"),
        upstream_ods_base=dict(upstream),
        path=path,
        digest=_sha256(path),
    )


def _catalog_mapping(publication_root: Path, policy: DwdDwsBasePolicy) -> Mapping[str, str]:
    path = (
        publication_root.resolve()
        / "canonical"
        / "catalogs"
        / policy.canonical_catalog_id
        / "catalog.json"
    )
    if _sha256(path) != policy.canonical_catalog_sha256:
        raise DwdDwsBaseError("canonical catalog checksum differs from Base policy")
    value = _object(path)
    mapping = value.get("market_date_generations")
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "canonical_catalog_snapshot"
        or value.get("catalog_id") != policy.canonical_catalog_id
        or not isinstance(mapping, Mapping)
    ):
        raise DwdDwsBaseError("canonical catalog contract is invalid")
    result: dict[str, str] = {}
    for market_date in policy.open_sessions:
        generation_id = mapping.get(market_date.isoformat())
        if not isinstance(generation_id, str) or _SAFE_ID.fullmatch(generation_id) is None:
            raise DwdDwsBaseError(
                f"canonical catalog has no safe generation for {market_date.isoformat()}"
            )
        result[market_date.isoformat()] = generation_id
    return result


def _generation_acceptance(
    *, publication_root: Path, market_date: date, generation_id: str
) -> tuple[dict[str, Any], str]:
    path = (
        publication_root.resolve()
        / "canonical"
        / "acceptance"
        / market_date.isoformat()
        / f"{generation_id}.json"
    )
    value = _object(path)
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "canonical_acceptance_record"
        or value.get("generation_id") != generation_id
        or value.get("market_date") != market_date.isoformat()
    ):
        raise DwdDwsBaseError("canonical acceptance record identity is invalid")
    return value, _sha256(path)


def _shared_references(epoch: EpochInput) -> dict[str, str]:
    value = _object(epoch.root / "manifest.json").get("shared_references")
    if not isinstance(value, Mapping):
        raise DwdDwsBaseError("epoch has no shared-reference contract")
    result = {
        "universe_policy_sha256": _digest(value, "universe_policy_sha256"),
        "code_validity_sha256": _digest(value, "code_validity_sha256"),
    }
    security_master = value.get("security_master_sha256")
    if security_master is not None:
        if not isinstance(security_master, str) or _SHA256.fullmatch(security_master) is None:
            raise DwdDwsBaseError("epoch security-master digest is invalid")
        result["security_master_sha256"] = security_master
    return result


def _collect_inputs(
    *,
    history_bundle: Path,
    history_policy: HistoryBasePolicy,
    dual_lane_root: Path,
    publication_root: Path,
    policy: DwdDwsBasePolicy,
) -> _ResolvedInputs:
    history = verify_history_base_bundle(history_bundle, policy=history_policy)
    if (
        history.bundle_id != policy.history_bundle_id
        or history.manifest_sha256 != policy.history_manifest_sha256
        or history.content_digest != policy.history_content_digest
    ):
        raise DwdDwsBaseError("historical seed differs from DWD/DWS Base policy")
    if policy.open_sessions[0] <= history.dwd_date_end:
        raise DwdDwsBaseError("daily generation sessions overlap the historical seed")
    catalog = _catalog_mapping(publication_root, policy)
    resolved: list[_ResolvedGeneration] = []
    ledger_generations: list[dict[str, Any]] = []
    previous_dwd_revision: str | None = None
    previous_dwd_manifest: str | None = None
    for market_date in policy.open_sessions:
        generation_id = catalog[market_date.isoformat()]
        acceptance, acceptance_sha256 = _generation_acceptance(
            publication_root=publication_root,
            market_date=market_date,
            generation_id=generation_id,
        )
        generation_root = (
            publication_root.resolve()
            / "canonical"
            / "generations"
            / market_date.isoformat()
            / generation_id
        )
        candidate = verify_lane_candidate(generation_root)
        candidate_manifest = _object(candidate.root / "manifest.json")
        epoch_id = _text(candidate_manifest, "epoch_id")
        epoch = verify_epoch_input(
            dual_lane_root.resolve() / "epochs" / market_date.isoformat() / epoch_id
        )
        dwd_manifest_sha256 = _sha256(candidate.dwd_root / "manifest.json")
        dws_manifest_sha256 = _sha256(candidate.dws_root / "manifest.json")
        dwd_manifest = _object(candidate.dwd_root / "manifest.json")
        parent = dwd_manifest.get("lineage", {}).get("parent")
        if not isinstance(parent, Mapping):
            raise DwdDwsBaseError("accepted DWD generation has no parent lineage")
        expected_parent: dict[str, str]
        if previous_dwd_revision is None:
            expected_parent = {
                "kind": "historical_base",
                "revision": history.source_revision,
                "manifest_sha256": history.source_meta_sha256,
            }
        else:
            assert previous_dwd_manifest is not None
            expected_parent = {
                "kind": "daily_partition",
                "revision": previous_dwd_revision,
                "manifest_sha256": previous_dwd_manifest,
            }
        if dict(parent) != expected_parent:
            raise DwdDwsBaseError(
                f"accepted DWD parent chain is discontinuous at {market_date.isoformat()}"
            )
        if (
            epoch.market_date != market_date
            or epoch.epoch_id != epoch_id
            or epoch.manifest_sha256 != candidate.epoch_manifest_sha256
            or epoch.source_kind != expected_parent["kind"]
            or epoch.source_revision != expected_parent["revision"]
            or epoch.source_manifest_sha256 != expected_parent["manifest_sha256"]
        ):
            raise DwdDwsBaseError("accepted generation differs from its exact epoch parent")
        if (
            acceptance.get("selected_lane") != candidate.lane_id
            or acceptance.get("candidate_manifest_sha256") != candidate.manifest_sha256
            or acceptance.get("candidate_content_digest") != candidate.candidate_content_digest
            or acceptance.get("business_content_digest") != candidate.business_content_digest
            or acceptance.get("epoch_manifest_sha256") != candidate.epoch_manifest_sha256
            or acceptance.get("ods_manifest_sha256") != candidate.ods_manifest_sha256
        ):
            raise DwdDwsBaseError("canonical acceptance differs from its generation package")
        knowledge_cutoff = _text(candidate_manifest, "knowledge_cutoff")
        hard_gates = candidate_manifest.get("hard_gates")
        if not isinstance(hard_gates, Mapping) or set(hard_gates.values()) != {"passed"}:
            raise DwdDwsBaseError("accepted generation hard gates are incomplete")
        ledger_generations.append(
            {
                "market_date": market_date.isoformat(),
                "authority_state": "canonical_accepted",
                "acceptance": {
                    "generation_id": generation_id,
                    "record_sha256": acceptance_sha256,
                    "accepted_at": _text(acceptance, "accepted_at"),
                    "reason": _text(acceptance, "reason"),
                    "selected_lane": candidate.lane_id,
                },
                "candidate": {
                    "candidate_id": candidate.candidate_id,
                    "manifest_sha256": candidate.manifest_sha256,
                    "content_digest": candidate.candidate_content_digest,
                    "business_content_digest": candidate.business_content_digest,
                    "epoch_id": epoch.epoch_id,
                    "epoch_manifest_sha256": epoch.manifest_sha256,
                    "contract_id": candidate.contract_id,
                    "policy_sha256": candidate.policy_sha256,
                    "code_revision": candidate.code_revision,
                    "runtime": dict(candidate.runtime),
                },
                "ods": {
                    "release_id": _text(candidate_manifest, "ods_release_id"),
                    "manifest_sha256": candidate.ods_manifest_sha256,
                    "knowledge_cutoff": knowledge_cutoff,
                    "source_inputs": [dict(item) for item in candidate.source_inputs],
                },
                "canonical_parent": expected_parent,
                "shared_references": _shared_references(epoch),
                "dwd": {
                    "revision": candidate.dwd_root.name,
                    "manifest_sha256": dwd_manifest_sha256,
                },
                "dws": {
                    "revision": candidate.dws_root.name,
                    "manifest_sha256": dws_manifest_sha256,
                },
                "quality": dict(sorted(hard_gates.items())),
            }
        )
        resolved.append(
            _ResolvedGeneration(
                candidate=candidate,
                epoch=epoch,
                acceptance=acceptance,
                acceptance_sha256=acceptance_sha256,
            )
        )
        previous_dwd_revision = candidate.dwd_root.name
        previous_dwd_manifest = dwd_manifest_sha256
    seed_meta = _object(history.dwd_root / "META.json")
    ledger_content = {
        "contract_id": policy.contract_id,
        "phase": policy.phase,
        "through_date": policy.through_date.isoformat(),
        "calendar": {
            "calendar_id": policy.calendar_id,
            "sha256": policy.calendar_sha256,
            "open_sessions": [item.isoformat() for item in policy.open_sessions],
            "closed_sessions": "represented_by_bound_calendar_only",
        },
        "historical_seed": {
            "authority_state": "legacy_accepted_disclosed",
            "bundle_id": history.bundle_id,
            "manifest_sha256": history.manifest_sha256,
            "content_digest": history.content_digest,
            "source_revision": history.source_revision,
            "source_meta_sha256": history.source_meta_sha256,
            "source_checksums_sha256": _digest(
                _object(history.root / "manifest.json")["source"], "checksums_sha256"
            ),
            "date_start": history.dwd_date_start.isoformat(),
            "date_end": history.dwd_date_end.isoformat(),
            "knowledge_cutoff": {
                "mode": "row_level_source_built_at",
                "seed_built_at": seed_meta.get("built_at"),
            },
        },
        "canonical_catalog": {
            "catalog_id": policy.canonical_catalog_id,
            "sha256": policy.canonical_catalog_sha256,
        },
        "upstream_ods_base": dict(policy.upstream_ods_base),
        "generations": ledger_generations,
    }
    ledger_value = {
        "schema_version": 1,
        "kind": "dwd_dws_base_input_ledger",
        **ledger_content,
        "ledger_digest": _canonical_digest(ledger_content),
    }
    return _ResolvedInputs(
        history=history,
        generations=tuple(resolved),
        ledger_value=ledger_value,
    )


def load_base_input_ledger(path: Path, *, policy: DwdDwsBasePolicy) -> BaseInputLedger:
    path = path.resolve()
    value = _object(path)
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "dwd_dws_base_input_ledger"
        or value.get("contract_id") != policy.contract_id
        or value.get("phase") != policy.phase
        or value.get("through_date") != policy.through_date.isoformat()
    ):
        raise DwdDwsBaseError("input ledger differs from active Base policy")
    generations = value.get("generations")
    if not isinstance(generations, list) or [
        item.get("market_date") if isinstance(item, Mapping) else None for item in generations
    ] != [item.isoformat() for item in policy.open_sessions]:
        raise DwdDwsBaseError("input ledger generation chain differs from Base policy")
    content = {
        key: item
        for key, item in value.items()
        if key not in {"schema_version", "kind", "ledger_digest"}
    }
    digest = _canonical_digest(content)
    if digest != value.get("ledger_digest"):
        raise DwdDwsBaseError("input ledger content digest mismatch")
    return BaseInputLedger(
        path=path,
        digest=digest,
        through_date=policy.through_date,
        value=value,
    )


def freeze_base_input_ledger(
    *,
    history_bundle: Path,
    history_policy: HistoryBasePolicy,
    dual_lane_root: Path,
    publication_root: Path,
    output: Path,
    policy: DwdDwsBasePolicy,
) -> BaseInputLedger:
    resolved = _collect_inputs(
        history_bundle=history_bundle,
        history_policy=history_policy,
        dual_lane_root=dual_lane_root,
        publication_root=publication_root,
        policy=policy,
    )
    output = output.resolve()
    if output.exists():
        existing = load_base_input_ledger(output, policy=policy)
        if dict(existing.value) != dict(resolved.ledger_value):
            raise DwdDwsBaseError("existing input ledger differs from frozen canonical inputs")
        return existing
    _atomic_json(output, resolved.ledger_value)
    return load_base_input_ledger(output, policy=policy)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _duckdb_connection() -> Any:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DwdDwsBaseError("P1 dependencies are missing; run make bootstrap-p1") from exc
    connection = duckdb.connect(":memory:")
    connection.execute("SET memory_limit = '1GB'")
    connection.execute("SET threads = 2")
    connection.execute("SET preserve_insertion_order = false")
    return connection


def _identifier(value: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise DwdDwsBaseError(f"unsafe Parquet column identifier: {value}")
    return f'"{value}"'


def _parquet_fact(
    *,
    package_root: Path,
    path: Path,
    business_key: Sequence[str],
    date_column: str | None,
) -> dict[str, Any]:
    relative = path.relative_to(package_root).as_posix()
    source = f"read_parquet({_sql_string(str(path))})"
    connection = _duckdb_connection()
    try:
        schema_rows = connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()
        schema = [{"name": str(row[0]), "type": str(row[1])} for row in schema_rows]
        columns = {item["name"] for item in schema}
        if any(key not in columns for key in business_key):
            raise DwdDwsBaseError(f"Parquet member lacks its business key: {relative}")
        if date_column is not None and date_column not in columns:
            raise DwdDwsBaseError(f"Parquet member lacks its date column: {relative}")
        key_sql = ", ".join(_identifier(item) for item in business_key)
        key_null = " OR ".join(f"{_identifier(item)} IS NULL" for item in business_key)
        null_sql = ", ".join(
            f"count(*) FILTER (WHERE {_identifier(item['name'])} IS NULL)" for item in schema
        )
        date_sql = (
            "NULL, NULL"
            if date_column is None
            else (
                f"min(CAST({_identifier(date_column)} AS DATE)), "
                f"max(CAST({_identifier(date_column)} AS DATE))"
            )
        )
        row = connection.execute(
            f"SELECT count(*), count(DISTINCT ({key_sql})), "
            f"count(*) FILTER (WHERE {key_null}), {date_sql}, {null_sql} FROM {source}"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise DwdDwsBaseError(f"Parquet audit returned no row: {relative}")
    row_count = int(row[0])
    key_count = int(row[1])
    date_start = None if row[3] is None else str(row[3])
    date_end = None if row[4] is None else str(row[4])
    null_counts = {item["name"]: int(count) for item, count in zip(schema, row[5:], strict=True)}
    return {
        "path": relative,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "row_count": row_count,
        "key_count": key_count,
        "duplicate_key_count": row_count - key_count,
        "key_null_count": int(row[2]),
        "business_key": list(business_key),
        "date_column": date_column,
        "date_start": date_start,
        "date_end": date_end,
        "schema": schema,
        "schema_sha256": _canonical_digest(schema),
        "null_counts": null_counts,
    }


def _fact_core(value: Mapping[str, Any]) -> dict[str, Any]:
    return {field: value.get(field) for field in _PARQUET_FACT_FIELDS}


def _copy_parquet(
    *,
    source: Path,
    staging: Path,
    relative: str,
    business_key: Sequence[str],
    date_column: str | None,
    source_identity: Mapping[str, Any],
) -> dict[str, Any]:
    target = _inside(staging, relative, "dataset part path")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    fact = _parquet_fact(
        package_root=staging,
        path=target,
        business_key=business_key,
        date_column=date_column,
    )
    if fact["duplicate_key_count"] != 0 or fact["key_null_count"] != 0:
        raise DwdDwsBaseError(f"conflicting or null business key in {relative}")
    return {**fact, "source": dict(source_identity)}


def _dataset(dataset_id: str, role: str, parts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not parts:
        raise DwdDwsBaseError(f"dataset has no members: {dataset_id}")
    return {
        "dataset_id": dataset_id,
        "role": role,
        "layout": "manifest_members_v1",
        "parts": [dict(item) for item in parts],
    }


def _package_content(value: Mapping[str, Any]) -> dict[str, Any]:
    content = {field: value.get(field) for field in _PACKAGE_CONTENT_FIELDS}
    if value.get("layer") == "dws":
        content["dwd_binding"] = value.get("dwd_binding")
        content["projection"] = value.get("projection")
    return content


def _pair_digest(pair: DwdDwsBasePair) -> str:
    return _canonical_digest(
        {
            "dwd_manifest_sha256": pair.dwd.manifest_sha256,
            "dwd_content_digest": pair.dwd.content_digest,
            "dws_manifest_sha256": pair.dws.manifest_sha256,
            "dws_content_digest": pair.dws.content_digest,
            "input_ledger_digest": pair.dwd.input_ledger_digest,
        }
    )


def _dwd_datasets(*, staging: Path, resolved: _ResolvedInputs) -> list[dict[str, Any]]:
    history = resolved.history
    seed_identity = {
        "authority_state": "legacy_accepted_disclosed",
        "bundle_id": history.bundle_id,
        "manifest_sha256": history.manifest_sha256,
        "source_revision": history.source_revision,
        "source_meta_sha256": history.source_meta_sha256,
    }
    specs = {
        "daily_ohlcv_none.parquet": (
            "historical_daily_market",
            "normalized_market_fact",
            ("security_id", "date"),
            "date",
        ),
        "daily_isST.parquet": (
            "historical_daily_status",
            "status_tradability_fact",
            ("security_id", "date"),
            "date",
        ),
        "daily_universe.parquet": (
            "historical_daily_universe",
            "identity_universe_reference",
            ("security_id", "market_date"),
            "market_date",
        ),
        "ref_security_code_validity.parquet": (
            "historical_code_validity",
            "identity_code_validity_reference",
            ("security_id", "stock_code", "exchange", "valid_from"),
            "valid_from",
        ),
        "st_status_pit.parquet": (
            "historical_st_status_pit",
            "pit_status_reference",
            ("security_id", "effective_start", "knowledge_cutoff"),
            "effective_start",
        ),
    }
    datasets: list[dict[str, Any]] = []
    for filename, (dataset_id, role, key, date_column) in specs.items():
        source = history.dwd_root / filename
        if not source.is_file():
            raise DwdDwsBaseError(f"historical seed lacks required member: {filename}")
        part = _copy_parquet(
            source=source,
            staging=staging,
            relative=f"datasets/{dataset_id}/part-00000.parquet",
            business_key=key,
            date_column=date_column,
            source_identity=seed_identity,
        )
        datasets.append(_dataset(dataset_id, role, [part]))
    daily_market: list[dict[str, Any]] = []
    daily_status: list[dict[str, Any]] = []
    references: dict[str, dict[str, Any]] = {}
    for item in resolved.generations:
        candidate = item.candidate
        market_date = candidate.market_date.isoformat()
        generation_id = _text(item.acceptance, "generation_id")
        reference = candidate.dwd_root / "ref_security_code_validity.parquet"
        reference_sha256 = _sha256(reference)
        manifest_identity = {
            "authority_state": "canonical_accepted",
            "generation_id": generation_id,
            "candidate_manifest_sha256": candidate.manifest_sha256,
            "dwd_revision": candidate.dwd_root.name,
            "dwd_manifest_sha256": _sha256(candidate.dwd_root / "manifest.json"),
            "ods_manifest_sha256": candidate.ods_manifest_sha256,
            "code_validity_sha256": reference_sha256,
        }
        partition = candidate.dwd_root / f"market_date={market_date}"
        daily_market.append(
            _copy_parquet(
                source=partition / "part-00000.parquet",
                staging=staging,
                relative=f"datasets/daily_market/market_date={market_date}/part-00000.parquet",
                business_key=("security_id", "market_date"),
                date_column="market_date",
                source_identity=manifest_identity,
            )
        )
        daily_status.append(
            _copy_parquet(
                source=partition / "daily_status_observation.parquet",
                staging=staging,
                relative=(
                    "datasets/daily_status_observation/"
                    f"market_date={market_date}/part-00000.parquet"
                ),
                business_key=("security_id", "market_date"),
                date_column="market_date",
                source_identity=manifest_identity,
            )
        )
        if reference_sha256 not in references:
            references[reference_sha256] = _copy_parquet(
                source=reference,
                staging=staging,
                relative=(
                    "datasets/daily_code_validity/"
                    f"{reference_sha256.removeprefix('sha256:')}.parquet"
                ),
                business_key=("security_id", "stock_code", "exchange", "valid_from"),
                date_column="valid_from",
                source_identity=manifest_identity,
            )
    datasets.append(_dataset("daily_market", "normalized_market_fact", daily_market))
    datasets.append(_dataset("daily_status_observation", "status_tradability_fact", daily_status))
    datasets.append(
        _dataset(
            "daily_code_validity",
            "identity_code_validity_reference",
            [references[key] for key in sorted(references)],
        )
    )
    return datasets


def _dataset_parts(value: Mapping[str, Any], dataset_id: str) -> list[Mapping[str, Any]]:
    datasets = value.get("datasets")
    if not isinstance(datasets, list):
        raise DwdDwsBaseError("Base package datasets must be an array")
    matches = [
        item
        for item in datasets
        if isinstance(item, Mapping) and item.get("dataset_id") == dataset_id
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("parts"), list):
        raise DwdDwsBaseError(f"Base package dataset is missing or ambiguous: {dataset_id}")
    parts = matches[0]["parts"]
    if any(not isinstance(item, Mapping) for item in parts):
        raise DwdDwsBaseError(f"Base package dataset has a non-object part: {dataset_id}")
    return [item for item in parts if isinstance(item, Mapping)]


def _dwd_continuity_quality(root: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    def source(part: Mapping[str, Any]) -> str:
        path = _inside(root, _text(part, "path"), "DWD continuity part path")
        return f"read_parquet({_sql_string(str(path))})"

    def scalar(connection: Any, query: str) -> int:
        row = connection.execute(query).fetchone()
        if row is None:
            raise DwdDwsBaseError("DWD continuity query returned no row")
        return int(row[0])

    history_bars = _dataset_parts(value, "historical_daily_market")[0]
    history_status = _dataset_parts(value, "historical_daily_status")[0]
    history_universe = _dataset_parts(value, "historical_daily_universe")[0]
    history_validity = _dataset_parts(value, "historical_code_validity")[0]
    daily_bars = _dataset_parts(value, "daily_market")
    daily_status = _dataset_parts(value, "daily_status_observation")
    daily_validity = {
        item.get("sha256"): item for item in _dataset_parts(value, "daily_code_validity")
    }
    statuses_by_date = {item.get("date_start"): item for item in daily_status}
    connection = _duckdb_connection()
    try:
        seed_date = _sql_string(_text(history_bars, "date_end"))
        history_bar_status_misses = scalar(
            connection,
            f"""
            SELECT count(*)
            FROM {source(history_bars)} b
            LEFT JOIN {source(history_status)} s
              ON b.security_id = s.security_id AND CAST(b.date AS DATE) = CAST(s.date AS DATE)
            WHERE CAST(b.date AS DATE) = CAST({seed_date} AS DATE) AND s.security_id IS NULL
            """,
        )
        history_identity_misses = scalar(
            connection,
            f"""
            SELECT count(*)
            FROM {source(history_status)} s
            LEFT JOIN {source(history_validity)} v
              ON s.security_id = v.security_id
             AND s.stock_code = v.stock_code
             AND CAST(s.date AS DATE) >= CAST(v.valid_from AS DATE)
             AND (v.valid_to IS NULL OR CAST(s.date AS DATE) <= CAST(v.valid_to AS DATE))
            WHERE CAST(s.date AS DATE) = CAST({seed_date} AS DATE) AND v.security_id IS NULL
            """,
        )
        history_invalid_status = scalar(
            connection,
            f"SELECT count(*) FROM {source(history_status)} "
            f"WHERE CAST(date AS DATE) = CAST({seed_date} AS DATE) "
            "AND (tradestatus NOT IN ('0', '1') OR isST NOT IN ('0', '1'))",
        )
        daily_bar_status_misses = 0
        daily_identity_misses = 0
        daily_invalid_status = 0
        daily_counts: list[dict[str, Any]] = []
        for bars in daily_bars:
            market_date = bars.get("date_start")
            statuses = statuses_by_date.get(market_date)
            source_identity = bars.get("source")
            if not isinstance(statuses, Mapping) or not isinstance(source_identity, Mapping):
                raise DwdDwsBaseError("DWD continuity lacks one daily status or source identity")
            reference = daily_validity.get(source_identity.get("code_validity_sha256"))
            if not isinstance(reference, Mapping):
                raise DwdDwsBaseError("DWD continuity lacks an exact daily code-validity member")
            misses = scalar(
                connection,
                f"""
                SELECT count(*)
                FROM {source(bars)} b
                LEFT JOIN {source(statuses)} s USING (security_id, market_date)
                WHERE s.security_id IS NULL
                """,
            )
            identity_misses = scalar(
                connection,
                f"""
                SELECT count(*)
                FROM {source(statuses)} s
                LEFT JOIN {source(reference)} v
                  ON s.security_id = v.security_id
                 AND s.stock_code = v.stock_code
                 AND CAST(s.market_date AS DATE) >= CAST(v.valid_from AS DATE)
                 AND (v.valid_to IS NULL OR CAST(s.market_date AS DATE) <= CAST(v.valid_to AS DATE))
                WHERE v.security_id IS NULL
                """,
            )
            invalid_status = scalar(
                connection,
                f"SELECT count(*) FROM {source(statuses)} "
                "WHERE observed_tradestatus NOT IN ('0', '1') "
                "OR observed_is_st NOT IN ('0', '1')",
            )
            daily_bar_status_misses += misses
            daily_identity_misses += identity_misses
            daily_invalid_status += invalid_status
            daily_counts.append(
                {
                    "market_date": market_date,
                    "bar_rows": bars.get("row_count"),
                    "status_rows": statuses.get("row_count"),
                    "bar_without_status_rows": misses,
                    "status_without_code_validity_rows": identity_misses,
                    "invalid_status_rows": invalid_status,
                }
            )
    finally:
        connection.close()
    failures = (
        history_bar_status_misses
        + history_identity_misses
        + history_invalid_status
        + daily_bar_status_misses
        + daily_identity_misses
        + daily_invalid_status
    )
    if failures:
        raise DwdDwsBaseError(f"DWD identity/status continuity has {failures} failing rows")
    return {
        "contract": "former-boundary-identity-status-v1",
        "historical_boundary_date": history_bars.get("date_end"),
        "historical_universe_snapshot_through": history_universe.get("date_end"),
        "continuity_basis": "status_and_code_validity",
        "historical_bar_without_status_rows": history_bar_status_misses,
        "historical_status_without_code_validity_rows": history_identity_misses,
        "historical_invalid_status_rows": history_invalid_status,
        "daily_bar_without_status_rows": daily_bar_status_misses,
        "daily_status_without_code_validity_rows": daily_identity_misses,
        "daily_invalid_status_rows": daily_invalid_status,
        "daily_counts": daily_counts,
    }


def _dws_projection_query(dwd_root: Path, dwd_manifest: Mapping[str, Any]) -> str:
    history_bars = _dataset_parts(dwd_manifest, "historical_daily_market")[0]
    history_status = _dataset_parts(dwd_manifest, "historical_daily_status")[0]
    history_revision = _text(history_bars["source"], "source_revision")
    history_bars_path = _inside(dwd_root, _text(history_bars, "path"), "DWD part path")
    history_status_path = _inside(dwd_root, _text(history_status, "path"), "DWD part path")
    queries = [
        f"""
        WITH status_rows AS (
            SELECT security_id, CAST(date AS DATE) AS market_date, isST, tradestatus
            FROM read_parquet({_sql_string(str(history_status_path))})
        ), scoped_bars AS (
            SELECT b.*
            FROM read_parquet({_sql_string(str(history_bars_path))}) b
            INNER JOIN status_rows s
              ON b.security_id = s.security_id AND CAST(b.date AS DATE) = s.market_date
        ), status_agg AS (
            SELECT market_date,
              count(*)::BIGINT AS security_count,
              count(*) FILTER (WHERE tradestatus = '1')::BIGINT AS tradable_security_count,
              count(*) FILTER (WHERE tradestatus = '0')::BIGINT AS suspended_security_count,
              count(*) FILTER (WHERE isST = '1')::BIGINT AS st_security_count
            FROM status_rows GROUP BY market_date
        ), bar_agg AS (
            SELECT CAST(date AS DATE) AS market_date,
              count(*)::BIGINT AS bar_security_count,
              count(*) FILTER (WHERE pct_change > 0)::BIGINT AS advanced_security_count,
              count(*) FILTER (WHERE pct_change < 0)::BIGINT AS declined_security_count,
              count(*) FILTER (WHERE pct_change = 0)::BIGINT AS flat_security_count,
              count(*) FILTER (WHERE pct_change IS NULL)::BIGINT
                AS missing_pct_change_security_count,
              coalesce(sum(CAST(volume AS DECIMAL(38, 6))), 0)::DOUBLE AS total_volume,
              coalesce(sum(CAST(amount AS DECIMAL(38, 6))), 0)::DOUBLE AS total_amount
            FROM scoped_bars GROUP BY CAST(date AS DATE)
        )
        SELECT s.market_date, {_sql_string(history_revision)} AS dwd_revision,
          s.security_count, s.tradable_security_count, s.suspended_security_count,
          s.st_security_count, coalesce(b.bar_security_count, 0)::BIGINT AS bar_security_count,
          coalesce(b.advanced_security_count, 0)::BIGINT AS advanced_security_count,
          coalesce(b.declined_security_count, 0)::BIGINT AS declined_security_count,
          coalesce(b.flat_security_count, 0)::BIGINT AS flat_security_count,
          coalesce(b.missing_pct_change_security_count, 0)::BIGINT
            AS missing_pct_change_security_count,
          coalesce(b.total_volume, 0)::DOUBLE AS total_volume,
          coalesce(b.total_amount, 0)::DOUBLE AS total_amount
        FROM status_agg s LEFT JOIN bar_agg b USING (market_date)
        """
    ]
    bars_by_date = {
        _text(item, "date_start"): item for item in _dataset_parts(dwd_manifest, "daily_market")
    }
    statuses_by_date = {
        _text(item, "date_start"): item
        for item in _dataset_parts(dwd_manifest, "daily_status_observation")
    }
    if set(bars_by_date) != set(statuses_by_date):
        raise DwdDwsBaseError("DWD daily bar and status date families differ")
    for market_date in sorted(bars_by_date):
        bars = bars_by_date[market_date]
        statuses = statuses_by_date[market_date]
        dwd_revision = _text(bars["source"], "dwd_revision")
        bars_path = _inside(dwd_root, _text(bars, "path"), "DWD part path")
        status_path = _inside(dwd_root, _text(statuses, "path"), "DWD part path")
        target = _sql_string(str(market_date))
        queries.append(
            f"""
            WITH status_rows AS (
              SELECT * FROM read_parquet({_sql_string(str(status_path))})
              WHERE CAST(market_date AS DATE) = CAST({target} AS DATE)
            ), bar_rows AS (
              SELECT * FROM read_parquet({_sql_string(str(bars_path))})
              WHERE CAST(market_date AS DATE) = CAST({target} AS DATE)
            )
            SELECT CAST({target} AS DATE) AS market_date,
              {_sql_string(dwd_revision)} AS dwd_revision,
              count(*)::BIGINT AS security_count,
              count(*) FILTER (WHERE observed_tradestatus = '1')::BIGINT
                AS tradable_security_count,
              count(*) FILTER (WHERE observed_tradestatus = '0')::BIGINT
                AS suspended_security_count,
              count(*) FILTER (WHERE observed_is_st = '1')::BIGINT AS st_security_count,
              (SELECT count(DISTINCT security_id)::BIGINT FROM bar_rows) AS bar_security_count,
              (SELECT count(DISTINCT security_id)::BIGINT FROM bar_rows WHERE pct_change > 0)
                AS advanced_security_count,
              (SELECT count(DISTINCT security_id)::BIGINT FROM bar_rows WHERE pct_change < 0)
                AS declined_security_count,
              (SELECT count(DISTINCT security_id)::BIGINT FROM bar_rows WHERE pct_change = 0)
                AS flat_security_count,
              (SELECT count(DISTINCT security_id)::BIGINT FROM bar_rows WHERE pct_change IS NULL)
                AS missing_pct_change_security_count,
              (SELECT coalesce(sum(CAST(volume AS DECIMAL(38, 6))), 0)::DOUBLE FROM bar_rows)
                AS total_volume,
              (SELECT coalesce(sum(CAST(amount AS DECIMAL(38, 6))), 0)::DOUBLE FROM bar_rows)
                AS total_amount
            FROM status_rows
            """
        )
    return " UNION ALL ".join(f"({query})" for query in queries)


def _expected_dws_query(resolved: _ResolvedInputs) -> str:
    paths = [resolved.history.dws_root / "daily_market_summary.parquet"]
    for item in resolved.generations:
        market_date = item.candidate.market_date.isoformat()
        paths.append(
            item.candidate.dws_root / f"market_date={market_date}" / "daily_market_summary.parquet"
        )
    return " UNION ALL ".join(
        f"SELECT * FROM read_parquet({_sql_string(str(path))})" for path in paths
    )


def _derive_dws(
    *, dwd_root: Path, dwd_manifest: Mapping[str, Any], output: Path, resolved: _ResolvedInputs
) -> dict[str, Any]:
    projection = _dws_projection_query(dwd_root, dwd_manifest)
    expected = _expected_dws_query(resolved)
    output.parent.mkdir(parents=True, exist_ok=True)
    connection = _duckdb_connection()
    try:
        # The projection scans the full historical DWD. Materialize both sides once so the
        # bidirectional EXCEPT does not expand and execute that scan twice in one query.
        connection.execute(f"CREATE TEMP TABLE projected_dws AS {projection}")
        connection.execute(f"CREATE TEMP TABLE expected_dws AS {expected}")
        difference = connection.execute(
            "SELECT count(*) FROM ((SELECT * FROM projected_dws EXCEPT ALL "
            "SELECT * FROM expected_dws) UNION ALL (SELECT * FROM expected_dws "
            "EXCEPT ALL SELECT * FROM projected_dws))"
        ).fetchone()
        if difference is None or int(difference[0]) != 0:
            raise DwdDwsBaseError("DWS projection differs from historical seed plus daily sources")
        connection.execute(
            "COPY (SELECT * FROM projected_dws ORDER BY market_date) "
            f"TO {_sql_string(str(output))} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        audit = connection.execute(
            f"SELECT count(*), count(DISTINCT market_date), min(market_date), max(market_date), "
            "count(*) FILTER (WHERE security_count <= 0 "
            "OR tradable_security_count + suspended_security_count != security_count "
            "OR st_security_count < 0 OR st_security_count > security_count "
            "OR bar_security_count < 0 OR bar_security_count > security_count "
            "OR advanced_security_count + declined_security_count + flat_security_count "
            "+ missing_pct_change_security_count != bar_security_count "
            "OR total_volume < 0 OR total_amount < 0) "
            f"FROM read_parquet({_sql_string(str(output))})"
        ).fetchone()
    finally:
        connection.close()
    expected_rows = resolved.history.dws_row_count + len(resolved.generations)
    if (
        audit is None
        or int(audit[0]) != expected_rows
        or int(audit[1]) != expected_rows
        or audit[2] != resolved.history.dws_date_start
        or audit[3] != resolved.generations[-1].candidate.market_date
        or int(audit[4]) != 0
    ):
        raise DwdDwsBaseError("derived DWS coverage or aggregate invariants failed")
    return {
        "source_equivalence": "exact_rows",
        "semantic_difference_rows": 0,
        "row_count": expected_rows,
        "date_start": str(audit[2]),
        "date_end": str(audit[3]),
        "aggregate_invariant_failures": 0,
    }


def _build_dwd_package(
    *,
    output: Path,
    ledger: BaseInputLedger,
    resolved: _ResolvedInputs,
    policy: DwdDwsBasePolicy,
    code_revision: str,
    runtime: Mapping[str, Any],
) -> LayerBasePackage:
    output = output.resolve()
    if output.name != policy.dwd_package_id:
        raise DwdDwsBaseError("DWD output must end with the policy package id")
    if output.exists():
        existing = verify_dwd_base_package(output, policy=policy)
        if (
            existing.value.get("code_revision") != code_revision
            or existing.value.get("runtime") != dict(runtime)
            or existing.input_ledger_digest != ledger.digest
        ):
            raise DwdDwsBaseError("existing DWD Base differs from the requested build identity")
        return existing
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        ledger_target = staging / "lineage" / "input-ledger.json"
        ledger_target.parent.mkdir()
        shutil.copy2(ledger.path, ledger_target)
        datasets = _dwd_datasets(staging=staging, resolved=resolved)
        continuity = _dwd_continuity_quality(staging, {"datasets": datasets})
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "kind": "dwd_dws_monthly_base_package",
            "manifest_id": f"{policy.dwd_package_id}-manifest-v1",
            "package_id": policy.dwd_package_id,
            "layer": "dwd",
            "contract_id": policy.contract_id,
            "policy_sha256": policy.digest,
            "phase": policy.phase,
            "through_date": policy.through_date.isoformat(),
            "code_revision": code_revision,
            "runtime": dict(runtime),
            "transformation": {
                "contract": "manifest-member-base-v1",
                "canonical_serialization": "sorted-json-no-attempt-time-v1",
                "deduplication": "reject-conflicting-business-keys-v1",
            },
            "input_ledger": {
                "path": "lineage/input-ledger.json",
                "sha256": _sha256(ledger_target),
                "digest": ledger.digest,
            },
            "upstream_ods_base": dict(policy.upstream_ods_base),
            "source_lineage": {
                "superseded_physical_seed_id": resolved.history.bundle_id,
                "superseded_physical_seed_manifest_sha256": resolved.history.manifest_sha256,
                "absorbed_generation_ids": [
                    _text(item.acceptance, "generation_id") for item in resolved.generations
                ],
            },
            "datasets": datasets,
            "quality": {
                "business_key_policy": "reject_conflicting_duplicates_v1",
                "duplicate_key_count": 0,
                "key_null_count": 0,
                "source_semantic_equivalence": "byte_identical_members",
                "former_boundary": {
                    "seed_through_date": resolved.history.dwd_date_end.isoformat(),
                    "first_daily_session": (
                        resolved.generations[0].candidate.market_date.isoformat()
                    ),
                    "overlap_rows": 0,
                },
                "continuity": continuity,
            },
            "authority": {
                "canonical_acceptance_only_after_seed": True,
                "legacy_seed_authority_disclosed": True,
                "row_or_lane_mixing": False,
                "consumer_pointer_changed": False,
            },
        }
        manifest["content_digest"] = _canonical_digest(_package_content(manifest))
        manifest["files"] = _manifest_files(staging)
        _seal(staging, output, manifest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_dwd_base_package(output, policy=policy)


def _build_dws_package(
    *,
    output: Path,
    dwd: LayerBasePackage,
    ledger: BaseInputLedger,
    resolved: _ResolvedInputs,
    policy: DwdDwsBasePolicy,
    code_revision: str,
    runtime: Mapping[str, Any],
) -> LayerBasePackage:
    output = output.resolve()
    if output.name != policy.dws_package_id:
        raise DwdDwsBaseError("DWS output must end with the policy package id")
    if output.exists():
        existing = verify_dws_base_package(output, policy=policy, dwd=dwd)
        if (
            existing.value.get("code_revision") != code_revision
            or existing.value.get("runtime") != dict(runtime)
            or existing.input_ledger_digest != ledger.digest
        ):
            raise DwdDwsBaseError("existing DWS Base differs from the requested build identity")
        return existing
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        ledger_target = staging / "lineage" / "input-ledger.json"
        ledger_target.parent.mkdir()
        shutil.copy2(ledger.path, ledger_target)
        summary = staging / "datasets" / "daily_market_summary" / "part-00000.parquet"
        quality = _derive_dws(
            dwd_root=dwd.root,
            dwd_manifest=dwd.value,
            output=summary,
            resolved=resolved,
        )
        part = _parquet_fact(
            package_root=staging,
            path=summary,
            business_key=("market_date",),
            date_column="market_date",
        )
        if part["duplicate_key_count"] != 0 or part["key_null_count"] != 0:
            raise DwdDwsBaseError("derived DWS has duplicate or null market-date keys")
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "kind": "dwd_dws_monthly_base_package",
            "manifest_id": f"{policy.dws_package_id}-manifest-v1",
            "package_id": policy.dws_package_id,
            "layer": "dws",
            "contract_id": policy.contract_id,
            "policy_sha256": policy.digest,
            "phase": policy.phase,
            "through_date": policy.through_date.isoformat(),
            "code_revision": code_revision,
            "runtime": dict(runtime),
            "transformation": {
                "contract": policy.projection_contract,
                "canonical_serialization": "sorted-json-no-attempt-time-v1",
                "deduplication": "unique-market-date-v1",
            },
            "input_ledger": {
                "path": "lineage/input-ledger.json",
                "sha256": _sha256(ledger_target),
                "digest": ledger.digest,
            },
            "upstream_ods_base": dict(policy.upstream_ods_base),
            "source_lineage": {
                "source_dws_seed_manifest_sha256": _sha256(
                    resolved.history.dws_root / "manifest.json"
                ),
                "absorbed_dws_manifest_sha256": [
                    _sha256(item.candidate.dws_root / "manifest.json")
                    for item in resolved.generations
                ],
            },
            "dwd_binding": {
                "package_id": dwd.package_id,
                "manifest_sha256": dwd.manifest_sha256,
                "content_digest": dwd.content_digest,
            },
            "projection": {
                "contract": policy.projection_contract,
                "source_equivalence": "exact_rows",
                "metric_columns": list(_DWS_METRICS),
            },
            "datasets": [_dataset("daily_market_summary", "service_layer_projection", [part])],
            "quality": {
                **quality,
                "business_key_policy": "unique_market_date_v1",
                "duplicate_key_count": 0,
                "key_null_count": 0,
            },
            "authority": {
                "derived_from_exact_dwd_package": True,
                "independent_normalized_truth": False,
                "consumer_pointer_changed": False,
            },
        }
        manifest["content_digest"] = _canonical_digest(_package_content(manifest))
        manifest["files"] = _manifest_files(staging)
        _seal(staging, output, manifest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_dws_base_package(output, policy=policy, dwd=dwd)


def build_dwd_dws_base_pair(
    *,
    history_bundle: Path,
    history_policy: HistoryBasePolicy,
    dual_lane_root: Path,
    publication_root: Path,
    ledger_path: Path,
    dwd_output: Path,
    dws_output: Path,
    policy: DwdDwsBasePolicy,
    code_revision: str,
) -> DwdDwsBasePair:
    if _GIT_OBJECT.fullmatch(code_revision) is None:
        raise DwdDwsBaseError("code_revision must be a full lowercase Git object id")
    ledger = load_base_input_ledger(ledger_path, policy=policy)
    resolved = _collect_inputs(
        history_bundle=history_bundle,
        history_policy=history_policy,
        dual_lane_root=dual_lane_root,
        publication_root=publication_root,
        policy=policy,
    )
    if dict(ledger.value) != dict(resolved.ledger_value):
        raise DwdDwsBaseError("frozen ledger differs from currently resolved immutable inputs")
    runtime = _runtime_identity()
    dwd = _build_dwd_package(
        output=dwd_output,
        ledger=ledger,
        resolved=resolved,
        policy=policy,
        code_revision=code_revision,
        runtime=runtime,
    )
    dws = _build_dws_package(
        output=dws_output,
        dwd=dwd,
        ledger=ledger,
        resolved=resolved,
        policy=policy,
        code_revision=code_revision,
        runtime=runtime,
    )
    return verify_dwd_dws_base_pair(dwd_root=dwd.root, dws_root=dws.root, policy=policy)


def _verify_layer_package(root: Path, *, layer: str, policy: DwdDwsBasePolicy) -> LayerBasePackage:
    root = root.resolve()
    value = _ready_manifest(root)
    expected_id = policy.dwd_package_id if layer == "dwd" else policy.dws_package_id
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "dwd_dws_monthly_base_package"
        or value.get("layer") != layer
        or value.get("package_id") != expected_id
        or root.name != expected_id
        or value.get("manifest_id") != f"{expected_id}-manifest-v1"
        or value.get("contract_id") != policy.contract_id
        or value.get("policy_sha256") != policy.digest
        or value.get("phase") != policy.phase
        or value.get("through_date") != policy.through_date.isoformat()
        or _GIT_OBJECT.fullmatch(str(value.get("code_revision", ""))) is None
        or value.get("upstream_ods_base") != dict(policy.upstream_ods_base)
    ):
        raise DwdDwsBaseError(f"unsupported or mismatched {layer.upper()} Base package")
    _verify_files(root, value.get("files"))
    ledger_value = value.get("input_ledger")
    if not isinstance(ledger_value, Mapping):
        raise DwdDwsBaseError("Base package has no input-ledger binding")
    ledger_path = _inside(root, _text(ledger_value, "path"), "input_ledger.path")
    if _sha256(ledger_path) != ledger_value.get("sha256"):
        raise DwdDwsBaseError("Base package input-ledger checksum mismatch")
    ledger = load_base_input_ledger(ledger_path, policy=policy)
    if ledger.digest != ledger_value.get("digest"):
        raise DwdDwsBaseError("Base package input-ledger content digest mismatch")
    datasets = value.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise DwdDwsBaseError("Base package has no declared datasets")
    seen_paths: set[str] = set()
    for dataset in datasets:
        if not isinstance(dataset, Mapping) or dataset.get("layout") != "manifest_members_v1":
            raise DwdDwsBaseError("Base package dataset contract is invalid")
        parts = dataset.get("parts")
        if not isinstance(parts, list) or not parts:
            raise DwdDwsBaseError("Base package dataset has no parts")
        for part in parts:
            if not isinstance(part, Mapping):
                raise DwdDwsBaseError("Base package part must be an object")
            part_path = _text(part, "path")
            if part_path in seen_paths:
                raise DwdDwsBaseError("Base package reuses one part in multiple datasets")
            seen_paths.add(part_path)
            actual = _parquet_fact(
                package_root=root,
                path=_inside(root, part_path, "datasets.parts.path"),
                business_key=tuple(part.get("business_key", ())),
                date_column=part.get("date_column"),
            )
            if actual != _fact_core(part):
                raise DwdDwsBaseError(f"Base package Parquet facts differ: {part_path}")
            if actual["duplicate_key_count"] != 0 or actual["key_null_count"] != 0:
                raise DwdDwsBaseError(f"Base package key gate failed: {part_path}")
    if _canonical_digest(_package_content(value)) != value.get("content_digest"):
        raise DwdDwsBaseError("Base package content digest mismatch")
    return LayerBasePackage(
        root=root,
        package_id=expected_id,
        layer=layer,
        manifest_sha256=_sha256(root / "manifest.json"),
        content_digest=_text(value, "content_digest"),
        through_date=policy.through_date,
        input_ledger_digest=ledger.digest,
        value=value,
    )


def verify_dwd_base_package(root: Path, *, policy: DwdDwsBasePolicy) -> LayerBasePackage:
    package = _verify_layer_package(root, layer="dwd", policy=policy)
    expected = {
        "historical_daily_market",
        "historical_daily_status",
        "historical_daily_universe",
        "historical_code_validity",
        "historical_st_status_pit",
        "daily_market",
        "daily_status_observation",
        "daily_code_validity",
    }
    actual = {
        item.get("dataset_id")
        for item in package.value.get("datasets", [])
        if isinstance(item, Mapping)
    }
    if actual != expected:
        raise DwdDwsBaseError("DWD Base dataset family is incomplete")
    dates = [item.get("date_start") for item in _dataset_parts(package.value, "daily_market")]
    if dates != [item.isoformat() for item in policy.open_sessions]:
        raise DwdDwsBaseError("DWD Base daily-market sessions differ from policy")
    status_dates = [
        item.get("date_start") for item in _dataset_parts(package.value, "daily_status_observation")
    ]
    if status_dates != dates or any(
        item.get("date_start") != item.get("date_end")
        for item in _dataset_parts(package.value, "daily_market")
        + _dataset_parts(package.value, "daily_status_observation")
    ):
        raise DwdDwsBaseError("DWD Base daily members are not exact session partitions")
    quality = package.value.get("quality")
    if not isinstance(quality, Mapping) or quality.get("continuity") != _dwd_continuity_quality(
        package.root, package.value
    ):
        raise DwdDwsBaseError("DWD Base continuity evidence differs from its members")
    return package


def verify_dws_base_package(
    root: Path, *, policy: DwdDwsBasePolicy, dwd: LayerBasePackage | None = None
) -> LayerBasePackage:
    package = _verify_layer_package(root, layer="dws", policy=policy)
    datasets = package.value.get("datasets")
    if not isinstance(datasets, list) or [item.get("dataset_id") for item in datasets] != [
        "daily_market_summary"
    ]:
        raise DwdDwsBaseError("DWS Base dataset family is invalid")
    parts = _dataset_parts(package.value, "daily_market_summary")
    if len(parts) != 1 or parts[0].get("date_end") != policy.through_date.isoformat():
        raise DwdDwsBaseError("DWS Base is not one complete through-date projection")
    binding = package.value.get("dwd_binding")
    if not isinstance(binding, Mapping):
        raise DwdDwsBaseError("DWS Base has no DWD package binding")
    if dwd is not None and dict(binding) != {
        "package_id": dwd.package_id,
        "manifest_sha256": dwd.manifest_sha256,
        "content_digest": dwd.content_digest,
    }:
        raise DwdDwsBaseError("DWS Base differs from the exact DWD package")
    return package


def _projection_difference_rows(dwd: LayerBasePackage, dws: LayerBasePackage) -> int:
    projection = _dws_projection_query(dwd.root, dwd.value)
    summary_part = _dataset_parts(dws.value, "daily_market_summary")[0]
    summary = _inside(dws.root, _text(summary_part, "path"), "DWS summary path")
    expected = f"SELECT * FROM read_parquet({_sql_string(str(summary))})"
    connection = _duckdb_connection()
    try:
        connection.execute(f"CREATE TEMP TABLE projected_dws AS {projection}")
        connection.execute(f"CREATE TEMP TABLE expected_dws AS {expected}")
        projected_rows = connection.execute(
            "SELECT * FROM projected_dws ORDER BY market_date"
        ).fetchall()
        expected_rows = connection.execute(
            "SELECT * FROM expected_dws ORDER BY market_date"
        ).fetchall()
    finally:
        connection.close()
    projected_counts = Counter(projected_rows)
    expected_counts = Counter(expected_rows)
    return sum((projected_counts - expected_counts).values()) + sum(
        (expected_counts - projected_counts).values()
    )


def verify_dwd_dws_base_pair(
    *, dwd_root: Path, dws_root: Path, policy: DwdDwsBasePolicy
) -> DwdDwsBasePair:
    dwd = verify_dwd_base_package(dwd_root, policy=policy)
    dws = verify_dws_base_package(dws_root, policy=policy, dwd=dwd)
    if dwd.input_ledger_digest != dws.input_ledger_digest:
        raise DwdDwsBaseError("DWD and DWS Base packages bind different input ledgers")
    if _projection_difference_rows(dwd, dws) != 0:
        raise DwdDwsBaseError("DWS Base is not reproducible from its exact DWD package")
    return DwdDwsBasePair(dwd=dwd, dws=dws)


def _install_one(
    *,
    ingress: LayerBasePackage,
    base_root: Path,
    policy: DwdDwsBasePolicy,
    owner_uid: int | None,
    group_gid: int | None,
) -> LayerBasePackage:
    base_root = base_root.resolve()
    base_root.mkdir(parents=True, exist_ok=True)
    if owner_uid is not None and group_gid is not None:
        base_root.chmod(0o750)
        os.chown(base_root, owner_uid, group_gid)
    output = base_root / ingress.package_id
    if output.exists():
        installed = (
            verify_dwd_base_package(output, policy=policy)
            if ingress.layer == "dwd"
            else verify_dws_base_package(output, policy=policy)
        )
        if installed.manifest_sha256 != ingress.manifest_sha256:
            raise DwdDwsBaseError("installed Base package differs from ingress")
        if owner_uid is not None and group_gid is not None:
            _apply_permissions(output, owner_uid=owner_uid, group_gid=group_gid)
        return installed
    staging_parent = Path(tempfile.mkdtemp(prefix=f".{ingress.package_id}.", dir=base_root))
    staging = staging_parent / ingress.package_id
    try:
        shutil.copytree(ingress.root, staging)
        installed = (
            verify_dwd_base_package(staging, policy=policy)
            if ingress.layer == "dwd"
            else verify_dws_base_package(staging, policy=policy)
        )
        if installed.manifest_sha256 != ingress.manifest_sha256:
            raise DwdDwsBaseError("copied Base package differs from ingress")
        if owner_uid is not None and group_gid is not None:
            _apply_permissions(staging, owner_uid=owner_uid, group_gid=group_gid)
        os.replace(staging, output)
        staging_parent.rmdir()
    except BaseException:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise
    return (
        verify_dwd_base_package(output, policy=policy)
        if ingress.layer == "dwd"
        else verify_dws_base_package(output, policy=policy)
    )


def install_dwd_dws_base_pair(
    *,
    ingress_dwd: Path,
    ingress_dws: Path,
    dwd_base_root: Path,
    dws_base_root: Path,
    policy: DwdDwsBasePolicy,
    owner_uid: int | None = None,
    group_gid: int | None = None,
) -> DwdDwsBasePair:
    if (owner_uid is None) != (group_gid is None):
        raise DwdDwsBaseError("Base package owner and group must be provided together")
    ingress = verify_dwd_dws_base_pair(dwd_root=ingress_dwd, dws_root=ingress_dws, policy=policy)
    dwd = _install_one(
        ingress=ingress.dwd,
        base_root=dwd_base_root,
        policy=policy,
        owner_uid=owner_uid,
        group_gid=group_gid,
    )
    dws = _install_one(
        ingress=ingress.dws,
        base_root=dws_base_root,
        policy=policy,
        owner_uid=owner_uid,
        group_gid=group_gid,
    )
    return verify_dwd_dws_base_pair(dwd_root=dwd.root, dws_root=dws.root, policy=policy)


def _base_descriptor(
    *, package: LayerBasePackage, peer: LayerBasePackage, pair_digest: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "dwd_dws_base_current",
        "layer": package.layer,
        "package_id": package.package_id,
        "package_path": package.package_id,
        "manifest_sha256": package.manifest_sha256,
        "content_digest": package.content_digest,
        "through_date": package.through_date.isoformat(),
        "input_ledger_digest": package.input_ledger_digest,
        "peer_package_id": peer.package_id,
        "peer_manifest_sha256": peer.manifest_sha256,
        "pair_digest": pair_digest,
    }


def _pointer_matches(path: Path, expected: Mapping[str, Any]) -> bool:
    return path.is_file() and _object(path) == dict(expected)


def _check_pointer_precondition(path: Path, expected_sha256: str | None) -> None:
    if expected_sha256 is None:
        if path.exists():
            raise DwdDwsBaseError(f"BASE_CURRENT precondition expected absence: {path}")
        return
    if _SHA256.fullmatch(expected_sha256) is None:
        raise DwdDwsBaseError("BASE_CURRENT precondition must be a SHA-256 digest or absent")
    if not path.is_file() or _sha256(path) != expected_sha256:
        raise DwdDwsBaseError(f"BASE_CURRENT SHA precondition failed: {path}")


def advance_dwd_dws_base_current(
    *,
    dwd_base_root: Path,
    dws_base_root: Path,
    policy: DwdDwsBasePolicy,
    expected_dwd_current_sha256: str | None,
    expected_dws_current_sha256: str | None,
) -> DwdDwsBasePair:
    pair = verify_dwd_dws_base_pair(
        dwd_root=dwd_base_root.resolve() / policy.dwd_package_id,
        dws_root=dws_base_root.resolve() / policy.dws_package_id,
        policy=policy,
    )
    pair_digest = _pair_digest(pair)
    dwd_value = _base_descriptor(package=pair.dwd, peer=pair.dws, pair_digest=pair_digest)
    dws_value = _base_descriptor(package=pair.dws, peer=pair.dwd, pair_digest=pair_digest)
    dwd_pointer = dwd_base_root.resolve() / "BASE_CURRENT.json"
    dws_pointer = dws_base_root.resolve() / "BASE_CURRENT.json"
    if _pointer_matches(dwd_pointer, dwd_value) and _pointer_matches(dws_pointer, dws_value):
        return pair
    _check_pointer_precondition(dwd_pointer, expected_dwd_current_sha256)
    _check_pointer_precondition(dws_pointer, expected_dws_current_sha256)
    _atomic_json(dwd_pointer, dwd_value)
    _atomic_json(dws_pointer, dws_value)
    load_dwd_dws_base_current(
        dwd_pointer=dwd_pointer,
        dws_pointer=dws_pointer,
        policy=policy,
    )
    return pair


def load_dwd_dws_base_current(
    *, dwd_pointer: Path, dws_pointer: Path, policy: DwdDwsBasePolicy
) -> DwdDwsBasePair:
    dwd_pointer = dwd_pointer.resolve()
    dws_pointer = dws_pointer.resolve()
    dwd_value = _object(dwd_pointer)
    dws_value = _object(dws_pointer)
    for value, layer in ((dwd_value, "dwd"), (dws_value, "dws")):
        if (
            value.get("schema_version") != 1
            or value.get("kind") != "dwd_dws_base_current"
            or value.get("layer") != layer
        ):
            raise DwdDwsBaseError("unsupported BASE_CURRENT descriptor")
    pair = verify_dwd_dws_base_pair(
        dwd_root=_inside(dwd_pointer.parent, _text(dwd_value, "package_path"), "package_path"),
        dws_root=_inside(dws_pointer.parent, _text(dws_value, "package_path"), "package_path"),
        policy=policy,
    )
    pair_digest = _pair_digest(pair)
    if (
        dwd_value.get("pair_digest") != pair_digest
        or dws_value.get("pair_digest") != pair_digest
        or dwd_value.get("peer_manifest_sha256") != pair.dws.manifest_sha256
        or dws_value.get("peer_manifest_sha256") != pair.dwd.manifest_sha256
    ):
        raise DwdDwsBaseError("DWD/DWS BASE_CURRENT pair binding is inconsistent")
    expected_dwd = _base_descriptor(package=pair.dwd, peer=pair.dws, pair_digest=pair_digest)
    expected_dws = _base_descriptor(package=pair.dws, peer=pair.dwd, pair_digest=pair_digest)
    if dwd_value != expected_dwd or dws_value != expected_dws:
        raise DwdDwsBaseError("BASE_CURRENT descriptor differs from its package pair")
    return pair


def _relative_inside_root(root: Path, path: Path, field: str) -> str:
    root = root.resolve()
    path = path.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise DwdDwsBaseError(f"{field} is outside the platform root") from exc
    return _safe_relative(relative.as_posix(), field).as_posix()


def _base_tail_identity(
    pair: DwdDwsBasePair, *, policy: DwdDwsBasePolicy
) -> dict[str, str]:
    ledger_value = pair.dwd.value.get("input_ledger")
    if not isinstance(ledger_value, Mapping):
        raise DwdDwsBaseError("DWD Base has no input-ledger binding")
    ledger = load_base_input_ledger(
        _inside(pair.dwd.root, _text(ledger_value, "path"), "input_ledger.path"),
        policy=policy,
    )
    generations = ledger.value.get("generations")
    if not isinstance(generations, list) or not generations:
        raise DwdDwsBaseError("Base input ledger has no absorbed generation")
    tail = generations[-1]
    dwd = tail.get("dwd") if isinstance(tail, Mapping) else None
    if (
        not isinstance(dwd, Mapping)
        or tail.get("market_date") != pair.dwd.through_date.isoformat()
    ):
        raise DwdDwsBaseError("Base input ledger tail differs from package through-date")
    return {
        "revision": _text(dwd, "revision"),
        "manifest_sha256": _digest(dwd, "manifest_sha256"),
    }


def _catalog_candidate_links(
    *,
    platform_root: Path,
    pair: DwdDwsBasePair,
    candidates: tuple[LaneCandidate, ...],
    policy: DwdDwsBasePolicy,
) -> tuple[dict[str, Any], ...]:
    dates = tuple(candidate.market_date for candidate in candidates)
    if dates != tuple(sorted(set(dates))) or any(
        market_date <= pair.dwd.through_date for market_date in dates
    ):
        raise DwdDwsBaseError("Base lane-catalog candidates are not unique post-Base dates")
    if not candidates:
        return ()
    tail = _base_tail_identity(pair, policy=policy)
    storage_root = platform_root.resolve() / "dual-lane"
    links: list[dict[str, Any]] = []
    previous: LaneCandidate | None = None
    for candidate in candidates:
        candidate_manifest = _object(candidate.root / "manifest.json")
        epoch_id = _text(candidate_manifest, "epoch_id")
        if _SAFE_ID.fullmatch(epoch_id) is None:
            raise DwdDwsBaseError("candidate epoch id is unsafe")
        epoch = verify_epoch_input(
            storage_root / "epochs" / candidate.market_date.isoformat() / epoch_id
        )
        if (
            epoch.epoch_id != epoch_id
            or epoch.market_date != candidate.market_date
            or epoch.manifest_sha256 != candidate.epoch_manifest_sha256
            or epoch.contract_id != candidate.contract_id
            or epoch.policy_sha256 != candidate.policy_sha256
        ):
            raise DwdDwsBaseError("Base lane-catalog candidate differs from its epoch")
        dwd_manifest = _object(candidate.dwd_root / "manifest.json")
        lineage = dwd_manifest.get("lineage")
        parent = lineage.get("parent") if isinstance(lineage, Mapping) else None
        if not isinstance(parent, Mapping):
            raise DwdDwsBaseError("Base lane-catalog candidate DWD has no parent")
        expected = {
            "kind": epoch.source_kind,
            "manifest_sha256": epoch.source_manifest_sha256,
            "revision": epoch.source_revision,
        }
        if epoch.source_kind != "daily_partition" or epoch.source_path is None:
            raise DwdDwsBaseError("post-Base candidate epoch has no canonical daily parent")
        parent_manifest = _object(epoch.source_path / "manifest.json")
        parent_date = _parse_date(parent_manifest.get("market_date"), "epoch parent date")
        if previous is None:
            if (
                epoch.source_revision != tail["revision"]
                or epoch.source_manifest_sha256 != tail["manifest_sha256"]
                or parent_date != pair.dwd.through_date
            ):
                raise DwdDwsBaseError("first post-Base candidate differs from the absorbed tail")
        elif (
            parent_date != previous.market_date
            or epoch.source_revision != previous.dwd_root.name
            or epoch.source_manifest_sha256
            != _sha256(previous.dwd_root / "manifest.json")
        ):
            raise DwdDwsBaseError(
                "post-Base candidate canonical parent identity is discontinuous"
            )
        if dict(parent) != expected:
            raise DwdDwsBaseError("post-Base candidate DWD differs from its epoch parent")
        links.append(
            {
                "epoch_id": epoch.epoch_id,
                "epoch_manifest_sha256": epoch.manifest_sha256,
                "canonical_parent": expected,
            }
        )
        previous = candidate
    return tuple(links)


def _base_catalog_content(
    *,
    platform_root: Path,
    lane_id: str,
    pair: DwdDwsBasePair,
    candidates: tuple[LaneCandidate, ...],
    policy: DwdDwsBasePolicy,
) -> dict[str, Any]:
    if lane_id not in {"cn01", "hermes"} or any(
        candidate.lane_id != lane_id for candidate in candidates
    ):
        raise DwdDwsBaseError("Base lane catalog contains the wrong lane")
    links = _catalog_candidate_links(
        platform_root=platform_root,
        pair=pair,
        candidates=candidates,
        policy=policy,
    )
    return {
        "lane_id": lane_id,
        "base": {
            "through_date": pair.dwd.through_date.isoformat(),
            "input_ledger_digest": pair.dwd.input_ledger_digest,
            "pair_digest": _pair_digest(pair),
            "dwd": {
                "package_id": pair.dwd.package_id,
                "path": _relative_inside_root(platform_root, pair.dwd.root, "base.dwd.path"),
                "manifest_sha256": pair.dwd.manifest_sha256,
                "content_digest": pair.dwd.content_digest,
            },
            "dws": {
                "package_id": pair.dws.package_id,
                "path": _relative_inside_root(platform_root, pair.dws.root, "base.dws.path"),
                "manifest_sha256": pair.dws.manifest_sha256,
                "content_digest": pair.dws.content_digest,
            },
        },
        "daily_partitions": [
            {
                "market_date": candidate.market_date.isoformat(),
                "candidate_id": candidate.candidate_id,
                "path": _relative_inside_root(
                    platform_root, candidate.root, "daily_partitions.path"
                ),
                "manifest_sha256": candidate.manifest_sha256,
                "dwd_manifest_sha256": _sha256(candidate.dwd_root / "manifest.json"),
                "dws_manifest_sha256": _sha256(candidate.dws_root / "manifest.json"),
                **link,
            }
            for candidate, link in zip(candidates, links, strict=True)
        ],
        "boundaries": {
            "base_through_date": pair.dwd.through_date.isoformat(),
            "through_date": (
                candidates[-1].market_date if candidates else pair.dwd.through_date
            ).isoformat(),
        },
    }


def _post_base_candidate_chain(
    *, candidate_root: Path, lane_id: str, through_date: date
) -> tuple[LaneCandidate, ...]:
    if lane_id not in {"cn01", "hermes"}:
        raise DwdDwsBaseError("lane_id must be cn01 or hermes")
    candidates: list[LaneCandidate] = []
    for date_root in sorted(candidate_root.resolve().iterdir()):
        if not date_root.is_dir() or re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_root.name) is None:
            continue
        pointer = date_root / "CURRENT.json"
        if not pointer.is_file():
            continue
        candidate = load_candidate_current(pointer)
        if candidate.lane_id != lane_id or candidate.market_date <= through_date:
            continue
        candidates.append(candidate)
    return tuple(candidates)


def build_dwd_dws_base_lane_catalog(
    *,
    platform_root: Path,
    dwd_pointer: Path,
    dws_pointer: Path,
    candidate_root: Path,
    catalog_root: Path,
    lane_id: str,
    policy: DwdDwsBasePolicy,
    owner_uid: int | None = None,
    group_gid: int | None = None,
) -> BaseLaneHistoryCatalog:
    if (owner_uid is None) != (group_gid is None):
        raise DwdDwsBaseError("catalog owner and group must be provided together")
    platform_root = platform_root.resolve()
    pair = load_dwd_dws_base_current(
        dwd_pointer=dwd_pointer,
        dws_pointer=dws_pointer,
        policy=policy,
    )
    candidates = _post_base_candidate_chain(
        candidate_root=candidate_root,
        lane_id=lane_id,
        through_date=pair.dwd.through_date,
    )
    content = _base_catalog_content(
        platform_root=platform_root,
        lane_id=lane_id,
        pair=pair,
        candidates=candidates,
        policy=policy,
    )
    content_digest = _canonical_digest(content)
    through_date = candidates[-1].market_date if candidates else pair.dwd.through_date
    catalog_id = (
        f"base-lane-history-{lane_id}-{through_date.strftime('%Y%m%d')}-"
        f"{content_digest.removeprefix('sha256:')[:16]}"
    )
    lane_root = catalog_root.resolve() / lane_id
    output = lane_root / catalog_id
    if output.exists():
        catalog = verify_dwd_dws_base_lane_catalog(
            output,
            platform_root=platform_root,
            policy=policy,
        )
        if catalog.content_digest != content_digest:
            raise DwdDwsBaseError("existing Base lane catalog differs from request")
    else:
        lane_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{catalog_id}.", dir=lane_root))
        try:
            manifest = {
                "schema_version": 1,
                "kind": "dwd_dws_base_lane_history_catalog",
                "catalog_id": catalog_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "contract_id": policy.contract_id,
                "policy_sha256": policy.digest,
                "content_digest": content_digest,
                "physical_merge": False,
                **content,
                "files": [],
            }
            _seal(staging, output, manifest)
            if owner_uid is not None and group_gid is not None:
                _apply_permissions(output, owner_uid=owner_uid, group_gid=group_gid)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        catalog = verify_dwd_dws_base_lane_catalog(
            output,
            platform_root=platform_root,
            policy=policy,
        )
    current = lane_root / "CURRENT.json"
    relative = os.path.relpath(catalog.root, start=current.parent)
    _atomic_json(
        current,
        {
            "schema_version": 1,
            "kind": "dwd_dws_base_lane_history_current",
            "lane_id": lane_id,
            "catalog_id": catalog.catalog_id,
            "catalog_path": Path(relative).as_posix(),
            "manifest_sha256": catalog.manifest_sha256,
            "content_digest": catalog.content_digest,
            "through_date": catalog.through_date.isoformat(),
            "base_pair_digest": _pair_digest(catalog.pair),
        },
    )
    if owner_uid is not None and group_gid is not None:
        os.chown(current, owner_uid, group_gid)
    return catalog


def verify_dwd_dws_base_lane_catalog(
    root: Path, *, platform_root: Path, policy: DwdDwsBasePolicy
) -> BaseLaneHistoryCatalog:
    root = root.resolve()
    platform_root = platform_root.resolve()
    manifest = _ready_manifest(root)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "dwd_dws_base_lane_history_catalog"
        or manifest.get("physical_merge") is not False
        or manifest.get("contract_id") != policy.contract_id
        or manifest.get("policy_sha256") != policy.digest
    ):
        raise DwdDwsBaseError("unsupported DWD/DWS Base lane catalog")
    _verify_files(root, manifest.get("files"))
    catalog_id = _text(manifest, "catalog_id")
    lane_id = _text(manifest, "lane_id")
    if root.name != catalog_id or lane_id not in {"cn01", "hermes"}:
        raise DwdDwsBaseError("DWD/DWS Base lane catalog identity is invalid")
    base_value = manifest.get("base")
    if not isinstance(base_value, Mapping):
        raise DwdDwsBaseError("DWD/DWS Base lane catalog has no Base pair")
    dwd_value = base_value.get("dwd")
    dws_value = base_value.get("dws")
    if not isinstance(dwd_value, Mapping) or not isinstance(dws_value, Mapping):
        raise DwdDwsBaseError("DWD/DWS Base lane catalog pair is incomplete")
    pair = verify_dwd_dws_base_pair(
        dwd_root=_inside(platform_root, _text(dwd_value, "path"), "base.dwd.path"),
        dws_root=_inside(platform_root, _text(dws_value, "path"), "base.dws.path"),
        policy=policy,
    )
    expected_base = _base_catalog_content(
        platform_root=platform_root,
        lane_id=lane_id,
        pair=pair,
        candidates=(),
        policy=policy,
    )["base"]
    if dict(base_value) != expected_base:
        raise DwdDwsBaseError("DWD/DWS Base lane catalog pair identity mismatch")
    entries = manifest.get("daily_partitions")
    if not isinstance(entries, list):
        raise DwdDwsBaseError("DWD/DWS Base lane catalog daily partitions must be an array")
    candidates: list[LaneCandidate] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise DwdDwsBaseError("DWD/DWS Base lane catalog entry must be an object")
        candidate = verify_lane_candidate(
            _inside(platform_root, _text(entry, "path"), "daily_partitions.path")
        )
        if (
            candidate.lane_id != lane_id
            or candidate.candidate_id != entry.get("candidate_id")
            or candidate.market_date.isoformat() != entry.get("market_date")
            or candidate.manifest_sha256 != entry.get("manifest_sha256")
            or _sha256(candidate.dwd_root / "manifest.json") != entry.get("dwd_manifest_sha256")
            or _sha256(candidate.dws_root / "manifest.json") != entry.get("dws_manifest_sha256")
        ):
            raise DwdDwsBaseError("DWD/DWS Base lane catalog candidate identity mismatch")
        candidates.append(candidate)
    chain = tuple(candidates)
    content = _base_catalog_content(
        platform_root=platform_root,
        lane_id=lane_id,
        pair=pair,
        candidates=chain,
        policy=policy,
    )
    if _canonical_digest(content) != manifest.get("content_digest"):
        raise DwdDwsBaseError("DWD/DWS Base lane catalog content digest mismatch")
    through_date = chain[-1].market_date if chain else pair.dwd.through_date
    if manifest.get("boundaries", {}).get("through_date") != through_date.isoformat():
        raise DwdDwsBaseError("DWD/DWS Base lane catalog through-date mismatch")
    return BaseLaneHistoryCatalog(
        root=root,
        catalog_id=catalog_id,
        lane_id=lane_id,
        manifest_sha256=_sha256(root / "manifest.json"),
        content_digest=_text(manifest, "content_digest"),
        pair=pair,
        candidates=chain,
        through_date=through_date,
    )


def load_dwd_dws_base_lane_catalog_current(
    pointer: Path,
    *,
    platform_root: Path,
    policy: DwdDwsBasePolicy,
    dwd_pointer: Path | None = None,
    dws_pointer: Path | None = None,
) -> BaseLaneHistoryCatalog:
    pointer = pointer.resolve()
    value = _object(pointer)
    if value.get("kind") != "dwd_dws_base_lane_history_current":
        raise DwdDwsBaseError("lane catalog CURRENT is not Base-aware")
    path = _inside(pointer.parent, _text(value, "catalog_path"), "catalog_path")
    catalog = verify_dwd_dws_base_lane_catalog(
        path,
        platform_root=platform_root,
        policy=policy,
    )
    expected = {
        "schema_version": 1,
        "kind": "dwd_dws_base_lane_history_current",
        "lane_id": catalog.lane_id,
        "catalog_id": catalog.catalog_id,
        "catalog_path": _safe_relative(
            os.path.relpath(catalog.root, start=pointer.parent), "catalog_path"
        ).as_posix(),
        "manifest_sha256": catalog.manifest_sha256,
        "content_digest": catalog.content_digest,
        "through_date": catalog.through_date.isoformat(),
        "base_pair_digest": _pair_digest(catalog.pair),
    }
    if value != expected:
        raise DwdDwsBaseError("Base-aware lane catalog CURRENT differs from its catalog")
    if (dwd_pointer is None) != (dws_pointer is None):
        raise DwdDwsBaseError("both active Base descriptors must be provided together")
    if dwd_pointer is not None and dws_pointer is not None:
        active = load_dwd_dws_base_current(
            dwd_pointer=dwd_pointer,
            dws_pointer=dws_pointer,
            policy=policy,
        )
        if _pair_digest(active) != _pair_digest(catalog.pair):
            raise DwdDwsBaseError("lane catalog does not bind the active DWD/DWS Base pair")
    return catalog
