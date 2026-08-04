"""Select and seal one READY ODS weekly-correction release."""

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


class WeeklyOdsCatalogError(RuntimeError):
    """Raised when a complete weekly ODS correction input cannot be sealed."""


@dataclass(frozen=True)
class WeeklyOdsCatalogResult:
    root: Path
    market_date: date
    catalog_digest: str
    artifact_count: int
    verified_bytes: int
    release_id: str


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


def _select_release(release_root: Path, market_date: date) -> Any:
    date_root = release_root / "weekly" / market_date.isoformat()
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
                release.schedule == "weekly"
                and release.market_date == market_date
                and release.release_id.startswith("hermes-weekly-corrections-")
            ):
                candidates.append(verified)
    if not candidates:
        raise WeeklyOdsCatalogError(
            f"weekly ODS correction release is absent or not READY: {market_date.isoformat()}"
        )
    candidates.sort(key=lambda item: (item.release.created_at, item.release.release_id))
    return candidates[-1]


def build_weekly_ods_catalog(
    *, release_root: Path, market_date: date, output: Path
) -> WeeklyOdsCatalogResult:
    """Verify and bind the newest READY weekly correction release for a date."""

    release_root = release_root.resolve()
    output = output.resolve()
    if output == release_root or output.is_relative_to(release_root):
        raise WeeklyOdsCatalogError("catalog output must be outside the ODS release root")
    if output.exists():
        return verify_weekly_ods_catalog(output)
    selected = _select_release(release_root, market_date)
    try:
        relative = selected.release_dir.relative_to(release_root).as_posix()
    except ValueError as exc:
        raise WeeklyOdsCatalogError("selected ODS release escapes release_root") from exc
    lineages = (
        selected.release.schedule_contract_id,
        selected.release.schedule_contract_revision,
        selected.release.schedule_config_sha256,
    )
    if lineages[0] is None:
        raise WeeklyOdsCatalogError("weekly ODS release has no complete schedule contract")
    semantic = {
        "schema_version": 1,
        "kind": "cn01_weekly_ods_input_catalog",
        "market_date": market_date.isoformat(),
        "schedule_contract": {
            "id": lineages[0],
            "revision": lineages[1],
            "config_sha256": lineages[2],
        },
        "release": {
            "release_id": selected.release.release_id,
            "release_path": relative,
            "release_manifest_sha256": selected.manifest_sha256,
            "created_at": selected.release.created_at.isoformat(),
            "artifacts": [artifact.to_dict() for artifact in selected.release.artifacts],
        },
    }
    semantic["catalog_digest"] = "sha256:" + hashlib.sha256(
        _canonical_bytes(semantic)
    ).hexdigest()
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
    return verify_weekly_ods_catalog(output)


def verify_weekly_ods_catalog(root: Path) -> WeeklyOdsCatalogResult:
    root = root.resolve()
    try:
        catalog = json.loads((root / "catalog.json").read_text(encoding="ascii"))
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except (OSError, json.JSONDecodeError) as exc:
        raise WeeklyOdsCatalogError("weekly ODS catalog is unreadable") from exc
    if not isinstance(catalog, Mapping) or catalog.get("kind") != "cn01_weekly_ods_input_catalog":
        raise WeeklyOdsCatalogError("weekly ODS catalog kind is invalid")
    if ready != _sha256(root / "catalog.json"):
        raise WeeklyOdsCatalogError("weekly ODS catalog READY differs")
    semantic = dict(catalog)
    digest = semantic.pop("catalog_digest", None)
    expected = "sha256:" + hashlib.sha256(_canonical_bytes(semantic)).hexdigest()
    if digest != expected:
        raise WeeklyOdsCatalogError("weekly ODS catalog semantic digest differs")
    release = catalog.get("release")
    if not isinstance(release, Mapping) or not release.get("release_id"):
        raise WeeklyOdsCatalogError("weekly ODS catalog release inventory is invalid")
    artifacts = release.get("artifacts")
    if not isinstance(artifacts, list):
        raise WeeklyOdsCatalogError("weekly ODS catalog artifact inventory is invalid")
    return WeeklyOdsCatalogResult(
        root=root,
        market_date=date.fromisoformat(str(catalog["market_date"])),
        catalog_digest=expected,
        artifact_count=len(artifacts),
        verified_bytes=sum(int(artifact["size_bytes"]) for artifact in artifacts),
        release_id=str(release["release_id"]),
    )
