"""Pre-result semantic-equivalence probe for P3-STRAT-003-B historical reconstruction.

This probe never produces candidate scores, target labels, strategy returns, or economic results.
It tests whether the exact daily payload consumed by the frozen replay/candidate path can be
reconstructed from immutable ODS without pretending that a reconstructed package is the original
historical DWD package.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)
from quanthus.expert_strategy import p3_strat_003_input_qualification as legacy
from quanthus.expert_strategy.p3_strat_003 import REQUEST_DIGEST
from quanthus.forward_validation.activation import load_prospective_runtime_config
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

REQUEST_SCHEMA = "quanthus.p3-strat-003-semantic-equivalence-probe.v1"
ARTIFACT_SCHEMA = "quanthus.p3-strat-003-semantic-equivalence-artifact.v1"
SUMMARY_SCHEMA = "quanthus.p3-strat-003-semantic-equivalence-summary.v1"
_CONSUMER_TABLES = (
    "dwd_security_bar",
    "dwd_tradability_state",
    "dwd_trading_calendar",
)
_PACKAGE_ID = re.compile(r"dwd-daily-([0-9]{8})-([0-9a-f]{12})-([0-9a-f]{7})")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_unreadable",
            f"{label} is unreadable",
        ) from exc
    if not isinstance(value, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            f"{label} must be an object",
        )
    return value


def _safe_relative(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith("/"):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            f"{label} path must be relative",
        )
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            f"{label} path escapes project root",
        )
    return path


@dataclass(frozen=True)
class _SentinelSpec:
    sentinel_id: str
    start: date
    end: date
    required_package_count: int


@dataclass(frozen=True)
class _BoundarySpec:
    market_date: date
    historical_builder_revision: str
    historical_builder_blob: str
    current_builder_blob: str
    only_builder_change_after_historical_revision: str
    compatibility_class: str
    artifact_lane: str
    artifact_name: str
    artifact_row_count: int


@dataclass(frozen=True)
class _ProbeConfig:
    raw: dict[str, Any]
    project: Path
    runtime_path: Path
    legacy_transport_path: Path
    sentinels: tuple[_SentinelSpec, ...]
    boundary: _BoundarySpec


@dataclass(frozen=True)
class _OriginalPackage:
    root: Path
    market_date: date
    package_id: str
    manifest_sha256: str
    prior_market_date: date | None
    prior_package_id: str | None
    prior_manifest_sha256: str | None


def _load_config(path: Path) -> _ProbeConfig:
    path = path.resolve()
    raw = _object(path, "P3-STRAT-003 semantic-equivalence probe")
    if (
        raw.get("schema_version") != REQUEST_SCHEMA
        or raw.get("work_item") != "P3-STRAT-003-B"
        or raw.get("state") != "FROZEN_PRE_RESULT_INPUT_ONLY"
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            "probe schema or state differs",
        )
    project = find_project_root(path)
    request = raw.get("request")
    runtime = raw.get("source_runtime")
    transport = raw.get("legacy_transport_contract")
    implementation = raw.get("runtime_implementation")
    consumer = raw.get("consumer_payload")
    boundaries = raw.get("boundaries")
    if not all(
        isinstance(value, dict)
        for value in (request, runtime, transport, implementation, consumer, boundaries)
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            "probe bindings are incomplete",
        )

    request_path = _safe_relative(project, request.get("path"), "request")
    if request.get("semantic_digest") != REQUEST_DIGEST or request.get("file_sha256") != _sha256(
        request_path
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_drift",
            "frozen A request differs",
        )

    runtime_path = _safe_relative(project, runtime.get("path"), "source_runtime")
    if (
        runtime.get("file_sha256") != _sha256(runtime_path)
        or runtime.get("p6_authority_inherited") is not False
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_drift",
            "runtime binding differs or inherits P6 authority",
        )

    legacy_transport_path = _safe_relative(
        project,
        transport.get("path"),
        "legacy_transport_contract",
    )
    if (
        transport.get("file_sha256") != _sha256(legacy_transport_path)
        or transport.get("usage") != "transport_and_resource_limits_only"
        or transport.get("exact_manifest_reconstruction_semantics_inherited") is not False
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_drift",
            "legacy transport-only binding differs",
        )

    expected_implementation = {
        "daily_domain": "src/quanthus/pipelines/securities_warehouse/daily_domain.py",
        "daily_ods_catalog": "src/quanthus/pipelines/securities_warehouse/daily_ods_catalog.py",
    }
    if set(implementation) != set(expected_implementation):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            "runtime implementation inventory differs",
        )
    for label, relative in expected_implementation.items():
        binding = implementation.get(label)
        if not isinstance(binding, dict) or binding.get("path") != relative:
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_invalid",
                f"{label} implementation binding differs",
            )
        source = _safe_relative(project, relative, f"runtime_implementation.{label}")
        if binding.get("file_sha256") != _sha256(source):
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_drift",
                f"{label} implementation bytes differ",
            )

    if (
        tuple(consumer.get("tables") or ()) != _CONSUMER_TABLES
        or consumer.get("equality") != "exact_declared_payload_file_sha256_size_and_row_count"
        or consumer.get("manifest_byte_equality_required") is not False
        or consumer.get("audit_json_equality_required") is not False
        or consumer.get("package_id_equality_required") is not False
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            "consumer semantic-equivalence boundary differs",
        )

    if boundaries != {
        "strategy_or_economic_results": False,
        "target_outcome_labels": False,
        "candidate_scoring": False,
        "mutate_daily_current": False,
        "mutate_p6": False,
        "job_local_output_only": True,
    }:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            "probe authority boundary differs",
        )

    sentinel_specs: list[_SentinelSpec] = []
    raw_sentinels = raw.get("sentinels")
    if not isinstance(raw_sentinels, list) or len(raw_sentinels) != 2:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            "exactly two overlap sentinels are required",
        )
    for value in raw_sentinels:
        if not isinstance(value, dict) or value.get("selection") != (
            "latest_verified_original_prior_child_pair"
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_invalid",
                "sentinel selection rule differs",
            )
        try:
            spec = _SentinelSpec(
                sentinel_id=str(value["sentinel_id"]),
                start=date.fromisoformat(str(value["date_start"])),
                end=date.fromisoformat(str(value["date_end"])),
                required_package_count=int(value["required_package_count"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_invalid",
                "sentinel fields are invalid",
            ) from exc
        if spec.required_package_count != 2 or spec.start > spec.end or not spec.sentinel_id:
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_invalid",
                "sentinel boundary differs",
            )
        sentinel_specs.append(spec)

    boundary = raw.get("missing_boundary")
    if not isinstance(boundary, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            "missing-boundary proof is absent",
        )
    artifact = boundary.get("required_ods_artifact")
    if not isinstance(artifact, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            "missing-boundary ODS proof is absent",
        )
    try:
        boundary_spec = _BoundarySpec(
            market_date=date.fromisoformat(str(boundary["market_date"])),
            historical_builder_revision=str(boundary["historical_builder_revision"]),
            historical_builder_blob=str(boundary["historical_builder_blob"]),
            current_builder_blob=str(boundary["current_builder_blob"]),
            only_builder_change_after_historical_revision=str(
                boundary["only_builder_change_after_historical_revision"]
            ),
            compatibility_class=str(boundary["compatibility_class"]),
            artifact_lane=str(artifact["lane"]),
            artifact_name=str(artifact["name"]),
            artifact_row_count=int(artifact["row_count"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            "missing-boundary fields are invalid",
        ) from exc
    if (
        boundary_spec.market_date != date(2026, 8, 27)
        or re.fullmatch(r"[0-9a-f]{40}", boundary_spec.historical_builder_revision) is None
        or re.fullmatch(
            r"[0-9a-f]{40}", boundary_spec.only_builder_change_after_historical_revision
        )
        is None
        or re.fullmatch(r"[0-9a-f]{40}", boundary_spec.historical_builder_blob) is None
        or re.fullmatch(r"[0-9a-f]{40}", boundary_spec.current_builder_blob) is None
        or boundary_spec.compatibility_class
        != "audit_only_financial_disclosure_accounting_when_empty"
        or boundary.get("source_identity_rule") != "selected_cn01_release_self_verified"
        or boundary.get("cross_source_physical_sha256_required") is not False
        or set(artifact) != {"lane", "name", "row_count"}
        or boundary_spec.artifact_lane != "calendar_daily_events"
        or boundary_spec.artifact_name != "financial_disclosures_delta"
        or boundary_spec.artifact_row_count != 0
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_invalid",
            "missing-boundary compatibility contract differs",
        )

    return _ProbeConfig(
        raw=raw,
        project=project,
        runtime_path=runtime_path,
        legacy_transport_path=legacy_transport_path,
        sentinels=tuple(sentinel_specs),
        boundary=boundary_spec,
    )


def _verified_original_packages_between(
    daily_root: Path,
    *,
    start: date,
    end: date,
) -> tuple[_OriginalPackage, ...]:
    packages_root = daily_root.resolve() / "packages"
    by_date: dict[date, list[_OriginalPackage]] = defaultdict(list)
    if not packages_root.is_dir():
        return ()
    for date_root in sorted(packages_root.iterdir()):
        if not date_root.is_dir():
            continue
        try:
            market_date = date.fromisoformat(date_root.name)
        except ValueError:
            continue
        if market_date < start or market_date > end:
            continue
        for package_root in sorted(path for path in date_root.iterdir() if path.is_dir()):
            try:
                verified = verify_daily_domain_increment(package_root)
            except DailyDomainBuildError:
                continue
            if verified.market_date != market_date:
                continue
            manifest = _object(package_root / "manifest.json", "original daily DWD manifest")
            prior = manifest.get("prior")
            prior_market_date: date | None = None
            prior_package_id: str | None = None
            prior_manifest_sha256: str | None = None
            if prior is not None:
                if not isinstance(prior, dict):
                    continue
                try:
                    prior_market_date = date.fromisoformat(str(prior["market_date"]))
                    prior_package_id = str(prior["package_id"])
                    prior_manifest_sha256 = str(prior["manifest_sha256"])
                except (KeyError, ValueError) as exc:
                    raise ExpertEvaluationError(
                        "p3_strat_003_semantic_probe_package_invalid",
                        verified.package_id,
                    ) from exc
                if (
                    prior_market_date >= market_date
                    or not prior_package_id
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", prior_manifest_sha256) is None
                ):
                    continue
            by_date[market_date].append(
                _OriginalPackage(
                    root=package_root.resolve(),
                    market_date=market_date,
                    package_id=verified.package_id,
                    manifest_sha256=verified.manifest_sha256,
                    prior_market_date=prior_market_date,
                    prior_package_id=prior_package_id,
                    prior_manifest_sha256=prior_manifest_sha256,
                )
            )
    unique = [
        values[0]
        for market_date, values in sorted(by_date.items())
        if len(values) == 1 and values[0].market_date == market_date
    ]
    return tuple(unique)


def _select_sentinel_pair(
    daily_root: Path,
    spec: _SentinelSpec,
) -> tuple[_OriginalPackage, _OriginalPackage]:
    candidates = _verified_original_packages_between(
        daily_root,
        start=spec.start,
        end=spec.end,
    )
    if len(candidates) < spec.required_package_count:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_sentinel_missing",
            f"{spec.sentinel_id}:{len(candidates)}",
        )
    by_identity = {
        (item.market_date, item.package_id, item.manifest_sha256): item for item in candidates
    }
    pairs: list[tuple[_OriginalPackage, _OriginalPackage]] = []
    for target in candidates:
        if (
            target.prior_market_date is None
            or target.prior_package_id is None
            or target.prior_manifest_sha256 is None
        ):
            continue
        anchor = by_identity.get(
            (
                target.prior_market_date,
                target.prior_package_id,
                target.prior_manifest_sha256,
            )
        )
        if anchor is not None:
            pairs.append((anchor, target))
    if not pairs:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_sentinel_missing",
            f"{spec.sentinel_id}:no_exact_prior_child_pair",
        )
    return pairs[-1]


def _consumer_payload_identity(package_root: Path) -> tuple[str, dict[str, Any]]:
    verified = verify_daily_domain_increment(package_root)
    manifest = _object(package_root / "manifest.json", "daily DWD manifest")
    files = manifest.get("files")
    row_counts = manifest.get("row_counts")
    if not isinstance(files, list) or not isinstance(row_counts, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_package_invalid",
            verified.package_id,
        )
    tables: dict[str, Any] = {}
    for table in _CONSUMER_TABLES:
        selected: list[dict[str, Any]] = []
        marker = f"/{table}/"
        for item in files:
            if not isinstance(item, dict):
                continue
            relative = item.get("path")
            if not isinstance(relative, str):
                continue
            if relative.startswith("payload/") and marker in f"/{relative}":
                selected.append(
                    {
                        "path": relative,
                        "sha256": item.get("sha256"),
                        "size_bytes": item.get("size_bytes"),
                    }
                )
        if not selected or not all(
            isinstance(item["sha256"], str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"])
            and isinstance(item["size_bytes"], int)
            and item["size_bytes"] >= 0
            for item in selected
        ):
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_package_invalid",
                f"{verified.package_id}:{table}",
            )
        count = row_counts.get(table)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_package_invalid",
                f"{verified.package_id}:{table}:row_count",
            )
        tables[table] = {
            "row_count": count,
            "files": sorted(selected, key=lambda item: item["path"]),
        }
    payload = {
        "schema_version": "quanthus.p3-strat-003-consumer-payload-identity.v1",
        "market_date": verified.market_date.isoformat(),
        "tables": tables,
    }
    return digest_value(payload), payload


def _selected_release_identity(catalog_root: Path) -> dict[str, tuple[str, str]]:
    catalog = _object(catalog_root / "catalog.json", "daily ODS catalog")
    lanes = catalog.get("lanes")
    if not isinstance(lanes, list):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_ods_invalid",
            "catalog lanes are absent",
        )
    observed: dict[str, tuple[str, str]] = {}
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        name = str(lane.get("lane") or "")
        release_id = str(lane.get("release_id") or "")
        manifest_sha = str(lane.get("release_manifest_sha256") or "")
        if not name or not release_id or re.fullmatch(r"sha256:[0-9a-f]{64}", manifest_sha) is None:
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_ods_invalid",
                "catalog release binding is invalid",
            )
        observed[name] = (release_id, manifest_sha)
    return observed


def _original_release_identity(package_root: Path) -> dict[str, tuple[str, str]]:
    manifest = _object(package_root / "manifest.json", "original daily DWD manifest")
    selected = manifest.get("selected_releases")
    if not isinstance(selected, dict):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_package_invalid",
            "original selected_releases are absent",
        )
    observed: dict[str, tuple[str, str]] = {}
    for lane, value in selected.items():
        if not isinstance(lane, str) or not isinstance(value, dict):
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_package_invalid",
                "original selected release binding is invalid",
            )
        release_id = str(value.get("release_id") or "")
        manifest_sha = str(value.get("manifest_sha256") or "")
        if not release_id or re.fullmatch(r"sha256:[0-9a-f]{64}", manifest_sha) is None:
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_package_invalid",
                "original selected release identity is invalid",
            )
        observed[lane] = (release_id, manifest_sha)
    return observed


def _build_reconstruction(
    *,
    reconstruction: Any,
    base_root: Path,
    market_date: date,
    anchor: _OriginalPackage,
    output_root: Path,
    code_revision: str,
    require_release_identity: dict[str, tuple[str, str]] | None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    release_root, source_inventory = legacy._pull_reconstruction_ods(
        reconstruction,
        market_date=market_date,
        output_root=output_root / "source",
    )
    catalog_root = output_root / "catalog"
    try:
        catalog = build_daily_ods_catalog(
            release_root=release_root,
            market_date=market_date,
            output=catalog_root,
        )
    except DailyOdsCatalogError as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_ods_invalid",
            str(exc),
        ) from exc
    selected_identity = _selected_release_identity(catalog_root)
    if require_release_identity is not None and selected_identity != require_release_identity:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_ods_drift",
            market_date.isoformat(),
        )
    prior_pointer = legacy._prior_pointer(
        candidate=legacy._PriorCandidate(
            root=anchor.root,
            market_date=anchor.market_date,
            package_id=anchor.package_id,
            manifest_sha256=anchor.manifest_sha256,
        ),
        root=output_root / "prior",
    )
    package_root = output_root / "package"
    try:
        built = build_daily_domain_increment(
            catalog_root=catalog_root,
            release_root=release_root,
            base_root=base_root,
            release_config=reconstruction.release_config,
            output=package_root,
            code_revision=code_revision,
            prior_pointer=prior_pointer,
        )
    except DailyDomainBuildError as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_build_failed",
            f"{market_date.isoformat()}:{exc}",
        ) from exc
    return (
        package_root,
        {
            "market_date": market_date.isoformat(),
            "package_id": built.package_id,
            "manifest_sha256": built.manifest_sha256,
            "catalog_digest": catalog.catalog_digest,
            "selected_releases": {
                lane: {"release_id": value[0], "manifest_sha256": value[1]}
                for lane, value in sorted(selected_identity.items())
            },
        },
        {"source_attempts": list(source_inventory)},
    )


def _sentinel_receipt(
    *,
    spec: _SentinelSpec,
    runtime: Any,
    reconstruction: Any,
    staging: Path,
    code_revision: str,
) -> dict[str, Any]:
    anchor, original = _select_sentinel_pair(runtime.daily_root, spec)
    original_release_identity = _original_release_identity(original.root)
    rebuilt_root, rebuilt, source = _build_reconstruction(
        reconstruction=reconstruction,
        base_root=runtime.historical_base_root,
        market_date=original.market_date,
        anchor=anchor,
        output_root=staging / spec.sentinel_id,
        code_revision=code_revision,
        require_release_identity=original_release_identity,
    )
    original_digest, original_payload = _consumer_payload_identity(original.root)
    rebuilt_digest, rebuilt_payload = _consumer_payload_identity(rebuilt_root)
    if original_digest != rebuilt_digest or original_payload != rebuilt_payload:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_payload_mismatch",
            spec.sentinel_id,
        )
    return {
        "sentinel_id": spec.sentinel_id,
        "selection_rule": "latest_verified_original_prior_child_pair",
        "anchor_market_date": anchor.market_date.isoformat(),
        "anchor_package_id": anchor.package_id,
        "original_market_date": original.market_date.isoformat(),
        "original_package_id": original.package_id,
        "original_manifest_sha256": original.manifest_sha256,
        "rebuilt_package_id": rebuilt["package_id"],
        "rebuilt_manifest_sha256": rebuilt["manifest_sha256"],
        "manifest_byte_equality": original.manifest_sha256 == rebuilt["manifest_sha256"],
        "consumer_payload_digest": original_digest,
        "consumer_payload_exact_match": True,
        "selected_releases_exact_match": True,
        "source": source,
    }


def _missing_child_binding(daily_root: Path, market_date: date) -> tuple[str, str]:
    bindings: set[tuple[str, str]] = set()
    packages_root = daily_root.resolve() / "packages"
    if not packages_root.is_dir():
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_boundary_missing",
            market_date.isoformat(),
        )
    for date_root in sorted(packages_root.iterdir()):
        if not date_root.is_dir():
            continue
        try:
            child_date = date.fromisoformat(date_root.name)
        except ValueError:
            continue
        if child_date <= market_date:
            continue
        for package_root in sorted(path for path in date_root.iterdir() if path.is_dir()):
            try:
                verify_daily_domain_increment(package_root)
            except DailyDomainBuildError:
                continue
            manifest = _object(package_root / "manifest.json", "child daily DWD manifest")
            prior = manifest.get("prior")
            if not isinstance(prior, dict):
                continue
            if str(prior.get("market_date")) != market_date.isoformat():
                continue
            package_id = str(prior.get("package_id") or "")
            manifest_sha = str(prior.get("manifest_sha256") or "")
            if package_id and re.fullmatch(r"sha256:[0-9a-f]{64}", manifest_sha):
                bindings.add((package_id, manifest_sha))
    if len(bindings) != 1:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_boundary_binding_ambiguous",
            f"{market_date.isoformat()}:{len(bindings)}",
        )
    return next(iter(bindings))


def _catalog_artifact(
    *,
    catalog_root: Path,
    release_root: Path,
    lane_name: str,
    artifact_name: str,
) -> tuple[Path, dict[str, Any]]:
    catalog = _object(catalog_root / "catalog.json", "daily ODS catalog")
    lanes = catalog.get("lanes")
    if not isinstance(lanes, list):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_ods_invalid",
            "catalog lane inventory is absent",
        )
    for lane in lanes:
        if not isinstance(lane, dict) or lane.get("lane") != lane_name:
            continue
        release_path = lane.get("release_path")
        artifacts = lane.get("artifacts")
        if not isinstance(release_path, str) or not isinstance(artifacts, list):
            break
        for artifact in artifacts:
            if not isinstance(artifact, dict) or artifact.get("name") != artifact_name:
                continue
            relative = artifact.get("relative_path")
            if not isinstance(relative, str):
                break
            path = (release_root.resolve() / release_path / relative).resolve()
            if not path.is_relative_to(release_root.resolve()):
                break
            return path, artifact
    raise ExpertEvaluationError(
        "p3_strat_003_semantic_probe_ods_invalid",
        f"{lane_name}/{artifact_name}",
    )


def _boundary_receipt(
    *,
    boundary: _BoundarySpec,
    runtime: Any,
    reconstruction: Any,
    staging: Path,
    code_revision: str,
) -> dict[str, Any]:
    expected_package_id, expected_manifest_sha256 = _missing_child_binding(
        runtime.daily_root,
        boundary.market_date,
    )
    matched = _PACKAGE_ID.fullmatch(expected_package_id)
    if matched is None:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_boundary_invalid",
            expected_package_id,
        )
    encoded_date = date(
        int(matched.group(1)[0:4]),
        int(matched.group(1)[4:6]),
        int(matched.group(1)[6:8]),
    )
    if (
        encoded_date != boundary.market_date
        or matched.group(3) != boundary.historical_builder_revision[:7]
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_boundary_invalid",
            "child binding differs from frozen historical revision",
        )

    anchors = _verified_original_packages_between(
        runtime.daily_root,
        start=boundary.market_date.replace(day=1),
        end=boundary.market_date,
    )
    anchors = tuple(item for item in anchors if item.market_date < boundary.market_date)
    if not anchors:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_boundary_anchor_missing",
            boundary.market_date.isoformat(),
        )
    anchor = anchors[-1]
    rebuilt_root, rebuilt, source = _build_reconstruction(
        reconstruction=reconstruction,
        base_root=runtime.historical_base_root,
        market_date=boundary.market_date,
        anchor=anchor,
        output_root=staging / "missing-boundary",
        code_revision=code_revision,
        require_release_identity=None,
    )
    if rebuilt["catalog_digest"][7:19] != matched.group(2):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_boundary_catalog_mismatch",
            boundary.market_date.isoformat(),
        )

    catalog_root = staging / "missing-boundary" / "catalog"
    release_root = staging / "missing-boundary" / "source" / "ods-releases"
    artifact_path, artifact = _catalog_artifact(
        catalog_root=catalog_root,
        release_root=release_root,
        lane_name=boundary.artifact_lane,
        artifact_name=boundary.artifact_name,
    )
    observed_artifact_sha256 = str(artifact.get("sha256") or "")
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", observed_artifact_sha256) is None
        or _sha256(artifact_path) != observed_artifact_sha256
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_boundary_artifact_integrity",
            boundary.artifact_name,
        )
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise ExpertEvaluationError(
            "dependency_missing",
            "pyarrow",
        ) from exc
    row_count = int(pq.ParquetFile(artifact_path).metadata.num_rows)
    if row_count != boundary.artifact_row_count:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_boundary_artifact_mismatch",
            f"{boundary.artifact_name}:{row_count}",
        )
    consumer_digest, _consumer_payload = _consumer_payload_identity(rebuilt_root)
    return {
        "market_date": boundary.market_date.isoformat(),
        "original_child_binding": {
            "package_id": expected_package_id,
            "manifest_sha256": expected_manifest_sha256,
        },
        "research_reconstruction": {
            "package_id": rebuilt["package_id"],
            "manifest_sha256": rebuilt["manifest_sha256"],
            "catalog_digest": rebuilt["catalog_digest"],
            "consumer_payload_digest": consumer_digest,
        },
        "anchor_market_date": anchor.market_date.isoformat(),
        "anchor_package_id": anchor.package_id,
        "compatibility_class": boundary.compatibility_class,
        "historical_builder_revision": boundary.historical_builder_revision,
        "historical_builder_blob": boundary.historical_builder_blob,
        "current_builder_blob": boundary.current_builder_blob,
        "only_builder_change_after_historical_revision": (
            boundary.only_builder_change_after_historical_revision
        ),
        "required_ods_artifact": {
            "lane": boundary.artifact_lane,
            "name": boundary.artifact_name,
            "row_count": row_count,
            "sha256": observed_artifact_sha256,
            "identity_rule": "selected_cn01_release_self_verified",
        },
        "source": source,
        "original_manifest_claimed_reproduced": False,
        "consumer_semantic_compatibility_precondition_passed": True,
    }


def build_semantic_equivalence_probe(
    *,
    request_path: Path,
    output: Path,
    code_revision: str,
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{40}", code_revision) is None:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_code_revision_invalid",
            "code revision must be a full lowercase Git object id",
        )
    config = _load_config(request_path)
    runtime = load_prospective_runtime_config(config.runtime_path, root=config.project)
    legacy_raw = legacy._request(config.legacy_transport_path)
    reconstruction = legacy._missing_prior_reconstruction(
        legacy_raw,
        project=config.project,
    )
    output = output.resolve()
    for source_root in (
        runtime.historical_base_root,
        runtime.correction_root,
        runtime.daily_root,
    ):
        source_root = source_root.resolve()
        if output == source_root or output.is_relative_to(source_root):
            raise ExpertEvaluationError(
                "p3_strat_003_semantic_probe_isolation_invalid",
                "probe output must stay outside read-only input roots",
            )
    request_digest = digest_value(config.raw)
    if output.exists():
        return verify_semantic_equivalence_probe_artifact(
            output,
            code_revision=code_revision,
            request_digest=request_digest,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        sentinel_receipts = [
            _sentinel_receipt(
                spec=spec,
                runtime=runtime,
                reconstruction=reconstruction,
                staging=staging,
                code_revision=code_revision,
            )
            for spec in config.sentinels
        ]
        boundary_receipt = _boundary_receipt(
            boundary=config.boundary,
            runtime=runtime,
            reconstruction=reconstruction,
            staging=staging,
            code_revision=code_revision,
        )
        summary = {
            "schema_version": SUMMARY_SCHEMA,
            "work_item": "P3-STRAT-003-B",
            "request_digest": request_digest,
            "frozen_research_request_digest": REQUEST_DIGEST,
            "code_revision": code_revision,
            "consumer_tables": list(_CONSUMER_TABLES),
            "sentinels": sentinel_receipts,
            "missing_boundary": boundary_receipt,
            "strategy_or_economic_result_count": 0,
            "target_outcome_label_count": 0,
            "candidate_scoring_count": 0,
            "p6_mutation": False,
            "daily_current_mutation": False,
            "disposition": "PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE",
        }
        summary_path = staging / "summary.json"
        summary_path.write_bytes(canonical_json_bytes(summary) + b"\n")
        manifest_payload = {
            "schema_version": ARTIFACT_SCHEMA,
            "work_item": "P3-STRAT-003-B",
            "request_digest": request_digest,
            "frozen_research_request_digest": REQUEST_DIGEST,
            "code_revision": code_revision,
            "files": {
                "summary": {
                    "path": "summary.json",
                    "sha256": _sha256(summary_path),
                }
            },
            "consumer_tables": list(_CONSUMER_TABLES),
            "sentinel_count": len(sentinel_receipts),
            "strategy_result_produced": False,
            "outcome_labels_produced": False,
            "candidate_scoring_performed": False,
            "p6_mutation": False,
            "daily_current_mutation": False,
            "disposition": summary["disposition"],
        }
        manifest = {
            **manifest_payload,
            "result_digest": digest_value(manifest_payload),
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
        (staging / "READY").write_text(_sha256(manifest_path) + "\n", encoding="ascii")
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_semantic_equivalence_probe_artifact(
        output,
        code_revision=code_revision,
        request_digest=request_digest,
    )


def verify_semantic_equivalence_probe_artifact(
    root: Path,
    *,
    code_revision: str,
    request_digest: str,
) -> dict[str, Any]:
    root = root.resolve()
    manifest = _object(root / "manifest.json", "semantic-equivalence manifest")
    summary = _object(root / "summary.json", "semantic-equivalence summary")
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_unreadable",
            "READY is unreadable",
        ) from exc
    if (
        ready != _sha256(root / "manifest.json")
        or manifest.get("schema_version") != ARTIFACT_SCHEMA
        or manifest.get("work_item") != "P3-STRAT-003-B"
        or manifest.get("request_digest") != request_digest
        or manifest.get("code_revision") != code_revision
        or manifest.get("consumer_tables") != list(_CONSUMER_TABLES)
        or manifest.get("sentinel_count") != 2
        or manifest.get("strategy_result_produced") is not False
        or manifest.get("outcome_labels_produced") is not False
        or manifest.get("candidate_scoring_performed") is not False
        or manifest.get("p6_mutation") is not False
        or manifest.get("daily_current_mutation") is not False
        or manifest.get("disposition") != "PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE"
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_artifact_invalid",
            "probe manifest boundary differs",
        )
    payload = {key: value for key, value in manifest.items() if key != "result_digest"}
    if manifest.get("result_digest") != digest_value(payload):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_artifact_drift",
            "probe result digest differs",
        )
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {"summary"}:
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_artifact_invalid",
            "probe file binding differs",
        )
    summary_binding = files["summary"]
    if (
        not isinstance(summary_binding, dict)
        or summary_binding.get("path") != "summary.json"
        or summary_binding.get("sha256") != _sha256(root / "summary.json")
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_artifact_drift",
            "probe summary binding differs",
        )
    sentinels = summary.get("sentinels")
    boundary = summary.get("missing_boundary")
    if (
        summary.get("schema_version") != SUMMARY_SCHEMA
        or summary.get("request_digest") != request_digest
        or summary.get("code_revision") != code_revision
        or summary.get("consumer_tables") != list(_CONSUMER_TABLES)
        or not isinstance(sentinels, list)
        or len(sentinels) != 2
        or any(item.get("consumer_payload_exact_match") is not True for item in sentinels)
        or any(item.get("selected_releases_exact_match") is not True for item in sentinels)
        or not isinstance(boundary, dict)
        or boundary.get("consumer_semantic_compatibility_precondition_passed") is not True
        or boundary.get("original_manifest_claimed_reproduced") is not False
        or summary.get("strategy_or_economic_result_count") != 0
        or summary.get("target_outcome_label_count") != 0
        or summary.get("candidate_scoring_count") != 0
        or summary.get("p6_mutation") is not False
        or summary.get("daily_current_mutation") is not False
        or summary.get("disposition") != manifest.get("disposition")
    ):
        raise ExpertEvaluationError(
            "p3_strat_003_semantic_probe_artifact_invalid",
            "probe summary boundary differs",
        )
    return {
        "artifact_ref": str(root),
        "manifest_sha256": _sha256(root / "manifest.json"),
        "result_digest": str(manifest["result_digest"]),
        "request_digest": request_digest,
        "sentinel_count": 2,
        "row_count": 0,
        "candidate_event_count": 0,
        "p2_probe_count": 0,
        "p2_fill_count": 0,
        "execution_authorized": False,
        "strategy_result_produced": False,
        "outcome_labels_produced": False,
        "p6_mutation": False,
        "disposition": str(manifest["disposition"]),
    }


def _parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        description="Probe P3-STRAT-003-B historical reconstruction semantic equivalence"
    )
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = build_semantic_equivalence_probe(
        request_path=args.request,
        output=args.output,
        code_revision=args.code_revision,
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
