"""Immutable publication of the complete domain-modeled DWD Base."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


class DomainBaseError(RuntimeError):
    """Raised when a domain candidate or DWD Base violates its contract."""


@dataclass(frozen=True)
class DomainBaseResult:
    """One sealed and published domain-modeled DWD Base."""

    root: Path
    package_id: str
    manifest_sha256: str
    content_digest: str
    table_count: int
    row_count: int
    payload_bytes: int
    payload_file_count: int


@dataclass(frozen=True)
class DomainCandidateVerification:
    """Deep verification result for one same-run domain candidate."""

    root: Path
    manifest_sha256: str
    semantic_digest: str
    row_counts: dict[str, int]
    payload_file_count: int


_EXPECTED_KINDS = {
    "listed_company": "dwd_listed_company_candidate",
    "trading_calendar": "dwd_trading_calendar_candidate",
    "security_market_observation": "dwd_market_observation_candidate",
    "tradability_state": "dwd_tradability_state_candidate",
    "disclosure_company_event": "dwd_disclosure_event_candidate",
    "financial_reporting": "dwd_financial_reporting_candidate",
    "industry_classification": "dwd_industry_classification_candidate",
    "ownership_capital": "dwd_ownership_capital_candidate",
    "governance_sidecar": "dwd_quality_governance_candidate",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DomainBaseError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise DomainBaseError(f"JSON value is not an object: {path}")
    return value


def _relative(value: object, field: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise DomainBaseError(f"{field} must be a relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() == "." or ".." in path.parts:
        raise DomainBaseError(f"{field} must be a safe relative path")
    return path


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(_canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _candidate(
    domain: str,
    root: Path,
    *,
    run_id: str,
    input_plan_digest: str,
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    if root.is_symlink() or not root.is_dir():
        raise DomainBaseError(f"candidate is not a physical directory: {root}")
    manifest_path = root / "manifest.json"
    manifest = _object(manifest_path)
    manifest_sha = _sha256(manifest_path)
    if (
        (root / "READY").read_text(encoding="ascii").strip() != manifest_sha
        or manifest.get("kind") != _EXPECTED_KINDS[domain]
        or manifest.get("run_id") != run_id
        or manifest.get("input_plan_digest") != input_plan_digest
    ):
        raise DomainBaseError(f"candidate binding differs: {domain}")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise DomainBaseError(f"candidate files are invalid: {domain}")
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            raise DomainBaseError(f"candidate file row is invalid: {domain}")
        relative = _relative(raw.get("path"), "candidate.files.path").as_posix()
        source = root.joinpath(*PurePosixPath(relative).parts)
        if relative in seen or source.is_symlink() or not source.is_file():
            raise DomainBaseError(f"candidate file inventory differs: {domain}/{relative}")
        seen.add(relative)
        if source.stat().st_size != raw.get("size_bytes") or _sha256(source) != raw.get("sha256"):
            raise DomainBaseError(f"candidate file checksum differs: {domain}/{relative}")
        files.append(
            {
                "path": relative,
                "sha256": str(raw["sha256"]),
                "size_bytes": int(raw["size_bytes"]),
            }
        )
    physical = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    }
    if physical != seen | {"manifest.json", "READY"}:
        raise DomainBaseError(f"candidate physical inventory differs: {domain}")
    return manifest, manifest_sha, files


def verify_domain_candidate(
    root: Path,
    *,
    domain: str,
    run_id: str,
    input_plan_digest: str,
) -> DomainCandidateVerification:
    """Deeply verify one immutable domain candidate and its run binding."""

    if domain not in _EXPECTED_KINDS:
        raise DomainBaseError(f"unknown DWD candidate domain: {domain}")
    manifest, manifest_sha, files = _candidate(
        domain,
        root.resolve(),
        run_id=run_id,
        input_plan_digest=input_plan_digest,
    )
    raw_counts = manifest.get("row_counts")
    if not isinstance(raw_counts, Mapping):
        raise DomainBaseError(f"candidate row counts are invalid: {domain}")
    return DomainCandidateVerification(
        root.resolve(),
        manifest_sha,
        str(manifest["semantic_digest"]),
        {str(key): int(value) for key, value in raw_counts.items()},
        sum(str(raw["path"]).startswith("payload/") for raw in files),
    )


def _link(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise DomainBaseError(f"DWD Base path collision: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.link(source, destination, follow_symlinks=False)


def _base_files(root: Path) -> set[str]:
    return {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    }


def verify_domain_base(root: Path) -> DomainBaseResult:
    """Deeply verify one sealed domain-modeled DWD Base."""

    if root.is_symlink() or not root.is_dir():
        raise DomainBaseError("DWD Base must be a physical directory")
    manifest = _object(root / "manifest.json")
    manifest_sha = _sha256(root / "manifest.json")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "dwd_domain_base_package"
        or (root / "READY").read_text(encoding="ascii").strip() != manifest_sha
    ):
        raise DomainBaseError("DWD Base READY or manifest differs")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise DomainBaseError("DWD Base files are invalid")
    expected: set[str] = set()
    payload_bytes = 0
    payload_files = 0
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            raise DomainBaseError("DWD Base file row is invalid")
        relative = _relative(raw.get("path"), "files.path").as_posix()
        item = root.joinpath(*PurePosixPath(relative).parts)
        if relative in expected or item.is_symlink() or not item.is_file():
            raise DomainBaseError(f"DWD Base file inventory differs: {relative}")
        expected.add(relative)
        if item.stat().st_size != raw.get("size_bytes") or _sha256(item) != raw.get("sha256"):
            raise DomainBaseError(f"DWD Base file checksum differs: {relative}")
        if relative.startswith("payload/"):
            payload_bytes += int(raw["size_bytes"])
            payload_files += 1
    if _base_files(root) != expected | {"manifest.json", "READY"}:
        raise DomainBaseError("DWD Base physical file set differs")
    tables = manifest.get("row_counts")
    if not isinstance(tables, Mapping) or len(tables) != 33:
        raise DomainBaseError("DWD Base must contain exactly 33 tables")
    if payload_bytes != manifest.get("payload_bytes") or payload_files != manifest.get(
        "payload_file_count"
    ):
        raise DomainBaseError("DWD Base payload totals differ")
    return DomainBaseResult(
        root.resolve(),
        str(manifest["package_id"]),
        manifest_sha,
        str(manifest["content_digest"]),
        len(tables),
        sum(int(value) for value in tables.values()),
        payload_bytes,
        payload_files,
    )


def _current_descriptor(result: DomainBaseResult, *, run_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "dwd_domain_base_current",
        "package_id": result.package_id,
        "package_path": result.root.name,
        "manifest_sha256": result.manifest_sha256,
        "content_digest": result.content_digest,
        "run_id": run_id,
        "through_date": "2026-07-31",
    }


def _verify_package_binding(
    root: Path,
    *,
    package_id: str,
    run_id: str,
    input_plan_digest: str,
    ods_base: Mapping[str, str],
    model_sha256: str,
    model_extension_sha256: str,
    code_revision: str,
) -> DomainBaseResult:
    result = verify_domain_base(root)
    manifest = _object(root / "manifest.json")
    expected = {
        "package_id": package_id,
        "run_id": run_id,
        "input_plan_digest": input_plan_digest,
        "ods_base": dict(ods_base),
        "model_sha256": model_sha256,
        "model_extension_sha256": model_extension_sha256,
        "code_revision": code_revision,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise DomainBaseError("existing DWD Base candidate has a different identity")
    return result


def seal_domain_base_candidate(
    *,
    candidates: Mapping[str, Path],
    output: Path,
    package_id: str,
    run_id: str,
    input_plan_digest: str,
    ods_base: Mapping[str, str],
    model_sha256: str,
    model_extension_sha256: str,
    code_revision: str,
) -> DomainBaseResult:
    """Hard-link nine immutable candidates and seal a Base without publishing a pointer."""

    if set(candidates) != set(_EXPECTED_KINDS) or output.name != package_id:
        raise DomainBaseError("DWD Base publication arguments differ")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        return _verify_package_binding(
            output,
            package_id=package_id,
            run_id=run_id,
            input_plan_digest=input_plan_digest,
            ods_base=ods_base,
            model_sha256=model_sha256,
            model_extension_sha256=model_extension_sha256,
            code_revision=code_revision,
        )
    staging = output.with_name(f".{package_id}.staging")
    if staging.exists():
        shutil.rmtree(staging)
    upstream: dict[str, Any] = {}
    row_counts: dict[str, int] = {}
    base_files: list[dict[str, Any]] = []
    destinations: set[str] = set()
    try:
        for domain, root in sorted(candidates.items()):
            manifest, manifest_sha, files = _candidate(
                domain, root, run_id=run_id, input_plan_digest=input_plan_digest
            )
            candidate_tables = manifest.get("row_counts")
            if not isinstance(candidate_tables, Mapping):
                raise DomainBaseError(f"candidate row counts are invalid: {domain}")
            overlap = set(row_counts).intersection(candidate_tables)
            if overlap:
                raise DomainBaseError(f"DWD table ownership overlaps: {sorted(overlap)}")
            row_counts.update({str(key): int(value) for key, value in candidate_tables.items()})
            upstream[domain] = {
                "candidate_id": root.name,
                "candidate_kind": manifest["kind"],
                "manifest_sha256": manifest_sha,
                "semantic_digest": manifest["semantic_digest"],
                "row_counts": dict(sorted(candidate_tables.items())),
            }
            for raw in files:
                source_relative = str(raw["path"])
                destination = (
                    source_relative
                    if source_relative.startswith("payload/")
                    else f"evidence/domains/{domain}/{source_relative}"
                )
                if destination in destinations:
                    raise DomainBaseError(f"DWD Base path collision: {destination}")
                destinations.add(destination)
                _link(root / source_relative, staging / destination)
                base_files.append({**raw, "path": destination, "candidate_domain": domain})
            for name in ("manifest.json", "READY"):
                destination = f"evidence/domains/{domain}/{name}"
                source = root / name
                _link(source, staging / destination)
                base_files.append(
                    {
                        "path": destination,
                        "sha256": _sha256(source),
                        "size_bytes": source.stat().st_size,
                        "candidate_domain": domain,
                    }
                )
        if len(row_counts) != 33:
            raise DomainBaseError(f"DWD Base table count differs: {len(row_counts)}")
        base_files.sort(key=lambda raw: str(raw["path"]))
        payload = [raw for raw in base_files if str(raw["path"]).startswith("payload/")]
        identity = {
            "package_id": package_id,
            "run_id": run_id,
            "through_date": "2026-07-31",
            "input_plan_digest": input_plan_digest,
            "ods_base": dict(ods_base),
            "model_sha256": model_sha256,
            "model_extension_sha256": model_extension_sha256,
            "code_revision": code_revision,
            "upstream_candidates": upstream,
            "row_counts": dict(sorted(row_counts.items())),
        }
        manifest = {
            "schema_version": 1,
            "kind": "dwd_domain_base_package",
            **identity,
            "content_digest": _digest({**identity, "files": base_files}),
            "table_count": len(row_counts),
            "total_rows": sum(row_counts.values()),
            "payload_file_count": len(payload),
            "payload_bytes": sum(int(raw["size_bytes"]) for raw in payload),
            "files": base_files,
        }
        _write_json(staging / "manifest.json", manifest)
        ready = staging / "READY"
        with ready.open("x", encoding="ascii") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(_sha256(staging / "manifest.json") + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        for directory in sorted(
            (item for item in staging.rglob("*") if item.is_dir()), reverse=True
        ):
            directory.chmod(0o750)
            _fsync_directory(directory)
        staging.chmod(0o750)
        _fsync_directory(staging)
        os.replace(staging, output)
        _fsync_directory(output.parent)
        return _verify_package_binding(
            output,
            package_id=package_id,
            run_id=run_id,
            input_plan_digest=input_plan_digest,
            ods_base=ods_base,
            model_sha256=model_sha256,
            model_extension_sha256=model_extension_sha256,
            code_revision=code_revision,
        )
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def publish_domain_base(
    *,
    candidates: Mapping[str, Path],
    output: Path,
    current: Path,
    package_id: str,
    run_id: str,
    input_plan_digest: str,
    ods_base: Mapping[str, str],
    model_sha256: str,
    model_extension_sha256: str,
    code_revision: str,
) -> DomainBaseResult:
    """Seal one immutable Base candidate and publish its CURRENT descriptor."""

    result = seal_domain_base_candidate(
        candidates=candidates,
        output=output,
        package_id=package_id,
        run_id=run_id,
        input_plan_digest=input_plan_digest,
        ods_base=ods_base,
        model_sha256=model_sha256,
        model_extension_sha256=model_extension_sha256,
        code_revision=code_revision,
    )
    current.parent.mkdir(parents=True, exist_ok=True)
    descriptor = _current_descriptor(result, run_id=run_id)
    if current.exists():
        if _object(current) != descriptor:
            raise DomainBaseError("DWD Base CURRENT already points elsewhere")
        return result
    _write_json(current, descriptor)
    return result
