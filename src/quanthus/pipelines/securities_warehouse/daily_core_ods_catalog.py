"""Select and seal versioned ODS inputs for each governed daily data product."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from quanthus.pipelines.contracts import (
    OdsReleaseError,
    OdsVerificationResult,
    verify_ods_release,
)
from quanthus.pipelines.securities_warehouse.daily_ods_catalog import DailyOdsCatalogError

_LANES = {
    "market_daily_core": ("daily", "hermes-market-daily-core-"),
    "calendar_daily_events": ("daily", "hermes-calendar-daily-events-"),
    "intraday_1m": ("daily/1m", "hermes-intraday-archive-1m-"),
    "intraday_5m": ("daily/5m", "hermes-intraday-archive-5m-"),
}

# The legacy four-lane input and its existing catalog digest remain byte-for-byte
# unchanged.  A consumer of the daily-core product explicitly selects a different
# versioned catalog and cannot interpret its missing minute inputs as READY.
LEGACY_PRODUCT_SCOPE = "legacy-four-lane-v1"
DAILY_CORE_PRODUCT_SCOPE = "daily-core-v1"
_REQUIRED_LANES_BY_SCOPE = {
    LEGACY_PRODUCT_SCOPE: tuple(_LANES),
    DAILY_CORE_PRODUCT_SCOPE: ("market_daily_core", "calendar_daily_events"),
}
_DAILY_CORE_EXCLUDED = (
    {"lane": "intraday_1m", "status": "not_in_product", "formal_ready": False},
    {"lane": "intraday_5m", "status": "not_in_product", "formal_ready": False},
)
_QUALITY_POLICY_PATH = (
    Path(__file__).resolve().parents[4] / "config/pipelines/warehouse-quality-policy.v1.json"
)


def _daily_core_quality_sha256() -> str:
    try:
        policy = json.loads(_QUALITY_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyOdsCatalogError("versioned warehouse quality policy is missing") from exc
    profile = policy.get("product_scopes", {}).get(DAILY_CORE_PRODUCT_SCOPE, {})
    if (
        policy.get("schema_version") != 1
        or policy.get("policy_id") != "p10-business-value-warehouse-quality.v1"
        or profile.get("status") != "implemented_opt_in"
        or profile.get("requires") != list(_REQUIRED_LANES_BY_SCOPE[DAILY_CORE_PRODUCT_SCOPE])
        or profile.get("excludes_as_optional") != ["intraday_1m", "intraday_5m"]
        or profile.get("allowed_interval_sizes") != ["1d"]
        or profile.get("legacy_pointer_compatible") is not False
        or profile.get("publish_separately_from_legacy") is not True
    ):
        raise DailyOdsCatalogError("versioned warehouse quality policy identity differs")
    return _sha256(_QUALITY_POLICY_PATH)


@dataclass(frozen=True)
class DailyOdsCatalogResult:
    root: Path
    market_date: date
    catalog_digest: str
    lane_count: int
    artifact_count: int
    verified_bytes: int
    product_scope: str = LEGACY_PRODUCT_SCOPE


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


def _write_atomic(path: Path, payload: bytes) -> None:
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


def _select_lane(release_root: Path, market_date: date, lane: str) -> OdsVerificationResult:
    relative_root, release_prefix = _LANES[lane]
    date_root = release_root.joinpath(*relative_root.split("/"), market_date.isoformat())
    candidates = []
    if date_root.is_dir():
        for item in sorted(date_root.iterdir()):
            if not item.is_dir() or item.is_symlink():
                continue
            try:
                verified = verify_ods_release(item)
            except OdsReleaseError:
                continue
            release = verified.release
            if (
                release.schedule == "daily"
                and release.market_date == market_date
                and release.release_id.startswith(release_prefix)
            ):
                candidates.append(verified)
    if not candidates:
        raise DailyOdsCatalogError(f"required ODS lane is absent or not READY: {lane}")
    candidates.sort(key=lambda item: (item.release.created_at, item.release.release_id))
    return candidates[-1]


def find_ready_daily_ods_lanes(
    *, release_root: Path, market_date: date
) -> dict[str, OdsVerificationResult]:
    """Return the newest verified release for each daily lane already complete."""

    ready: dict[str, OdsVerificationResult] = {}
    for lane in _LANES:
        try:
            ready[lane] = _select_lane(release_root, market_date, lane)
        except DailyOdsCatalogError:
            continue
    return ready


def build_daily_ods_catalog(
    *,
    release_root: Path,
    market_date: date,
    output: Path,
    product_scope: str = LEGACY_PRODUCT_SCOPE,
) -> DailyOdsCatalogResult:
    """Build an immutable catalog for an explicitly selected consumer product.

    The default remains the old four-lane contract; only daily-core-v1 may use
    fewer lanes, and optional minute products remain expressly NOT IN PRODUCT.
    """
    if product_scope not in _REQUIRED_LANES_BY_SCOPE:
        raise DailyOdsCatalogError("unknown versioned daily product scope")

    release_root = release_root.resolve()
    output = output.resolve()
    if output == release_root or output.is_relative_to(release_root):
        raise DailyOdsCatalogError("catalog output must be outside the ODS release root")
    if output.exists():
        existing = verify_daily_ods_catalog(output)
        if existing.market_date != market_date or existing.product_scope != product_scope:
            raise DailyOdsCatalogError("existing catalog product identity differs")
        return existing
    required_lanes = _REQUIRED_LANES_BY_SCOPE[product_scope]
    selected = {lane: _select_lane(release_root, market_date, lane) for lane in required_lanes}
    lineages = {
        (
            item.release.schedule_contract_id,
            item.release.schedule_contract_revision,
            item.release.schedule_config_sha256,
        )
        for item in selected.values()
    }
    if len(lineages) != 1 or next(iter(lineages))[0] is None:
        raise DailyOdsCatalogError("required ODS lanes do not share one complete schedule contract")
    lanes = []
    for lane, item in selected.items():
        try:
            relative = item.release_dir.relative_to(release_root).as_posix()
        except ValueError as exc:
            raise DailyOdsCatalogError("selected ODS release escapes release_root") from exc
        lanes.append(
            {
                "lane": lane,
                "release_id": item.release.release_id,
                "release_path": relative,
                "release_manifest_sha256": item.manifest_sha256,
                "created_at": item.release.created_at.isoformat(),
                "artifacts": [artifact.to_dict() for artifact in item.release.artifacts],
            }
        )
    semantic = {
        "schema_version": 1,
        "kind": "daily_ods_input_catalog",
        "market_date": market_date.isoformat(),
        "schedule_contract": {
            "id": next(iter(lineages))[0],
            "revision": next(iter(lineages))[1],
            "config_sha256": next(iter(lineages))[2],
        },
        "lanes": lanes,
    }
    if product_scope == DAILY_CORE_PRODUCT_SCOPE:
        semantic["schema_version"] = 2
        semantic["kind"] = "daily_ods_product_catalog"
        semantic["product_scope"] = DAILY_CORE_PRODUCT_SCOPE
        semantic["required_lanes"] = list(required_lanes)
        semantic["excluded_optional_lanes"] = [dict(item) for item in _DAILY_CORE_EXCLUDED]
        semantic["consumer_scope"] = "daily-only-no-minute-input"
        semantic["source_provenance_required"] = True
        semantic["quality_policy_id"] = "p10-business-value-warehouse-quality.v1"
        semantic["quality_policy_sha256"] = _daily_core_quality_sha256()
    semantic["catalog_digest"] = "sha256:" + hashlib.sha256(_canonical_bytes(semantic)).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        _write_atomic(work / "catalog.json", _canonical_bytes(semantic) + b"\n")
        _write_atomic(work / "READY", (_sha256(work / "catalog.json") + "\n").encode("ascii"))
        os.replace(work, output)
    except BaseException:
        if work.exists():
            import shutil

            shutil.rmtree(work)
        raise
    return verify_daily_ods_catalog(output)


def verify_daily_ods_catalog(root: Path) -> DailyOdsCatalogResult:
    root = root.resolve()
    try:
        catalog = json.loads((root / "catalog.json").read_text(encoding="ascii"))
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyOdsCatalogError("daily ODS catalog is unreadable") from exc
    if not isinstance(catalog, Mapping) or catalog.get("kind") not in {
        "cn01_daily_ods_input_catalog",
        "daily_ods_input_catalog",
        "daily_ods_product_catalog",
    }:
        raise DailyOdsCatalogError("daily ODS catalog kind is invalid")
    if ready != _sha256(root / "catalog.json"):
        raise DailyOdsCatalogError("daily ODS catalog READY differs")
    semantic = dict(catalog)
    digest = semantic.pop("catalog_digest", None)
    expected = "sha256:" + hashlib.sha256(_canonical_bytes(semantic)).hexdigest()
    if digest != expected:
        raise DailyOdsCatalogError("daily ODS catalog semantic digest differs")
    product_scope = catalog.get("product_scope", LEGACY_PRODUCT_SCOPE)
    if product_scope not in _REQUIRED_LANES_BY_SCOPE:
        raise DailyOdsCatalogError("daily ODS product scope is unknown")
    is_core = product_scope == DAILY_CORE_PRODUCT_SCOPE
    if is_core:
        if (
            catalog.get("kind") != "daily_ods_product_catalog"
            or catalog.get("schema_version") != 2
            or catalog.get("required_lanes") != list(_REQUIRED_LANES_BY_SCOPE[product_scope])
            or catalog.get("excluded_optional_lanes") != [dict(x) for x in _DAILY_CORE_EXCLUDED]
            or catalog.get("consumer_scope") != "daily-only-no-minute-input"
            or catalog.get("source_provenance_required") is not True
            or catalog.get("quality_policy_id") != "p10-business-value-warehouse-quality.v1"
            or catalog.get("quality_policy_sha256") != _daily_core_quality_sha256()
        ):
            raise DailyOdsCatalogError("daily-core product contract differs")
    elif catalog.get("kind") == "daily_ods_product_catalog":
        raise DailyOdsCatalogError("legacy catalog cannot be a product-scoped catalog")
    lanes = catalog.get("lanes")
    expected_lanes = set(_REQUIRED_LANES_BY_SCOPE[product_scope])
    if not isinstance(lanes, list) or any(not isinstance(x, Mapping) for x in lanes):
        raise DailyOdsCatalogError("daily ODS catalog lane inventory differs")
    if len(lanes) != len(expected_lanes) or {item.get("lane") for item in lanes} != expected_lanes:
        raise DailyOdsCatalogError("daily ODS catalog lane inventory differs")
    return DailyOdsCatalogResult(
        root=root,
        market_date=date.fromisoformat(str(catalog["market_date"])),
        catalog_digest=expected,
        lane_count=len(lanes),
        artifact_count=sum(len(item["artifacts"]) for item in lanes),
        verified_bytes=sum(
            int(artifact["size_bytes"]) for item in lanes for artifact in item["artifacts"]
        ),
        product_scope=product_scope,
    )
