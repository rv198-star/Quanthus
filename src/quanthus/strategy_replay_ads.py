"""Portable, role-scoped ADS bundles for historical strategy replay."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.backtest.benchmark import BenchmarkCurve, load_benchmark_curve
from quanthus.backtest.contracts import MARKET_TIMEZONE
from quanthus.backtest.historical_bundle import (
    HistoricalReplayBundle,
    HistoricalReplayBundleError,
    load_historical_replay_bundle,
    validate_historical_replay_bundle_contract,
)
from quanthus.backtest.historical_frames import (
    HistoricalFrameAuditReport,
    HistoricalReplayFrameReader,
    HistoricalReplayFrameSequence,
)
from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_normalization import CorporateActionNormalizationPolicy
from quanthus.data_foundation import DatasetManifest
from quanthus.industry_mapping import StaticIndustryMapping, load_static_industry_mapping
from quanthus.policy import ConfigurationError, find_project_root

ADS_SCHEMA_VERSION = 1
ADS_KIND = "portable_research_ads_bundle"
ADS_PROFILE = "strategy_replay"
CONTENT_DIGEST_METHOD = "canonical_logical_row_stream_sha256_v1"
PIT_EVIDENCE_MODE = "frozen_historical_replay_snapshot_bootstrap_v1"
PIT_EVIDENCE_STATUS = "capability_fixture_without_per_decision_publication_proof"
RIGHT_CENSOR_POLICY = "scope_end_right_censor_v1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_OBJECT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")


class StrategyReplayADSError(RuntimeError):
    """Raised when a portable replay ADS violates its publication or access contract."""


@dataclass(frozen=True)
class StrategyReplayADSScope:
    scope_id: str
    partition_start: date
    partition_end: date
    reader_role: str


@dataclass(frozen=True)
class StrategyReplayADSPolicy:
    contract_id: str
    context_policy: str
    bootstrap_source_snapshot_id: str
    bootstrap_source_bundle_digest: str
    bootstrap_source_manifest_sha256: str
    benchmark_instrument_id: str
    benchmark_logical_digest: str
    benchmark_file_sha256: str
    industry_mapping_version: str
    industry_mapping_logical_digest: str
    industry_mapping_file_sha256: str
    scopes: Mapping[str, StrategyReplayADSScope]
    digest: str

    def scope(self, scope_id: str) -> StrategyReplayADSScope:
        try:
            return self.scopes[scope_id]
        except KeyError as exc:
            raise StrategyReplayADSError(f"unknown strategy-replay ADS scope: {scope_id}") from exc


@dataclass(frozen=True)
class StrategyReplayADSBundle:
    root: Path
    bundle_id: str
    bundle_content_digest: str
    manifest_sha256: str
    scope_id: str
    reader_role: str
    decision_start: date
    decision_end: date
    execution_end: date
    publisher_code_revision: str
    publisher_runtime: Mapping[str, Any]
    source_snapshot_id: str
    source_manifest_sha256: str
    pit_mapping_digest: str
    pit_bindings: Mapping[date, StrategyReplayADSPITBinding]
    profile_parts: Mapping[str, tuple[str, ...]]
    historical_bundle: HistoricalReplayBundle

    @property
    def bundle_digest(self) -> str:
        """Compatibility alias for the layout-bound bundle content identity."""

        return self.bundle_content_digest

    @property
    def execution_authorized(self) -> bool:
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_content_digest": self.bundle_content_digest,
            "manifest_sha256": self.manifest_sha256,
            "scope_id": self.scope_id,
            "reader_role": self.reader_role,
            "decision_start": self.decision_start.isoformat(),
            "decision_end": self.decision_end.isoformat(),
            "execution_end": self.execution_end.isoformat(),
            "publisher_code_revision": self.publisher_code_revision,
            "publisher_runtime": self.publisher_runtime,
            "source_snapshot_id": self.source_snapshot_id,
            "source_manifest_sha256": self.source_manifest_sha256,
            "pit_mapping_digest": self.pit_mapping_digest,
            "profile_tables": sorted(self.profile_parts),
            "table_count": len(self.historical_bundle.datasets),
            "execution_authorized": self.execution_authorized,
            "root": str(self.root),
        }

    def profile_paths(self, name: str) -> tuple[Path, ...]:
        try:
            parts = self.profile_parts[name]
        except KeyError as exc:
            raise StrategyReplayADSError(f"ADS profile table is not bound: {name}") from exc
        return tuple((self.root / part).resolve() for part in parts)


@dataclass(frozen=True)
class StrategyReplayADSPITBinding:
    decision_date: date
    decision_cutoff: datetime
    source_snapshot_id: str
    source_knowledge_cutoff: datetime
    evidence_mode: str
    evidence_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_date": self.decision_date.isoformat(),
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "source_snapshot_id": self.source_snapshot_id,
            "source_knowledge_cutoff": self.source_knowledge_cutoff.isoformat(),
            "evidence_mode": self.evidence_mode,
            "evidence_status": self.evidence_status,
            "per_decision_publication_proven": False,
        }


@dataclass(frozen=True)
class _TableContract:
    role: str
    date_column: str
    sort_keys: tuple[str, ...]
    primary_key: tuple[str, ...]
    materialize_identity: bool = False
    allow_empty: bool = False
    clip_end_columns: tuple[str, ...] = ()


_TABLES: Mapping[str, _TableContract] = {
    "corporate_action_exceptions": _TableContract(
        "runner_only",
        "blocked_from",
        ("blocked_from", "security_code", "exception_id"),
        ("exception_id",),
        True,
        True,
        ("blocked_through",),
    ),
    "corporate_actions_normalized": _TableContract(
        "runner_only",
        "effective_date",
        ("effective_date", "security_code", "action_id"),
        ("action_id",),
        True,
    ),
    "daily_ohlcv_none": _TableContract(
        "decision_visible", "date", ("date", "security_id"), ("date", "security_id")
    ),
    "daily_universe": _TableContract(
        "decision_visible",
        "market_date",
        ("market_date", "security_id"),
        ("market_date", "security_id"),
    ),
    "price_limit_execution_daily": _TableContract(
        "runner_only",
        "execution_date",
        ("execution_date", "security_id"),
        ("execution_date", "security_id"),
    ),
    "security_code_validity": _TableContract(
        "runner_only",
        "valid_from",
        ("valid_from", "security_id", "stock_code"),
        ("security_id", "stock_code", "valid_from"),
        clip_end_columns=("valid_to",),
    ),
    "trading_calendar": _TableContract(
        "runner_only", "trade_date", ("trade_date", "exchange"), ("trade_date", "exchange")
    ),
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _canonical_digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrategyReplayADSError(f"cannot load JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise StrategyReplayADSError(f"JSON value must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _safe_relative(value: str, *, field: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.as_posix() == "." or path.is_absolute() or ".." in path.parts:
        raise StrategyReplayADSError(f"{field} must be a safe relative path")
    return path


def _resolve_inside(root: Path, value: str, *, field: str) -> Path:
    relative = _safe_relative(value, field=field)
    resolved = root.joinpath(*relative.parts).resolve()
    if root.resolve() not in resolved.parents:
        raise StrategyReplayADSError(f"{field} escapes the ADS root")
    return resolved


def _required_text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise StrategyReplayADSError(f"{key} must be a non-empty string")
    return result


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_parquet_expression(paths: Sequence[Path]) -> str:
    if not paths:
        raise StrategyReplayADSError("ADS source table has no parts")
    values = ",".join(_sql_string(str(path.resolve())) for path in paths)
    return f"read_parquet([{values}])"


def load_strategy_replay_ads_policy(path: Path | None = None) -> StrategyReplayADSPolicy:
    resolved = (path or find_project_root() / "config" / "strategy-replay-ads.v1.json").resolve()
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"configuration file does not exist: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid JSON in {resolved}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("strategy-replay ADS policy must be an object")
    expected = {
        "schema_version",
        "contract_id",
        "profile",
        "context_policy",
        "artifact_discovery",
        "mandatory_year_partitioning",
        "bootstrap_source",
        "profile_tables",
        "scopes",
    }
    if set(raw) != expected:
        raise ConfigurationError("strategy-replay ADS policy fields are not exact")
    if (
        raw["schema_version"] != ADS_SCHEMA_VERSION
        or raw["profile"] != ADS_PROFILE
        or raw["context_policy"] != "all_available_history_through_execution_end"
        or raw["artifact_discovery"] != "manifest_tables_parts"
        or raw["mandatory_year_partitioning"] is not False
    ):
        raise ConfigurationError("strategy-replay ADS policy differs from the v1 contract")
    contract_id = raw["contract_id"]
    if not isinstance(contract_id, str) or not contract_id.strip():
        raise ConfigurationError("strategy-replay ADS contract_id is invalid")
    bootstrap = raw["bootstrap_source"]
    if not isinstance(bootstrap, dict) or set(bootstrap) != {
        "kind",
        "snapshot_id",
        "bundle_digest",
        "manifest_sha256",
    }:
        raise ConfigurationError("strategy-replay ADS bootstrap source is invalid")
    if bootstrap.get("kind") != "accepted_historical_replay_snapshot_bootstrap":
        raise ConfigurationError("strategy-replay ADS bootstrap source kind is invalid")
    bootstrap_snapshot_id = bootstrap.get("snapshot_id")
    bootstrap_bundle_digest = bootstrap.get("bundle_digest")
    bootstrap_manifest_sha256 = bootstrap.get("manifest_sha256")
    if (
        not isinstance(bootstrap_snapshot_id, str)
        or not _SAFE_ID.fullmatch(bootstrap_snapshot_id)
        or not isinstance(bootstrap_bundle_digest, str)
        or not _SHA256.fullmatch(bootstrap_bundle_digest)
        or not isinstance(bootstrap_manifest_sha256, str)
        or not _SHA256.fullmatch(bootstrap_manifest_sha256)
    ):
        raise ConfigurationError("strategy-replay ADS bootstrap source identity is invalid")
    profile_tables = raw["profile_tables"]
    if not isinstance(profile_tables, dict) or set(profile_tables) != {
        "benchmark_curve",
        "static_industry_mapping",
    }:
        raise ConfigurationError("strategy-replay ADS profile tables are invalid")
    benchmark_profile = profile_tables["benchmark_curve"]
    industry_profile = profile_tables["static_industry_mapping"]
    if not isinstance(benchmark_profile, dict) or set(benchmark_profile) != {
        "instrument_id",
        "logical_digest",
        "file_sha256",
    }:
        raise ConfigurationError("strategy-replay ADS benchmark profile is invalid")
    if not isinstance(industry_profile, dict) or set(industry_profile) != {
        "mapping_version",
        "logical_digest",
        "file_sha256",
    }:
        raise ConfigurationError("strategy-replay ADS industry profile is invalid")
    benchmark_instrument_id = benchmark_profile.get("instrument_id")
    industry_mapping_version = industry_profile.get("mapping_version")
    profile_digests = (
        benchmark_profile.get("logical_digest"),
        benchmark_profile.get("file_sha256"),
        industry_profile.get("logical_digest"),
        industry_profile.get("file_sha256"),
    )
    if (
        not isinstance(benchmark_instrument_id, str)
        or not benchmark_instrument_id.strip()
        or not isinstance(industry_mapping_version, str)
        or not industry_mapping_version.strip()
        or any(
            not isinstance(value, str) or not _SHA256.fullmatch(value)
            for value in profile_digests
        )
    ):
        raise ConfigurationError("strategy-replay ADS profile identities are invalid")
    scope_values = raw["scopes"]
    if not isinstance(scope_values, dict) or set(scope_values) != {
        "development",
        "arbitration",
        "permanent_blind",
    }:
        raise ConfigurationError("strategy-replay ADS scopes are incomplete")
    scopes: dict[str, StrategyReplayADSScope] = {}
    prior_end: date | None = None
    roles: set[str] = set()
    for scope_id in ("development", "arbitration", "permanent_blind"):
        value = scope_values[scope_id]
        if not isinstance(value, dict) or set(value) != {
            "partition_start",
            "partition_end",
            "reader_role",
        }:
            raise ConfigurationError(f"strategy-replay ADS scope is invalid: {scope_id}")
        try:
            start = date.fromisoformat(str(value["partition_start"]))
            end = date.fromisoformat(str(value["partition_end"]))
        except ValueError as exc:
            raise ConfigurationError(
                f"strategy-replay ADS scope dates are invalid: {scope_id}"
            ) from exc
        reader_role = value["reader_role"]
        if (
            start > end
            or (prior_end is not None and start <= prior_end)
            or not isinstance(reader_role, str)
            or not reader_role.strip()
            or reader_role in roles
        ):
            raise ConfigurationError(f"strategy-replay ADS scope boundary is invalid: {scope_id}")
        scopes[scope_id] = StrategyReplayADSScope(scope_id, start, end, reader_role)
        roles.add(reader_role)
        prior_end = end
    return StrategyReplayADSPolicy(
        contract_id=contract_id,
        context_policy=str(raw["context_policy"]),
        bootstrap_source_snapshot_id=bootstrap_snapshot_id,
        bootstrap_source_bundle_digest=bootstrap_bundle_digest,
        bootstrap_source_manifest_sha256=bootstrap_manifest_sha256,
        benchmark_instrument_id=benchmark_instrument_id,
        benchmark_logical_digest=str(benchmark_profile["logical_digest"]),
        benchmark_file_sha256=str(benchmark_profile["file_sha256"]),
        industry_mapping_version=industry_mapping_version,
        industry_mapping_logical_digest=str(industry_profile["logical_digest"]),
        industry_mapping_file_sha256=str(industry_profile["file_sha256"]),
        scopes=scopes,
        digest=_canonical_digest(raw),
    )


def _schema_sha256(path: Path) -> str:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise StrategyReplayADSError("P1 dependencies are required for ADS publication") from exc
    schema = pq.ParquetFile(path).schema_arrow
    return f"sha256:{hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()}"


def _runtime_identity() -> dict[str, object]:
    packages: dict[str, str] = {}
    for package in ("duckdb", "polars", "pyarrow", "quanthus"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = "source-tree"
    return {
        "python_series": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": dict(sorted(packages.items())),
    }


def _logical_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return {"decimal": format(value, "f")}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, float):
        return {"float_hex": value.hex()}
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, Mapping):
        return {
            "mapping": {
                str(key): _logical_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        }
    if isinstance(value, Sequence):
        return {"sequence": [_logical_value(item) for item in value]}
    raise StrategyReplayADSError(
        f"ADS logical row digest does not support value type: {type(value).__name__}"
    )


def _logical_schema(description: Sequence[Sequence[object]]) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "name": str(item[0]),
            "type": str(item[1]).upper(),
            "nullable": "unknown" if len(item) < 3 or item[2] is None else str(item[2]).lower(),
        }
        for item in description
    )


def _table_content_digest(
    connection: Any,
    source: str,
    *,
    columns: Sequence[str],
    logical_schema: Sequence[Mapping[str, str]],
    sort_keys: Sequence[str],
) -> tuple[str, dict[str, object]]:
    digest = hashlib.sha256()
    header = {
        "method": CONTENT_DIGEST_METHOD,
        "columns": list(columns),
        "logical_schema": [dict(item) for item in logical_schema],
        "sort_keys": list(sort_keys),
    }
    header_bytes = _canonical_bytes(header)
    digest.update(len(header_bytes).to_bytes(8, "big"))
    digest.update(header_bytes)
    projection = ", ".join(f'"{column}"' for column in columns)
    ordering = ", ".join(f'"{column}"' for column in sort_keys)
    cursor = connection.execute(f"SELECT {projection} FROM {source} ORDER BY {ordering}")
    row_count = 0
    while True:
        rows = cursor.fetchmany(10_000)
        if not rows:
            break
        for row in rows:
            row_bytes = _canonical_bytes([_logical_value(value) for value in row])
            digest.update(len(row_bytes).to_bytes(8, "big"))
            digest.update(row_bytes)
            row_count += 1
    value = f"sha256:{digest.hexdigest()}"
    return value, {
        "method": CONTENT_DIGEST_METHOD,
        "row_count": row_count,
        "logical_row_stream_sha256": value,
    }


def _source_manifest_payload(manifest: DatasetManifest) -> dict[str, object]:
    return {
        "dataset": manifest.dataset,
        "version": manifest.version,
        "manifest_id": manifest.manifest_id,
        "source_revisions": dict(sorted(manifest.source_revisions.items())),
        "row_count": manifest.row_count,
        "partitions": list(manifest.partitions),
        "schema_hash": manifest.schema_hash,
        "content_checksums": dict(sorted(manifest.content_checksums.items())),
        "created_at": manifest.created_at.isoformat(),
        "knowledge_cutoff": manifest.knowledge_cutoff.isoformat(),
        "parent_manifest_ids": list(manifest.parent_manifest_ids),
    }


def _complete_sessions(
    connection: Any,
    calendar_paths: Sequence[Path],
    scope: StrategyReplayADSScope,
) -> tuple[date, ...]:
    rows = connection.execute(
        f"""
        SELECT CAST(trade_date AS DATE) AS trade_date
        FROM {_read_parquet_expression(calendar_paths)}
        WHERE exchange IN ('SSE', 'SZSE')
          AND is_open = 1
          AND CAST(trade_date AS DATE) BETWEEN ? AND ?
        GROUP BY trade_date
        HAVING count(*) = 2 AND count(DISTINCT exchange) = 2
        ORDER BY trade_date
        """,
        [scope.partition_start, scope.partition_end],
    ).fetchall()
    sessions = tuple(row[0] for row in rows)
    if len(sessions) < 2:
        raise StrategyReplayADSError("ADS scope requires at least two complete market sessions")
    return sessions


def _source_filter(contract: _TableContract, execution_end: date) -> str:
    date_value = _sql_string(execution_end.isoformat())
    return f'CAST("{contract.date_column}" AS DATE) <= DATE {date_value}'


def _scoped_source_query(
    source_expression: str,
    contract: _TableContract,
    *,
    execution_end: date,
    validity_expression: str,
) -> str:
    where = _source_filter(contract, execution_end)
    end_value = _sql_string(execution_end.isoformat())
    replacements = ", ".join(
        f'CASE WHEN "{column}" IS NULL THEN NULL '
        f'WHEN CAST("{column}" AS DATE) > DATE {end_value} THEN DATE {end_value} '
        f'ELSE CAST("{column}" AS DATE) END AS "{column}"'
        for column in contract.clip_end_columns
    )
    censor_flags = ", ".join(
        f'("{column}" IS NOT NULL AND CAST("{column}" AS DATE) > DATE {end_value}) '
        f'AS "{column}_right_censored"'
        for column in contract.clip_end_columns
    )
    source_columns = f"source.* REPLACE ({replacements})" if replacements else "source.*"
    scoped_columns = f"{source_columns}, {censor_flags}" if censor_flags else source_columns
    if not contract.materialize_identity:
        return f"SELECT {scoped_columns} FROM {source_expression} source WHERE {where}"
    return f"""
        SELECT {scoped_columns}, identity_map.security_id
        FROM {source_expression} source
        JOIN {validity_expression} identity_map
          ON identity_map.stock_code = source.security_code
         AND CAST(source."{contract.date_column}" AS DATE)
             >= CAST(identity_map.valid_from AS DATE)
         AND (
                identity_map.valid_to IS NULL
             OR CAST(source."{contract.date_column}" AS DATE)
                <= CAST(identity_map.valid_to AS DATE)
         )
        WHERE {where}
    """


def build_strategy_replay_ads(
    *,
    source_snapshot_dir: Path,
    output: Path,
    bundle_id: str,
    scope_id: str,
    code_revision: str,
    benchmark_path: Path,
    industry_mapping_path: Path,
    ads_policy: StrategyReplayADSPolicy,
    corporate_action_policy: CorporateActionNormalizationPolicy,
) -> StrategyReplayADSBundle:
    """Publish one immutable, self-contained research ADS beside its source data."""

    if not _SAFE_ID.fullmatch(bundle_id):
        raise StrategyReplayADSError("bundle_id contains unsupported characters")
    if not _HEX_OBJECT.fullmatch(code_revision):
        raise StrategyReplayADSError("code_revision must be a full lowercase Git object id")
    scope = ads_policy.scope(scope_id)
    target = output.resolve()
    if target.name != bundle_id:
        raise StrategyReplayADSError("ADS output directory name must equal bundle_id")
    if target.exists():
        existing = load_strategy_replay_ads(
            target,
            ads_policy=ads_policy,
            reader_role=scope.reader_role,
            corporate_action_policy=corporate_action_policy,
        )
        if existing.publisher_code_revision != code_revision:
            raise StrategyReplayADSError(
                "existing ADS bundle was built by a different code revision"
            )
        return existing
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise StrategyReplayADSError("P1 dependencies are required for ADS publication") from exc
    try:
        source_bundle = load_historical_replay_bundle(source_snapshot_dir, corporate_action_policy)
    except HistoricalReplayBundleError as exc:
        raise StrategyReplayADSError("ADS source replay snapshot failed its gate") from exc
    source_manifest_path = source_snapshot_dir.resolve() / "snapshot.json"
    source_manifest_sha256 = _sha256(source_manifest_path)
    if (
        source_bundle.snapshot.snapshot_id != ads_policy.bootstrap_source_snapshot_id
        or source_bundle.digest != ads_policy.bootstrap_source_bundle_digest
        or source_manifest_sha256 != ads_policy.bootstrap_source_manifest_sha256
    ):
        raise StrategyReplayADSError("ADS bootstrap source is not the frozen accepted snapshot")
    benchmark = load_benchmark_curve(benchmark_path)
    industry_mapping = load_static_industry_mapping(
        industry_mapping_path,
        expected_version=ads_policy.industry_mapping_version,
    )
    if (
        benchmark.instrument_id != ads_policy.benchmark_instrument_id
        or benchmark.digest != ads_policy.benchmark_logical_digest
        or _sha256(benchmark_path) != ads_policy.benchmark_file_sha256
        or industry_mapping.artifact_digest != ads_policy.industry_mapping_logical_digest
        or _sha256(industry_mapping_path) != ads_policy.industry_mapping_file_sha256
    ):
        raise StrategyReplayADSError("ADS profile table does not match frozen policy identity")
    source_by_name = {item.dataset: item for item in source_bundle.datasets}
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{bundle_id}.", dir=target.parent))
    connection = duckdb.connect(":memory:")
    try:
        sessions = _complete_sessions(
            connection,
            source_bundle.partition_paths("trading_calendar"),
            scope,
        )
        decision_start = sessions[0]
        decision_end = sessions[-2]
        execution_end = sessions[-1]
        available_benchmark_dates = {item.market_date for item in benchmark.observations}
        missing_benchmark_dates = sorted(set(sessions).difference(available_benchmark_dates))
        if missing_benchmark_dates:
            raise StrategyReplayADSError(
                "ADS benchmark does not cover the authorized replay sessions"
            )
        scoped_benchmark = replace(
            benchmark,
            observations=tuple(
                item for item in benchmark.observations if item.market_date <= execution_end
            ),
        )
        tables_root = staging / "tables"
        tables_root.mkdir()
        table_manifests: list[dict[str, object]] = []
        validity_expression = _read_parquet_expression(
            source_bundle.partition_paths("security_code_validity")
        )
        for name in sorted(_TABLES):
            contract = _TABLES[name]
            source_paths = source_bundle.partition_paths(name)
            target_path = tables_root / f"{name}.parquet"
            source_expression = _read_parquet_expression(source_paths)
            scoped_query = _scoped_source_query(
                source_expression,
                contract,
                execution_end=execution_end,
                validity_expression=validity_expression,
            )
            order_by = ", ".join(f'"{column}"' for column in contract.sort_keys)
            connection.execute(
                f"COPY (SELECT * FROM ({scoped_query}) scoped ORDER BY {order_by}) "
                f"TO {_sql_string(str(target_path))} "
                "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
            )
            relation = f"read_parquet({_sql_string(str(target_path))})"
            description = connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
            columns = tuple(str(row[0]) for row in description)
            logical_schema = _logical_schema(description)
            missing_keys = set(contract.primary_key + contract.sort_keys) - set(columns)
            if missing_keys:
                raise StrategyReplayADSError(
                    f"ADS source table {name} is missing contract columns: {sorted(missing_keys)}"
                )
            counts = connection.execute(
                f'SELECT count(*), min(CAST("{contract.date_column}" AS DATE)), '
                f'max(CAST("{contract.date_column}" AS DATE)) FROM {relation}'
            ).fetchone()
            if counts is None:
                raise StrategyReplayADSError(f"ADS table count returned no row: {name}")
            row_count = int(counts[0])
            if row_count <= 0 and not contract.allow_empty:
                raise StrategyReplayADSError(f"ADS table is empty after scoping: {name}")
            primary = ", ".join(f'"{column}"' for column in contract.primary_key)
            duplicate = connection.execute(
                f"SELECT count(*) FROM (SELECT {primary}, count(*) AS n FROM {relation} "
                f"GROUP BY {primary} HAVING n <> 1)"
            ).fetchone()
            if duplicate is None or int(duplicate[0]):
                raise StrategyReplayADSError(f"ADS table primary key is not unique: {name}")
            schema_sha256 = _schema_sha256(target_path)
            censor_counts = {
                column: int(
                    connection.execute(
                        f'SELECT count(*) FROM {relation} '
                        f'WHERE "{column}_right_censored" = TRUE'
                    ).fetchone()[0]
                )
                for column in contract.clip_end_columns
            }
            table_digest, fingerprint = _table_content_digest(
                connection,
                relation,
                columns=columns,
                logical_schema=logical_schema,
                sort_keys=contract.sort_keys,
            )
            relative = target_path.relative_to(staging).as_posix()
            table_manifests.append(
                {
                    "name": name,
                    "role": contract.role,
                    "date_column": contract.date_column,
                    "primary_key": list(contract.primary_key),
                    "identity_materialization": (
                        "source_code_validity_to_security_id"
                        if contract.materialize_identity
                        else "none"
                    ),
                    "table_content_digest": table_digest,
                    "content_fingerprint": fingerprint,
                    "logical_schema": [dict(item) for item in logical_schema],
                    "right_censor": {
                        "policy": RIGHT_CENSOR_POLICY,
                        "columns": list(contract.clip_end_columns),
                        "counts": censor_counts,
                    },
                    "source_manifest": _source_manifest_payload(source_by_name[name]),
                    "parts": [
                        {
                            "path": relative,
                            "sha256": _sha256(target_path),
                            "size_bytes": target_path.stat().st_size,
                            "row_count": row_count,
                            "schema_sha256": schema_sha256,
                            "min_date": counts[1].isoformat() if counts[1] else None,
                            "max_date": counts[2].isoformat() if counts[2] else None,
                            "sort_keys": list(contract.sort_keys),
                        }
                    ],
                }
            )
        profile_manifests: list[dict[str, object]] = []
        for name, source_path, role, logical_digest, source_logical_digest, source_file_sha256 in (
            (
                "benchmark_curve",
                benchmark_path,
                "decision_visible_reference",
                scoped_benchmark.digest,
                benchmark.digest,
                ads_policy.benchmark_file_sha256,
            ),
            (
                "static_industry_mapping",
                industry_mapping_path,
                "runner_policy_reference",
                industry_mapping.artifact_digest,
                industry_mapping.artifact_digest,
                ads_policy.industry_mapping_file_sha256,
            ),
        ):
            target_path = tables_root / f"{name}.json"
            if name == "benchmark_curve":
                _write_json(target_path, scoped_benchmark.to_dict())
            else:
                shutil.copyfile(source_path, target_path)
            relative = target_path.relative_to(staging).as_posix()
            profile_manifests.append(
                {
                    "name": name,
                    "role": role,
                    "format": "json",
                    "logical_digest": logical_digest,
                    "source_identity": {
                        "logical_digest": source_logical_digest,
                        "file_sha256": source_file_sha256,
                    },
                    "parts": [
                        {
                            "path": relative,
                            "sha256": _sha256(target_path),
                            "size_bytes": target_path.stat().st_size,
                        }
                    ],
                }
            )
        source_cutoff = source_bundle.snapshot.knowledge_cutoff
        pit_rows = [
            StrategyReplayADSPITBinding(
                decision_date=market_date,
                decision_cutoff=datetime.combine(
                    market_date,
                    time(15, 0),
                    tzinfo=MARKET_TIMEZONE,
                ),
                source_snapshot_id=source_bundle.snapshot.snapshot_id,
                source_knowledge_cutoff=source_cutoff,
                evidence_mode=PIT_EVIDENCE_MODE,
                evidence_status=PIT_EVIDENCE_STATUS,
            ).to_dict()
            for market_date in sessions[:-1]
        ]
        pit_mapping = {
            "mode": PIT_EVIDENCE_MODE,
            "status": PIT_EVIDENCE_STATUS,
            "per_decision_publication_proven": False,
            "rows": pit_rows,
        }
        pit_mapping["digest"] = _canonical_digest(pit_mapping)
        digest_value = {
            "contract_id": ads_policy.contract_id,
            "policy_sha256": ads_policy.digest,
            "profile": ADS_PROFILE,
            "bundle_id": bundle_id,
            "scope": {
                "scope_id": scope.scope_id,
                "reader_role": scope.reader_role,
                "partition_start": scope.partition_start.isoformat(),
                "partition_end": scope.partition_end.isoformat(),
                "decision_start": decision_start.isoformat(),
                "decision_end": decision_end.isoformat(),
                "execution_end": execution_end.isoformat(),
                "context_policy": ads_policy.context_policy,
            },
            "publisher": {
                "code_revision": code_revision,
                "runtime": _runtime_identity(),
            },
            "source": {
                "kind": "accepted_historical_replay_snapshot_bootstrap",
                "snapshot_id": source_bundle.snapshot.snapshot_id,
                "snapshot_digest": source_bundle.digest,
                "manifest_sha256": source_manifest_sha256,
                "source_revision": source_bundle.snapshot.source_revision,
                "knowledge_cutoff": source_bundle.snapshot.knowledge_cutoff.isoformat(),
                "universe_policy_version": source_bundle.universe_policy_version,
            },
            "pit_mapping": pit_mapping,
            "tables": table_manifests,
            "profile_tables": profile_manifests,
        }
        manifest = {
            "schema_version": ADS_SCHEMA_VERSION,
            "kind": ADS_KIND,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "research_only": True,
            "execution_authorized": False,
            **digest_value,
            "bundle_content_digest": _canonical_digest(digest_value),
        }
        _write_json(staging / "manifest.json", manifest)
        (staging / "READY").write_text(_sha256(staging / "manifest.json") + "\n", encoding="ascii")
        for path in sorted(staging.rglob("*")):
            if path.is_symlink():
                raise StrategyReplayADSError("symbolic links are forbidden in ADS bundles")
            path.chmod(0o750 if path.is_dir() else 0o640)
        staging.chmod(0o750)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        connection.close()
    return load_strategy_replay_ads(
        target,
        ads_policy=ads_policy,
        reader_role=scope.reader_role,
        corporate_action_policy=corporate_action_policy,
    )


def _parse_date(value: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(_required_text(value, key))
    except ValueError as exc:
        raise StrategyReplayADSError(f"{key} must be an ISO date") from exc


def _verify_table(
    connection: Any,
    root: Path,
    raw: Mapping[str, Any],
    contract: _TableContract,
    *,
    verify_logical_content: bool,
) -> tuple[DatasetManifest, date, date]:
    name = _required_text(raw, "name")
    if raw.get("role") != contract.role or raw.get("date_column") != contract.date_column:
        raise StrategyReplayADSError(f"ADS table role/date contract mismatch: {name}")
    if raw.get("primary_key") != list(contract.primary_key):
        raise StrategyReplayADSError(f"ADS table primary key contract mismatch: {name}")
    expected_identity = (
        "source_code_validity_to_security_id" if contract.materialize_identity else "none"
    )
    if raw.get("identity_materialization") != expected_identity:
        raise StrategyReplayADSError(f"ADS table identity contract mismatch: {name}")
    parts = raw.get("parts")
    if (
        not isinstance(parts, list)
        or not parts
        or any(not isinstance(part, dict) for part in parts)
    ):
        raise StrategyReplayADSError(f"ADS table must contain semantic parts: {name}")
    paths: list[Path] = []
    part_paths: list[str] = []
    part_checksums: dict[str, str] = {}
    declared_rows = 0
    schema_sha256: str | None = None
    for index, part in enumerate(parts):
        assert isinstance(part, dict)
        part_name = _required_text(part, "path")
        path = _resolve_inside(root, part_name, field=f"{name}.parts[{index}].path")
        size = part.get("size_bytes")
        rows = part.get("row_count")
        if (
            path.is_symlink()
            or not path.is_file()
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(rows, int)
            or isinstance(rows, bool)
            or rows < 0
            or path.stat().st_size != size
            or _sha256(path) != part.get("sha256")
            or part.get("sort_keys") != list(contract.sort_keys)
        ):
            raise StrategyReplayADSError(f"ADS table part integrity failed: {name}")
        current_schema_sha256 = _schema_sha256(path)
        if current_schema_sha256 != part.get("schema_sha256") or (
            schema_sha256 is not None and current_schema_sha256 != schema_sha256
        ):
            raise StrategyReplayADSError(f"ADS table schema mismatch: {name}")
        schema_sha256 = current_schema_sha256
        paths.append(path)
        part_paths.append(part_name)
        part_checksums[part_name] = _required_text(part, "sha256")
        declared_rows += rows
    assert schema_sha256 is not None
    if declared_rows == 0 and not contract.allow_empty:
        raise StrategyReplayADSError(f"ADS table is empty: {name}")
    relation = _read_parquet_expression(paths)
    description = connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    columns = tuple(str(row[0]) for row in description)
    logical_schema = _logical_schema(description)
    if raw.get("logical_schema") != [dict(item) for item in logical_schema]:
        raise StrategyReplayADSError(f"ADS table logical schema mismatch: {name}")
    primary = ", ".join(f'"{column}"' for column in contract.primary_key)
    stats = connection.execute(
        f'SELECT count(*), min(CAST("{contract.date_column}" AS DATE)), '
        f'max(CAST("{contract.date_column}" AS DATE)) FROM {relation}'
    ).fetchone()
    duplicate = connection.execute(
        f"SELECT count(*) FROM (SELECT {primary}, count(*) AS n FROM {relation} "
        f"GROUP BY {primary} HAVING n <> 1)"
    ).fetchone()
    if stats is None or int(stats[0]) != declared_rows or duplicate is None or int(duplicate[0]):
        raise StrategyReplayADSError(f"ADS table row/key verification failed: {name}")
    min_date, max_date = stats[1], stats[2]
    for index, (part, path) in enumerate(zip(parts, paths, strict=True)):
        assert isinstance(part, dict)
        part_stats = connection.execute(
            f'SELECT min(CAST("{contract.date_column}" AS DATE)), '
            f'max(CAST("{contract.date_column}" AS DATE)) FROM '
            f"read_parquet({_sql_string(str(path))})"
        ).fetchone()
        if part_stats is None or (
            part_stats[0].isoformat() if part_stats[0] else None
        ) != part.get("min_date") or (
            part_stats[1].isoformat() if part_stats[1] else None
        ) != part.get("max_date"):
            raise StrategyReplayADSError(
                f"ADS table part date bounds mismatch: {name}[{index}]"
            )
    censor = raw.get("right_censor")
    expected_censor_columns = list(contract.clip_end_columns)
    if (
        not isinstance(censor, dict)
        or censor.get("policy") != RIGHT_CENSOR_POLICY
        or censor.get("columns") != expected_censor_columns
        or not isinstance(censor.get("counts"), dict)
        or set(censor["counts"]) != set(expected_censor_columns)
    ):
        raise StrategyReplayADSError(f"ADS table right-censor contract mismatch: {name}")
    for column in contract.clip_end_columns:
        flag = f"{column}_right_censored"
        if flag not in columns:
            raise StrategyReplayADSError(f"ADS table is missing right-censor flag: {name}")
        count = connection.execute(
            f'SELECT count(*) FROM {relation} WHERE "{flag}" = TRUE'
        ).fetchone()
        if count is None or int(count[0]) != censor["counts"].get(column):
            raise StrategyReplayADSError(f"ADS table right-censor count mismatch: {name}")
    content_digest = raw.get("table_content_digest")
    fingerprint = raw.get("content_fingerprint")
    if (
        not isinstance(content_digest, str)
        or not _SHA256.fullmatch(content_digest)
        or fingerprint
        != {
            "method": CONTENT_DIGEST_METHOD,
            "row_count": declared_rows,
            "logical_row_stream_sha256": content_digest,
        }
    ):
        raise StrategyReplayADSError(f"ADS table content digest mismatch: {name}")
    if verify_logical_content:
        verified_digest, verified_fingerprint = _table_content_digest(
            connection,
            relation,
            columns=columns,
            logical_schema=logical_schema,
            sort_keys=contract.sort_keys,
        )
        if verified_digest != content_digest or verified_fingerprint != fingerprint:
            raise StrategyReplayADSError(f"ADS table logical content verification failed: {name}")
    source = raw.get("source_manifest")
    if not isinstance(source, dict) or source.get("dataset") != name:
        raise StrategyReplayADSError(f"ADS table source manifest is invalid: {name}")
    source_revisions = source.get("source_revisions")
    parent_ids = source.get("parent_manifest_ids")
    source_partitions = source.get("partitions")
    source_checksums = source.get("content_checksums")
    if (
        not isinstance(source_revisions, dict)
        or not isinstance(parent_ids, list)
        or not isinstance(source_partitions, list)
        or not isinstance(source_checksums, dict)
    ):
        raise StrategyReplayADSError(f"ADS table source lineage is invalid: {name}")
    try:
        source_dataset = DatasetManifest(
            dataset=name,
            version=_required_text(source, "version"),
            source_revisions={str(key): str(value) for key, value in source_revisions.items()},
            row_count=int(source["row_count"]),
            partitions=tuple(str(item) for item in source_partitions),
            schema_hash=_required_text(source, "schema_hash"),
            content_checksums={str(key): str(value) for key, value in source_checksums.items()},
            created_at=datetime.fromisoformat(_required_text(source, "created_at")),
            knowledge_cutoff=datetime.fromisoformat(_required_text(source, "knowledge_cutoff")),
            parent_manifest_ids=tuple(str(item) for item in parent_ids),
        )
        if source_dataset.manifest_id != source.get("manifest_id"):
            raise StrategyReplayADSError(f"ADS table source manifest digest mismatch: {name}")
        dataset = DatasetManifest(
            dataset=name,
            version=source_dataset.version,
            source_revisions=source_dataset.source_revisions,
            row_count=declared_rows,
            partitions=tuple(part_paths),
            schema_hash=schema_sha256,
            content_checksums=part_checksums,
            created_at=source_dataset.created_at,
            knowledge_cutoff=source_dataset.knowledge_cutoff,
            parent_manifest_ids=source_dataset.parent_manifest_ids,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StrategyReplayADSError(f"ADS table dataset lineage is invalid: {name}") from exc
    return dataset, min_date, max_date


def _verify_profile_tables(
    root: Path,
    raw_tables: object,
    ads_policy: StrategyReplayADSPolicy,
    *,
    execution_end: date,
) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(raw_tables, list):
        raise StrategyReplayADSError("strategy-replay ADS profile tables are invalid")
    by_name: dict[str, Mapping[str, Any]] = {}
    for item in raw_tables:
        if not isinstance(item, dict):
            raise StrategyReplayADSError("strategy-replay ADS profile table is invalid")
        name = _required_text(item, "name")
        if name in by_name:
            raise StrategyReplayADSError(f"duplicate ADS profile table: {name}")
        by_name[name] = item
    expected = {
        "benchmark_curve": (
            "decision_visible_reference",
            ads_policy.benchmark_logical_digest,
            ads_policy.benchmark_file_sha256,
        ),
        "static_industry_mapping": (
            "runner_policy_reference",
            ads_policy.industry_mapping_logical_digest,
            ads_policy.industry_mapping_file_sha256,
        ),
    }
    if set(by_name) != set(expected):
        raise StrategyReplayADSError("strategy-replay ADS profile table set is incomplete")
    resolved: dict[str, tuple[str, ...]] = {}
    for name, (role, source_logical_digest, source_file_sha256) in expected.items():
        item = by_name[name]
        parts = item.get("parts")
        source_identity = item.get("source_identity")
        if (
            item.get("role") != role
            or item.get("format") != "json"
            or not isinstance(item.get("logical_digest"), str)
            or not _SHA256.fullmatch(str(item.get("logical_digest")))
            or source_identity
            != {
                "logical_digest": source_logical_digest,
                "file_sha256": source_file_sha256,
            }
            or not isinstance(parts, list)
            or len(parts) != 1
            or not isinstance(parts[0], dict)
        ):
            raise StrategyReplayADSError(f"ADS profile table contract mismatch: {name}")
        part = parts[0]
        path_value = _required_text(part, "path")
        path = _resolve_inside(root, path_value, field=f"{name}.part.path")
        size = part.get("size_bytes")
        if (
            path.is_symlink()
            or not path.is_file()
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or path.stat().st_size != size
            or not isinstance(part.get("sha256"), str)
            or not _SHA256.fullmatch(str(part.get("sha256")))
            or _sha256(path) != part.get("sha256")
        ):
            raise StrategyReplayADSError(f"ADS profile table integrity failed: {name}")
        resolved[name] = (path_value,)
    benchmark = load_benchmark_curve(root / resolved["benchmark_curve"][0])
    industry = load_static_industry_mapping(
        root / resolved["static_industry_mapping"][0],
        expected_version=ads_policy.industry_mapping_version,
    )
    if (
        benchmark.instrument_id != ads_policy.benchmark_instrument_id
        or benchmark.digest != by_name["benchmark_curve"].get("logical_digest")
        or benchmark.observations[-1].market_date > execution_end
        or industry.artifact_digest != ads_policy.industry_mapping_logical_digest
        or industry.artifact_digest
        != by_name["static_industry_mapping"].get("logical_digest")
    ):
        raise StrategyReplayADSError("ADS profile logical identity mismatch")
    return resolved


def _parse_pit_bindings(
    value: object,
    *,
    expected_dates: Sequence[date],
    source_snapshot_id: str,
) -> tuple[str, Mapping[date, StrategyReplayADSPITBinding]]:
    if not isinstance(value, dict):
        raise StrategyReplayADSError("strategy-replay ADS PIT mapping is invalid")
    digest = _required_text(value, "digest")
    unsigned = {key: item for key, item in value.items() if key != "digest"}
    rows = value.get("rows")
    if (
        digest != _canonical_digest(unsigned)
        or value.get("mode") != PIT_EVIDENCE_MODE
        or value.get("status") != PIT_EVIDENCE_STATUS
        or value.get("per_decision_publication_proven") is not False
        or not isinstance(rows, list)
    ):
        raise StrategyReplayADSError("strategy-replay ADS PIT mapping contract mismatch")
    bindings: dict[date, StrategyReplayADSPITBinding] = {}
    for item in rows:
        if not isinstance(item, dict) or set(item) != {
            "decision_date",
            "decision_cutoff",
            "source_snapshot_id",
            "source_knowledge_cutoff",
            "evidence_mode",
            "evidence_status",
            "per_decision_publication_proven",
        }:
            raise StrategyReplayADSError("strategy-replay ADS PIT row is invalid")
        try:
            decision_date = date.fromisoformat(_required_text(item, "decision_date"))
            decision_cutoff = datetime.fromisoformat(_required_text(item, "decision_cutoff"))
            source_cutoff = datetime.fromisoformat(
                _required_text(item, "source_knowledge_cutoff")
            )
        except ValueError as exc:
            raise StrategyReplayADSError("strategy-replay ADS PIT timestamp is invalid") from exc
        binding = StrategyReplayADSPITBinding(
            decision_date=decision_date,
            decision_cutoff=decision_cutoff,
            source_snapshot_id=_required_text(item, "source_snapshot_id"),
            source_knowledge_cutoff=source_cutoff,
            evidence_mode=_required_text(item, "evidence_mode"),
            evidence_status=_required_text(item, "evidence_status"),
        )
        if (
            decision_date in bindings
            or binding.to_dict() != item
            or binding.source_snapshot_id != source_snapshot_id
            or binding.decision_cutoff.tzinfo is None
            or binding.decision_cutoff.astimezone(MARKET_TIMEZONE).date() != decision_date
        ):
            raise StrategyReplayADSError("strategy-replay ADS PIT binding is invalid")
        bindings[decision_date] = binding
    if tuple(sorted(bindings)) != tuple(expected_dates):
        raise StrategyReplayADSError("strategy-replay ADS PIT dates do not match decision sessions")
    return digest, bindings


def load_strategy_replay_ads(
    bundle_dir: Path,
    *,
    ads_policy: StrategyReplayADSPolicy,
    reader_role: str,
    corporate_action_policy: CorporateActionNormalizationPolicy,
    verify_logical_content: bool = False,
) -> StrategyReplayADSBundle:
    """Verify an ADS envelope and authorize exactly one configured role before reading rows."""

    root = bundle_dir.resolve()
    manifest_path = root / "manifest.json"
    ready_path = root / "READY"
    if not manifest_path.is_file() or not ready_path.is_file():
        raise StrategyReplayADSError("strategy-replay ADS is missing manifest.json or READY")
    manifest_sha256 = _sha256(manifest_path)
    if ready_path.read_text(encoding="ascii").strip() != manifest_sha256:
        raise StrategyReplayADSError("strategy-replay ADS READY does not bind its manifest")
    manifest = _load_object(manifest_path)
    if (
        manifest.get("schema_version") != ADS_SCHEMA_VERSION
        or manifest.get("kind") != ADS_KIND
        or manifest.get("profile") != ADS_PROFILE
        or manifest.get("contract_id") != ads_policy.contract_id
        or manifest.get("policy_sha256") != ads_policy.digest
        or manifest.get("research_only") is not True
        or manifest.get("execution_authorized") is not False
    ):
        raise StrategyReplayADSError("strategy-replay ADS envelope contract mismatch")
    bundle_id = _required_text(manifest, "bundle_id")
    if root.name != bundle_id or not _SAFE_ID.fullmatch(bundle_id):
        raise StrategyReplayADSError("strategy-replay ADS bundle id does not match its directory")
    scope_value = manifest.get("scope")
    source = manifest.get("source")
    publisher = manifest.get("publisher")
    tables = manifest.get("tables")
    profile_tables = manifest.get("profile_tables")
    pit_mapping = manifest.get("pit_mapping")
    if (
        not isinstance(scope_value, dict)
        or not isinstance(source, dict)
        or not isinstance(publisher, dict)
        or not isinstance(tables, list)
        or not isinstance(profile_tables, list)
        or not isinstance(pit_mapping, dict)
    ):
        raise StrategyReplayADSError("strategy-replay ADS manifest sections are invalid")
    digest_value = {
        key: value
        for key, value in manifest.items()
        if key
        not in {
            "schema_version",
            "kind",
            "built_at",
            "research_only",
            "execution_authorized",
            "bundle_content_digest",
        }
    }
    bundle_content_digest = _required_text(manifest, "bundle_content_digest")
    if bundle_content_digest != _canonical_digest(digest_value):
        raise StrategyReplayADSError("strategy-replay ADS bundle content digest mismatch")
    publisher_code_revision = _required_text(publisher, "code_revision")
    publisher_runtime = publisher.get("runtime")
    if (
        not _HEX_OBJECT.fullmatch(publisher_code_revision)
        or not isinstance(publisher_runtime, dict)
        or not isinstance(publisher_runtime.get("python_series"), str)
        or not isinstance(publisher_runtime.get("packages"), dict)
        or set(publisher_runtime["packages"])
        != {
            "duckdb",
            "polars",
            "pyarrow",
            "quanthus",
        }
        or any(
            not isinstance(value, str) or not value
            for value in publisher_runtime["packages"].values()
        )
    ):
        raise StrategyReplayADSError("strategy-replay ADS publisher identity is invalid")
    scope_id = _required_text(scope_value, "scope_id")
    scope = ads_policy.scope(scope_id)
    if reader_role != scope.reader_role or scope_value.get("reader_role") != reader_role:
        raise StrategyReplayADSError("reader role is not authorized for this ADS scope")
    decision_start = _parse_date(scope_value, "decision_start")
    decision_end = _parse_date(scope_value, "decision_end")
    execution_end = _parse_date(scope_value, "execution_end")
    if (
        scope_value.get("partition_start") != scope.partition_start.isoformat()
        or scope_value.get("partition_end") != scope.partition_end.isoformat()
        or scope_value.get("context_policy") != ads_policy.context_policy
        or not (
            scope.partition_start
            <= decision_start
            <= decision_end
            < execution_end
            <= scope.partition_end
        )
    ):
        raise StrategyReplayADSError("strategy-replay ADS scope boundary mismatch")
    by_name: dict[str, Mapping[str, Any]] = {}
    for item in tables:
        if not isinstance(item, dict):
            raise StrategyReplayADSError("strategy-replay ADS table manifest is invalid")
        name = _required_text(item, "name")
        if name in by_name:
            raise StrategyReplayADSError(f"duplicate ADS table: {name}")
        by_name[name] = item
    if set(by_name) != set(_TABLES):
        raise StrategyReplayADSError("strategy-replay ADS table set is incomplete")
    profile_parts = _verify_profile_tables(
        root,
        profile_tables,
        ads_policy,
        execution_end=execution_end,
    )
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise StrategyReplayADSError("P1 dependencies are required for ADS verification") from exc
    connection = duckdb.connect(":memory:")
    try:
        datasets: list[DatasetManifest] = []
        for name in sorted(_TABLES):
            dataset, _minimum, maximum = _verify_table(
                connection,
                root,
                by_name[name],
                _TABLES[name],
                verify_logical_content=verify_logical_content,
            )
            if maximum is not None and maximum > execution_end:
                raise StrategyReplayADSError(f"ADS table crosses its sealed scope: {name}")
            datasets.append(dataset)
        calendar_relation = _read_parquet_expression(
            tuple(
                (root / partition).resolve()
                for dataset in datasets
                if dataset.dataset == "trading_calendar"
                for partition in dataset.partitions
            )
        )
        expected_decision_dates = tuple(
            row[0]
            for row in connection.execute(
                f"SELECT CAST(trade_date AS DATE) FROM {calendar_relation} "
                "WHERE exchange IN ('SSE', 'SZSE') AND is_open = 1 "
                "AND CAST(trade_date AS DATE) BETWEEN ? AND ? "
                "GROUP BY trade_date HAVING count(*) = 2 AND count(DISTINCT exchange) = 2 "
                "ORDER BY trade_date",
                [decision_start, decision_end],
            ).fetchall()
        )
    finally:
        connection.close()
    source_snapshot_id = _required_text(source, "snapshot_id")
    source_manifest_sha256 = _required_text(source, "manifest_sha256")
    if (
        source.get("kind") != "accepted_historical_replay_snapshot_bootstrap"
        or source_snapshot_id != ads_policy.bootstrap_source_snapshot_id
        or source.get("snapshot_digest") != ads_policy.bootstrap_source_bundle_digest
        or source_manifest_sha256 != ads_policy.bootstrap_source_manifest_sha256
        or not _SHA256.fullmatch(source_manifest_sha256)
    ):
        raise StrategyReplayADSError("strategy-replay ADS source lineage is invalid")
    pit_mapping_digest, pit_bindings = _parse_pit_bindings(
        pit_mapping,
        expected_dates=expected_decision_dates,
        source_snapshot_id=source_snapshot_id,
    )
    source_cutoff = datetime.fromisoformat(_required_text(source, "knowledge_cutoff"))
    by_dataset = {dataset.dataset: dataset for dataset in datasets}
    normalized = by_dataset["corporate_actions_normalized"]
    exception_count = by_dataset["corporate_action_exceptions"].row_count
    by_dataset["corporate_actions_normalized"] = replace(
        normalized,
        source_revisions={
            **normalized.source_revisions,
            "normalization_exception_count": str(exception_count),
        },
    )
    historical = HistoricalReplayBundle(
        root=root,
        snapshot=SnapshotRef(
            market_date=execution_end,
            knowledge_cutoff=source_cutoff,
            source_revision=(
                f"strategy-replay-ads={bundle_id}|content={bundle_content_digest}"
                f"|manifest={manifest_sha256}|pit={pit_mapping_digest}"
                f"|source={source_snapshot_id}"
            ),
            snapshot_id=bundle_id,
        ),
        universe_policy_version=_required_text(source, "universe_policy_version"),
        datasets=tuple(by_dataset[name] for name in sorted(by_dataset)),
    )
    try:
        historical = validate_historical_replay_bundle_contract(historical, corporate_action_policy)
    except HistoricalReplayBundleError as exc:
        raise StrategyReplayADSError("strategy-replay ADS replay semantics failed") from exc
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() not in {"manifest.json", "READY"}
    }
    declared_files = {
        _required_text(part, "path")
        for table in tables
        if isinstance(table, dict)
        for part in table.get("parts", [])
        if isinstance(part, dict)
    } | {
        _required_text(part, "path")
        for table in profile_tables
        if isinstance(table, dict)
        for part in table.get("parts", [])
        if isinstance(part, dict)
    }
    if actual_files != declared_files:
        raise StrategyReplayADSError("strategy-replay ADS file set differs from its manifest")
    return StrategyReplayADSBundle(
        root=root,
        bundle_id=bundle_id,
        bundle_content_digest=bundle_content_digest,
        manifest_sha256=manifest_sha256,
        scope_id=scope_id,
        reader_role=reader_role,
        decision_start=decision_start,
        decision_end=decision_end,
        execution_end=execution_end,
        publisher_code_revision=publisher_code_revision,
        publisher_runtime=publisher_runtime,
        source_snapshot_id=source_snapshot_id,
        source_manifest_sha256=source_manifest_sha256,
        pit_mapping_digest=pit_mapping_digest,
        pit_bindings=pit_bindings,
        profile_parts=profile_parts,
        historical_bundle=historical,
    )


class StrategyReplayADSFrameReader(HistoricalReplayFrameReader):
    """Frame reader that prevents context rows from becoming decision samples."""

    def __init__(self, ads_bundle: StrategyReplayADSBundle) -> None:
        self._ads_bundle = ads_bundle
        self._accesses: list[tuple[str, str]] = []
        self._runner_access_count = 0
        super().__init__(ads_bundle.historical_bundle)
        self._execution_decisions = {
            execution_date: decision_date
            for decision_date, execution_date in self._date_pairs(
                ads_bundle.decision_start,
                ads_bundle.decision_end,
            )
        }

    @property
    def ads_identity(self) -> Mapping[str, str]:
        return {
            "bundle_id": self._ads_bundle.bundle_id,
            "bundle_content_digest": self._ads_bundle.bundle_content_digest,
            "manifest_sha256": self._ads_bundle.manifest_sha256,
            "pit_mapping_digest": self._ads_bundle.pit_mapping_digest,
        }

    @property
    def data_content_digest(self) -> str:
        return self._ads_bundle.bundle_content_digest

    @property
    def data_manifest_sha256(self) -> str:
        return self._ads_bundle.manifest_sha256

    @property
    def runner_only_access_count(self) -> int:
        return self._runner_access_count

    @property
    def access_ledger_digest(self) -> str:
        return _canonical_digest(
            [
                {"sequence": index, "role": role, "table": table}
                for index, (role, table) in enumerate(self._accesses)
            ]
        )

    def _record_access(self, role: str, *tables: str) -> None:
        for table in tables:
            self._accesses.append((role, table))
        if role == "runner_only":
            self._runner_access_count += 1

    def load_benchmark(self) -> BenchmarkCurve:
        self._record_access("decision_visible_reference", "benchmark_curve")
        paths = self._ads_bundle.profile_paths("benchmark_curve")
        if len(paths) != 1:
            raise StrategyReplayADSError("benchmark profile requires one JSON part")
        return load_benchmark_curve(paths[0])

    def load_industry_mapping(self, *, expected_version: str) -> StaticIndustryMapping:
        self._record_access("runner_policy_reference", "static_industry_mapping")
        paths = self._ads_bundle.profile_paths("static_industry_mapping")
        if len(paths) != 1:
            raise StrategyReplayADSError("industry profile requires one JSON part")
        return load_static_industry_mapping(paths[0], expected_version=expected_version)

    def _snapshot(self, market_date: date, *, role: str) -> SnapshotRef:
        if role == "decision":
            try:
                binding = self._ads_bundle.pit_bindings[market_date]
            except KeyError as exc:
                raise StrategyReplayADSError(
                    f"decision date is missing its ADS PIT binding: {market_date.isoformat()}"
                ) from exc
            knowledge_cutoff = binding.decision_cutoff
            authorization = f"decision={market_date.isoformat()}"
        elif role == "execution":
            try:
                decision_date = self._execution_decisions[market_date]
                binding = self._ads_bundle.pit_bindings[decision_date]
            except KeyError as exc:
                raise StrategyReplayADSError(
                    "execution date is not the next session of an authorized ADS decision: "
                    f"{market_date.isoformat()}"
                ) from exc
            knowledge_cutoff = datetime.combine(
                market_date,
                time(15, 0),
                tzinfo=MARKET_TIMEZONE,
            )
            authorization = f"authorized-by-decision={decision_date.isoformat()}"
        else:
            raise StrategyReplayADSError(f"unsupported ADS snapshot role: {role}")
        return SnapshotRef(
            market_date=market_date,
            knowledge_cutoff=knowledge_cutoff,
            source_revision=(
                f"{self.bundle.snapshot.source_revision}|pit-mode={binding.evidence_mode}"
                f"|pit-status={binding.evidence_status}|{authorization}"
            ),
            snapshot_id=(
                f"{self.bundle.snapshot.snapshot_id}.{role}.{market_date.strftime('%Y%m%d')}"
            ),
        )

    def _bars_for_date(
        self, market_date: date, security_code: str | None = None
    ) -> tuple[Any, ...]:
        self._record_access("decision_visible", "daily_universe", "daily_ohlcv_none")
        return super()._bars_for_date(market_date, security_code)

    def _history(
        self, security_code: str, market_date: date, sessions: int
    ) -> tuple[Any, ...]:
        self._record_access("decision_visible", "daily_ohlcv_none")
        return super()._history(security_code, market_date, sessions)

    def _session(self, security_code: str, execution_date: date) -> Any:
        self._record_access(
            "runner_only", "price_limit_execution_daily", "daily_ohlcv_none"
        )
        return super()._session(security_code, execution_date)

    def _block_reason(self, security_code: str, market_date: date) -> str | None:
        self._record_access(
            "runner_only", "corporate_action_exceptions", "price_limit_execution_daily"
        )
        return super()._block_reason(security_code, market_date)

    def _corporate_actions(
        self, security_code: str, execution_date: date, prior_mark_price: Decimal
    ) -> tuple[Any, ...]:
        self._record_access("runner_only", "corporate_actions_normalized")
        return super()._corporate_actions(security_code, execution_date, prior_mark_price)

    def _authorize_scope(self, scope_start: date, scope_end: date) -> None:
        if (
            scope_start < self._ads_bundle.decision_start
            or scope_end > self._ads_bundle.decision_end
        ):
            raise StrategyReplayADSError(
                "requested decision range is outside the authorized ADS scope"
            )

    def frames(self, scope_start: date, scope_end: date) -> HistoricalReplayFrameSequence:
        self._authorize_scope(scope_start, scope_end)
        return super().frames(scope_start, scope_end)

    def audit(self, scope_start: date, scope_end: date) -> HistoricalFrameAuditReport:
        self._authorize_scope(scope_start, scope_end)
        return super().audit(scope_start, scope_end)


def load_strategy_replay_ads_reader(
    bundle_dir: Path,
    *,
    ads_policy: StrategyReplayADSPolicy,
    reader_role: str,
    corporate_action_policy: CorporateActionNormalizationPolicy,
) -> StrategyReplayADSFrameReader:
    """Open the runner-owned typed surface; strategy callbacks never receive ADS paths."""

    bundle = load_strategy_replay_ads(
        bundle_dir,
        ads_policy=ads_policy,
        reader_role=reader_role,
        corporate_action_policy=corporate_action_policy,
    )
    return StrategyReplayADSFrameReader(bundle)
