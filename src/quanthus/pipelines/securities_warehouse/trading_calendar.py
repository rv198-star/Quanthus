"""Build the trading-calendar backbone from sealed ODS and same-run venue inputs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse.listed_company import (
    ListedCompanyBuildError,
    verify_listed_company_candidate,
)


class TradingCalendarBuildError(RuntimeError):
    """Raised when a calendar candidate cannot be built without ambiguity."""


@dataclass(frozen=True)
class TradingCalendarBuildResult:
    root: Path
    semantic_digest: str
    row_count: int
    year_count: int
    selected_source: str


_SHA256 = "sha256:"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_OWNED_TABLE = "dwd_trading_calendar"
_PLAN_FILES = {
    "capacity-estimate.json",
    "coverage-matrix.json",
    "input-plan.json",
    "run-receipt.json",
    "source-disposition-ledger.json",
}
_MIC_TO_EXCHANGE = {"XSHG": "SSE", "XSHE": "SZSE", "BJSE": "BSE"}
_SESSION_TYPE = "REGULAR_DAY"
_CALENDAR_COLUMNS = {
    "calendar_session_id": "string",
    "venue_id": "string",
    "trade_date": "date32",
    "session_type": "string",
    "is_open": "bool",
    "open_at": "timestamp_us_utc?",
    "close_at": "timestamp_us_utc?",
    "calendar_reason": "string?",
}


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TradingCalendarBuildError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise TradingCalendarBuildError(f"JSON root must be an object: {path}")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TradingCalendarBuildError(f"{field} must be an object")
    return value


def _sequence(value: object, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise TradingCalendarBuildError(f"{field} must be an array")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise TradingCalendarBuildError(f"{key} must be a non-empty string")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise TradingCalendarBuildError(f"cannot hash file: {path}") from exc
    return f"sha256:{digest.hexdigest()}"


def _normalize(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    return value


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        _normalize(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TradingCalendarBuildError(
                f"{field} must be an ISO-8601 timestamp"
            ) from exc
    else:
        raise TradingCalendarBuildError(f"{field} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TradingCalendarBuildError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date_value(value: object, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise TradingCalendarBuildError(f"{field} must be a date") from exc
    raise TradingCalendarBuildError(f"{field} must be a date")


def _safe_file(root: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise TradingCalendarBuildError("source path must be a relative string")
    path = PurePosixPath(relative)
    if path.is_absolute() or path.as_posix() == "." or ".." in path.parts:
        raise TradingCalendarBuildError(f"unsafe source path: {relative}")
    resolved = root.joinpath(*path.parts).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise TradingCalendarBuildError(f"source path escapes package: {relative}")
    return resolved


def _stable_id(kind: str, *parts: str) -> str:
    payload = "\x1f".join(("securities-warehouse-v1", kind, *parts)).encode("utf-8")
    return f"{kind}_{hashlib.sha256(payload).hexdigest()[:32]}"


def _verify_manifest_package(
    root: Path,
    *,
    expected_files: set[str],
    expected_kind: str,
) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise TradingCalendarBuildError("package must be a physical directory")
    manifest_path = root / "manifest.json"
    manifest = _object(manifest_path)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise TradingCalendarBuildError("package READY is unreadable") from exc
    if ready != _sha256(manifest_path):
        raise TradingCalendarBuildError("package READY does not match manifest")
    if manifest.get("schema_version") != 1 or manifest.get("kind") != expected_kind:
        raise TradingCalendarBuildError("package manifest has an unsupported contract")
    declared: set[str] = set()
    for raw in _sequence(manifest.get("files"), "manifest files"):
        item = _mapping(raw, "manifest file")
        relative = _text(item, "path")
        if relative in declared:
            raise TradingCalendarBuildError("package manifest has duplicate files")
        declared.add(relative)
        path = _safe_file(root, relative)
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item.get("size_bytes")
            or _sha256(path) != item.get("sha256")
        ):
            raise TradingCalendarBuildError(
                f"package file failed verification: {relative}"
            )
    if declared != expected_files:
        raise TradingCalendarBuildError("package manifest file inventory is not exact")
    physical = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if physical != expected_files | {"manifest.json", "READY"}:
        raise TradingCalendarBuildError("package physical file inventory is not exact")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise TradingCalendarBuildError("package contains a symbolic link")
    expected_directories = {
        parent.as_posix()
        for relative in expected_files
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    physical_directories = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_dir() and not path.is_symlink()
    }
    if physical_directories != expected_directories:
        raise TradingCalendarBuildError("package physical directory inventory is not exact")
    return manifest


def _verify_plan_package(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _verify_manifest_package(
        root,
        expected_files=_PLAN_FILES,
        expected_kind="dwd_dws_input_plan_package",
    )
    plan = _object(root / "input-plan.json")
    ledger = _object(root / "source-disposition-ledger.json")
    receipt = _object(root / "run-receipt.json")
    if (
        plan.get("schema_version") != 1
        or plan.get("kind") != "dwd_dws_input_plan"
        or ledger.get("schema_version") != 1
    ):
        raise TradingCalendarBuildError("input plan has an unsupported contract")
    semantic_plan = _mapping(plan.get("plan"), "input plan")
    model_identity = _mapping(semantic_plan.get("model"), "input plan model")
    input_identity = _mapping(semantic_plan.get("input"), "input plan ODS identity")
    if (
        manifest.get("plan_digest") != plan.get("plan_digest")
        or plan.get("plan_digest") != _canonical_digest(semantic_plan)
        or semantic_plan.get("source_ledger_digest") != _canonical_digest(ledger)
        or receipt.get("schema_version") != 1
        or receipt.get("kind") != "dwd_dws_input_plan_receipt"
        or receipt.get("status") != "planned"
        or receipt.get("run_id") != plan.get("run_id")
        or receipt.get("semantic_digest") != plan.get("plan_digest")
        or receipt.get("schema_digest") != model_identity.get("sha256")
        or receipt.get("input_manifest_sha256")
        != input_identity.get("manifest_sha256")
    ):
        raise TradingCalendarBuildError("input plan package identity is inconsistent")
    return plan, ledger


def _verify_source(record: Mapping[str, Any], ods_root: Path) -> Path:
    path = _safe_file(ods_root, record.get("path"))
    if (
        path.is_symlink()
        or not path.is_file()
        or not isinstance(record.get("size_bytes"), int)
        or path.stat().st_size != record.get("size_bytes")
        or _sha256(path) != record.get("sha256")
    ):
        raise TradingCalendarBuildError(
            f"ODS source failed verification: {record.get('path')}"
        )
    return path


def _calendar_schema() -> Any:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise TradingCalendarBuildError("pyarrow is required for DWD construction") from exc
    return pa.schema(
        [
            pa.field("record_source", pa.string(), nullable=False),
            pa.field("source_record_id", pa.string(), nullable=False),
            pa.field("source_revision", pa.string(), nullable=False),
            pa.field("source_artifact_sha256", pa.string(), nullable=False),
            pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("knowledge_to", pa.timestamp("us", tz="UTC"), nullable=True),
            pa.field("observation_start", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("observation_end", pa.timestamp("us", tz="UTC"), nullable=True),
            pa.field("calendar_session_id", pa.string(), nullable=False),
            pa.field("venue_id", pa.string(), nullable=False),
            pa.field("trade_date", pa.date32(), nullable=False),
            pa.field("session_type", pa.string(), nullable=False),
            pa.field("is_open", pa.bool_(), nullable=False),
            pa.field("open_at", pa.timestamp("us", tz="UTC"), nullable=True),
            pa.field("close_at", pa.timestamp("us", tz="UTC"), nullable=True),
            pa.field("calendar_reason", pa.string(), nullable=True),
        ]
    )


def _verify_model_calendar_contract(model: Mapping[str, Any]) -> None:
    tables = [
        _mapping(raw, "model table")
        for raw in _sequence(model.get("tables"), "model tables")
        if isinstance(raw, Mapping) and raw.get("name") == _OWNED_TABLE
    ]
    if len(tables) != 1:
        raise TradingCalendarBuildError("model must define one trading-calendar table")
    table = tables[0]
    foreign_keys = _sequence(table.get("foreign_keys"), "calendar foreign keys")
    expected_foreign_key = {
        "column": "venue_id",
        "table": "dwd_trading_venue",
        "reference_column": "venue_id",
        "nullable": False,
    }
    lineage = _mapping(
        _mapping(model.get("column_sets"), "model column sets").get("lineage"),
        "lineage columns",
    )
    market_time = _mapping(
        _mapping(model.get("time_profiles"), "model time profiles").get(
            "market_observation"
        ),
        "market-observation columns",
    )
    if (
        table.get("time_profile") != "market_observation"
        or table.get("inherits") != ["lineage"]
        or table.get("columns") != _CALENDAR_COLUMNS
        or table.get("business_key") != ["venue_id", "trade_date", "session_type"]
        or table.get("row_key")
        != ["venue_id", "trade_date", "session_type", "knowledge_from"]
        or foreign_keys != [expected_foreign_key]
        or table.get("partition_by") != ["trade_date_year"]
        or dict(lineage)
        != {
            "record_source": "string",
            "source_record_id": "string",
            "source_revision": "string",
            "source_artifact_sha256": "string",
            "knowledge_from": "timestamp_us_utc",
            "knowledge_to": "timestamp_us_utc?",
        }
        or dict(market_time)
        != {
            "observation_start": "timestamp_us_utc",
            "observation_end": "timestamp_us_utc?",
        }
    ):
        raise TradingCalendarBuildError(
            "calendar physical schema differs from the approved model contract"
        )


def _verify_ods_binding(
    ods_package: Path,
    semantic_plan: Mapping[str, Any],
    model: Mapping[str, Any],
) -> date:
    if ods_package.is_symlink() or not ods_package.is_dir():
        raise TradingCalendarBuildError("ODS package must be a physical directory")
    plan_input = _mapping(semantic_plan.get("input"), "input plan ODS identity")
    model_input = _mapping(model.get("build_input_contract"), "model build input")
    for key in ("bundle_id", "manifest_sha256", "content_digest", "through_date"):
        if plan_input.get(key) != model_input.get(key):
            raise TradingCalendarBuildError(
                "input plan does not bind the model-authorized ODS Base"
            )
    if plan_input.get("kind") != "ods_base_rollup" or plan_input.get(
        "verification_mode"
    ) != "deep":
        raise TradingCalendarBuildError("ODS Base must have deep rollup verification")
    manifest_path = ods_package / "manifest.json"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or _sha256(manifest_path) != plan_input.get("manifest_sha256")
    ):
        raise TradingCalendarBuildError("ODS package manifest differs from the sealed plan")
    return _date_value(plan_input.get("through_date"), "ODS through_date")


def _verify_upstream_candidate(
    root: Path,
    *,
    run_id: str,
    input_plan_digest: str,
    model_sha256: str,
    knowledge_cutoff: datetime,
) -> tuple[dict[str, Mapping[str, Any]], str, str]:
    try:
        verified = verify_listed_company_candidate(
            root,
            run_id=run_id,
            input_plan_digest=input_plan_digest,
            model_sha256=model_sha256,
        )
    except ListedCompanyBuildError as exc:
        raise TradingCalendarBuildError(
            f"upstream candidate failed deep verification: {exc}"
        ) from exc
    venues: dict[str, Mapping[str, Any]] = {}
    for row in verified.venue_rows:
        mic = row.get("mic_code")
        exchange = _MIC_TO_EXCHANGE.get(mic) if isinstance(mic, str) else None
        if exchange is None or exchange in venues:
            raise TradingCalendarBuildError("upstream venue set is ambiguous")
        if row.get("timezone") != "Asia/Shanghai":
            raise TradingCalendarBuildError("upstream venue timezone is unsupported")
        venue_knowledge = _timestamp(row.get("knowledge_from"), "venue knowledge_from")
        venue_knowledge_to = row.get("knowledge_to")
        if venue_knowledge > knowledge_cutoff or (
            venue_knowledge_to is not None
            and _timestamp(venue_knowledge_to, "venue knowledge_to") <= knowledge_cutoff
        ):
            raise TradingCalendarBuildError(
                "upstream venue does not contain the plan knowledge cutoff"
            )
        venues[exchange] = row
    if set(venues) != {"SSE", "SZSE", "BSE"}:
        raise TradingCalendarBuildError("upstream must contain SSE, SZSE, and BSE venues")
    return (
        venues,
        verified.semantic_digest,
        verified.manifest_sha256,
    )


def _source_priority(path: str) -> int:
    if "/market/" in path:
        return 3
    if "/intraday_5m/" in path:
        return 2
    if "/intraday_1m/" in path:
        return 1
    return 0


@dataclass(frozen=True)
class _CalendarSnapshot:
    record: Mapping[str, Any]
    rows: Mapping[tuple[str, date, str], Mapping[str, Any]]
    maximum_date: date


def _load_calendar_snapshots(
    ledger: Mapping[str, Any], ods_root: Path, knowledge_cutoff: datetime
) -> tuple[_CalendarSnapshot, list[dict[str, Any]]]:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise TradingCalendarBuildError("pyarrow is required for DWD construction") from exc
    records = [
        _mapping(raw, "source ledger record")
        for raw in _sequence(ledger.get("records"), "source ledger records")
        if isinstance(raw, Mapping)
        and raw.get("record_type") == "file"
        and raw.get("disposition") == "modeled"
        and raw.get("mapping_identity") == "trading_calendar"
        and _OWNED_TABLE in raw.get("target_tables", [])
    ]
    if not records:
        raise TradingCalendarBuildError("input plan has no modeled trading calendar")
    snapshots: list[_CalendarSnapshot] = []
    for record in records:
        path = _verify_source(record, ods_root)
        table = pq.ParquetFile(path).read()
        required = {"exchange", "trade_date", "is_open", "session", "source", "built_at"}
        if not required.issubset(table.column_names):
            raise TradingCalendarBuildError("calendar source has an unsupported schema")
        rows: dict[tuple[str, date, str], Mapping[str, Any]] = {}
        for raw in table.to_pylist():
            row = dict(raw)
            exchange = row.get("exchange")
            session = row.get("session")
            trade_date = _date_value(row.get("trade_date"), "calendar trade_date")
            built_at = _timestamp(row.get("built_at"), "calendar built_at")
            if exchange not in {"SSE", "SZSE", "BSE"} or session != "day":
                raise TradingCalendarBuildError("calendar source has an unsupported venue/session")
            is_open = row.get("is_open")
            if not isinstance(is_open, int | bool) or is_open not in (0, 1):
                raise TradingCalendarBuildError("calendar is_open must be binary")
            if not isinstance(row.get("source"), str) or not row.get("source"):
                raise TradingCalendarBuildError("calendar source authority is missing")
            if built_at > knowledge_cutoff:
                raise TradingCalendarBuildError("calendar evidence exceeds knowledge cutoff")
            key = (str(exchange), trade_date, str(session))
            if key in rows:
                raise TradingCalendarBuildError("calendar source has a duplicate business key")
            row["trade_date"] = trade_date
            row["built_at"] = built_at
            rows[key] = row
        if not rows:
            raise TradingCalendarBuildError("calendar source is empty")
        exchanges = {key[0] for key in rows}
        if exchanges != {"SSE", "SZSE", "BSE"}:
            raise TradingCalendarBuildError("calendar snapshot has incomplete venue coverage")
        venue_coordinates = {
            exchange: {(key[1], key[2]) for key in rows if key[0] == exchange}
            for exchange in exchanges
        }
        if len({frozenset(value) for value in venue_coordinates.values()}) != 1:
            raise TradingCalendarBuildError(
                "calendar snapshot has inconsistent venue date coverage"
            )
        snapshots.append(
            _CalendarSnapshot(record=record, rows=rows, maximum_date=max(k[1] for k in rows))
        )

    selected = max(
        snapshots,
        key=lambda item: (
            item.maximum_date,
            _source_priority(_text(item.record, "path")),
            _text(item.record, "path"),
        ),
    )
    selected_business = {key: bool(row["is_open"]) for key, row in selected.rows.items()}
    reconciliation: list[dict[str, Any]] = []
    for snapshot in sorted(snapshots, key=lambda item: _text(item.record, "path")):
        business = {key: bool(row["is_open"]) for key, row in snapshot.rows.items()}
        if any(selected_business.get(key) != value for key, value in business.items()):
            raise TradingCalendarBuildError("calendar snapshots conflict on a business key")
        if not set(business).issubset(selected_business):
            raise TradingCalendarBuildError("selected calendar does not contain prior evidence")
        reconciliation.append(
            {
                "dataset_id": snapshot.record.get("dataset_id"),
                "path": snapshot.record.get("path"),
                "source_rows": len(snapshot.rows),
                "minimum_date": min(key[1] for key in snapshot.rows).isoformat(),
                "maximum_date": snapshot.maximum_date.isoformat(),
                "source_sha256": snapshot.record.get("sha256"),
                "outcome": (
                    "selected_latest_market_authority"
                    if snapshot is selected
                    else (
                        "corroborated_identical_snapshot"
                        if set(business) == set(selected_business)
                        else "corroborated_consistent_prefix"
                    )
                ),
                "conflicting_rows": 0,
            }
        )
    return selected, reconciliation


def _session_rows(
    snapshot: _CalendarSnapshot,
    venues: Mapping[str, Mapping[str, Any]],
    knowledge_cutoff: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_path = _text(snapshot.record, "path")
    source_sha = _text(snapshot.record, "sha256")
    source_revision = (
        f"{_text(snapshot.record, 'dataset_id')}@{source_sha.removeprefix(_SHA256)[:16]}"
    )
    result: list[dict[str, Any]] = []
    excluded_bse_dates: list[date] = []
    ordered_source_rows = sorted(
        snapshot.rows.items(),
        key=lambda item: (item[0][1], item[0][0], item[0][2]),
    )
    for (exchange, trade_date, _session), source_row in ordered_source_rows:
        venue = venues[exchange]
        venue_id = _text(venue, "venue_id")
        start = datetime.combine(trade_date, time.min, _SHANGHAI).astimezone(timezone.utc)
        end = start + timedelta(days=1)
        venue_start = _timestamp(venue.get("effective_from"), "venue effective_from")
        venue_end_raw = venue.get("effective_to")
        outside_venue_interval = venue_start > start or (
            venue_end_raw is not None
            and _timestamp(venue_end_raw, "venue effective_to") < end
        )
        if outside_venue_interval and exchange == "BSE" and end <= venue_start:
            excluded_bse_dates.append(trade_date)
            continue
        if outside_venue_interval:
            raise TradingCalendarBuildError(
                "calendar session is outside its venue effective interval"
            )
        is_open = bool(source_row["is_open"])
        result.append(
            {
                "record_source": str(source_row["source"]),
                "source_record_id": (
                    f"{source_path}#exchange={exchange}&trade_date={trade_date.isoformat()}"
                    "&session=day"
                ),
                "source_revision": source_revision,
                "source_artifact_sha256": source_sha,
                "knowledge_from": max(
                    knowledge_cutoff,
                    _timestamp(source_row["built_at"], "calendar built_at"),
                    _timestamp(venue.get("knowledge_from"), "venue knowledge_from"),
                ),
                "knowledge_to": None,
                "observation_start": start,
                "observation_end": end,
                "calendar_session_id": _stable_id(
                    "calendar_session", venue_id, trade_date.isoformat(), _SESSION_TYPE
                ),
                "venue_id": venue_id,
                "trade_date": trade_date,
                "session_type": _SESSION_TYPE,
                "is_open": is_open,
                "open_at": (
                    datetime.combine(trade_date, time(9, 30), _SHANGHAI).astimezone(
                        timezone.utc
                    )
                    if is_open
                    else None
                ),
                "close_at": (
                    datetime.combine(trade_date, time(15), _SHANGHAI).astimezone(
                        timezone.utc
                    )
                    if is_open
                    else None
                ),
                "calendar_reason": None if is_open else "SCHEDULED_CLOSED",
            }
        )
    keys = [
        (row["venue_id"], row["trade_date"], row["session_type"], row["knowledge_from"])
        for row in result
    ]
    ids = [row["calendar_session_id"] for row in result]
    if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
        raise TradingCalendarBuildError("calendar output has duplicate identity")
    coverage_exceptions = []
    if excluded_bse_dates:
        coverage_exceptions.append(
            {
                "code": "BSE_CALENDAR_PRE_VENUE_EFFECTIVE_RANGE",
                "policy": "BSE_PARTIAL_NONBLOCKING",
                "affected_rows": len(excluded_bse_dates),
                "minimum_trade_date": min(excluded_bse_dates).isoformat(),
                "maximum_trade_date": max(excluded_bse_dates).isoformat(),
                "resolution": "excluded_noneligible_pre_venue_rows",
            }
        )
    if len(result) + len(excluded_bse_dates) != len(snapshot.rows):
        raise TradingCalendarBuildError("calendar source-row reconciliation is incomplete")
    return result, coverage_exceptions


def _write_json_atomic(path: Path, value: object) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o640)
            json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_text_atomic(path: Path, value: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_orphaned_temporaries(work: Path, output_files: set[str]) -> None:
    final_paths = {
        work / relative for relative in output_files | {"manifest.json", "READY"}
    }
    for final_path in sorted(final_paths):
        if final_path.parent.is_dir():
            for candidate in final_path.parent.glob(f".{final_path.name}.*"):
                if candidate.is_dir() and not candidate.is_symlink():
                    raise TradingCalendarBuildError(
                        f"orphaned temporary path is not a file: {candidate}"
                    )
                candidate.unlink(missing_ok=True)
    for partial in work.glob(
        f"payload/tradability_state/{_OWNED_TABLE}/trade_date_year=*/"
        "part-00000.parquet.partial"
    ):
        if partial.is_dir() and not partial.is_symlink():
            raise TradingCalendarBuildError(
                f"orphaned partition temporary is not a file: {partial}"
            )
        partial.unlink(missing_ok=True)


def _prepare_work(
    work: Path,
    checkpoint_path: Path,
    checkpoint_identity: Mapping[str, Any],
    output_files: set[str],
) -> dict[str, Any]:
    expected_files = output_files | {"manifest.json", "READY"}
    expected_directories = {
        parent.as_posix()
        for relative in expected_files
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    if work.exists():
        if work.is_symlink() or not work.is_dir():
            raise TradingCalendarBuildError("build work path must be a physical directory")
        checkpoint = _object(checkpoint_path) if checkpoint_path.is_file() else None
        if checkpoint is not None and any(
            checkpoint.get(key) != checkpoint_identity[key]
            for key in (
                "schema_version",
                "kind",
                "run_id",
                "semantic_digest",
                "input_plan_digest",
            )
        ):
            raise TradingCalendarBuildError("checkpoint belongs to another build")
        _remove_orphaned_temporaries(work, output_files)
        for path in work.rglob("*"):
            relative = path.relative_to(work).as_posix()
            if path.is_symlink():
                raise TradingCalendarBuildError("build work contains a symbolic link")
            if path.is_dir() and relative in expected_directories:
                continue
            if path.is_file() and relative in expected_files:
                continue
            raise TradingCalendarBuildError(
                f"build work contains an unexpected path: {relative}"
            )
    else:
        work.mkdir(mode=0o750)
        _fsync_directory(work)
        _fsync_directory(work.parent)
        checkpoint = None
    for relative in sorted(expected_directories, key=lambda value: len(Path(value).parts)):
        directory = work / relative
        existed = directory.exists()
        directory.mkdir(exist_ok=True)
        if not existed:
            _fsync_directory(directory)
            _fsync_directory(directory.parent)
    if checkpoint is None:
        checkpoint = dict(checkpoint_identity)
        _write_json_atomic(checkpoint_path, checkpoint)
    return checkpoint


def _partition_path(root: Path, year: int) -> Path:
    return (
        root
        / "payload"
        / "tradability_state"
        / _OWNED_TABLE
        / f"trade_date_year={year}"
        / "part-00000.parquet"
    )


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _verify_existing_output(
    output: Path,
    *,
    run_id: str,
    semantic_digest: str,
    row_count: int,
    expected_files: set[str],
    expected_receipt: Mapping[str, Any],
    expected_reconciliation: Mapping[str, Any],
) -> None:
    manifest = _verify_manifest_package(
        output,
        expected_files=expected_files,
        expected_kind="dwd_trading_calendar_candidate",
    )
    if (
        manifest.get("run_id") != run_id
        or manifest.get("semantic_digest") != semantic_digest
        or manifest.get("owned_tables") != [_OWNED_TABLE]
        or manifest.get("row_counts") != {_OWNED_TABLE: row_count}
        or manifest.get("partition_by") != ["trade_date_year"]
        or manifest.get("input_plan_digest") != expected_receipt.get("input_plan_digest")
        or manifest.get("upstream_semantic_digest")
        != expected_receipt.get("upstream_semantic_digest")
        or manifest.get("upstream_manifest_sha256")
        != expected_receipt.get("upstream_manifest_sha256")
    ):
        raise TradingCalendarBuildError("existing output has another build identity")
    if _object(output / "receipts/trading-calendar-build.json") != expected_receipt:
        raise TradingCalendarBuildError("existing output receipt differs")
    if (
        _object(output / "reconciliation/source-row-reconciliation.json")
        != expected_reconciliation
    ):
        raise TradingCalendarBuildError("existing output reconciliation differs")
    checkpoint = _object(output / "checkpoint.json")
    completed = _mapping(checkpoint.get("completed_years"), "completed years")
    partition_digests = _mapping(
        expected_receipt.get("partition_digests"), "partition digests"
    )
    if (
        checkpoint.get("schema_version") != 1
        or checkpoint.get("kind") != "dwd_trading_calendar_checkpoint"
        or checkpoint.get("run_id") != run_id
        or checkpoint.get("semantic_digest") != semantic_digest
        or checkpoint.get("input_plan_digest")
        != expected_receipt.get("input_plan_digest")
        or set(completed) != set(partition_digests)
    ):
        raise TradingCalendarBuildError("existing output checkpoint is inconsistent")
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise TradingCalendarBuildError("pyarrow is required for output verification") from exc
    tables = []
    schema = _calendar_schema()
    for year_key in sorted(partition_digests):
        state = _mapping(completed.get(year_key), f"completed year {year_key}")
        path = _partition_path(output, int(year_key))
        table = pq.ParquetFile(path).read()
        rows = table.to_pylist()
        if (
            not table.schema.equals(schema, check_metadata=True)
            or table.num_rows != state.get("row_count")
            or state.get("path") != path.relative_to(output).as_posix()
            or state.get("schema_digest") != _canonical_digest(str(schema))
            or _canonical_digest(rows) != partition_digests[year_key]
            or state.get("semantic_digest") != partition_digests[year_key]
            or state.get("sha256") != _sha256(path)
        ):
            raise TradingCalendarBuildError(
                f"existing output partition is invalid: {year_key}"
            )
        tables.append(table)
    combined = pa.concat_tables(tables).to_pylist()
    if len(combined) != row_count or _canonical_digest(combined) != expected_receipt.get(
        "table_digest"
    ):
        raise TradingCalendarBuildError("existing output table semantic digest differs")


def _build_locked(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    upstream_candidate: Path,
    output: Path,
    run_id: str,
) -> TradingCalendarBuildResult:
    plan, ledger = _verify_plan_package(input_plan)
    semantic_plan = _mapping(plan.get("plan"), "input plan")
    if plan.get("run_id") != run_id or semantic_plan.get("mode") != "base":
        raise TradingCalendarBuildError("builder must match one sealed Base run")
    model = _object(model_path)
    model_sha = _sha256(model_path)
    model_identity = _mapping(semantic_plan.get("model"), "input plan model")
    execution = _mapping(model.get("build_execution_contract"), "build execution contract")
    ownership = _mapping(execution.get("dwd_script_ownership"), "DWD script ownership")
    if (
        model_identity.get("sha256") != model_sha
        or model_identity.get("contract_id") != model.get("contract_id")
        or ownership.get("72") != [_OWNED_TABLE]
        or [57, 72] not in execution.get("required_dependency_edges", [])
    ):
        raise TradingCalendarBuildError("model does not authorize Issue #72 calendar ownership")
    _verify_model_calendar_contract(model)
    through_date = _verify_ods_binding(ods_package, semantic_plan, model)
    knowledge_cutoff = _timestamp(semantic_plan.get("knowledge_cutoff"), "knowledge cutoff")
    venues, upstream_digest, upstream_manifest_sha = _verify_upstream_candidate(
        upstream_candidate,
        run_id=run_id,
        input_plan_digest=_text(plan, "plan_digest"),
        model_sha256=model_sha,
        knowledge_cutoff=knowledge_cutoff,
    )
    selected, source_reconciliation = _load_calendar_snapshots(
        ledger, ods_package, knowledge_cutoff
    )
    if selected.maximum_date != through_date:
        raise TradingCalendarBuildError(
            "latest calendar snapshot does not reach the ODS through date"
        )
    rows, coverage_exceptions = _session_rows(selected, venues, knowledge_cutoff)
    schema = _calendar_schema()
    table_digest = _canonical_digest(rows)
    selected_path = _text(selected.record, "path")
    semantic_digest = _canonical_digest(
        {
            "input_plan_digest": plan["plan_digest"],
            "model_sha256": model_sha,
            "upstream_semantic_digest": upstream_digest,
            "upstream_manifest_sha256": upstream_manifest_sha,
            "selected_source_sha256": selected.record.get("sha256"),
            "table_digest": table_digest,
        }
    )
    years = sorted({row["trade_date"].year for row in rows})
    by_year = {year: [row for row in rows if row["trade_date"].year == year] for year in years}
    partition_digests = {
        str(year): _canonical_digest(by_year[year]) for year in years
    }
    receipt = {
        "schema_version": 1,
        "kind": "dwd_trading_calendar_build_receipt",
        "status": "built",
        "run_id": run_id,
        "input_plan_digest": plan["plan_digest"],
        "model_sha256": model_sha,
        "upstream_kind": "dwd_listed_company_candidate",
        "upstream_semantic_digest": upstream_digest,
        "upstream_manifest_sha256": upstream_manifest_sha,
        "selected_calendar_source": selected_path,
        "selected_calendar_source_sha256": selected.record.get("sha256"),
        "knowledge_cutoff": knowledge_cutoff.isoformat(),
        "row_counts": {_OWNED_TABLE: len(rows)},
        "year_count": len(years),
        "minimum_trade_date": min(row["trade_date"] for row in rows).isoformat(),
        "maximum_trade_date": max(row["trade_date"] for row in rows).isoformat(),
        "open_row_count": sum(row["is_open"] for row in rows),
        "closed_row_count": sum(not row["is_open"] for row in rows),
        "coverage_exceptions": coverage_exceptions,
        "table_digest": table_digest,
        "partition_digests": partition_digests,
        "semantic_digest": semantic_digest,
    }
    reconciliation = {
        "schema_version": 1,
        "kind": "dwd_trading_calendar_source_reconciliation",
        "run_id": run_id,
        "selected_source": selected_path,
        "source_count": len(source_reconciliation),
        "sources": source_reconciliation,
        "source_rows_selected": len(selected.rows),
        "output_rows": len(rows),
        "excluded_noneligible_rows": sum(
            item["affected_rows"] for item in coverage_exceptions
        ),
        "unaccounted_selected_rows": 0,
        "conflicting_rows": 0,
    }
    partition_relatives = {
        _partition_path(Path("."), year).as_posix().removeprefix("./") for year in years
    }
    output_files = {
        "checkpoint.json",
        "receipts/trading-calendar-build.json",
        "reconciliation/source-row-reconciliation.json",
        *partition_relatives,
    }
    if output.exists():
        _verify_existing_output(
            output,
            run_id=run_id,
            semantic_digest=semantic_digest,
            row_count=len(rows),
            expected_files=output_files,
            expected_receipt=receipt,
            expected_reconciliation=reconciliation,
        )
        return TradingCalendarBuildResult(
            output.resolve(), semantic_digest, len(rows), len(years), selected_path
        )

    work_identity = _canonical_digest(
        {"run_id": run_id, "semantic_digest": semantic_digest}
    ).removeprefix(_SHA256)
    work = output.parent / f".{output.name}.{work_identity}.work"
    checkpoint_path = work / "checkpoint.json"
    checkpoint_identity = {
        "schema_version": 1,
        "kind": "dwd_trading_calendar_checkpoint",
        "run_id": run_id,
        "semantic_digest": semantic_digest,
        "input_plan_digest": plan["plan_digest"],
        "completed_years": {},
    }
    checkpoint = _prepare_work(
        work, checkpoint_path, checkpoint_identity, output_files
    )
    completed = dict(_mapping(checkpoint.get("completed_years"), "completed years"))

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise TradingCalendarBuildError("pyarrow is required for DWD construction") from exc
    for year in years:
        year_key = str(year)
        partition = _partition_path(work, year)
        expected_digest = partition_digests[year_key]
        existing = completed.get(year_key)
        if existing is not None:
            state = _mapping(existing, f"completed year {year}")
            existing_table = pq.ParquetFile(partition).read() if partition.is_file() else None
            if (
                existing_table is None
                or state.get("path") != partition.relative_to(work).as_posix()
                or state.get("row_count") != len(by_year[year])
                or state.get("semantic_digest") != expected_digest
                or state.get("schema_digest") != _canonical_digest(str(schema))
                or state.get("sha256") != _sha256(partition)
                or not existing_table.schema.equals(schema, check_metadata=True)
                or _canonical_digest(existing_table.to_pylist()) != expected_digest
            ):
                raise TradingCalendarBuildError(f"checkpoint partition is invalid: {year}")
            continue
        partition.parent.mkdir(parents=True, exist_ok=True)
        partial = partition.with_suffix(".parquet.partial")
        partial.unlink(missing_ok=True)
        table = pa.Table.from_pylist(by_year[year], schema=schema)
        pq.write_table(
            table,
            partial,
            compression="zstd",
            version="2.6",
            data_page_version="2.0",
            use_dictionary=True,
            write_statistics=True,
        )
        os.chmod(partial, 0o640)
        _fsync_file(partial)
        os.replace(partial, partition)
        _fsync_directory(partition.parent)
        completed[year_key] = {
            "path": partition.relative_to(work).as_posix(),
            "row_count": len(by_year[year]),
            "semantic_digest": expected_digest,
            "schema_digest": _canonical_digest(str(schema)),
            "sha256": _sha256(partition),
        }
        checkpoint = {**checkpoint_identity, "completed_years": completed}
        _write_json_atomic(checkpoint_path, checkpoint)

    checkpoint = {**checkpoint_identity, "completed_years": completed}
    _write_json_atomic(checkpoint_path, checkpoint)
    _write_json_atomic(work / "receipts/trading-calendar-build.json", receipt)
    _write_json_atomic(
        work / "reconciliation/source-row-reconciliation.json", reconciliation
    )
    manifest_files = [
        _file_record(work, work / relative) for relative in sorted(output_files)
    ]
    manifest = {
        "schema_version": 1,
        "kind": "dwd_trading_calendar_candidate",
        "run_id": run_id,
        "input_plan_digest": plan["plan_digest"],
        "upstream_semantic_digest": upstream_digest,
        "upstream_manifest_sha256": upstream_manifest_sha,
        "semantic_digest": semantic_digest,
        "owned_tables": [_OWNED_TABLE],
        "row_counts": {_OWNED_TABLE: len(rows)},
        "partition_by": ["trade_date_year"],
        "files": manifest_files,
    }
    _write_json_atomic(work / "manifest.json", manifest)
    _write_text_atomic(work / "READY", f"{_sha256(work / 'manifest.json')}\n")
    _fsync_directory(work)
    _verify_existing_output(
        work,
        run_id=run_id,
        semantic_digest=semantic_digest,
        row_count=len(rows),
        expected_files=output_files,
        expected_receipt=receipt,
        expected_reconciliation=reconciliation,
    )
    work.rename(output)
    _fsync_directory(output.parent)
    _verify_existing_output(
        output,
        run_id=run_id,
        semantic_digest=semantic_digest,
        row_count=len(rows),
        expected_files=output_files,
        expected_receipt=receipt,
        expected_reconciliation=reconciliation,
    )
    return TradingCalendarBuildResult(
        output.resolve(), semantic_digest, len(rows), len(years), selected_path
    )


def build_trading_calendar(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    upstream_candidate: Path,
    output: Path,
    run_id: str,
) -> TradingCalendarBuildResult:
    """Build one immutable, idempotent Issue #72 calendar candidate."""

    if output.is_symlink():
        raise TradingCalendarBuildError("candidate output path must not be a symbolic link")
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized_output = output.resolve()
    lock_path = normalized_output.parent / f".{normalized_output.name}.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o640)
    except OSError as exc:
        raise TradingCalendarBuildError(f"cannot open build lock: {lock_path}") from exc
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TradingCalendarBuildError("build lock must be a physical file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        return _build_locked(
            ods_package=ods_package,
            input_plan=input_plan,
            model_path=model_path,
            upstream_candidate=upstream_candidate,
            output=normalized_output,
            run_id=run_id,
        )
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
