"""Prepare immutable CN01 DWD daily and weekly input runs.

The schedule controller owns input selection and run identity.  It deliberately does not
interpret the business rows and it never writes either Base package.  Domain builders consume
the sealed run after they implement the declared ``daily`` and ``weekly`` modes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.securities_warehouse.daily_ods_catalog import (
    DailyOdsCatalogError,
    DailyOdsCatalogResult,
    build_daily_ods_catalog,
)
from quanthus.pipelines.securities_warehouse.weekly_ods_catalog import (
    WeeklyOdsCatalogError,
    WeeklyOdsCatalogResult,
    build_weekly_ods_catalog,
)


class DwdScheduleError(RuntimeError):
    """Raised when a scheduled DWD input run is not safe to prepare or execute."""


@dataclass(frozen=True)
class DwdScheduleResult:
    """The sealed control-plane result for one daily or weekly run."""

    root: Path
    run_id: str
    mode: str
    market_date: date
    manifest_sha256: str
    catalog_digest: str
    builder_count: int
    status: str


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_MODES = {"daily", "weekly"}

# Eight externally visible domain scripts.  The calendar backbone is an internal sub-step of
# the tradability owner (#59); it is not exposed as a ninth scheduled business script.
_BUILDERS: tuple[dict[str, Any], ...] = (
    {
        "issue": 57,
        "id": "listed_company",
        "command": "build-dwd-listed-company",
        "depends_on": [],
    },
    {
        "issue": 58,
        "id": "market_observation",
        "command": "build-dwd-market-observation",
        "depends_on": [57],
    },
    {
        "issue": 59,
        "id": "tradability_state",
        "command": "build-dwd-tradability",
        "depends_on": [57, 58],
        "internal_steps": ["trading_calendar_backbone"],
    },
    {
        "issue": 60,
        "id": "disclosure_company_event",
        "command": "build-dwd-disclosure-event",
        "depends_on": [57, 61],
    },
    {
        "issue": 61,
        "id": "financial_reporting",
        "command": "build-dwd-financial-reporting",
        "depends_on": [57],
    },
    {
        "issue": 62,
        "id": "industry_classification",
        "command": "build-dwd-industry-classification",
        "depends_on": [57],
    },
    {
        "issue": 63,
        "id": "ownership_capital",
        "command": "build-dwd-ownership-capital",
        "depends_on": [57, 60],
    },
    {
        "issue": 64,
        "id": "quality_governance",
        "command": "build-dwd-quality-governance",
        "depends_on": [57, 58, 59, 60, 61, 62, 63],
    },
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _object(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdScheduleError(f"cannot read {field}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdScheduleError(f"{field} must be a JSON object: {path}")
    return value


def _safe_relative(value: object, field: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise DwdScheduleError(f"{field} must be a relative path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or relative.as_posix() == "." or ".." in relative.parts:
        raise DwdScheduleError(f"{field} must be a safe relative path")
    return relative


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _verify_base(
    root: Path,
    *,
    layer: str,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    root = root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise DwdScheduleError(f"{layer} Base must be a physical directory: {root}")
    manifest_path = root / "manifest.json"
    ready_path = root / "READY"
    manifest = _object(manifest_path, f"{layer} Base manifest")
    manifest_sha = _sha256(manifest_path)
    try:
        ready = ready_path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise DwdScheduleError(f"{layer} Base READY is unreadable: {ready_path}") from exc
    if ready != manifest_sha:
        raise DwdScheduleError(f"{layer} Base READY does not match its manifest")
    package_id = manifest.get("package_id", manifest.get("bundle_id"))
    if expected.get("package_id") != package_id and expected.get("package_id") is not None:
        raise DwdScheduleError(f"{layer} Base package_id differs from the release contract")
    if expected.get("content_digest") != manifest.get("content_digest") and expected.get(
        "content_digest"
    ) is not None:
        raise DwdScheduleError(f"{layer} Base content_digest differs from the release contract")
    if expected.get("manifest_sha256") not in (None, manifest_sha):
        raise DwdScheduleError(f"{layer} Base manifest checksum differs from the release contract")
    return {
        "package_id": str(package_id or ""),
        "manifest_sha256": manifest_sha,
        "content_digest": str(manifest.get("content_digest", "")),
        "root": str(root),
        "through_date": manifest.get("through_date"),
    }


def _catalog_binding(
    result: DailyOdsCatalogResult | WeeklyOdsCatalogResult,
    *,
    root: Path,
) -> dict[str, Any]:
    catalog_path = root / "catalog.json"
    return {
        "path": str(catalog_path),
        "manifest_sha256": _sha256(catalog_path),
        "catalog_digest": result.catalog_digest,
        "market_date": result.market_date.isoformat(),
        "artifact_count": result.artifact_count,
        "verified_bytes": result.verified_bytes,
        "release_id": getattr(result, "release_id", None),
        "lane_count": getattr(result, "lane_count", None),
    }


def _verify_existing(root: Path) -> DwdScheduleResult:
    manifest_path = root / "run.json"
    ready_path = root / "READY"
    manifest = _object(manifest_path, "DWD schedule run manifest")
    manifest_sha = _sha256(manifest_path)
    try:
        ready = ready_path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise DwdScheduleError("DWD schedule run READY is unreadable") from exc
    if ready != manifest_sha:
        raise DwdScheduleError("DWD schedule run READY differs")
    if manifest.get("kind") != "cn01_dwd_schedule_run":
        raise DwdScheduleError("DWD schedule run kind is invalid")
    if manifest.get("mode") not in _MODES:
        raise DwdScheduleError("DWD schedule run mode is invalid")
    try:
        date.fromisoformat(str(manifest.get("market_date")))
    except ValueError as exc:
        raise DwdScheduleError("DWD schedule run market_date is invalid") from exc
    execution = manifest.get("execution")
    if not isinstance(execution, Mapping) or execution.get("builder_count") != len(_BUILDERS):
        raise DwdScheduleError("DWD schedule builder inventory is invalid")
    builders = execution.get("builders")
    if builders != [dict(item) for item in _BUILDERS]:
        raise DwdScheduleError("DWD schedule builder order or ownership differs")
    input_section = manifest.get("input")
    if not isinstance(input_section, Mapping):
        raise DwdScheduleError("DWD schedule input binding is invalid")
    catalog = input_section.get("ods_catalog", {})
    if not isinstance(catalog, Mapping) or not catalog.get("catalog_digest"):
        raise DwdScheduleError("DWD schedule catalog binding is invalid")
    catalog_path = root / "input_catalog" / "catalog.json"
    catalog_ready = root / "input_catalog" / "READY"
    if (
        catalog.get("path") != str(catalog_path)
        or not catalog_path.is_file()
        or not catalog_ready.is_file()
        or catalog_ready.read_text(encoding="ascii").strip() != _sha256(catalog_path)
        or catalog.get("manifest_sha256") != _sha256(catalog_path)
    ):
        raise DwdScheduleError("DWD schedule ODS catalog checksum binding differs")
    return DwdScheduleResult(
        root=root,
        run_id=str(manifest["run_id"]),
        mode=str(manifest["mode"]),
        market_date=date.fromisoformat(str(manifest["market_date"])),
        manifest_sha256=manifest_sha,
        catalog_digest=str(catalog["catalog_digest"]),
        builder_count=int(execution["builder_count"]),
        status=str(execution["status"]),
    )


def prepare_dwd_schedule_run(
    *,
    mode: str,
    market_date: date,
    release_root: Path,
    ods_base: Path,
    dwd_base: Path,
    release_config: Path,
    output: Path,
    run_id: str,
    code_revision: str,
) -> DwdScheduleResult:
    """Seal one daily/weekly input run without mutating any source or Base package."""

    if mode not in _MODES:
        raise DwdScheduleError("mode must be daily or weekly")
    if _SAFE_ID.fullmatch(run_id) is None:
        raise DwdScheduleError("run_id is not a safe identifier")
    if _REVISION.fullmatch(code_revision) is None:
        raise DwdScheduleError("code_revision must be a lowercase 40-character revision")
    if not isinstance(market_date, date):
        raise DwdScheduleError("market_date must be a date")

    release_root = release_root.resolve()
    ods_base = ods_base.resolve()
    dwd_base = dwd_base.resolve()
    output = output.resolve()
    for source, label in (
        (release_root, "ODS release"),
        (ods_base, "ODS Base"),
        (dwd_base, "DWD Base"),
    ):
        if output == source or output.is_relative_to(source):
            raise DwdScheduleError(f"run output must be outside the {label} root")
    if output.exists():
        existing = _verify_existing(output)
        manifest = _object(output / "run.json", "existing DWD schedule run manifest")
        if (
            existing.run_id != run_id
            or existing.mode != mode
            or existing.market_date != market_date
            or manifest.get("code_revision") != code_revision
        ):
            raise DwdScheduleError("existing DWD schedule run identity differs")
        return existing

    config = _object(release_config.resolve(), "V2 release config")
    layers = config.get("layers")
    if not isinstance(layers, Mapping):
        raise DwdScheduleError("V2 release config has no layers")
    ods_expected = layers.get("ods", {})
    dwd_expected = layers.get("dwd", {})
    if not isinstance(ods_expected, Mapping) or not isinstance(dwd_expected, Mapping):
        raise DwdScheduleError("V2 release config layer bindings are invalid")
    ods_binding = _verify_base(ods_base, layer="ODS", expected=ods_expected)
    dwd_binding = _verify_base(dwd_base, layer="DWD", expected=dwd_expected)

    output.parent.mkdir(parents=True, exist_ok=True)
    catalog_stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.catalog.", dir=output.parent))
    catalog_root = catalog_stage / "input_catalog"
    catalog: DailyOdsCatalogResult | WeeklyOdsCatalogResult
    try:
        if mode == "daily":
            catalog = build_daily_ods_catalog(
                release_root=release_root,
                market_date=market_date,
                output=catalog_root,
            )
        else:
            catalog = build_weekly_ods_catalog(
                release_root=release_root,
                market_date=market_date,
                output=catalog_root,
            )
    except (DailyOdsCatalogError, WeeklyOdsCatalogError, OSError, ValueError) as exc:
        import shutil

        shutil.rmtree(catalog_stage, ignore_errors=True)
        raise DwdScheduleError(f"cannot seal {mode} ODS input catalog: {exc}") from exc

    binding = _catalog_binding(catalog, root=catalog_root)
    manifest = {
        "schema_version": 1,
        "kind": "cn01_dwd_schedule_run",
        "run_id": run_id,
        "mode": mode,
        "market_date": market_date.isoformat(),
        "code_revision": code_revision,
        "input": {
            "ods_base": ods_binding,
            "dwd_base": dwd_binding,
            "ods_catalog": binding,
        },
        "execution": {
            "builder_count": len(_BUILDERS),
            "builders": [dict(item) for item in _BUILDERS],
            "dws": "disabled_by_policy",
            "base_mutation": "forbidden",
            "status": "prepared",
            "executor": "domain_builders_must_explicitly_support_daily_or_weekly",
        },
    }
    work = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        # Keep the ODS catalog as a separately verified immutable child of the run.
        target_catalog = work / "input_catalog"
        target_catalog.mkdir(parents=True)
        for name in ("catalog.json", "READY"):
            source = catalog_root / name
            if source.is_symlink() or not source.is_file():
                raise DwdScheduleError(f"sealed ODS catalog is missing {name}")
            os.link(source, target_catalog / name, follow_symlinks=False)
        manifest["input"]["ods_catalog"]["path"] = str(output / "input_catalog" / "catalog.json")
        _write_atomic(work / "run.json", _canonical_bytes(manifest) + b"\n")
        _write_atomic(work / "READY", (_sha256(work / "run.json") + "\n").encode("ascii"))
        os.replace(work, output)
    except BaseException:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        # The temporary catalog is outside the run and is no longer needed after hard-linking.
        import shutil

        shutil.rmtree(catalog_stage, ignore_errors=True)
    return _verify_existing(output)


def builder_contract() -> tuple[dict[str, Any], ...]:
    """Return the immutable eight-builder logical contract for tests and operators."""

    return tuple(dict(item) for item in _BUILDERS)
