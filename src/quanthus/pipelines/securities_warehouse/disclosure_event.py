"""Audit and build the governed disclosure and company-event domain for Issue #60."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialReportingBuildError,
    financial_company_resolver_from_listed_company_rows,
    verify_financial_reporting_candidate,
)
from quanthus.pipelines.securities_warehouse.listed_company import (
    ListedCompanyBuildError,
    verify_listed_company_candidate,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    TradingCalendarBuildError,
    _mapping,
    _object,
    _sequence,
    _verify_ods_binding,
    _verify_plan_package,
)


class DisclosureEventBuildError(RuntimeError):
    """Raised when Issue #60 inputs cannot be represented losslessly."""


@dataclass(frozen=True)
class DisclosureEventSourceAudit:
    """Checksum-bound preflight evidence for the Issue #60 build."""

    report: Mapping[str, Any]


@dataclass(frozen=True)
class _Source:
    source_id: str
    path: Path
    relative_path: str
    sha256: str
    row_count: int | None
    member: str | None = None


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SOURCE_SPECS = (
    ("announcement_archive", "announcement_archive", "file", "source_evidence", None),
    (
        "announcements",
        "announcement_archive|sqlite_table|announcements",
        "container_member",
        None,
        "announcements",
    ),
    ("corporate_actions", "corporate_actions", "file", "source_evidence", None),
    ("dividend_history", "dividend_history", "file", "source_evidence", None),
    (
        "adjustment_factors",
        "adjustment_factors_pit",
        "file",
        "source_evidence",
        None,
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DisclosureEventBuildError(f"{field} must be a non-empty string")
    return value.strip()


def _source_path(ods_package: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise DisclosureEventBuildError("disclosure source path is unsafe")
    root = ods_package.resolve()
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise DisclosureEventBuildError(f"disclosure source is not physical: {relative_path}")
    return path


def _select_sources(
    records: Sequence[Mapping[str, Any]], ods_package: Path
) -> dict[str, _Source]:
    selected: dict[str, _Source] = {}
    by_identity = {record.get("mapping_identity"): record for record in records}
    for source_id, identity, record_type, role, member in _SOURCE_SPECS:
        matches = [
            record
            for record in records
            if record.get("mapping_identity") == identity
            and record.get("record_type") == record_type
            and record.get("disposition") == "modeled"
            and (role is None or record.get("role") == role)
            and (member is None or record.get("member") == member)
        ]
        if len(matches) != 1:
            raise DisclosureEventBuildError(
                f"expected one governed {source_id} source, found {len(matches)}"
            )
        record = matches[0]
        artifact = record
        relative_path = _text(record.get("path"), f"{source_id} path")
        if record_type == "container_member":
            artifact = by_identity.get(record.get("container_source_id"))
            if not isinstance(artifact, Mapping):
                raise DisclosureEventBuildError("announcement container artifact is absent")
            relative_path = _text(record.get("container_path"), "container path")
        path = _source_path(ods_package, relative_path)
        sha = _text(artifact.get("sha256"), f"{source_id} sha256")
        if path.stat().st_size != artifact.get("size_bytes") or _sha256(path) != sha:
            raise DisclosureEventBuildError(f"{source_id} differs from sealed input plan")
        row_count = record.get("row_count")
        if row_count is not None and (
            not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0
        ):
            raise DisclosureEventBuildError(f"{source_id} row count is invalid")
        selected[source_id] = _Source(
            source_id,
            path,
            relative_path,
            sha,
            row_count,
            member,
        )
    if selected["announcements"].path != selected["announcement_archive"].path:
        raise DisclosureEventBuildError("announcement member is not bound to its archive")
    return selected


def _load_vocabulary(path: Path) -> tuple[dict[str, Any], str]:
    vocabulary = _object(path)
    if (
        vocabulary.get("schema_version") != 1
        or vocabulary.get("kind") != "quanthus_disclosure_event_vocabulary"
        or not isinstance(vocabulary.get("mapping_version"), str)
        or not vocabulary["mapping_version"]
        or vocabulary.get("date_only_availability")
        != "asia_shanghai_end_of_day_then_utc"
    ):
        raise DisclosureEventBuildError("disclosure event vocabulary is unsupported")
    for field in ("action_types", "action_statuses", "term_types", "factor_methods"):
        if not isinstance(vocabulary.get(field), Mapping) or not vocabulary[field]:
            raise DisclosureEventBuildError(f"event vocabulary has no {field}")
    return vocabulary, _sha256(path)


def _provider_timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return datetime.combine(value, time(23, 59, 59, 999999), _SHANGHAI).astimezone(
            timezone.utc
        )
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DisclosureEventBuildError(f"{field} is not a supported timestamp") from exc
    else:
        raise DisclosureEventBuildError(f"{field} is empty")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=_SHANGHAI)
    return parsed.astimezone(timezone.utc)


def _report_date(value: object, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()[:10]
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise DisclosureEventBuildError(f"{field} must contain YYYY-MM-DD") from exc
    raise DisclosureEventBuildError(f"{field} is empty")


def _load_company_resolver(listed_company_candidate: Path):
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise DisclosureEventBuildError("pyarrow is required for disclosure audit") from exc
    root = listed_company_candidate / "payload/listed_company"
    return financial_company_resolver_from_listed_company_rows(
        pq.read_table(root / "dwd_security.parquet").to_pylist(),
        pq.read_table(root / "dwd_security_identifier.parquet").to_pylist(),
    )


def _audit_announcements(source: _Source, resolver) -> dict[str, Any]:
    uri = f"file:{source.path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise DisclosureEventBuildError("cannot open announcement archive") from exc
    required = {"code", "title", "publish_time", "ann_id", "url", "category", "impact"}
    categories: Counter[str] = Counter()
    impacts: Counter[str] = Counter()
    quality: Counter[str] = Counter()
    row_count = 0
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(announcements)")}
        if not required.issubset(columns):
            raise DisclosureEventBuildError("announcement table schema is incomplete")
        duplicate = connection.execute(
            """
            SELECT ann_id FROM announcements
            GROUP BY ann_id HAVING COUNT(*) > 1 LIMIT 1
            """
        ).fetchone()
        if duplicate is not None:
            raise DisclosureEventBuildError("announcement archive has duplicate ann_id")
        cursor = connection.execute(
            "SELECT code,title,publish_time,ann_id,url,category,impact FROM announcements"
        )
        while True:
            batch = cursor.fetchmany(8192)
            if not batch:
                break
            for code, title, publish_time, ann_id, _url, category, impact in batch:
                row_count += 1
                stock_code = _text(code, "announcement code")
                if not re.fullmatch(r"\d{6}", stock_code):
                    raise DisclosureEventBuildError("announcement code is invalid")
                _text(title, "announcement title")
                _text(ann_id, "announcement id")
                published_at = _provider_timestamp(publish_time, "announcement publish_time")
                identity, reason = resolver.resolve_for_report(
                    stock_code, period_end=published_at.date()
                )
                if identity is None or reason is not None:
                    quality[str(reason)] += 1
                categories[str(category or "UNCLASSIFIED")] += 1
                impacts[str(impact or "UNCLASSIFIED")] += 1
    except sqlite3.Error as exc:
        raise DisclosureEventBuildError("cannot audit announcement archive") from exc
    finally:
        connection.close()
    if source.row_count != row_count:
        raise DisclosureEventBuildError("announcement count differs from input plan")
    return {
        "input_rows": row_count,
        "unique_document_ids": row_count,
        "categories": dict(sorted(categories.items())),
        "impacts": dict(sorted(impacts.items())),
        "identity_quality_exceptions": dict(sorted(quality.items())),
    }


def _audit_parquet_sources(
    sources: Mapping[str, _Source], vocabulary: Mapping[str, Any], resolver
) -> dict[str, Any]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise DisclosureEventBuildError("pyarrow is required for event audit") from exc
    contracts = {
        "corporate_actions": {
            "action_id",
            "stock_code",
            "ex_date",
            "announce_date",
            "action_type",
            "cash_per_share",
            "bonus_shares",
            "transfer_shares",
            "progress",
            "source_row_key",
            "built_at",
        },
        "dividend_history": {
            "stock_code",
            "announce_date",
            "dividend_per_share",
            "bonus_shares",
            "transfer_shares",
            "ex_dividend_date",
            "progress",
        },
        "adjustment_factors": {
            "stock_code",
            "effective_date",
            "fore_adjust_factor",
            "back_adjust_factor",
            "adjust_factor",
            "adj_version",
            "knowledge_cutoff",
            "source_revision",
        },
    }
    report: dict[str, Any] = {}
    for source_id, required in contracts.items():
        source = sources[source_id]
        parquet = pq.ParquetFile(source.path)
        missing = sorted(required.difference(parquet.schema_arrow.names))
        if missing:
            raise DisclosureEventBuildError(f"{source_id} lacks columns: {missing}")
        if source.row_count != parquet.metadata.num_rows:
            raise DisclosureEventBuildError(f"{source_id} count differs from input plan")
        quality: Counter[str] = Counter()
        observed_types: set[str] = set()
        observed_statuses: set[str] = set()
        unique_keys: set[tuple[object, ...]] = set()
        for batch in parquet.iter_batches(batch_size=8192):
            for row in batch.to_pylist():
                stock_code = _text(row.get("stock_code"), f"{source_id} stock_code")
                if source_id == "corporate_actions":
                    key = (_text(row.get("action_id"), "action id"),)
                    observed_types.add(_text(row.get("action_type"), "action type"))
                    observed_statuses.add(_text(row.get("progress"), "action status"))
                    coordinate = _report_date(
                        row.get("ex_date") or row.get("announce_date"), "action date"
                    )
                elif source_id == "dividend_history":
                    coordinate = _report_date(
                        row.get("ex_dividend_date") or row.get("announce_date"),
                        "dividend date",
                    )
                    key = (stock_code, coordinate, row.get("announce_date"))
                    observed_statuses.add(_text(row.get("progress"), "dividend status"))
                else:
                    coordinate = _report_date(row.get("effective_date"), "factor date")
                    key = (stock_code, coordinate, row.get("adj_version"))
                if key in unique_keys:
                    raise DisclosureEventBuildError(f"{source_id} has a duplicate business key")
                unique_keys.add(key)
                identity, reason = resolver.resolve_for_report(
                    stock_code, period_end=coordinate
                )
                if identity is None or reason is not None:
                    quality[str(reason)] += 1
        if source_id == "corporate_actions" and observed_types.difference(
            vocabulary["action_types"]
        ):
            raise DisclosureEventBuildError("event vocabulary lacks action types")
        if observed_statuses.difference(vocabulary["action_statuses"]):
            raise DisclosureEventBuildError(f"event vocabulary lacks {source_id} statuses")
        report[source_id] = {
            "input_rows": parquet.metadata.num_rows,
            "unique_business_keys": len(unique_keys),
            "observed_types": sorted(observed_types),
            "observed_statuses": sorted(observed_statuses),
            "identity_quality_exceptions": dict(sorted(quality.items())),
        }
    return report


def _write_json_immutable(path: Path, value: Mapping[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "ascii"
    )
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise DisclosureEventBuildError("disclosure audit already exists and differs")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def audit_disclosure_event_source_package(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    financial_reporting_candidate: Path,
    vocabulary_path: Path,
    output: Path,
    run_id: str,
) -> DisclosureEventSourceAudit:
    """Verify all authoritative #60 sources before any candidate payload is written."""

    try:
        plan, ledger = _verify_plan_package(input_plan)
        semantic_plan = _mapping(plan.get("plan"), "input plan")
        model = _object(model_path)
        _verify_ods_binding(ods_package, semantic_plan, model)
    except TradingCalendarBuildError as exc:
        raise DisclosureEventBuildError("disclosure input plan verification failed") from exc
    if plan.get("run_id") != run_id or semantic_plan.get("mode") != "base":
        raise DisclosureEventBuildError("disclosure audit must match one Base run")
    model_sha = _sha256(model_path)
    if _mapping(semantic_plan.get("model"), "input plan model").get("sha256") != model_sha:
        raise DisclosureEventBuildError("disclosure model differs from input plan")
    plan_digest = _text(plan.get("plan_digest"), "input plan digest")
    try:
        listed = verify_listed_company_candidate(
            listed_company_candidate,
            run_id=run_id,
            input_plan_digest=plan_digest,
            model_sha256=model_sha,
        )
        financial = verify_financial_reporting_candidate(
            financial_reporting_candidate,
            run_id=run_id,
            input_plan_digest=plan_digest,
            model_sha256=model_sha,
        )
    except (ListedCompanyBuildError, FinancialReportingBuildError) as exc:
        raise DisclosureEventBuildError("disclosure predecessor verification failed") from exc
    records = [
        _mapping(raw, "source ledger record")
        for raw in _sequence(ledger.get("records"), "source ledger records")
    ]
    sources = _select_sources(records, ods_package)
    vocabulary, vocabulary_sha = _load_vocabulary(vocabulary_path)
    resolver = _load_company_resolver(listed_company_candidate)
    source_rows = {
        "announcements": _audit_announcements(sources["announcements"], resolver),
        **_audit_parquet_sources(sources, vocabulary, resolver),
    }
    report = {
        "schema_version": 1,
        "kind": "dwd_disclosure_event_source_audit",
        "run_id": run_id,
        "input_plan_digest": plan_digest,
        "model_sha256": model_sha,
        "listed_company_semantic_digest": listed.semantic_digest,
        "financial_reporting_semantic_digest": financial.semantic_digest,
        "vocabulary_sha256": vocabulary_sha,
        "vocabulary_version": vocabulary["mapping_version"],
        "source_sha256": {
            source_id: source.sha256 for source_id, source in sorted(sources.items())
        },
        "source_rows": source_rows,
        "candidate_size_calibration": {
            "announcement_disclosures": source_rows["announcements"]["input_rows"],
            "corporate_actions": source_rows["corporate_actions"]["input_rows"],
            "adjustment_factor_coordinates": source_rows["adjustment_factors"][
                "input_rows"
            ],
        },
        "authority_policy": {
            "announcements": "canonical_archive_authority_daily_deltas_reconciliation_only",
            "corporate_actions": "canonical_normalized_authority_revisions_lineage_only",
            "adjustment_factors": "canonical_pit_authority_revisions_lineage_only",
        },
    }
    _write_json_immutable(output, report)
    return DisclosureEventSourceAudit(report)
