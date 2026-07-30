"""Portable historical base and lane-local logical history catalogs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.dual_lane import LaneCandidate, load_candidate_current


class DualLaneHistoryError(RuntimeError):
    """Raised when a historical base or logical lane history is invalid."""


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_LANES = ("cn01", "hermes")
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


@dataclass(frozen=True)
class HistoryBasePolicy:
    contract_id: str
    source_revision: str
    source_meta_sha256: str
    source_checksums_sha256: str
    required_payloads: tuple[str, ...]
    dwd_date_start: date
    dwd_date_end: date
    dws_contract: str
    dws_date_start: date
    dws_date_end: date
    lanes: tuple[str, ...]
    path: Path
    digest: str


@dataclass(frozen=True)
class HistoryBaseBundle:
    root: Path
    bundle_id: str
    manifest_sha256: str
    content_digest: str
    source_revision: str
    source_meta_sha256: str
    dwd_root: Path
    dws_root: Path
    dwd_date_start: date
    dwd_date_end: date
    dws_date_start: date
    dws_date_end: date
    dws_row_count: int


@dataclass(frozen=True)
class LaneHistoryCatalog:
    root: Path
    catalog_id: str
    lane_id: str
    manifest_sha256: str
    content_digest: str
    base: HistoryBaseBundle
    candidates: tuple[LaneCandidate, ...]
    through_date: date


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


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DualLaneHistoryError(f"cannot load JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise DualLaneHistoryError(f"JSON value must be an object: {path}")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise DualLaneHistoryError(f"{key} must be a non-empty string")
    return item


def _parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise DualLaneHistoryError(f"{field} must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DualLaneHistoryError(f"{field} must use YYYY-MM-DD") from exc


def _safe_relative(value: str, field: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() == "." or ".." in path.parts:
        raise DualLaneHistoryError(f"{field} must be a safe relative path")
    return path


def _inside(root: Path, value: str, field: str) -> Path:
    root = root.resolve()
    relative = _safe_relative(value, field)
    resolved = root.joinpath(*relative.parts).resolve()
    if root not in resolved.parents:
        raise DualLaneHistoryError(f"{field} escapes its storage root")
    return resolved


def _relative_inside(root: Path, path: Path, field: str) -> str:
    root = root.resolve()
    path = path.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise DualLaneHistoryError(f"{field} is outside the storage root") from exc
    return _safe_relative(relative.as_posix(), field).as_posix()


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
        raise DualLaneHistoryError("artifact files must be an array")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise DualLaneHistoryError("artifact file entry must be an object")
        relative = _safe_relative(_text(item, "path"), "files.path")
        name = relative.as_posix()
        if name in seen:
            raise DualLaneHistoryError(f"duplicate artifact file entry: {name}")
        seen.add(name)
        path = _inside(root, name, "files.path")
        size = item.get("size_bytes")
        if not isinstance(size, int) or size < 0 or not path.is_file():
            raise DualLaneHistoryError(f"artifact file is missing or invalid: {name}")
        if path.stat().st_size != size or _sha256(path) != item.get("sha256"):
            raise DualLaneHistoryError(f"artifact file integrity failed: {name}")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "READY"}
    }
    if actual != seen:
        raise DualLaneHistoryError("artifact manifest file set differs from disk")


def _seal(staging: Path, output: Path, manifest: Mapping[str, Any]) -> None:
    _write_json(staging / "manifest.json", manifest)
    (staging / "READY").write_text(
        f"{_sha256(staging / 'manifest.json')}\n",
        encoding="ascii",
    )
    for path in sorted(staging.rglob("*")):
        if path.is_symlink():
            raise DualLaneHistoryError("symbolic links are forbidden in history bundles")
        path.chmod(0o750 if path.is_dir() else 0o640)
    staging.chmod(0o750)
    os.replace(staging, output)


def _ready_manifest(root: Path) -> dict[str, Any]:
    manifest = root / "manifest.json"
    ready = root / "READY"
    if not manifest.is_file() or not ready.is_file():
        raise DualLaneHistoryError(f"artifact is missing manifest.json or READY: {root}")
    if ready.read_text(encoding="ascii").strip() != _sha256(manifest):
        raise DualLaneHistoryError(f"READY does not bind manifest.json: {root}")
    return _object(manifest)


def _checksum_ledger(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise DualLaneHistoryError(f"cannot read checksum ledger: {path}") from exc
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or re.fullmatch(r"[0-9a-f]{64}", parts[0]) is None:
            raise DualLaneHistoryError(f"invalid checksum ledger line: {line}")
        relative = _safe_relative(parts[1], "checksum path").as_posix()
        if relative in result:
            raise DualLaneHistoryError(f"duplicate checksum ledger path: {relative}")
        result[relative] = f"sha256:{parts[0]}"
    return result


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def load_history_base_policy(path: Path) -> HistoryBasePolicy:
    path = path.resolve()
    value = _object(path)
    source = value.get("source")
    dws = value.get("dws")
    merge = value.get("merge")
    retention = value.get("retention")
    if value.get("schema_version") != 1 or not all(
        isinstance(item, Mapping) for item in (source, dws, merge, retention)
    ):
        raise DualLaneHistoryError("unsupported historical-base policy")
    assert isinstance(source, Mapping)
    assert isinstance(dws, Mapping)
    assert isinstance(merge, Mapping)
    assert isinstance(retention, Mapping)
    required = source.get("required_payloads")
    metrics = dws.get("metric_columns")
    lanes = merge.get("lanes")
    if (
        not isinstance(required, list)
        or tuple(required) != tuple(sorted(required))
        or any(not isinstance(item, str) or not item for item in required)
        or tuple(metrics or ()) != _DWS_METRICS
        or tuple(lanes or ()) != _LANES
        or merge.get("daily_candidate_kind") != "dual_lane_dwd_dws_candidate"
        or merge.get("parent_chain") != "exact_manifest_v1"
        or merge.get("physical_merge") is not False
        or retention.get("migration_mode") != "copy_only"
        or retention.get("delete_enabled") is not False
        or retention.get("minimum_sealed_node_copies") != 2
        or dws.get("status_scope") != "observed_daily_status_keys"
        or dws.get("bar_scope") != "intersection_with_observed_daily_status_keys"
    ):
        raise DualLaneHistoryError("historical-base policy invariants are invalid")
    for field in ("meta_sha256", "checksums_sha256"):
        if _SHA256.fullmatch(_text(source, field)) is None:
            raise DualLaneHistoryError(f"source.{field} must be a SHA-256 digest")
    dwd_start = _parse_date(source.get("dwd_date_start"), "source.dwd_date_start")
    dwd_end = _parse_date(source.get("dwd_date_end"), "source.dwd_date_end")
    dws_start = _parse_date(dws.get("date_start"), "dws.date_start")
    dws_end = _parse_date(dws.get("date_end"), "dws.date_end")
    if not dwd_start <= dws_start <= dws_end == dwd_end:
        raise DualLaneHistoryError("historical DWD/DWS date boundaries are inconsistent")
    return HistoryBasePolicy(
        contract_id=_text(value, "contract_id"),
        source_revision=_text(source, "revision"),
        source_meta_sha256=_text(source, "meta_sha256"),
        source_checksums_sha256=_text(source, "checksums_sha256"),
        required_payloads=tuple(required),
        dwd_date_start=dwd_start,
        dwd_date_end=dwd_end,
        dws_contract=_text(dws, "contract"),
        dws_date_start=dws_start,
        dws_date_end=dws_end,
        lanes=tuple(lanes),
        path=path,
        digest=_sha256(path),
    )


def _verify_dwd_source(source: Path, policy: HistoryBasePolicy) -> dict[str, str]:
    source = source.resolve()
    if source.name != policy.source_revision or source.is_symlink():
        raise DualLaneHistoryError("historical DWD source revision is invalid")
    meta_path = source / "META.json"
    checksums_path = source / "CHECKSUMS.sha256"
    if _sha256(meta_path) != policy.source_meta_sha256:
        raise DualLaneHistoryError("historical DWD META checksum differs from policy")
    if _sha256(checksums_path) != policy.source_checksums_sha256:
        raise DualLaneHistoryError("historical DWD checksum-ledger digest differs from policy")
    meta = _object(meta_path)
    if meta.get("revision") != policy.source_revision:
        raise DualLaneHistoryError("historical DWD metadata revision differs from policy")
    ledger = _checksum_ledger(checksums_path)
    if tuple(sorted(ledger)) != policy.required_payloads:
        raise DualLaneHistoryError("historical DWD payload set differs from policy")
    declared = meta.get("checksums")
    if not isinstance(declared, Mapping) or dict(declared) != ledger:
        raise DualLaneHistoryError("historical DWD metadata and checksum ledger disagree")
    for relative, digest in ledger.items():
        path = source / relative
        if not path.is_file() or path.is_symlink() or _sha256(path) != digest:
            raise DualLaneHistoryError(f"historical DWD payload checksum mismatch: {relative}")
    actual = {path.name for path in source.iterdir() if path.is_file()}
    expected = set(policy.required_payloads) | {"META.json", "CHECKSUMS.sha256"}
    if actual != expected or any(path.is_dir() for path in source.iterdir()):
        raise DualLaneHistoryError("historical DWD source contains an unexpected file set")
    return ledger


def _build_dws_summary(
    *, dwd_root: Path, output: Path, policy: HistoryBasePolicy
) -> tuple[dict[str, Any], str]:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DualLaneHistoryError("P1 dependencies are missing; run make bootstrap-p1") from exc
    bars = dwd_root / "daily_ohlcv_none.parquet"
    statuses = dwd_root / "daily_isST.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(":memory:")
    try:
        bar_bounds = connection.execute(
            f"SELECT min(date), max(date), count(*) FROM read_parquet({_sql_string(str(bars))})"
        ).fetchone()
        status_bounds = connection.execute(
            f"SELECT min(date), max(date), count(*), count(DISTINCT (security_id, date)) "
            f"FROM read_parquet({_sql_string(str(statuses))})"
        ).fetchone()
        invalid_status = connection.execute(
            f"SELECT count(*) FROM read_parquet({_sql_string(str(statuses))}) "
            "WHERE tradestatus NOT IN ('0', '1') OR isST NOT IN ('0', '1')"
        ).fetchone()
        if (
            bar_bounds is None
            or status_bounds is None
            or bar_bounds[0] != policy.dwd_date_start
            or bar_bounds[1] != policy.dwd_date_end
            or status_bounds[0] != policy.dws_date_start
            or status_bounds[1] != policy.dws_date_end
            or status_bounds[2] != status_bounds[3]
            or invalid_status is None
            or invalid_status[0] != 0
        ):
            raise DualLaneHistoryError("historical DWD date, key, or status gates failed")
        query = f"""
            WITH status_rows AS (
                SELECT security_id, date AS market_date, isST, tradestatus
                FROM read_parquet({_sql_string(str(statuses))})
            ),
            scoped_bars AS (
                SELECT b.*
                FROM read_parquet({_sql_string(str(bars))}) b
                INNER JOIN status_rows s
                  ON b.security_id = s.security_id AND b.date = s.market_date
            ),
            status_agg AS (
                SELECT
                    market_date,
                    count(*)::BIGINT AS security_count,
                    count(*) FILTER (WHERE tradestatus = '1')::BIGINT
                      AS tradable_security_count,
                    count(*) FILTER (WHERE tradestatus = '0')::BIGINT
                      AS suspended_security_count,
                    count(*) FILTER (WHERE isST = '1')::BIGINT AS st_security_count
                FROM status_rows
                GROUP BY market_date
            ),
            bar_agg AS (
                SELECT
                    date AS market_date,
                    count(*)::BIGINT AS bar_security_count,
                    count(*) FILTER (WHERE pct_change > 0)::BIGINT AS advanced_security_count,
                    count(*) FILTER (WHERE pct_change < 0)::BIGINT AS declined_security_count,
                    count(*) FILTER (WHERE pct_change = 0)::BIGINT AS flat_security_count,
                    count(*) FILTER (WHERE pct_change IS NULL)::BIGINT
                      AS missing_pct_change_security_count,
                    coalesce(sum(CAST(volume AS DECIMAL(38, 6))), 0)::DOUBLE AS total_volume,
                    coalesce(sum(CAST(amount AS DECIMAL(38, 6))), 0)::DOUBLE AS total_amount
                FROM scoped_bars
                GROUP BY date
            )
            SELECT
                s.market_date,
                {_sql_string(policy.source_revision)} AS dwd_revision,
                s.security_count,
                s.tradable_security_count,
                s.suspended_security_count,
                s.st_security_count,
                coalesce(b.bar_security_count, 0)::BIGINT AS bar_security_count,
                coalesce(b.advanced_security_count, 0)::BIGINT AS advanced_security_count,
                coalesce(b.declined_security_count, 0)::BIGINT AS declined_security_count,
                coalesce(b.flat_security_count, 0)::BIGINT AS flat_security_count,
                coalesce(b.missing_pct_change_security_count, 0)::BIGINT
                  AS missing_pct_change_security_count,
                coalesce(b.total_volume, 0)::DOUBLE AS total_volume,
                coalesce(b.total_amount, 0)::DOUBLE AS total_amount
            FROM status_agg s
            LEFT JOIN bar_agg b USING (market_date)
            ORDER BY s.market_date
        """
        connection.execute(
            f"COPY ({query}) TO {_sql_string(str(output))} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        audit = connection.execute(
            f"""
            SELECT
              count(*), min(market_date), max(market_date),
              count(*) FILTER (
                WHERE security_count <= 0
                   OR tradable_security_count + suspended_security_count != security_count
                   OR st_security_count < 0 OR st_security_count > security_count
                   OR bar_security_count < 0 OR bar_security_count > security_count
                   OR advanced_security_count + declined_security_count + flat_security_count
                      + missing_pct_change_security_count != bar_security_count
                   OR total_volume < 0 OR total_amount < 0
              )
            FROM read_parquet({_sql_string(str(output))})
            """
        ).fetchone()
        if (
            audit is None
            or audit[0] <= 0
            or audit[1] != policy.dws_date_start
            or audit[2] != policy.dws_date_end
            or audit[3] != 0
        ):
            raise DualLaneHistoryError("historical DWS aggregate invariants failed")
        schema = connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet({_sql_string(str(output))})"
        ).fetchall()
    finally:
        connection.close()
    metadata = {
        "schema_version": 1,
        "kind": "dual_lane_historical_dws",
        "revision": f"historical_market_summary_{policy.source_revision}_v1",
        "contract": policy.dws_contract,
        "date_start": policy.dws_date_start.isoformat(),
        "date_end": policy.dws_date_end.isoformat(),
        "row_count": int(audit[0]),
        "dwd_lineage": {
            "revision": policy.source_revision,
            "meta_sha256": policy.source_meta_sha256,
            "metadata_path": f"dwd/{policy.source_revision}/META.json",
        },
        "status_scope": "observed_daily_status_keys",
        "bar_scope": "intersection_with_observed_daily_status_keys",
        "metric_columns": list(_DWS_METRICS),
        "schema": [{"name": str(row[0]), "type": str(row[1])} for row in schema],
        "checksums": {"daily_market_summary.parquet": _sha256(output)},
    }
    return metadata, _sha256(output)


def build_history_base_bundle(
    *,
    source: Path,
    output: Path,
    bundle_id: str,
    policy: HistoryBasePolicy,
    code_revision: str,
) -> HistoryBaseBundle:
    if _SAFE_ID.fullmatch(bundle_id) is None:
        raise DualLaneHistoryError("history bundle id contains unsupported characters")
    if re.fullmatch(r"[0-9a-f]{40}", code_revision) is None:
        raise DualLaneHistoryError("code_revision must be a full Git object id")
    output = output.resolve()
    if output.name != bundle_id:
        raise DualLaneHistoryError("history bundle output must end with bundle_id")
    if output.exists():
        existing = verify_history_base_bundle(output, policy=policy)
        if existing.bundle_id != bundle_id:
            raise DualLaneHistoryError("existing history bundle differs from request")
        return existing
    _verify_dwd_source(source, policy)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{bundle_id}.", dir=output.parent))
    try:
        dwd_target = staging / "dwd" / policy.source_revision
        dwd_target.parent.mkdir()
        shutil.copytree(source.resolve(), dwd_target)
        _verify_dwd_source(dwd_target, policy)
        dws_target = staging / "dws"
        dws_target.mkdir()
        summary_path = dws_target / "daily_market_summary.parquet"
        dws_manifest, dws_digest = _build_dws_summary(
            dwd_root=dwd_target,
            output=summary_path,
            policy=policy,
        )
        _write_json(dws_target / "manifest.json", dws_manifest)
        (dws_target / "CHECKSUMS.sha256").write_text(
            f"{dws_digest.removeprefix('sha256:')}  daily_market_summary.parquet\n",
            encoding="ascii",
        )
        content_value = {
            "contract_id": policy.contract_id,
            "source_revision": policy.source_revision,
            "source_meta_sha256": policy.source_meta_sha256,
            "source_checksums_sha256": policy.source_checksums_sha256,
            "dws_manifest_sha256": _sha256(dws_target / "manifest.json"),
            "dws_summary_sha256": dws_digest,
            "dwd_date_start": policy.dwd_date_start.isoformat(),
            "dwd_date_end": policy.dwd_date_end.isoformat(),
            "dws_date_start": policy.dws_date_start.isoformat(),
            "dws_date_end": policy.dws_date_end.isoformat(),
            "dws_row_count": dws_manifest["row_count"],
        }
        manifest = {
            "schema_version": 1,
            "kind": "dual_lane_history_base_bundle",
            "bundle_id": bundle_id,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "code_revision": code_revision,
            "contract_id": policy.contract_id,
            "policy_sha256": policy.digest,
            "content_digest": _canonical_digest(content_value),
            "source": {
                "revision": policy.source_revision,
                "path": f"dwd/{policy.source_revision}",
                "meta_sha256": policy.source_meta_sha256,
                "checksums_sha256": policy.source_checksums_sha256,
            },
            "dwd_boundary": {
                "date_start": policy.dwd_date_start.isoformat(),
                "date_end": policy.dwd_date_end.isoformat(),
            },
            "dws": {
                "path": "dws",
                "manifest_sha256": _sha256(dws_target / "manifest.json"),
                "summary_sha256": dws_digest,
                "date_start": policy.dws_date_start.isoformat(),
                "date_end": policy.dws_date_end.isoformat(),
                "row_count": dws_manifest["row_count"],
            },
            "authority": {
                "historical_seed": True,
                "independent_lane_evidence": False,
                "execution_authorized": False,
            },
            "files": _manifest_files(staging),
        }
        _seal(staging, output, manifest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_history_base_bundle(output, policy=policy)


def _verify_dws_bundle(
    root: Path, value: Mapping[str, Any], policy: HistoryBasePolicy | None
) -> tuple[Path, date, date, int, str]:
    dws_root = _inside(root, _text(value, "path"), "dws.path")
    manifest_path = dws_root / "manifest.json"
    manifest = _object(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "dual_lane_historical_dws":
        raise DualLaneHistoryError("unsupported historical DWS manifest")
    if _sha256(manifest_path) != value.get("manifest_sha256"):
        raise DualLaneHistoryError("historical DWS manifest checksum mismatch")
    summary = dws_root / "daily_market_summary.parquet"
    summary_digest = _sha256(summary)
    if summary_digest != value.get("summary_sha256"):
        raise DualLaneHistoryError("historical DWS summary checksum mismatch")
    ledger = _checksum_ledger(dws_root / "CHECKSUMS.sha256")
    if ledger != {"daily_market_summary.parquet": summary_digest}:
        raise DualLaneHistoryError("historical DWS checksum ledger is invalid")
    date_start = _parse_date(manifest.get("date_start"), "DWS date_start")
    date_end = _parse_date(manifest.get("date_end"), "DWS date_end")
    row_count = manifest.get("row_count")
    if not isinstance(row_count, int) or row_count <= 0:
        raise DualLaneHistoryError("historical DWS row_count is invalid")
    lineage = manifest.get("dwd_lineage")
    if not isinstance(lineage, Mapping):
        raise DualLaneHistoryError("historical DWS has no DWD lineage")
    if policy is not None and (
        manifest.get("contract") != policy.dws_contract
        or date_start != policy.dws_date_start
        or date_end != policy.dws_date_end
        or lineage.get("revision") != policy.source_revision
        or lineage.get("meta_sha256") != policy.source_meta_sha256
        or tuple(manifest.get("metric_columns", ())) != _DWS_METRICS
    ):
        raise DualLaneHistoryError("historical DWS differs from active policy")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DualLaneHistoryError("P1 dependencies are missing; run make bootstrap-p1") from exc
    connection = duckdb.connect(":memory:")
    try:
        audit = connection.execute(
            f"""
            SELECT count(*), min(market_date), max(market_date), count(*) FILTER (
              WHERE security_count <= 0
                 OR tradable_security_count + suspended_security_count != security_count
                 OR st_security_count < 0 OR st_security_count > security_count
                 OR bar_security_count < 0 OR bar_security_count > security_count
                 OR advanced_security_count + declined_security_count + flat_security_count
                    + missing_pct_change_security_count != bar_security_count
                 OR total_volume < 0 OR total_amount < 0
            ) FROM read_parquet({_sql_string(str(summary))})
            """
        ).fetchone()
        columns = connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet({_sql_string(str(summary))})"
        ).fetchall()
    finally:
        connection.close()
    expected_columns = ["market_date", "dwd_revision", *_DWS_METRICS]
    if (
        audit is None
        or audit != (row_count, date_start, date_end, 0)
        or [str(row[0]) for row in columns] != expected_columns
    ):
        raise DualLaneHistoryError("historical DWS data audit failed")
    return dws_root, date_start, date_end, row_count, summary_digest


def verify_history_base_bundle(
    root: Path, *, policy: HistoryBasePolicy | None = None
) -> HistoryBaseBundle:
    root = root.resolve()
    manifest = _ready_manifest(root)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "dual_lane_history_base_bundle"
        or re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("code_revision", ""))) is None
    ):
        raise DualLaneHistoryError("unsupported history-base bundle")
    _verify_files(root, manifest.get("files"))
    bundle_id = _text(manifest, "bundle_id")
    if _SAFE_ID.fullmatch(bundle_id) is None or root.name != bundle_id:
        raise DualLaneHistoryError("history bundle identity differs from its directory")
    source = manifest.get("source")
    dwd_boundary = manifest.get("dwd_boundary")
    dws = manifest.get("dws")
    authority = manifest.get("authority")
    if not all(isinstance(item, Mapping) for item in (source, dwd_boundary, dws, authority)):
        raise DualLaneHistoryError("history bundle sections are incomplete")
    assert isinstance(source, Mapping)
    assert isinstance(dwd_boundary, Mapping)
    assert isinstance(dws, Mapping)
    assert isinstance(authority, Mapping)
    dwd_root = _inside(root, _text(source, "path"), "source.path")
    if policy is not None:
        _verify_dwd_source(dwd_root, policy)
        if (
            manifest.get("contract_id") != policy.contract_id
            or manifest.get("policy_sha256") != policy.digest
        ):
            raise DualLaneHistoryError("history bundle policy identity mismatch")
    source_revision = _text(source, "revision")
    source_meta = _text(source, "meta_sha256")
    dwd_start = _parse_date(dwd_boundary.get("date_start"), "DWD date_start")
    dwd_end = _parse_date(dwd_boundary.get("date_end"), "DWD date_end")
    dws_root, dws_start, dws_end, dws_rows, dws_digest = _verify_dws_bundle(root, dws, policy)
    content_value = {
        "contract_id": manifest.get("contract_id"),
        "source_revision": source_revision,
        "source_meta_sha256": source_meta,
        "source_checksums_sha256": source.get("checksums_sha256"),
        "dws_manifest_sha256": dws.get("manifest_sha256"),
        "dws_summary_sha256": dws_digest,
        "dwd_date_start": dwd_start.isoformat(),
        "dwd_date_end": dwd_end.isoformat(),
        "dws_date_start": dws_start.isoformat(),
        "dws_date_end": dws_end.isoformat(),
        "dws_row_count": dws_rows,
    }
    if _canonical_digest(content_value) != manifest.get("content_digest"):
        raise DualLaneHistoryError("history bundle content digest mismatch")
    if dict(authority) != {
        "execution_authorized": False,
        "historical_seed": True,
        "independent_lane_evidence": False,
    }:
        raise DualLaneHistoryError("history bundle authority boundary is invalid")
    return HistoryBaseBundle(
        root=root,
        bundle_id=bundle_id,
        manifest_sha256=_sha256(root / "manifest.json"),
        content_digest=_text(manifest, "content_digest"),
        source_revision=source_revision,
        source_meta_sha256=source_meta,
        dwd_root=dwd_root,
        dws_root=dws_root,
        dwd_date_start=dwd_start,
        dwd_date_end=dwd_end,
        dws_date_start=dws_start,
        dws_date_end=dws_end,
        dws_row_count=dws_rows,
    )


def _apply_permissions(root: Path, *, owner_uid: int, group_gid: int) -> None:
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_symlink():
            raise DualLaneHistoryError("symbolic links are forbidden in sealed history roots")
        os.chown(path, owner_uid, group_gid)
        path.chmod(0o750 if path.is_dir() else 0o640)


def promote_history_base_bundle(
    *,
    ingress_bundle: Path,
    base_root: Path,
    current_pointer: Path,
    policy: HistoryBasePolicy,
    owner_uid: int | None = None,
    group_gid: int | None = None,
) -> HistoryBaseBundle:
    source = verify_history_base_bundle(ingress_bundle, policy=policy)
    base_root = base_root.resolve()
    base_root.mkdir(parents=True, exist_ok=True)
    destination = base_root / source.bundle_id
    if destination.exists():
        promoted = verify_history_base_bundle(destination, policy=policy)
        if promoted.manifest_sha256 != source.manifest_sha256:
            raise DualLaneHistoryError("sealed history base differs from ingress")
    else:
        staging_parent = Path(tempfile.mkdtemp(prefix=f".{source.bundle_id}.", dir=base_root))
        staging = staging_parent / source.bundle_id
        try:
            shutil.copytree(source.root, staging)
            copied = verify_history_base_bundle(staging, policy=policy)
            if copied.manifest_sha256 != source.manifest_sha256:
                raise DualLaneHistoryError("copied history base differs from ingress")
            if owner_uid is not None and group_gid is not None:
                _apply_permissions(staging, owner_uid=owner_uid, group_gid=group_gid)
            os.replace(staging, destination)
            staging_parent.rmdir()
        except BaseException:
            shutil.rmtree(staging_parent, ignore_errors=True)
            raise
        promoted = verify_history_base_bundle(destination, policy=policy)
    relative = os.path.relpath(promoted.root, start=current_pointer.resolve().parent)
    _atomic_json(
        current_pointer.resolve(),
        {
            "schema_version": 1,
            "bundle_id": promoted.bundle_id,
            "bundle_path": Path(relative).as_posix(),
            "manifest_sha256": promoted.manifest_sha256,
            "content_digest": promoted.content_digest,
        },
    )
    if owner_uid is not None and group_gid is not None:
        os.chown(current_pointer.resolve(), owner_uid, group_gid)
    return promoted


def load_history_base_current(
    pointer: Path, *, policy: HistoryBasePolicy | None = None
) -> HistoryBaseBundle:
    pointer = pointer.resolve()
    value = _object(pointer)
    path = Path(_text(value, "bundle_path"))
    if not path.is_absolute():
        path = pointer.parent / path
    bundle = verify_history_base_bundle(path, policy=policy)
    if (
        bundle.bundle_id != value.get("bundle_id")
        or bundle.manifest_sha256 != value.get("manifest_sha256")
        or bundle.content_digest != value.get("content_digest")
    ):
        raise DualLaneHistoryError("history-base CURRENT differs from its bundle")
    return bundle


def _candidate_chain(
    *, candidate_root: Path, lane_id: str, base: HistoryBaseBundle
) -> tuple[LaneCandidate, ...]:
    if lane_id not in _LANES:
        raise DualLaneHistoryError("lane_id must be cn01 or hermes")
    candidates: list[LaneCandidate] = []
    for date_root in sorted(candidate_root.resolve().iterdir()):
        if not date_root.is_dir() or re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_root.name) is None:
            continue
        pointer = date_root / "CURRENT.json"
        if not pointer.is_file():
            continue
        candidate = load_candidate_current(pointer)
        if candidate.lane_id != lane_id or candidate.market_date <= base.dwd_date_end:
            continue
        candidates.append(candidate)
    if not candidates:
        raise DualLaneHistoryError("lane history has no post-base candidate")
    previous: LaneCandidate | None = None
    for candidate in candidates:
        dwd_manifest = _object(candidate.dwd_root / "manifest.json")
        lineage = dwd_manifest.get("lineage")
        parent = lineage.get("parent") if isinstance(lineage, Mapping) else None
        if not isinstance(parent, Mapping):
            raise DualLaneHistoryError("candidate DWD has no parent lineage")
        if previous is None:
            if dict(parent) != {
                "kind": "historical_base",
                "manifest_sha256": base.source_meta_sha256,
                "revision": base.source_revision,
            }:
                raise DualLaneHistoryError("first candidate does not bind the sealed history base")
        else:
            previous_manifest = _object(previous.dwd_root / "manifest.json")
            expected = {
                "kind": "daily_partition",
                "manifest_sha256": _sha256(previous.dwd_root / "manifest.json"),
                "revision": previous_manifest.get("revision"),
            }
            if dict(parent) != expected:
                raise DualLaneHistoryError("candidate DWD parent chain is discontinuous")
        previous = candidate
    return tuple(candidates)


def _catalog_content(
    *,
    storage_root: Path,
    lane_id: str,
    base: HistoryBaseBundle,
    candidates: tuple[LaneCandidate, ...],
) -> dict[str, Any]:
    return {
        "lane_id": lane_id,
        "base": {
            "bundle_id": base.bundle_id,
            "path": _relative_inside(storage_root, base.root, "base.path"),
            "manifest_sha256": base.manifest_sha256,
            "content_digest": base.content_digest,
        },
        "daily_partitions": [
            {
                "market_date": candidate.market_date.isoformat(),
                "candidate_id": candidate.candidate_id,
                "path": _relative_inside(storage_root, candidate.root, "candidate.path"),
                "manifest_sha256": candidate.manifest_sha256,
                "dwd_manifest_sha256": _sha256(candidate.dwd_root / "manifest.json"),
                "dws_manifest_sha256": _sha256(candidate.dws_root / "manifest.json"),
            }
            for candidate in candidates
        ],
        "boundaries": {
            "dwd_date_start": base.dwd_date_start.isoformat(),
            "dws_date_start": base.dws_date_start.isoformat(),
            "through_date": candidates[-1].market_date.isoformat(),
        },
    }


def build_lane_history_catalog(
    *,
    storage_root: Path,
    base_current: Path,
    candidate_root: Path,
    catalog_root: Path,
    lane_id: str,
    policy: HistoryBasePolicy,
    owner_uid: int | None = None,
    group_gid: int | None = None,
) -> LaneHistoryCatalog:
    storage_root = storage_root.resolve()
    base = load_history_base_current(base_current, policy=policy)
    candidates = _candidate_chain(candidate_root=candidate_root, lane_id=lane_id, base=base)
    content = _catalog_content(
        storage_root=storage_root,
        lane_id=lane_id,
        base=base,
        candidates=candidates,
    )
    content_digest = _canonical_digest(content)
    catalog_id = (
        f"lane-history-{lane_id}-{candidates[-1].market_date.strftime('%Y%m%d')}-"
        f"{content_digest.removeprefix('sha256:')[:16]}"
    )
    lane_root = catalog_root.resolve() / lane_id
    output = lane_root / catalog_id
    if output.exists():
        catalog = verify_lane_history_catalog(output, storage_root=storage_root, policy=policy)
        if catalog.content_digest != content_digest:
            raise DualLaneHistoryError("existing lane-history catalog differs from request")
    else:
        lane_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{catalog_id}.", dir=lane_root))
        try:
            manifest = {
                "schema_version": 1,
                "kind": "dual_lane_logical_history_catalog",
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
        catalog = verify_lane_history_catalog(output, storage_root=storage_root, policy=policy)
    current = lane_root / "CURRENT.json"
    relative = os.path.relpath(catalog.root, start=current.parent)
    _atomic_json(
        current,
        {
            "schema_version": 1,
            "lane_id": lane_id,
            "catalog_id": catalog.catalog_id,
            "catalog_path": Path(relative).as_posix(),
            "manifest_sha256": catalog.manifest_sha256,
            "content_digest": catalog.content_digest,
            "through_date": catalog.through_date.isoformat(),
        },
    )
    if owner_uid is not None and group_gid is not None:
        os.chown(current, owner_uid, group_gid)
    return catalog


def verify_lane_history_catalog(
    root: Path, *, storage_root: Path, policy: HistoryBasePolicy
) -> LaneHistoryCatalog:
    root = root.resolve()
    storage_root = storage_root.resolve()
    manifest = _ready_manifest(root)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "dual_lane_logical_history_catalog"
        or manifest.get("physical_merge") is not False
        or manifest.get("contract_id") != policy.contract_id
        or manifest.get("policy_sha256") != policy.digest
    ):
        raise DualLaneHistoryError("unsupported lane-history catalog")
    _verify_files(root, manifest.get("files"))
    catalog_id = _text(manifest, "catalog_id")
    lane_id = _text(manifest, "lane_id")
    if root.name != catalog_id or lane_id not in _LANES:
        raise DualLaneHistoryError("lane-history catalog identity is invalid")
    base_value = manifest.get("base")
    entries = manifest.get("daily_partitions")
    if not isinstance(base_value, Mapping) or not isinstance(entries, list) or not entries:
        raise DualLaneHistoryError("lane-history catalog content is incomplete")
    base = verify_history_base_bundle(
        _inside(storage_root, _text(base_value, "path"), "base.path"),
        policy=policy,
    )
    if (
        base.bundle_id != base_value.get("bundle_id")
        or base.manifest_sha256 != base_value.get("manifest_sha256")
        or base.content_digest != base_value.get("content_digest")
    ):
        raise DualLaneHistoryError("lane-history base identity mismatch")
    candidates: list[LaneCandidate] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise DualLaneHistoryError("lane-history candidate entry must be an object")
        candidate = load_candidate_current_from_catalog(
            storage_root=storage_root,
            path=_text(entry, "path"),
            manifest_sha256=_text(entry, "manifest_sha256"),
        )
        if (
            candidate.lane_id != lane_id
            or candidate.candidate_id != entry.get("candidate_id")
            or candidate.market_date.isoformat() != entry.get("market_date")
            or _sha256(candidate.dwd_root / "manifest.json") != entry.get("dwd_manifest_sha256")
            or _sha256(candidate.dws_root / "manifest.json") != entry.get("dws_manifest_sha256")
        ):
            raise DualLaneHistoryError("lane-history candidate identity mismatch")
        candidates.append(candidate)
    chain = _candidate_chain_from_values(candidates=tuple(candidates), base=base)
    content = _catalog_content(
        storage_root=storage_root,
        lane_id=lane_id,
        base=base,
        candidates=chain,
    )
    if _canonical_digest(content) != manifest.get("content_digest"):
        raise DualLaneHistoryError("lane-history catalog content digest mismatch")
    return LaneHistoryCatalog(
        root=root,
        catalog_id=catalog_id,
        lane_id=lane_id,
        manifest_sha256=_sha256(root / "manifest.json"),
        content_digest=_text(manifest, "content_digest"),
        base=base,
        candidates=chain,
        through_date=chain[-1].market_date,
    )


def load_candidate_current_from_catalog(
    *, storage_root: Path, path: str, manifest_sha256: str
) -> LaneCandidate:
    from quanthus.pipelines.dual_lane import verify_lane_candidate

    candidate = verify_lane_candidate(_inside(storage_root, path, "candidate.path"))
    if candidate.manifest_sha256 != manifest_sha256:
        raise DualLaneHistoryError("catalog candidate manifest checksum mismatch")
    return candidate


def _candidate_chain_from_values(
    *, candidates: tuple[LaneCandidate, ...], base: HistoryBaseBundle
) -> tuple[LaneCandidate, ...]:
    if tuple(sorted(candidate.market_date for candidate in candidates)) != tuple(
        candidate.market_date for candidate in candidates
    ):
        raise DualLaneHistoryError("lane-history candidates are not date ordered")
    previous: LaneCandidate | None = None
    for candidate in candidates:
        manifest = _object(candidate.dwd_root / "manifest.json")
        lineage = manifest.get("lineage")
        parent = lineage.get("parent") if isinstance(lineage, Mapping) else None
        if not isinstance(parent, Mapping):
            raise DualLaneHistoryError("candidate DWD has no parent lineage")
        if previous is None:
            expected = {
                "kind": "historical_base",
                "manifest_sha256": base.source_meta_sha256,
                "revision": base.source_revision,
            }
        else:
            prior_manifest = _object(previous.dwd_root / "manifest.json")
            expected = {
                "kind": "daily_partition",
                "manifest_sha256": _sha256(previous.dwd_root / "manifest.json"),
                "revision": prior_manifest.get("revision"),
            }
        if dict(parent) != expected:
            raise DualLaneHistoryError("lane-history candidate parent chain is discontinuous")
        previous = candidate
    return candidates


def load_lane_history_current(
    pointer: Path, *, storage_root: Path, policy: HistoryBasePolicy
) -> LaneHistoryCatalog:
    pointer = pointer.resolve()
    value = _object(pointer)
    path = Path(_text(value, "catalog_path"))
    if not path.is_absolute():
        path = pointer.parent / path
    catalog = verify_lane_history_catalog(path, storage_root=storage_root, policy=policy)
    if (
        catalog.catalog_id != value.get("catalog_id")
        or catalog.lane_id != value.get("lane_id")
        or catalog.manifest_sha256 != value.get("manifest_sha256")
        or catalog.content_digest != value.get("content_digest")
        or catalog.through_date.isoformat() != value.get("through_date")
    ):
        raise DualLaneHistoryError("lane-history CURRENT differs from its catalog")
    return catalog
