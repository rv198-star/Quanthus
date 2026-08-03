"""Select and seal the four ODS lanes required by one DWD daily build."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.pipelines.contracts import OdsReleaseError, verify_ods_release


class DailyOdsCatalogError(RuntimeError):
    """Raised when a complete, unambiguous daily ODS input set cannot be sealed."""


_LANES = {
    "market_daily_core": ("daily", "hermes-market-daily-core-"),
    "calendar_daily_events": ("daily", "hermes-calendar-daily-events-"),
    "intraday_1m": ("daily/1m", "hermes-intraday-archive-1m-"),
    "intraday_5m": ("daily/5m", "hermes-intraday-archive-5m-"),
}


@dataclass(frozen=True)
class DailyOdsCatalogResult:
    root: Path
    market_date: date
    catalog_digest: str
    lane_count: int
    artifact_count: int
    verified_bytes: int


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


def _select_lane(release_root: Path, market_date: date, lane: str) -> Any:
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


def build_daily_ods_catalog(
    *, release_root: Path, market_date: date, output: Path
) -> DailyOdsCatalogResult:
    """Verify and bind exactly one latest READY release for every required daily lane."""

    release_root = release_root.resolve()
    output = output.resolve()
    if output == release_root or output.is_relative_to(release_root):
        raise DailyOdsCatalogError("catalog output must be outside the ODS release root")
    if output.exists():
        return verify_daily_ods_catalog(output)
    selected = {lane: _select_lane(release_root, market_date, lane) for lane in _LANES}
    lineages = {
        (
            item.release.schedule_contract_id,
            item.release.schedule_contract_revision,
            item.release.schedule_config_sha256,
        )
        for item in selected.values()
    }
    if len(lineages) != 1 or next(iter(lineages))[0] is None:
        raise DailyOdsCatalogError("four ODS lanes do not share one complete schedule contract")
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
        "kind": "cn01_daily_ods_input_catalog",
        "market_date": market_date.isoformat(),
        "schedule_contract": {
            "id": next(iter(lineages))[0],
            "revision": next(iter(lineages))[1],
            "config_sha256": next(iter(lineages))[2],
        },
        "lanes": lanes,
    }
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
    if not isinstance(catalog, Mapping) or catalog.get("kind") != "cn01_daily_ods_input_catalog":
        raise DailyOdsCatalogError("daily ODS catalog kind is invalid")
    if ready != _sha256(root / "catalog.json"):
        raise DailyOdsCatalogError("daily ODS catalog READY differs")
    semantic = dict(catalog)
    digest = semantic.pop("catalog_digest", None)
    expected = "sha256:" + hashlib.sha256(_canonical_bytes(semantic)).hexdigest()
    if digest != expected:
        raise DailyOdsCatalogError("daily ODS catalog semantic digest differs")
    lanes = catalog.get("lanes")
    if not isinstance(lanes, list) or {item.get("lane") for item in lanes} != set(_LANES):
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
    )
