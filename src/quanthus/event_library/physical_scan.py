"""P4 Research ADM materialization and deterministic forward event scan."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time as runtime_clock
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.backtest.data_provider import (
    ReplayDataProvider,
    ReplayDataSession,
    load_replay_data_provider_config,
)
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.backtest.inputs import P1DailyExecutionEvidence
from quanthus.concentration import StaticIndustryMembership
from quanthus.event_library.contracts import sha256_digest
from quanthus.event_library.job_local import require_job_local_session
from quanthus.event_library.rule_card import CandidateRuleCard, load_candidate_rule_card
from quanthus.event_library.scan import (
    CandidateDailyInput,
    CandidateEntryEvidence,
    CandidateReason,
    CandidateScanResult,
    CandidateScanShard,
    EntryPartitionResult,
    ScannedCandidate,
    finalize_candidate_scan_shards,
    partition_executable_entries,
    scan_forward_candidate_series,
)
from quanthus.event_library.source import EventScanSourcePolicy, load_event_scan_source_policy
from quanthus.event_library.splits import SplitPolicy, load_split_policy
from quanthus.industry_mapping import (
    StaticIndustryMapping,
    load_static_industry_mapping,
)
from quanthus.policy import find_project_root
from quanthus.research_adm import (
    HistoricalIndustryReference,
    P4ResearchADMPolicy,
    ResearchADMError,
    collect_p4_historical_industry_supplement,
    load_p4_historical_industry_supplement,
    load_p4_research_adm_policy,
)

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
ADM_SCHEMA = "quanthus.p4-research-adm.v1"
PACKAGE_SCHEMA = "quanthus.p4-event-library.v2"
MATCHER_POLICY_VERSION = "p2-daily-research-v1"


class PhysicalEventScanError(RuntimeError):
    """Raised when physical P4 projection, scan, or publication fails closed."""


@dataclass(frozen=True)
class P4ResearchADM:
    """Immutable application-data-mart input package consumed by the P4 Event Library."""

    root: Path
    policy: P4ResearchADMPolicy
    candidate_path: Path
    industry_mapping: StaticIndustryMapping
    manifest_path: Path
    manifest_sha256: str
    content_digest: str
    snapshot_id: str
    trading_dates: tuple[date, ...]
    row_count: int

    @property
    def projection_manifest_path(self) -> Path:
        """Compatibility alias for pre-ADM callers."""
        return self.manifest_path

    @property
    def projection_manifest_sha256(self) -> str:
        """Compatibility alias for pre-ADM callers."""
        return self.manifest_sha256

    @property
    def projection_content_digest(self) -> str:
        """Compatibility alias for pre-ADM callers."""
        return self.content_digest


EventCandidateProjection = P4ResearchADM


@dataclass(frozen=True)
class PhysicalEventScanResult:
    root: Path
    manifest_sha256: str
    content_digest: str
    candidate_events: int
    executable_events: int
    not_executable_events: int


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _tree_bytes(root: Path) -> int:
    if root.is_file():
        return root.stat().st_size
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_bytes(dict(value)) + b"\n")


def _write_jsonl(path: Path, values: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        for value in values:
            stream.write(_canonical_bytes(dict(value)) + b"\n")


def _sql_path(path: Path) -> str:
    return "'" + str(path.resolve()).replace("'", "''") + "'"


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_parquet_expression(paths: Sequence[Path]) -> str:
    if not paths:
        raise PhysicalEventScanError("physical projection source table has no parquet files")
    values = ",".join(_sql_path(path) for path in paths)
    return f"read_parquet([{values}], union_by_name=true, hive_partitioning=true)"


def _safe_relative(value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise PhysicalEventScanError("DWD manifest path must be text")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() == ".":
        raise PhysicalEventScanError("DWD manifest contains an unsafe file path")
    return relative


def _table_paths(dwd_root: Path, table: str) -> tuple[Path, ...]:
    try:
        manifest = json.loads((dwd_root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PhysicalEventScanError("cannot read DWD manifest for P4 projection") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise PhysicalEventScanError("DWD manifest file inventory is invalid")
    paths: list[Path] = []
    for raw in manifest["files"]:
        if not isinstance(raw, dict):
            raise PhysicalEventScanError("DWD manifest file inventory row is invalid")
        relative = _safe_relative(raw.get("path"))
        text = relative.as_posix()
        if text.endswith(f"/{table}.parquet") or f"/{table}/" in text:
            path = dwd_root.joinpath(*relative.parts)
            if not path.is_file() or path.is_symlink():
                raise PhysicalEventScanError(f"DWD table member is unreadable: {text}")
            paths.append(path)
    if not paths:
        raise PhysicalEventScanError(f"DWD manifest has no physical members for {table}")
    return tuple(sorted(paths))


def _json_scalar(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _aware(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PhysicalEventScanError(f"{field_name} is not an ISO timestamp") from exc
    else:
        raise PhysicalEventScanError(f"{field_name} must be a timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PhysicalEventScanError(f"{field_name} must be timezone-aware")
    return parsed


def _decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    if not parsed.is_finite():
        return None
    return parsed


def _materialize_dwd_static_industry_mapping(
    *,
    dwd_root: Path,
    candidate_path: Path,
    source_policy: EventScanSourcePolicy,
    output_path: Path,
    source_receipt_path: Path,
    hierarchy_path: Path,
    historical_reference: HistoricalIndustryReference,
    bs_module: Any | None = None,
) -> StaticIndustryMapping:
    """Build the P4 static industry map from DWD plus historical-only ADM supplementation.

    Current DWD company-industry membership remains the first authority. Securities outside that
    current snapshot are supplemented from fixed BaoStock CSRC snapshots and mapped onto the same
    governed DWD security identity. The result is static, non-PIT and concentration-only.
    """

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise PhysicalEventScanError("P4 static industry projection requires duckdb") from exc

    membership_paths = _table_paths(dwd_root, "dwd_company_industry_membership")
    node_paths = _table_paths(dwd_root, "dwd_industry_node")
    membership_expression = _read_parquet_expression(membership_paths)
    node_expression = _read_parquet_expression(node_paths)
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            f"""
            CREATE VIEW p4_candidate_identities AS
            SELECT DISTINCT dwd_security_id, dwd_company_id, listing_code, venue_id
            FROM read_parquet({_sql_path(candidate_path)});

            CREATE VIEW p4_static_industry_resolved AS
            WITH membership AS (
              SELECT * FROM {membership_expression}
              WHERE knowledge_to IS NULL
                AND membership_type='PRIMARY_CLASSIFICATION'
            ),
            nodes AS (
              SELECT * FROM {node_expression}
              WHERE knowledge_to IS NULL
            )
            SELECT candidate.dwd_security_id,
                   candidate.dwd_company_id,
                   candidate.listing_code,
                   candidate.venue_id,
                   CASE
                     WHEN node.node_level=1 THEN node.industry_node_id
                     WHEN node.node_level=2 AND parent.node_level=1
                       THEN parent.industry_node_id
                   END AS level1_industry_id
            FROM p4_candidate_identities candidate
            JOIN membership
              ON membership.company_id=candidate.dwd_company_id
            JOIN nodes node
              ON node.industry_node_id=membership.industry_node_id
            LEFT JOIN nodes parent
              ON parent.industry_node_id=node.parent_node_id;
            """
        )
        ambiguity = connection.execute(
            """
            SELECT count(*) FROM (
              SELECT dwd_security_id
              FROM p4_static_industry_resolved
              WHERE level1_industry_id IS NOT NULL
              GROUP BY dwd_security_id
              HAVING count(DISTINCT level1_industry_id) <> 1
            )
            """
        ).fetchone()
        if ambiguity is None or int(ambiguity[0]):
            raise PhysicalEventScanError(
                "P4 DWD static industry projection contains ambiguous security membership"
            )
        rows = list(
            connection.execute(
                """
                WITH ranked AS (
                  SELECT dwd_security_id, listing_code, venue_id, level1_industry_id,
                         row_number() OVER (
                           PARTITION BY dwd_security_id
                           ORDER BY listing_code, venue_id
                         ) AS identity_rank
                  FROM p4_static_industry_resolved
                  WHERE level1_industry_id IS NOT NULL
                )
                SELECT dwd_security_id, listing_code, venue_id, level1_industry_id
                FROM ranked
                WHERE identity_rank=1
                ORDER BY dwd_security_id
                """
            ).fetchall()
        )
        candidate_identities = connection.execute(
            """
            SELECT DISTINCT dwd_security_id, listing_code, venue_id
            FROM p4_candidate_identities
            ORDER BY dwd_security_id
            """
        ).fetchall()
        level1_by_code = {
            str(node_code): str(industry_node_id)
            for node_code, industry_node_id in connection.execute(
                f"""
                SELECT node_code, industry_node_id
                FROM {node_expression}
                WHERE knowledge_to IS NULL
                  AND node_level=1
                ORDER BY node_code
                """
            ).fetchall()
        }
    finally:
        connection.close()

    covered = {str(row[0]) for row in rows}
    missing_identities = [row for row in candidate_identities if str(row[0]) not in covered]
    supplement_receipt: Mapping[str, Any]
    if missing_identities:
        missing_codes = tuple(sorted({str(row[1]) for row in missing_identities}))
        try:
            frozen_receipt = os.environ.get(
                "QUANTHUS_P4_HISTORICAL_INDUSTRY_RECEIPT_PATH", ""
            ).strip()
            if frozen_receipt:
                supplement, supplement_receipt = load_p4_historical_industry_supplement(
                    missing_codes,
                    receipt_path=Path(frozen_receipt),
                    hierarchy_path=hierarchy_path,
                    binding=historical_reference,
                )
            else:
                supplement, supplement_receipt = collect_p4_historical_industry_supplement(
                    missing_codes,
                    hierarchy_path=hierarchy_path,
                    binding=historical_reference,
                    bs_module=bs_module,
                )
        except ResearchADMError as exc:
            raise PhysicalEventScanError(
                f"P4 historical industry supplement failed: {exc}"
            ) from exc
        for security_id, stock_code, venue_id in missing_identities:
            membership = supplement.get(str(stock_code))
            if membership is None:
                raise PhysicalEventScanError(
                    f"P4 historical industry supplement lacks {stock_code}"
                )
            leaf_code = str(membership["industry_code"])
            level1_id = level1_by_code.get(leaf_code[0])
            if level1_id is None:
                raise PhysicalEventScanError(
                    f"P4 historical industry code has no DWD level-one node: {leaf_code}"
                )
            rows.append((security_id, stock_code, venue_id, level1_id))
    else:
        supplement_receipt = {
            "schema_version": "quanthus.p4-adm-industry-source-receipt.v1",
            "provider": "not_required",
            "selected_security_count": 0,
            "source_rows": [],
        }
        supplement_receipt = {
            **supplement_receipt,
            "content_digest": _digest(supplement_receipt),
        }

    combined_receipt_payload = {
        "schema_version": "quanthus.p4-adm-static-industry-receipt.v1",
        "dwd_base_package_id": source_policy.base_release.package_id,
        "dwd_membership_count": len(covered),
        "historical_supplement": dict(supplement_receipt),
    }
    combined_receipt = {
        **combined_receipt_payload,
        "content_digest": _digest(combined_receipt_payload),
    }
    _write_json(source_receipt_path, combined_receipt)

    retrieved_at = (
        "2021-09-30T15:00:00+08:00"
        if missing_identities
        else datetime.combine(
            source_policy.base_release.through_date,
            time(23, 59, 59),
            tzinfo=timezone.utc,
        ).isoformat()
    )
    unsigned = {
        "schema_version": "1.0.0",
        "mapping_version": source_policy.static_industry_reference.version,
        "usage": source_policy.static_industry_reference.usage,
        "source": {
            "provider": "quanthus-p4-research-adm",
            "endpoint": "dwd-v2.1+baostock-csrc-static-industry",
            "retrieved_at": retrieved_at,
            "source_revision": str(combined_receipt["content_digest"]),
        },
        "memberships": [
            {
                "security_id": str(security_id),
                "stock_code": str(stock_code),
                "exchange": str(exchange),
                "level1_industry_id": str(level1_industry_id),
            }
            for security_id, stock_code, exchange, level1_industry_id in sorted(
                rows, key=lambda item: (str(item[0]), str(item[1]), str(item[2]))
            )
        ],
    }
    _write_json(output_path, {**unsigned, "artifact_digest": _digest(unsigned)})
    return load_static_industry_mapping(
        output_path,
        expected_version=source_policy.static_industry_reference.version,
    )


def _audit_projection_static_industry_coverage(
    *,
    dwd_root: Path,
    candidate_path: Path,
    industry_mapping: StaticIndustryMapping,
    output_path: Path,
) -> dict[str, Any]:
    """Classify DWD-static industry coverage over the full P4 candidate-security universe."""

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise PhysicalEventScanError("P4 static industry coverage audit requires duckdb") from exc

    listing_expression = _read_parquet_expression(_table_paths(dwd_root, "dwd_listing"))
    membership_expression = _read_parquet_expression(
        _table_paths(dwd_root, "dwd_company_industry_membership")
    )
    node_expression = _read_parquet_expression(_table_paths(dwd_root, "dwd_industry_node"))
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("CREATE TABLE mapped_security(security_id VARCHAR PRIMARY KEY)")
        connection.executemany(
            "INSERT INTO mapped_security VALUES (?)",
            [(security_id,) for security_id in sorted(industry_mapping.by_security_id)],
        )
        connection.execute(
            f"""
            CREATE VIEW audit_candidates AS
            SELECT dwd_security_id,
                   dwd_company_id,
                   min(listing_code) AS listing_code
            FROM read_parquet({_sql_path(candidate_path)})
            GROUP BY dwd_security_id, dwd_company_id;

            CREATE VIEW audit_membership AS
            SELECT company_id, industry_node_id
            FROM {membership_expression}
            WHERE knowledge_to IS NULL
              AND membership_type='PRIMARY_CLASSIFICATION';

            CREATE VIEW audit_nodes AS
            SELECT industry_node_id, parent_node_id, node_level
            FROM {node_expression}
            WHERE knowledge_to IS NULL;

            CREATE VIEW audit_current_listing AS
            SELECT DISTINCT security_id
            FROM {listing_expression}
            WHERE knowledge_to IS NULL
              AND effective_to IS NULL;

            CREATE VIEW audit_classified AS
            SELECT candidate.dwd_security_id AS security_id,
                   candidate.dwd_company_id AS company_id,
                   candidate.listing_code,
                   current_listing.security_id IS NOT NULL AS current_listing,
                   CASE
                     WHEN mapped.security_id IS NOT NULL THEN 'covered'
                     WHEN membership.company_id IS NULL
                       THEN 'no_company_industry_membership'
                     WHEN node.industry_node_id IS NULL
                       THEN 'industry_node_missing'
                     WHEN node.node_level=1 THEN 'unexpected_mapping_gap'
                     WHEN node.node_level=2
                          AND (parent.industry_node_id IS NULL OR parent.node_level<>1)
                       THEN 'level1_parent_unresolved'
                     ELSE 'unexpected_mapping_gap'
                   END AS coverage_reason
            FROM audit_candidates candidate
            LEFT JOIN mapped_security mapped
              ON mapped.security_id=candidate.dwd_security_id
            LEFT JOIN audit_membership membership
              ON membership.company_id=candidate.dwd_company_id
            LEFT JOIN audit_nodes node
              ON node.industry_node_id=membership.industry_node_id
            LEFT JOIN audit_nodes parent
              ON parent.industry_node_id=node.parent_node_id
            LEFT JOIN audit_current_listing current_listing
              ON current_listing.security_id=candidate.dwd_security_id;
            """
        )
        duplicate = connection.execute(
            """
            SELECT count(*) FROM (
              SELECT security_id
              FROM audit_classified
              GROUP BY security_id
              HAVING count(*) <> 1
            )
            """
        ).fetchone()
        if duplicate is None or int(duplicate[0]):
            raise PhysicalEventScanError(
                "P4 static industry coverage audit produced ambiguous security rows"
            )
        counts = connection.execute(
            """
            SELECT count(*) AS candidate_security_count,
                   count(*) FILTER (WHERE coverage_reason='covered') AS mapped_security_count,
                   count(*) FILTER (WHERE coverage_reason<>'covered') AS missing_security_count,
                   count(*) FILTER (
                     WHERE coverage_reason<>'covered' AND current_listing
                   ) AS missing_current_listed,
                   count(*) FILTER (
                     WHERE coverage_reason<>'covered' AND NOT current_listing
                   ) AS missing_historical_only
            FROM audit_classified
            """
        ).fetchone()
        if counts is None:
            raise PhysicalEventScanError("P4 static industry coverage audit returned no counts")
        reason_rows = connection.execute(
            """
            SELECT coverage_reason, count(*)
            FROM audit_classified
            WHERE coverage_reason<>'covered'
            GROUP BY coverage_reason
            ORDER BY coverage_reason
            """
        ).fetchall()
        missing_rows = connection.execute(
            """
            SELECT security_id, company_id, listing_code, current_listing, coverage_reason
            FROM audit_classified
            WHERE coverage_reason<>'covered'
            ORDER BY current_listing DESC, listing_code, security_id
            LIMIT 1000
            """
        ).fetchall()
    finally:
        connection.close()

    payload: dict[str, Any] = {
        "schema_version": "quanthus.p4-static-industry-coverage-audit.v1",
        "candidate_security_count": int(counts[0]),
        "mapped_security_count": int(counts[1]),
        "missing_security_count": int(counts[2]),
        "missing_current_listed": int(counts[3]),
        "missing_historical_only": int(counts[4]),
        "missing_reason_counts": {str(reason): int(count) for reason, count in reason_rows},
        "missing_securities": [
            {
                "security_id": str(security_id),
                "company_id": str(company_id),
                "listing_code": str(listing_code),
                "current_listing": bool(current_listing),
                "coverage_reason": str(reason),
            }
            for security_id, company_id, listing_code, current_listing, reason in missing_rows
        ],
    }
    _write_json(output_path, payload)
    return payload


def _adm_identity(
    *,
    adm_policy: P4ResearchADMPolicy,
    source_policy: EventScanSourcePolicy,
    provider_identity: Mapping[str, Any],
    candidate_path: Path,
    industry_mapping: StaticIndustryMapping,
    source_receipt_path: Path,
    coverage_audit_path: Path,
    row_count: int,
    trading_dates: Sequence[date],
) -> dict[str, Any]:
    if not trading_dates:
        raise PhysicalEventScanError("P4 Research ADM has no trading dates")
    return {
        "schema_version": ADM_SCHEMA,
        "logical_layer": "research_adm",
        "adm_policy": adm_policy.to_dict(),
        "source_policy_id": source_policy.policy_id,
        "source_policy_version": source_policy.policy_version,
        "source_policy_digest": source_policy.semantic_digest,
        "historical_projection_id": source_policy.historical_visibility.projection_id,
        "per_decision_publication_proven": False,
        "dwd_base": source_policy.base_release.to_dict(),
        "dwd_correction": source_policy.correction_release.to_dict(),
        "static_industry_reference": source_policy.static_industry_reference.to_dict(),
        "static_industry_projection": {
            "path": adm_policy.assets["static_industry_mapping"],
            "mapping_version": industry_mapping.mapping_version,
            "artifact_digest": industry_mapping.artifact_digest,
            "sha256": _sha256(industry_mapping.path),
            "membership_count": len(industry_mapping.memberships),
        },
        "static_industry_source_receipt": {
            "path": adm_policy.assets["static_industry_source_receipt"],
            "sha256": _sha256(source_receipt_path),
        },
        "static_industry_coverage_audit": {
            "path": adm_policy.assets["static_industry_coverage_audit"],
            "sha256": _sha256(coverage_audit_path),
        },
        "provider_id": provider_identity.get("provider_id"),
        "provider_version": provider_identity.get("provider_version"),
        "provider_config_digest": provider_identity.get("provider_config_digest"),
        "provider_lineage_digest": provider_identity.get("provider_lineage_digest"),
        "candidate_projection": {
            "path": adm_policy.assets["candidate_daily"],
            "sha256": _sha256(candidate_path),
            "row_count": row_count,
        },
        "trading_dates": {
            "first": trading_dates[0].isoformat(),
            "last": trading_dates[-1].isoformat(),
            "count": len(trading_dates),
        },
    }


def materialize_p4_research_adm(
    *,
    reader: HistoricalReplayFrameReader,
    dwd_root: Path,
    source_policy: EventScanSourcePolicy,
    provider_identity: Mapping[str, Any],
    output_root: Path,
    adm_policy: P4ResearchADMPolicy | None = None,
) -> P4ResearchADM:
    """Materialize the governed application data mart consumed by the P4 Event Library."""

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise PhysicalEventScanError("P4 physical scan requires duckdb") from exc

    source_policy.historical_visibility.require_full_scan_authority()
    if adm_policy is None:
        adm_policy = load_p4_research_adm_policy(
            find_project_root(source_policy.path) / "config/p4-research-adm.v1.json"
        )
    output_root.mkdir(parents=True, exist_ok=False)
    candidate_path = output_root / adm_policy.assets["candidate_daily"]
    bars_path = reader.bundle.partition_paths("daily_ohlcv_none")[0]
    universe_path = reader.bundle.partition_paths("daily_universe")[0]
    limits_path = reader.bundle.partition_paths("price_limit_execution_daily")[0]
    calendar_path = reader.bundle.partition_paths("trading_calendar")[0]
    factor_paths = _table_paths(dwd_root, "dwd_adjustment_factor")

    start = source_policy.development_partition.start.isoformat()
    end = source_policy.development_partition.end.isoformat()
    factor_expression = _read_parquet_expression(factor_paths)
    connection = duckdb.connect(":memory:")
    connection.execute("SET preserve_insertion_order=false")
    try:
        connection.execute(
            f"""
            COPY (
              WITH ranked_universe AS (
                SELECT *, row_number() OVER (
                  PARTITION BY dwd_listing_id ORDER BY market_date
                ) AS listing_sessions
                FROM read_parquet({_sql_path(universe_path)})
              ),
              factor_boundary AS (
                SELECT security_id,
                       CAST(timezone('Asia/Shanghai', effective_from) AS DATE) AS effective_date,
                       min(effective_from) AS factor_effective_from,
                       max(knowledge_from) AS dwd_factor_knowledge_from,
                       count(*) AS factor_boundary_count
                FROM {factor_expression}
                WHERE knowledge_to IS NULL
                GROUP BY security_id, effective_date
              )
              SELECT universe.dwd_security_id,
                     universe.dwd_company_id,
                     universe.dwd_listing_id,
                     universe.stock_code AS listing_code,
                     universe.exchange AS venue_id,
                     universe.dwd_instrument_type AS instrument_type,
                     universe.market_date AS session_date,
                     universe.listing_sessions,
                     bars.dwd_open_decimal AS open_price,
                     bars.dwd_high_decimal AS high_price,
                     bars.dwd_low_decimal AS low_price,
                     bars.dwd_close_decimal AS close_price,
                     bars.dwd_turnover_decimal AS turnover,
                     CASE
                       WHEN limits.observed_tradestatus='1' THEN true
                       WHEN limits.observed_tradestatus='0' THEN false
                       ELSE NULL
                     END AS is_tradable,
                     factor.factor_boundary_count IS NOT NULL AS adjustment_effective,
                     factor.factor_effective_from,
                     factor.factor_boundary_count,
                     bars.dwd_bar_knowledge_from,
                     bars.dwd_bar_source_record_id,
                     bars.dwd_bar_source_revision,
                     bars.dwd_bar_source_artifact_sha256,
                     universe.dwd_security_effective_from,
                     universe.dwd_listing_effective_from,
                     universe.dwd_security_knowledge_from,
                     universe.dwd_listing_knowledge_from,
                     limits.dwd_state_knowledge_from,
                     factor.dwd_factor_knowledge_from
              FROM ranked_universe universe
              LEFT JOIN read_parquet({_sql_path(bars_path)}) bars
                ON bars.security_id=universe.security_id
               AND bars.date=universe.market_date
              LEFT JOIN read_parquet({_sql_path(limits_path)}) limits
                ON limits.security_id=universe.security_id
               AND limits.execution_date=universe.market_date
              LEFT JOIN factor_boundary factor
                ON factor.security_id=universe.dwd_security_id
               AND factor.effective_date=universe.market_date
              WHERE universe.market_date BETWEEN DATE '{start}' AND DATE '{end}'
              ORDER BY universe.dwd_security_id,
                       universe.dwd_listing_effective_from,
                       universe.dwd_listing_id,
                       universe.market_date
            ) TO {_sql_path(candidate_path)}
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)
            """
        )
        row = connection.execute(
            f"SELECT count(*) FROM read_parquet({_sql_path(candidate_path)})"
        ).fetchone()
        row_count = 0 if row is None else int(row[0])
        if row_count <= 0:
            raise PhysicalEventScanError("P4 candidate projection is empty")
        duplicate = connection.execute(
            f"""
            SELECT count(*) FROM (
              SELECT dwd_security_id, session_date
              FROM read_parquet({_sql_path(candidate_path)})
              GROUP BY ALL HAVING count(*) <> 1
            )
            """
        ).fetchone()
        if duplicate is None or int(duplicate[0]):
            raise PhysicalEventScanError(
                "P4 candidate projection contains overlapping security-date identities"
            )
        raw_dates = connection.execute(
            f"""
            SELECT DISTINCT trade_date
            FROM read_parquet({_sql_path(calendar_path)})
            WHERE is_open=1 AND trade_date BETWEEN DATE '{start}' AND DATE '{end}'
            ORDER BY trade_date
            """
        ).fetchall()
        trading_dates = tuple(row[0] for row in raw_dates)
    finally:
        connection.close()

    if not trading_dates:
        raise PhysicalEventScanError("P4 candidate projection has no development trading calendar")
    for value in trading_dates:
        if not isinstance(value, date):
            raise PhysicalEventScanError("P4 trading calendar returned a non-date value")
        source_policy.require_development_date(value)

    project_root = find_project_root(source_policy.path)
    source_receipt_path = output_root / adm_policy.assets["static_industry_source_receipt"]
    industry_mapping = _materialize_dwd_static_industry_mapping(
        dwd_root=dwd_root,
        candidate_path=candidate_path,
        source_policy=source_policy,
        output_path=output_root / adm_policy.assets["static_industry_mapping"],
        source_receipt_path=source_receipt_path,
        hierarchy_path=(
            project_root / adm_policy.historical_industry_reference.hierarchy_path
        ),
        historical_reference=adm_policy.historical_industry_reference,
        bs_module=None,
    )
    coverage_audit_path = output_root / adm_policy.assets["static_industry_coverage_audit"]
    coverage_audit = _audit_projection_static_industry_coverage(
        dwd_root=dwd_root,
        candidate_path=candidate_path,
        industry_mapping=industry_mapping,
        output_path=coverage_audit_path,
    )
    missing_security_count = int(coverage_audit["missing_security_count"])
    if missing_security_count:
        reason_text = ",".join(
            f"{reason}:{count}"
            for reason, count in sorted(coverage_audit["missing_reason_counts"].items())
        )
        missing_codes = ",".join(
            str(item["listing_code"])
            for item in coverage_audit["missing_securities"]
        )
        raise PhysicalEventScanError(
            "P4 static industry projection does not cover full candidate-security universe: "
            f"missing_securities={missing_security_count} "
            f"current_listed={coverage_audit['missing_current_listed']} "
            f"historical_only={coverage_audit['missing_historical_only']} "
            f"reasons={reason_text} codes={missing_codes}"
        )
    identity = _adm_identity(
        adm_policy=adm_policy,
        source_policy=source_policy,
        provider_identity=provider_identity,
        candidate_path=candidate_path,
        industry_mapping=industry_mapping,
        source_receipt_path=source_receipt_path,
        coverage_audit_path=coverage_audit_path,
        row_count=row_count,
        trading_dates=trading_dates,
    )
    content_digest = _digest(identity)
    adm_manifest = {**identity, "content_digest": content_digest}
    manifest_path = output_root / "adm.json"
    _write_json(manifest_path, adm_manifest)
    manifest_sha = _sha256(manifest_path)
    snapshot_id = f"p4-adm-development-{content_digest.removeprefix('sha256:')[:24]}"
    return P4ResearchADM(
        root=output_root.resolve(),
        policy=adm_policy,
        candidate_path=candidate_path.resolve(),
        industry_mapping=industry_mapping,
        manifest_path=manifest_path.resolve(),
        manifest_sha256=manifest_sha,
        content_digest=content_digest,
        snapshot_id=snapshot_id,
        trading_dates=trading_dates,
        row_count=row_count,
    )


def load_p4_research_adm(adm_root: Path, *, project_root: Path | None = None) -> P4ResearchADM:
    """Restore and minimally verify a generation-bound P4 Research ADM."""

    root = adm_root.resolve()
    manifest_path = root / "adm.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PhysicalEventScanError("P4 Research ADM manifest is unreadable") from exc
    if not isinstance(raw, dict):
        raise PhysicalEventScanError("P4 Research ADM manifest must be an object")
    candidate = raw.get("candidate_projection")
    industry = raw.get("static_industry_projection")
    dates = raw.get("trading_dates")
    if (
        not isinstance(candidate, dict)
        or not isinstance(industry, dict)
        or not isinstance(dates, dict)
    ):
        raise PhysicalEventScanError("P4 Research ADM manifest bindings are incomplete")
    project = find_project_root(manifest_path) if project_root is None else project_root.resolve()
    policy = load_p4_research_adm_policy(project / "config/p4-research-adm.v1.json")
    candidate_path = root / _safe_relative(candidate.get("path"))
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise PhysicalEventScanError("P4 Research ADM candidate projection is missing")
    if _sha256(candidate_path) != candidate.get("sha256"):
        raise PhysicalEventScanError("P4 Research ADM candidate projection checksum differs")
    mapping = load_static_industry_mapping(
        root / _safe_relative(industry.get("path")),
        expected_version=str(industry.get("mapping_version")),
    )
    first = date.fromisoformat(str(dates.get("first")))
    last = date.fromisoformat(str(dates.get("last")))
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise PhysicalEventScanError("P4 physical scan requires duckdb") from exc
    connection = duckdb.connect(":memory:")
    try:
        raw_dates = connection.execute(
            "SELECT DISTINCT session_date FROM read_parquet(?) ORDER BY session_date",
            [str(candidate_path)],
        ).fetchall()
    finally:
        connection.close()
    trading_dates = tuple(item[0] for item in raw_dates)
    if (
        not trading_dates
        or trading_dates[0] != first
        or trading_dates[-1] != last
        or len(trading_dates) != int(dates.get("count", -1))
    ):
        raise PhysicalEventScanError("P4 Research ADM trading dates differ")
    content_digest = str(raw.get("content_digest"))
    return P4ResearchADM(
        root=root,
        policy=policy,
        candidate_path=candidate_path,
        industry_mapping=mapping,
        manifest_path=manifest_path,
        manifest_sha256=_sha256(manifest_path),
        content_digest=content_digest,
        snapshot_id=f"p4-adm-development-{content_digest.removeprefix('sha256:')[:24]}",
        trading_dates=trading_dates,
        row_count=int(candidate["row_count"]),
    )


def plan_candidate_security_buckets(
    projection: P4ResearchADM, workers: int
) -> tuple[tuple[str, ...], ...]:
    """Plan deterministic LPT buckets over complete security series."""

    if workers not in {1, 2, 3, 4}:
        raise PhysicalEventScanError("candidate shard workers must be between one and four")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise PhysicalEventScanError("P4 physical scan requires duckdb") from exc
    connection = duckdb.connect(":memory:")
    try:
        weighted = tuple(
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT dwd_security_id, count(*) AS row_count
                FROM read_parquet(?) GROUP BY dwd_security_id
                ORDER BY row_count DESC, dwd_security_id
                """,
                [str(projection.candidate_path)],
            ).fetchall()
        )
    finally:
        connection.close()
    if workers > len(weighted):
        raise PhysicalEventScanError("candidate shard count exceeds security count")
    buckets: list[list[str]] = [[] for _ in range(workers)]
    weights = [0] * workers
    for security_id, row_count in weighted:
        selected = min(range(workers), key=lambda index: (weights[index], index))
        buckets[selected].append(security_id)
        weights[selected] += row_count
    return tuple(tuple(sorted(bucket)) for bucket in buckets)


# Compatibility alias for callers created before the P4 Research ADM boundary was named.
materialize_event_candidate_projection = materialize_p4_research_adm


_CANDIDATE_SCAN_COLUMNS = (
    "dwd_security_id",
    "dwd_listing_id",
    "listing_code",
    "dwd_company_id",
    "venue_id",
    "instrument_type",
    "session_date",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "turnover",
    "is_tradable",
    "listing_sessions",
    "adjustment_effective",
    "factor_effective_from",
    "dwd_bar_knowledge_from",
    "dwd_security_knowledge_from",
    "dwd_listing_knowledge_from",
    "dwd_state_knowledge_from",
    "dwd_factor_knowledge_from",
    "dwd_bar_source_record_id",
    "dwd_bar_source_revision",
    "dwd_bar_source_artifact_sha256",
    "factor_boundary_count",
    "dwd_security_effective_from",
    "dwd_listing_effective_from",
)


def _source_evidence_digest_values(
    *,
    source_policy: EventScanSourcePolicy,
    security_id: object,
    listing_id: object,
    session_date: object,
    bar_knowledge_from: object,
    security_knowledge_from: object,
    listing_knowledge_from: object,
    state_knowledge_from: object,
    factor_knowledge_from: object,
    bar_source_record_id: object,
    bar_source_revision: object,
    bar_source_artifact_sha256: object,
    factor_boundary_count: object,
) -> str:
    return sha256_digest(
        {
            "projection_id": source_policy.historical_visibility.projection_id,
            "source_policy_digest": source_policy.semantic_digest,
            "security_id": security_id,
            "listing_id": listing_id,
            "session_date": _json_scalar(session_date),
            "raw_dwd_knowledge": {
                "bar": _json_scalar(bar_knowledge_from),
                "security": _json_scalar(security_knowledge_from),
                "listing": _json_scalar(listing_knowledge_from),
                "tradability": _json_scalar(state_knowledge_from),
                "adjustment_factor": _json_scalar(factor_knowledge_from),
            },
            "bar_source": {
                "record_id": _json_scalar(bar_source_record_id),
                "revision": _json_scalar(bar_source_revision),
                "artifact_sha256": _json_scalar(bar_source_artifact_sha256),
            },
            "factor_boundary_count": _json_scalar(factor_boundary_count),
        }
    )


def _source_evidence_digest(
    row: Mapping[str, Any], source_policy: EventScanSourcePolicy
) -> str:
    return _source_evidence_digest_values(
        source_policy=source_policy,
        security_id=row["dwd_security_id"],
        listing_id=row["dwd_listing_id"],
        session_date=row["session_date"],
        bar_knowledge_from=row.get("dwd_bar_knowledge_from"),
        security_knowledge_from=row.get("dwd_security_knowledge_from"),
        listing_knowledge_from=row.get("dwd_listing_knowledge_from"),
        state_knowledge_from=row.get("dwd_state_knowledge_from"),
        factor_knowledge_from=row.get("dwd_factor_knowledge_from"),
        bar_source_record_id=row.get("dwd_bar_source_record_id"),
        bar_source_revision=row.get("dwd_bar_source_revision"),
        bar_source_artifact_sha256=row.get("dwd_bar_source_artifact_sha256"),
        factor_boundary_count=row.get("factor_boundary_count"),
    )


def _projected_knowledge_cutoff(
    row: Mapping[str, Any], source_policy: EventScanSourcePolicy
) -> datetime:
    session_date = row["session_date"]
    if not isinstance(session_date, date):
        raise PhysicalEventScanError("projected candidate session_date is invalid")
    close_at = datetime.combine(session_date, time(15, 0), MARKET_TIMEZONE)
    coordinates = [
        source_policy.historical_visibility.project_candidate_knowledge(
            table="dwd_security_bar", session_close=close_at
        ),
        source_policy.historical_visibility.project_candidate_knowledge(
            table="dwd_tradability_state", session_close=close_at
        ),
        source_policy.historical_visibility.project_candidate_knowledge(
            table="dwd_security",
            session_close=close_at,
            effective_from=_aware(
                row["dwd_security_effective_from"], "dwd_security_effective_from"
            ),
        ),
        source_policy.historical_visibility.project_candidate_knowledge(
            table="dwd_listing",
            session_close=close_at,
            effective_from=_aware(
                row["dwd_listing_effective_from"], "dwd_listing_effective_from"
            ),
        ),
    ]
    if row.get("adjustment_effective") is True:
        coordinates.append(
            source_policy.historical_visibility.project_candidate_knowledge(
                table="dwd_adjustment_factor",
                session_close=close_at,
                effective_from=_aware(row["factor_effective_from"], "factor_effective_from"),
            )
        )
    return max(coordinates)


def _candidate_input(
    row: Mapping[str, Any],
    *,
    source_policy: EventScanSourcePolicy,
    industry_mapping: StaticIndustryMapping,
) -> CandidateDailyInput:
    session_date = row["session_date"]
    if not isinstance(session_date, date):
        raise PhysicalEventScanError("projected candidate session date is invalid")
    security_id = str(row["dwd_security_id"])
    membership = industry_mapping.by_security_id.get(security_id)
    return CandidateDailyInput(
        security_id=security_id,
        listing_id=str(row["dwd_listing_id"]),
        listing_code=str(row["listing_code"]),
        company_id=str(row["dwd_company_id"]),
        venue_id=str(row["venue_id"]),
        instrument_type=str(row["instrument_type"]),
        static_level1_industry_id=(
            membership.level1_industry_id if membership is not None else None
        ),
        session_date=session_date,
        close_at=datetime.combine(session_date, time(15, 0), MARKET_TIMEZONE),
        knowledge_cutoff=_projected_knowledge_cutoff(row, source_policy),
        open_price=_decimal(row.get("open_price")),
        high_price=_decimal(row.get("high_price")),
        low_price=_decimal(row.get("low_price")),
        close_price=_decimal(row.get("close_price")),
        turnover=_decimal(row.get("turnover")),
        is_tradable=(
            bool(row["is_tradable"]) if row.get("is_tradable") is not None else None
        ),
        listing_sessions=int(row["listing_sessions"]),
        adjustment_effective=(
            bool(row["adjustment_effective"])
            if row.get("adjustment_effective") is not None
            else None
        ),
        source_evidence_digest=_source_evidence_digest(row, source_policy),
    )


def _candidate_input_values(
    values: Sequence[Any],
    *,
    source_policy: EventScanSourcePolicy,
    industry_by_security_id: Mapping[str, StaticIndustryMembership],
) -> CandidateDailyInput:
    if len(values) != len(_CANDIDATE_SCAN_COLUMNS):
        raise PhysicalEventScanError("projected candidate column count differs")
    (
        raw_security_id,
        raw_listing_id,
        raw_listing_code,
        raw_company_id,
        raw_venue_id,
        raw_instrument_type,
        raw_session_date,
        raw_open_price,
        raw_high_price,
        raw_low_price,
        raw_close_price,
        raw_turnover,
        raw_is_tradable,
        raw_listing_sessions,
        raw_adjustment_effective,
        raw_factor_effective_from,
        raw_bar_knowledge_from,
        raw_security_knowledge_from,
        raw_listing_knowledge_from,
        raw_state_knowledge_from,
        raw_factor_knowledge_from,
        raw_bar_source_record_id,
        raw_bar_source_revision,
        raw_bar_source_artifact_sha256,
        raw_factor_boundary_count,
        raw_security_effective_from,
        raw_listing_effective_from,
    ) = values
    if not isinstance(raw_session_date, date):
        raise PhysicalEventScanError("projected candidate session date is invalid")
    security_id = str(raw_security_id)
    membership = industry_by_security_id.get(security_id)
    close_at = datetime.combine(raw_session_date, time(15, 0), MARKET_TIMEZONE)
    visibility = source_policy.historical_visibility
    visibility.project_candidate_knowledge(
        table="dwd_security",
        session_close=close_at,
        effective_from=_aware(raw_security_effective_from, "dwd_security_effective_from"),
    )
    visibility.project_candidate_knowledge(
        table="dwd_listing",
        session_close=close_at,
        effective_from=_aware(raw_listing_effective_from, "dwd_listing_effective_from"),
    )
    adjustment_effective = (
        bool(raw_adjustment_effective) if raw_adjustment_effective is not None else None
    )
    if adjustment_effective is True:
        visibility.project_candidate_knowledge(
            table="dwd_adjustment_factor",
            session_close=close_at,
            effective_from=_aware(raw_factor_effective_from, "factor_effective_from"),
        )
    return CandidateDailyInput(
        security_id=security_id,
        listing_id=str(raw_listing_id),
        listing_code=str(raw_listing_code),
        company_id=str(raw_company_id),
        venue_id=str(raw_venue_id),
        instrument_type=str(raw_instrument_type),
        static_level1_industry_id=(
            membership.level1_industry_id if membership is not None else None
        ),
        session_date=raw_session_date,
        close_at=close_at,
        knowledge_cutoff=close_at,
        open_price=_decimal(raw_open_price),
        high_price=_decimal(raw_high_price),
        low_price=_decimal(raw_low_price),
        close_price=_decimal(raw_close_price),
        turnover=_decimal(raw_turnover),
        is_tradable=(bool(raw_is_tradable) if raw_is_tradable is not None else None),
        listing_sessions=int(raw_listing_sessions),
        adjustment_effective=adjustment_effective,
        source_evidence_digest=_source_evidence_digest_values(
            source_policy=source_policy,
            security_id=raw_security_id,
            listing_id=raw_listing_id,
            session_date=raw_session_date,
            bar_knowledge_from=raw_bar_knowledge_from,
            security_knowledge_from=raw_security_knowledge_from,
            listing_knowledge_from=raw_listing_knowledge_from,
            state_knowledge_from=raw_state_knowledge_from,
            factor_knowledge_from=raw_factor_knowledge_from,
            bar_source_record_id=raw_bar_source_record_id,
            bar_source_revision=raw_bar_source_revision,
            bar_source_artifact_sha256=raw_bar_source_artifact_sha256,
            factor_boundary_count=raw_factor_boundary_count,
        ),
    )


def iter_projected_candidate_series(
    projection: P4ResearchADM,
    *,
    source_policy: EventScanSourcePolicy,
    industry_mapping: StaticIndustryMapping,
    fetch_rows: int = 4096,
    allowed_security_ids: Sequence[str] | None = None,
    duckdb_memory_limit: str | None = None,
    duckdb_temp_directory: Path | None = None,
) -> Iterator[tuple[CandidateDailyInput, ...]]:
    """Stream one stable listing series at a time from the materialized projection."""

    if fetch_rows <= 0:
        raise ValueError("fetch_rows must be positive")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise PhysicalEventScanError("P4 physical scan requires duckdb") from exc
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET threads=1")
        connection.execute("SET preserve_insertion_order=true")
        if duckdb_memory_limit is not None:
            connection.execute(f"SET memory_limit={_sql_text(duckdb_memory_limit)}")
        if duckdb_temp_directory is not None:
            duckdb_temp_directory.mkdir(parents=True, exist_ok=False)
            connection.execute(
                f"SET temp_directory={_sql_path(duckdb_temp_directory.resolve())}"
            )
        selected_security_ids: tuple[str, ...] | None = None
        selection_clause = ""
        if allowed_security_ids is not None:
            selected_security_ids = tuple(sorted(allowed_security_ids))
            if (
                not selected_security_ids
                or any(not value.strip() for value in selected_security_ids)
                or len(selected_security_ids) != len(set(selected_security_ids))
            ):
                raise PhysicalEventScanError(
                    "candidate projection security selection must be non-empty and unique"
                )
            selection_clause = (
                " WHERE dwd_security_id IN ("
                + ",".join(_sql_text(value) for value in selected_security_ids)
                + ")"
            )
        selected_columns = ", ".join(_CANDIDATE_SCAN_COLUMNS)
        cursor = connection.execute(
            f"""
            SELECT {selected_columns}
            FROM read_parquet({_sql_path(projection.candidate_path)})
            {selection_clause}
            """
        )
        names = tuple(str(item[0]) for item in cursor.description)
        if names != _CANDIDATE_SCAN_COLUMNS:
            raise PhysicalEventScanError("projected candidate columns differ")
        industry_by_security_id = industry_mapping.by_security_id
        current_key: tuple[str, str, str, str] | None = None
        current: list[CandidateDailyInput] = []
        previous_sort_key: tuple[str, datetime, str, date] | None = None
        while True:
            batch = cursor.fetchmany(fetch_rows)
            if not batch:
                break
            for values in batch:
                candidate = _candidate_input_values(
                    values,
                    source_policy=source_policy,
                    industry_by_security_id=industry_by_security_id,
                )
                listing_effective_from = values[26]
                if not isinstance(listing_effective_from, datetime):
                    raise PhysicalEventScanError(
                        "projected candidate listing effective coordinate is invalid"
                    )
                sort_key = (
                    candidate.security_id,
                    listing_effective_from,
                    candidate.listing_id,
                    candidate.session_date,
                )
                if previous_sort_key is not None and sort_key < previous_sort_key:
                    raise PhysicalEventScanError(
                        "projected candidate physical order differs"
                    )
                previous_sort_key = sort_key
                key = (
                    candidate.security_id,
                    candidate.listing_id,
                    candidate.listing_code,
                    candidate.company_id,
                )
                if current_key is not None and key != current_key:
                    yield tuple(current)
                    current = []
                current_key = key
                current.append(candidate)
        if current:
            yield tuple(current)
    finally:
        connection.close()


def _p1_evidence_payload(evidence: P1DailyExecutionEvidence) -> dict[str, Any]:
    return {
        "snapshot": {
            "market_date": evidence.snapshot.market_date.isoformat(),
            "knowledge_cutoff": evidence.snapshot.knowledge_cutoff.isoformat(),
            "source_revision": evidence.snapshot.source_revision,
            "snapshot_id": evidence.snapshot.snapshot_id,
        },
        "security_code": evidence.security_code,
        "open_at": evidence.open_at.isoformat(),
        "close_at": evidence.close_at.isoformat(),
        "coverage_status": evidence.coverage_status.value,
        "data_valid": evidence.data_valid,
        "is_executable": evidence.is_executable,
        "entry_policy_allows": evidence.entry_policy_allows,
        "reason_code": evidence.reason_code,
        "open_price": str(evidence.open_price) if evidence.open_price is not None else None,
        "high_price": str(evidence.high_price) if evidence.high_price is not None else None,
        "low_price": str(evidence.low_price) if evidence.low_price is not None else None,
        "close_price": str(evidence.close_price) if evidence.close_price is not None else None,
        "upper_limit_price": (
            str(evidence.upper_limit_price) if evidence.upper_limit_price is not None else None
        ),
        "lower_limit_price": (
            str(evidence.lower_limit_price) if evidence.lower_limit_price is not None else None
        ),
    }


def _entry_bindings(
    candidates: Sequence[ScannedCandidate],
    reader: HistoricalReplayFrameReader,
    *,
    candidate_snapshot_id: str,
) -> dict[str, CandidateEntryEvidence]:
    requests = tuple(
        sorted({(candidate.listing_code, candidate.entry_date) for candidate in candidates})
    )
    evidence = reader.execution_evidence_batch(requests)
    result: dict[str, CandidateEntryEvidence] = {}
    for candidate in candidates:
        if candidate.record.identity.snapshot_id != candidate_snapshot_id:
            raise PhysicalEventScanError("candidate set contains a different ADM snapshot")
        item = evidence[(candidate.listing_code, candidate.entry_date)]
        result[candidate.event_id] = CandidateEntryEvidence(
            event_id=candidate.event_id,
            candidate_snapshot_id=candidate_snapshot_id,
            evidence=item,
            evidence_digest=sha256_digest(_p1_evidence_payload(item)),
            matcher_policy_version=MATCHER_POLICY_VERSION,
        )
    return result


def scan_event_candidate_projection(
    projection: P4ResearchADM,
    *,
    reader: HistoricalReplayFrameReader,
    industry_mapping: StaticIndustryMapping,
    source_policy: EventScanSourcePolicy,
    card: CandidateRuleCard,
    split_policy: SplitPolicy,
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> tuple[CandidateScanResult, EntryPartitionResult]:
    """Run the frozen forward scan and P2-backed T+1 partition over one projection."""

    def scan_progress(stage: str, details: Mapping[str, Any]) -> None:
        if progress is not None:
            progress(stage, {"projection_rows": projection.row_count, **dict(details)})

    scan = scan_forward_candidate_series(
        iter_projected_candidate_series(
            projection,
            source_policy=source_policy,
            industry_mapping=industry_mapping,
        ),
        trading_dates=projection.trading_dates,
        snapshot_id=projection.snapshot_id,
        snapshot_manifest_digest=projection.manifest_sha256,
        card=card,
        split_policy=split_policy,
        source_policy=source_policy,
        progress=scan_progress if progress is not None else None,
    )
    missing_industry = int(
        scan.counters.reason_counts.get(CandidateReason.STATIC_INDUSTRY_MISSING.value, 0)
    )
    if missing_industry:
        raise PhysicalEventScanError(
            "P4 static industry projection does not cover all isolated candidates: "
            f"missing={missing_industry}"
        )
    entry_started = runtime_clock.perf_counter()
    entries = partition_executable_entries(
        scan.candidates,
        evidence_by_event_id=_entry_bindings(
            scan.candidates,
            reader,
            candidate_snapshot_id=projection.snapshot_id,
        ),
    )
    if progress is not None:
        progress(
            "entry-partition",
            {
                "terminal": True,
                "elapsed_seconds": round(runtime_clock.perf_counter() - entry_started, 3),
                "candidate_events": scan.counters.candidate_events,
                "executable_events": entries.executable_events,
                "not_executable_events": entries.not_executable_events,
            },
        )
    return scan, entries


def publish_event_candidate_shards(
    *,
    projection: P4ResearchADM,
    reader: HistoricalReplayFrameReader,
    source_policy: EventScanSourcePolicy,
    card: CandidateRuleCard,
    split_policy: SplitPolicy,
    shards: Sequence[CandidateScanShard],
    staging: Path,
    output: Path,
    code_revision: str,
) -> PhysicalEventScanResult:
    """Finalize disjoint scan shards, bind T+1 evidence, and publish the normal package."""

    scan = finalize_candidate_scan_shards(
        shards,
        trading_dates=projection.trading_dates,
        snapshot_id=projection.snapshot_id,
        snapshot_manifest_digest=projection.manifest_sha256,
        card=card,
        split_policy=split_policy,
        source_policy=source_policy,
    )
    missing_industry = int(
        scan.counters.reason_counts.get(CandidateReason.STATIC_INDUSTRY_MISSING.value, 0)
    )
    if missing_industry:
        raise PhysicalEventScanError(
            f"P4 static industry projection is incomplete: missing={missing_industry}"
        )
    entries = partition_executable_entries(
        scan.candidates,
        evidence_by_event_id=_entry_bindings(
            scan.candidates,
            reader,
            candidate_snapshot_id=projection.snapshot_id,
        ),
    )
    return _publish_package(
        staging,
        output=output,
        source_policy=source_policy,
        card=card,
        split_policy=split_policy,
        projection=projection,
        scan=scan,
        entries=entries,
        code_revision=code_revision,
    )


def _event_rows(scan: CandidateScanResult) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "event": candidate.record.to_dict(),
            "listing_id": candidate.listing_id,
            "listing_code": candidate.listing_code,
            "company_id": candidate.company_id,
            "static_level1_industry_id": candidate.static_level1_industry_id,
            "feature_start": candidate.feature_start.isoformat(),
            "entry_date": candidate.entry_date.isoformat(),
            "feature_evidence": candidate.feature_evidence.to_dict(),
        }
        for candidate in scan.candidates
    )


def _entry_rows(entries: EntryPartitionResult) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "event_id": item.event_id,
            "status": item.status.value,
            "reason_code": item.reason_code,
            "open_execution_state": item.open_execution_state.value,
            "entry_open_price": (
                str(item.entry_open_price) if item.entry_open_price is not None else None
            ),
            "candidate_snapshot_id": item.candidate.record.identity.snapshot_id,
            "entry_source_snapshot_id": item.entry_source_snapshot_id,
            "entry_evidence_digest": item.entry_evidence_digest,
            "matcher_policy_version": item.matcher_policy_version,
        }
        for item in entries.records
    )


def _package_files(root: Path) -> tuple[dict[str, Any], ...]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in {"manifest.json", "READY"}:
            continue
        files.append(
            {
                "path": relative,
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return tuple(files)


def _publish_package(
    staging: Path,
    *,
    output: Path,
    source_policy: EventScanSourcePolicy,
    card: CandidateRuleCard,
    split_policy: SplitPolicy,
    projection: P4ResearchADM,
    scan: CandidateScanResult,
    entries: EntryPartitionResult,
    code_revision: str,
) -> PhysicalEventScanResult:
    events_path = staging / "events.jsonl"
    entries_path = staging / "entry-partition.jsonl"
    summary_path = staging / "summary.json"
    _write_jsonl(events_path, _event_rows(scan))
    _write_jsonl(entries_path, _entry_rows(entries))
    _write_json(
        summary_path,
        {
            "schema_version": PACKAGE_SCHEMA,
            "source_policy_digest": source_policy.semantic_digest,
            "candidate_rule_digest": card.semantic_digest,
            "split_policy_digest": split_policy.semantic_digest,
            "adm_manifest_sha256": projection.manifest_sha256,
            "adm_content_digest": projection.content_digest,
            "scan_counters": scan.counters.to_dict(),
            "entry_partition": {
                "executable_events": entries.executable_events,
                "not_executable_events": entries.not_executable_events,
            },
        },
    )
    files = _package_files(staging)
    content_digest = _digest(files)
    manifest = {
        "schema_version": PACKAGE_SCHEMA,
        "package_id": f"event-library-{content_digest.removeprefix('sha256:')[:24]}",
        "content_digest": content_digest,
        "code_revision": code_revision,
        "research_only": True,
        "execution_authorized": False,
        "source_policy": {
            "id": source_policy.policy_id,
            "version": source_policy.policy_version,
            "digest": source_policy.semantic_digest,
        },
        "candidate_rule": {
            "id": card.rule_id,
            "version": card.rule_version,
            "digest": card.semantic_digest,
        },
        "split_policy": {
            "id": split_policy.policy_id,
            "version": split_policy.policy_version,
            "digest": split_policy.semantic_digest,
        },
        "adm": {
            "id": projection.policy.adm_id,
            "version": projection.policy.adm_version,
            "policy_digest": projection.policy.semantic_digest,
            "snapshot_id": projection.snapshot_id,
            "manifest_sha256": projection.manifest_sha256,
            "content_digest": projection.content_digest,
        },
        "candidate_events": scan.counters.candidate_events,
        "executable_events": entries.executable_events,
        "not_executable_events": entries.not_executable_events,
        "files": list(files),
    }
    manifest_path = staging / "manifest.json"
    _write_json(manifest_path, manifest)
    manifest_sha = _sha256(manifest_path)
    (staging / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite P4 event library: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, output)
    return PhysicalEventScanResult(
        root=output.resolve(),
        manifest_sha256=manifest_sha,
        content_digest=content_digest,
        candidate_events=scan.counters.candidate_events,
        executable_events=entries.executable_events,
        not_executable_events=entries.not_executable_events,
    )


def verify_physical_event_scan(root: Path) -> PhysicalEventScanResult:
    """Verify one immutable P4 event-library package without trusting its manifest claims."""

    if root.is_symlink() or not root.is_dir():
        raise PhysicalEventScanError("P4 event library must be a physical directory")
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PhysicalEventScanError("P4 event library manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PACKAGE_SCHEMA:
        raise PhysicalEventScanError("P4 event library manifest schema differs")
    manifest_sha = _sha256(root / "manifest.json")
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise PhysicalEventScanError("P4 event library READY is unreadable") from exc
    if ready != manifest_sha:
        raise PhysicalEventScanError("P4 event library READY does not bind its manifest")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise PhysicalEventScanError("P4 event library file inventory is invalid")
    observed: list[dict[str, Any]] = []
    expected_paths: set[str] = {"manifest.json", "READY"}
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise PhysicalEventScanError("P4 event library file row is invalid")
        relative = _safe_relative(raw.get("path"))
        path = root.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file():
            raise PhysicalEventScanError(f"P4 event library file is missing: {relative}")
        item = {
            "path": relative.as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        if item != raw:
            raise PhysicalEventScanError(f"P4 event library file differs: {relative}")
        expected_paths.add(relative.as_posix())
        observed.append(item)
    physical = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if physical != expected_paths:
        raise PhysicalEventScanError("P4 event library physical inventory differs")
    content_digest = _digest(tuple(observed))
    if content_digest != manifest.get("content_digest"):
        raise PhysicalEventScanError("P4 event library content digest differs")
    adm_binding = manifest.get("adm")
    if not isinstance(adm_binding, dict):
        raise PhysicalEventScanError("P4 event library ADM binding is missing")
    adm_manifest_path = root / "adm" / "adm.json"
    try:
        adm_manifest = json.loads(adm_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PhysicalEventScanError("P4 Research ADM manifest is unreadable") from exc
    if not isinstance(adm_manifest, dict) or adm_manifest.get("schema_version") != ADM_SCHEMA:
        raise PhysicalEventScanError("P4 Research ADM manifest schema differs")
    adm_policy = adm_manifest.get("adm_policy")
    if not isinstance(adm_policy, dict):
        raise PhysicalEventScanError("P4 Research ADM policy binding is missing")
    observed_adm = {
        "id": adm_policy.get("adm_id"),
        "version": adm_policy.get("adm_version"),
        "policy_digest": adm_policy.get("semantic_digest"),
        "snapshot_id": (
            "p4-adm-development-"
            + str(adm_manifest.get("content_digest", "")).removeprefix("sha256:")[:24]
        ),
        "manifest_sha256": _sha256(adm_manifest_path),
        "content_digest": adm_manifest.get("content_digest"),
    }
    if observed_adm != adm_binding:
        raise PhysicalEventScanError("P4 event library ADM binding differs")
    return PhysicalEventScanResult(
        root=root.resolve(),
        manifest_sha256=manifest_sha,
        content_digest=content_digest,
        candidate_events=int(manifest["candidate_events"]),
        executable_events=int(manifest["executable_events"]),
        not_executable_events=int(manifest["not_executable_events"]),
    )


def build_physical_event_scan(
    *,
    source_policy_path: Path,
    provider_config_path: Path,
    rule_card_path: Path,
    split_policy_path: Path,
    output: Path,
    code_revision: str,
    provider_session: ReplayDataSession | None = None,
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> PhysicalEventScanResult:
    """Build one real-corpus P4 event library from DWD V2.1 + correction-001."""

    if not code_revision.strip():
        raise ValueError("code_revision must not be empty")
    root = find_project_root(source_policy_path)
    source_policy = load_event_scan_source_policy(
        source_policy_path,
        release_path=root / "config/releases/v2-build202607.json",
        provider_path=provider_config_path,
    )
    card = load_candidate_rule_card(rule_card_path)
    split_policy = load_split_policy(split_policy_path)
    provider_config = load_replay_data_provider_config(provider_config_path)
    if provider_config.mode != "direct-dwd-job-local":
        raise PhysicalEventScanError("P4 physical scan requires direct DWD provider mode")
    provider = ReplayDataProvider(provider_config)
    if provider_session is not None:
        require_job_local_session(
            provider_session,
            provider_config,
            scope_start=date(2012, 1, 4),
            scope_end=date(2021, 12, 30),
        )
    session_context = (
        provider.open(scope_start=date(2012, 1, 4), scope_end=date(2021, 12, 30))
        if provider_session is None
        else nullcontext(provider_session)
    )
    output_parent = output.parent.resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output_parent))
    try:
        with session_context as session:
            adm_started = runtime_clock.perf_counter()
            projection = materialize_p4_research_adm(
                reader=session.reader,
                dwd_root=provider_config.dwd_root,
                source_policy=source_policy,
                provider_identity=session.identity,
                output_root=staging / "adm",
            )
            if progress is not None:
                progress(
                    "adm-materialization",
                    {
                        "terminal": True,
                        "elapsed_seconds": round(
                            runtime_clock.perf_counter() - adm_started, 3
                        ),
                        "rows": projection.row_count,
                        "bytes": _tree_bytes(projection.root),
                    },
                )
            scan, entries = scan_event_candidate_projection(
                projection,
                reader=session.reader,
                industry_mapping=projection.industry_mapping,
                source_policy=source_policy,
                card=card,
                split_policy=split_policy,
                progress=progress,
            )
            publication_started = runtime_clock.perf_counter()
            result = _publish_package(
                staging,
                output=output,
                source_policy=source_policy,
                card=card,
                split_policy=split_policy,
                projection=projection,
                scan=scan,
                entries=entries,
                code_revision=code_revision,
            )
            if progress is not None:
                progress(
                    "package-publication",
                    {
                        "terminal": True,
                        "elapsed_seconds": round(
                            runtime_clock.perf_counter() - publication_started, 3
                        ),
                        "candidate_events": result.candidate_events,
                        "bytes": _tree_bytes(result.root),
                    },
                )
        verification_started = runtime_clock.perf_counter()
        verified = verify_physical_event_scan(result.root)
        if progress is not None:
            progress(
                "package-verification",
                {
                    "terminal": True,
                    "elapsed_seconds": round(
                        runtime_clock.perf_counter() - verification_started, 3
                    ),
                    "candidate_events": verified.candidate_events,
                },
            )
        return verified
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
