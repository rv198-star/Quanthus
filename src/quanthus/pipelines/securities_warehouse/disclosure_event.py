"""Audit and build the governed disclosure and company-event domain for Issue #60."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialReportingBuildError,
    financial_company_resolver_from_listed_company_rows,
    verify_financial_reporting_candidate,
)
from quanthus.pipelines.securities_warehouse.industry_classification import (
    _digest,
    _file_record,
    _fsync_directory,
    _write_json_atomic,
    _write_parquet_atomic,
    _write_text_atomic,
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
class DisclosureEventBuildResult:
    """A sealed, non-published six-object Issue #60 candidate."""

    root: Path
    semantic_digest: str
    row_counts: Mapping[str, int]


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


def _stable_id(kind: str, *parts: str) -> str:
    payload = "\x1f".join(("securities-warehouse-v1", kind, *parts)).encode("utf-8")
    return f"{kind}_{hashlib.sha256(payload).hexdigest()[:32]}"


def _decimal(value: object, field: str, *, scale: int = 10) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise DisclosureEventBuildError(f"{field} is not decimal") from exc
    if not parsed.is_finite():
        raise DisclosureEventBuildError(f"{field} is not finite")
    if parsed.as_tuple().exponent < -scale:
        if not isinstance(value, float):
            raise DisclosureEventBuildError(f"{field} exceeds decimal scale {scale}")
        parsed = parsed.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_EVEN)
    return parsed


def _effective_start(value: date) -> datetime:
    return datetime.combine(value, time.min, _SHANGHAI).astimezone(timezone.utc)


def disclosure_event_schemas() -> dict[str, Any]:
    """Return physical schemas for the six Issue #60-owned DWD objects."""

    try:
        import pyarrow as pa
    except ModuleNotFoundError as exc:
        raise DisclosureEventBuildError("pyarrow is required for disclosure DWD rows") from exc
    lineage = [
        pa.field("record_source", pa.string(), nullable=False),
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("source_revision", pa.string(), nullable=False),
        pa.field("source_artifact_sha256", pa.string(), nullable=False),
        pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("knowledge_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    event = [
        pa.field("effective_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("announced_at", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    interval = [
        pa.field("effective_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("effective_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    decimal_38_10 = pa.decimal128(38, 10)
    decimal_38_18 = pa.decimal128(38, 18)
    return {
        "dwd_disclosure": pa.schema(
            lineage
            + event
            + [
                pa.field("disclosure_id", pa.string(), nullable=False),
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("disclosure_type", pa.string(), nullable=False),
                pa.field("source_document_id", pa.string(), nullable=False),
                pa.field("title", pa.string(), nullable=False),
                pa.field("document_uri", pa.string(), nullable=True),
                pa.field("language", pa.string(), nullable=False),
            ]
        ),
        "dwd_disclosure_subject": pa.schema(
            lineage
            + event
            + [
                pa.field("disclosure_subject_id", pa.string(), nullable=False),
                pa.field("disclosure_id", pa.string(), nullable=False),
                pa.field("subject_type", pa.string(), nullable=False),
                pa.field("security_id", pa.string(), nullable=True),
                pa.field("corporate_action_id", pa.string(), nullable=True),
                pa.field("financial_report_id", pa.string(), nullable=True),
            ]
        ),
        "dwd_corporate_action": pa.schema(
            lineage
            + event
            + [
                pa.field("corporate_action_id", pa.string(), nullable=False),
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("action_type", pa.string(), nullable=False),
                pa.field("action_status", pa.string(), nullable=False),
                pa.field("record_date", pa.date32(), nullable=True),
                pa.field("ex_date", pa.date32(), nullable=True),
                pa.field("payment_date", pa.date32(), nullable=True),
            ]
        ),
        "dwd_corporate_action_term": pa.schema(
            lineage
            + event
            + [
                pa.field("corporate_action_term_id", pa.string(), nullable=False),
                pa.field("corporate_action_id", pa.string(), nullable=False),
                pa.field("term_type", pa.string(), nullable=False),
                pa.field("option_id", pa.string(), nullable=True),
                pa.field("currency", pa.string(), nullable=True),
                pa.field("amount_per_share", decimal_38_10, nullable=True),
                pa.field("ratio_numerator", decimal_38_10, nullable=True),
                pa.field("ratio_denominator", decimal_38_10, nullable=True),
                pa.field("subscription_price", decimal_38_10, nullable=True),
            ]
        ),
        "dwd_adjustment_factor": pa.schema(
            lineage
            + interval
            + [
                pa.field("adjustment_factor_id", pa.string(), nullable=False),
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("adjustment_method", pa.string(), nullable=False),
                pa.field("adjustment_direction", pa.string(), nullable=False),
                pa.field("factor_value", decimal_38_18, nullable=False),
                pa.field("anchor_date", pa.date32(), nullable=False),
                pa.field("evidence_set_digest", pa.string(), nullable=False),
            ]
        ),
        "dwd_adjustment_factor_input": pa.schema(
            lineage
            + interval
            + [
                pa.field("adjustment_factor_id", pa.string(), nullable=False),
                pa.field("input_type", pa.string(), nullable=False),
                pa.field("corporate_action_id", pa.string(), nullable=True),
                pa.field("input_evidence_digest", pa.string(), nullable=False),
                pa.field("input_revision", pa.string(), nullable=False),
            ]
        ),
    }


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


def _select_sources(records: Sequence[Mapping[str, Any]], ods_package: Path) -> dict[str, _Source]:
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
        or vocabulary.get("date_only_availability") != "asia_shanghai_end_of_day_then_utc"
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
        return datetime.combine(value, time(23, 59, 59, 999999), _SHANGHAI).astimezone(timezone.utc)
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


def _audit_announcements(source: _Source, resolver, *, through_date: date) -> dict[str, Any]:
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
                publication_date = published_at.astimezone(_SHANGHAI).date()
                if publication_date > through_date:
                    quality["AFTER_THROUGH_DATE"] += 1
                    categories[str(category or "UNCLASSIFIED")] += 1
                    impacts[str(impact or "UNCLASSIFIED")] += 1
                    continue
                identity, reason = resolver.resolve_for_report(
                    stock_code, period_end=publication_date
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
    sources: Mapping[str, _Source],
    vocabulary: Mapping[str, Any],
    resolver,
    *,
    through_date: date,
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
                if coordinate > through_date:
                    quality["AFTER_THROUGH_DATE"] += 1
                    continue
                identity, reason = resolver.resolve_for_report(stock_code, period_end=coordinate)
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


def materialize_announcement_rows(
    row: Mapping[str, Any],
    *,
    company_id: str,
    security_id: str,
    source_sha256: str,
    source_revision: str,
    financial_report_ids: Sequence[str] = (),
    parent_knowledge_from: datetime | None = None,
    financial_report_knowledge: Mapping[str, datetime] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Materialize one governed announcement and its typed subjects."""

    ann_id = _text(row.get("ann_id"), "announcement ann_id")
    published_at = _provider_timestamp(row.get("publish_time"), "announcement publish_time")
    knowledge_from = max(
        value for value in (published_at, parent_knowledge_from) if value is not None
    )
    disclosure_id = _stable_id("disclosure", "announcement_archive", ann_id)
    common = {
        "record_source": "announcement_archive",
        "source_record_id": ann_id,
        "source_revision": source_revision,
        "source_artifact_sha256": source_sha256,
        "knowledge_from": knowledge_from,
        "knowledge_to": None,
        "effective_at": published_at,
        "announced_at": published_at,
    }
    disclosure = {
        **common,
        "disclosure_id": disclosure_id,
        "company_id": company_id,
        "disclosure_type": str(row.get("category") or "UNCLASSIFIED"),
        "source_document_id": ann_id,
        "title": _text(row.get("title"), "announcement title"),
        "document_uri": str(row["url"]).strip() if row.get("url") else None,
        "language": "zh-CN",
    }
    subjects = [
        {
            **common,
            "disclosure_subject_id": _stable_id(
                "disclosure_subject", disclosure_id, "SECURITY", security_id
            ),
            "disclosure_id": disclosure_id,
            "subject_type": "SECURITY",
            "security_id": security_id,
            "corporate_action_id": None,
            "financial_report_id": None,
        }
    ]
    for report_id in sorted(set(financial_report_ids)):
        report_knowledge = (
            financial_report_knowledge.get(report_id)
            if financial_report_knowledge is not None
            else None
        )
        subjects.append(
            {
                **common,
                "knowledge_from": max(
                    value for value in (knowledge_from, report_knowledge) if value is not None
                ),
                "disclosure_subject_id": _stable_id(
                    "disclosure_subject", disclosure_id, "FINANCIAL_REPORT", report_id
                ),
                "disclosure_id": disclosure_id,
                "subject_type": "FINANCIAL_REPORT",
                "security_id": None,
                "corporate_action_id": None,
                "financial_report_id": report_id,
            }
        )
    return {"dwd_disclosure": [disclosure], "dwd_disclosure_subject": subjects}


def _action_coordinate(row: Mapping[str, Any]) -> tuple[date, date | None]:
    announced = (
        _report_date(row["announce_date"], "action announce_date")
        if row.get("announce_date") not in (None, "")
        else None
    )
    effective = (
        _report_date(row["ex_date"], "action ex_date")
        if row.get("ex_date") not in (None, "")
        else announced
    )
    if effective is None:
        raise DisclosureEventBuildError("corporate action has no effective or announced date")
    return effective, announced


def materialize_corporate_action_rows(
    row: Mapping[str, Any],
    *,
    source_id: str,
    source_sha256: str,
    source_revision: str,
    company_id: str,
    security_id: str,
    vocabulary: Mapping[str, Any],
    parent_knowledge_from: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Materialize one provider action or dividend row without losing term roles."""

    effective_date, announced_date = _action_coordinate(row)
    announced_at = (
        _provider_timestamp(announced_date, "action announced_at") if announced_date else None
    )
    effective_at = _provider_timestamp(effective_date, "action effective_at")
    raw_type = str(row.get("action_type") or "cash_div")
    raw_status = _text(row.get("progress"), "action progress")
    try:
        action_type = str(vocabulary["action_types"][raw_type])
        action_status = str(vocabulary["action_statuses"][raw_status])
    except KeyError as exc:
        raise DisclosureEventBuildError("corporate action vocabulary is incomplete") from exc
    stock_code = _text(row.get("stock_code"), "action stock_code")
    source_key = str(
        row.get("action_id")
        or row.get("source_row_key")
        or _stable_id(
            "action_source",
            stock_code,
            effective_date.isoformat(),
            str(row.get("announce_date") or ""),
            str(row.get("dividend_per_share") or row.get("cash_per_share") or ""),
            str(row.get("bonus_shares") or ""),
            str(row.get("transfer_shares") or ""),
        )
    )
    action_id = _stable_id("corporate_action", source_id, source_key)
    source_knowledge_from = (
        _provider_timestamp(row["built_at"], "action built_at")
        if row.get("built_at") not in (None, "")
        else _provider_timestamp(announced_date or effective_date, "action knowledge date")
    )
    knowledge_from = max(
        value for value in (source_knowledge_from, parent_knowledge_from) if value is not None
    )
    common = {
        "record_source": source_id,
        "source_record_id": source_key,
        "source_revision": source_revision,
        "source_artifact_sha256": source_sha256,
        "knowledge_from": knowledge_from,
        "knowledge_to": None,
        "effective_at": effective_at,
        "announced_at": announced_at,
    }
    action = {
        **common,
        "corporate_action_id": action_id,
        "company_id": company_id,
        "security_id": security_id,
        "action_type": action_type,
        "action_status": action_status,
        "record_date": (
            _report_date(row["record_date"], "action record_date")
            if row.get("record_date") not in (None, "")
            else None
        ),
        "ex_date": effective_date if row.get("ex_date") not in (None, "") else None,
        "payment_date": (
            _report_date(row["payment_date"], "action payment_date")
            if row.get("payment_date") not in (None, "")
            else None
        ),
    }
    terms: list[dict[str, Any]] = []
    term_fields = (
        ("cash_per_share", row.get("cash_per_share", row.get("dividend_per_share"))),
        ("bonus_shares", row.get("bonus_shares")),
        ("transfer_shares", row.get("transfer_shares")),
    )
    for raw_term, raw_value in term_fields:
        value = _decimal(raw_value, f"action {raw_term}")
        if value is None:
            continue
        term_type = str(vocabulary["term_types"][raw_term])
        term_id = _stable_id("corporate_action_term", action_id, term_type)
        terms.append(
            {
                **common,
                "corporate_action_term_id": term_id,
                "corporate_action_id": action_id,
                "term_type": term_type,
                "option_id": None,
                "currency": "CNY" if raw_term == "cash_per_share" else None,
                "amount_per_share": value if raw_term == "cash_per_share" else None,
                "ratio_numerator": value if raw_term != "cash_per_share" else None,
                "ratio_denominator": Decimal("1") if raw_term != "cash_per_share" else None,
                "subscription_price": None,
            }
        )
    return {"dwd_corporate_action": [action], "dwd_corporate_action_term": terms}


def materialize_adjustment_factor_rows(
    row: Mapping[str, Any],
    *,
    source_sha256: str,
    company_security_id: str,
    vocabulary: Mapping[str, Any],
    parent_knowledge_from: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Materialize every provider factor method and its raw evidence input."""

    stock_code = _text(row.get("stock_code"), "factor stock_code")
    effective_date = _report_date(row.get("effective_date"), "factor effective_date")
    effective_from = _effective_start(effective_date)
    revision = _text(row.get("adj_version"), "factor adj_version")
    source_revision = str(row.get("source_revision") or revision)
    source_knowledge_from = _provider_timestamp(
        row.get("knowledge_cutoff"), "factor knowledge_cutoff"
    )
    knowledge_from = max(
        value for value in (source_knowledge_from, parent_knowledge_from) if value is not None
    )
    factors: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    for field, raw_mapping in vocabulary["factor_methods"].items():
        value = _decimal(row.get(field), f"factor {field}", scale=18)
        if value is None:
            continue
        mapping = _mapping(raw_mapping, f"factor mapping {field}")
        method = _text(mapping.get("method"), f"factor method {field}")
        direction = _text(mapping.get("direction"), f"factor direction {field}")
        factor_id = _stable_id(
            "adjustment_factor",
            company_security_id,
            method,
            direction,
            revision,
        )
        evidence_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "source_sha256": source_sha256,
                        "stock_code": stock_code,
                        "effective_date": effective_date.isoformat(),
                        "field": field,
                        "value": str(value),
                        "revision": source_revision,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("ascii")
            ).hexdigest()
        )
        common = {
            "record_source": "adjustment_factors_pit",
            "source_record_id": (
                f"stock_code={stock_code}&effective_date={effective_date.isoformat()}"
                f"&method={field}&revision={revision}"
            ),
            "source_revision": source_revision,
            "source_artifact_sha256": source_sha256,
            "knowledge_from": knowledge_from,
            "knowledge_to": None,
            "effective_from": effective_from,
            "effective_to": None,
        }
        factors.append(
            {
                **common,
                "adjustment_factor_id": factor_id,
                "security_id": company_security_id,
                "adjustment_method": method,
                "adjustment_direction": direction,
                "factor_value": value,
                "anchor_date": effective_date,
                "evidence_set_digest": evidence_digest,
            }
        )
        inputs.append(
            {
                **common,
                "adjustment_factor_id": factor_id,
                "input_type": "SOURCE_EVIDENCE",
                "corporate_action_id": None,
                "input_evidence_digest": evidence_digest,
                "input_revision": source_revision,
            }
        )
    return {"dwd_adjustment_factor": factors, "dwd_adjustment_factor_input": inputs}


def close_adjustment_factor_intervals(
    factors: list[dict[str, Any]], inputs: list[dict[str, Any]]
) -> None:
    """Close each factor revision at its next effective coordinate in place."""

    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in factors:
        by_id.setdefault(str(row["adjustment_factor_id"]), []).append(row)
    closes: dict[tuple[str, datetime], datetime | None] = {}
    for factor_id, rows in by_id.items():
        rows.sort(key=lambda row: row["effective_from"])
        for index, row in enumerate(rows):
            next_from = rows[index + 1]["effective_from"] if index + 1 < len(rows) else None
            row["effective_to"] = next_from
            closes[(factor_id, row["effective_from"])] = next_from
    for row in inputs:
        row["effective_to"] = closes[(str(row["adjustment_factor_id"]), row["effective_from"])]


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
    through_date = _report_date(semantic_plan.get("through_date"), "disclosure through_date")
    source_rows = {
        "announcements": _audit_announcements(
            sources["announcements"], resolver, through_date=through_date
        ),
        **_audit_parquet_sources(sources, vocabulary, resolver, through_date=through_date),
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
            "adjustment_factor_coordinates": source_rows["adjustment_factors"]["input_rows"],
        },
        "authority_policy": {
            "announcements": "canonical_archive_authority_daily_deltas_reconciliation_only",
            "corporate_actions": "canonical_normalized_authority_revisions_lineage_only",
            "adjustment_factors": "canonical_pit_authority_revisions_lineage_only",
        },
    }
    _write_json_immutable(output, report)
    return DisclosureEventSourceAudit(report)


_OWNED_TABLES = (
    "dwd_disclosure",
    "dwd_disclosure_subject",
    "dwd_corporate_action",
    "dwd_corporate_action_term",
    "dwd_adjustment_factor",
    "dwd_adjustment_factor_input",
)


def _read_audit(path: Path) -> tuple[dict[str, Any], str]:
    audit = _object(path)
    if audit.get("schema_version") != 1 or audit.get("kind") != "dwd_disclosure_event_source_audit":
        raise DisclosureEventBuildError("disclosure source audit is unsupported")
    return audit, _sha256(path)


def _financial_report_index(
    candidate: Path,
) -> dict[tuple[str, date], tuple[tuple[str, datetime], ...]]:
    try:
        import pyarrow.dataset as ds
    except ModuleNotFoundError as exc:
        raise DisclosureEventBuildError("pyarrow is required for report subjects") from exc
    root = candidate / "payload/financial_reporting/dwd_financial_report"
    index: dict[tuple[str, date], dict[str, datetime]] = {}
    scanner = ds.dataset(root, format="parquet", partitioning="hive").scanner(
        columns=["company_id", "period_end", "financial_report_id", "knowledge_from"],
        batch_size=32768,
    )
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            key = (str(row["company_id"]), row["period_end"])
            report_id = str(row["financial_report_id"])
            knowledge_from = row["knowledge_from"]
            if not isinstance(knowledge_from, datetime):
                raise DisclosureEventBuildError("financial report index lacks knowledge_from")
            prior = index.setdefault(key, {}).get(report_id)
            if prior is not None and prior != knowledge_from:
                raise DisclosureEventBuildError("financial report index has conflicting knowledge")
            index[key][report_id] = knowledge_from
    return {key: tuple(sorted(values.items())) for key, values in index.items()}


def _title_period(title: str) -> date | None:
    year_match = re.search(r"(?<!\d)(20\d{2})\s*年", title)
    if year_match is None:
        return None
    year = int(year_match.group(1))
    if re.search(r"(?:第一季度|一季度)报告", title):
        return date(year, 3, 31)
    if re.search(r"(?:半年度|中期)报告", title):
        return date(year, 6, 30)
    if re.search(r"(?:第三季度|三季度)报告", title):
        return date(year, 9, 30)
    if "年度报告" in title:
        return date(year, 12, 31)
    return None


def _partition_path(work: Path, table: str, row: Mapping[str, Any], part: str) -> Path:
    root = work / "payload/disclosure_company_event" / table
    if table in {"dwd_disclosure", "dwd_disclosure_subject", "dwd_corporate_action"}:
        coordinate = row["effective_at"]
        return (
            root
            / f"effective_at_year={coordinate.year:04d}"
            / f"effective_at_month={coordinate.month:02d}"
            / f"part-{part}.parquet"
        )
    if table == "dwd_corporate_action_term":
        coordinate = row["effective_at"]
        return root / f"effective_at_year={coordinate.year:04d}" / f"part-{part}.parquet"
    return root / f"part-{part}.parquet"


def _write_partitioned_rows(
    work: Path,
    table: str,
    rows: Sequence[Mapping[str, Any]],
    schema: Any,
    part: str,
) -> list[str]:
    try:
        import pyarrow as pa
    except ModuleNotFoundError as exc:
        raise DisclosureEventBuildError("pyarrow is required for disclosure writes") from exc
    grouped: dict[Path, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_partition_path(work, table, row, part), []).append(row)
    files: list[str] = []
    for path, values in sorted(grouped.items(), key=lambda item: str(item[0])):
        _write_parquet_atomic(path, pa.Table.from_pylist(values, schema=schema))
        files.append(path.relative_to(work).as_posix())
    return files


def _synthetic_action_disclosure(
    action: Mapping[str, Any], *, source_sha256: str
) -> dict[str, list[dict[str, Any]]]:
    action_id = str(action["corporate_action_id"])
    disclosure_id = _stable_id("disclosure", "corporate_action", action_id)
    common = {
        key: action[key]
        for key in (
            "record_source",
            "source_record_id",
            "source_revision",
            "knowledge_from",
            "knowledge_to",
            "effective_at",
            "announced_at",
        )
    }
    common["source_artifact_sha256"] = source_sha256
    disclosure = {
        **common,
        "disclosure_id": disclosure_id,
        "company_id": action["company_id"],
        "disclosure_type": "CORPORATE_ACTION_DECLARATION",
        "source_document_id": action["source_record_id"],
        "title": f"{action['action_type']} {action['action_status']}",
        "document_uri": None,
        "language": "zh-CN",
    }
    subject = {
        **common,
        "disclosure_subject_id": _stable_id(
            "disclosure_subject", disclosure_id, "CORPORATE_ACTION", action_id
        ),
        "disclosure_id": disclosure_id,
        "subject_type": "CORPORATE_ACTION",
        "security_id": None,
        "corporate_action_id": action_id,
        "financial_report_id": None,
    }
    return {"dwd_disclosure": [disclosure], "dwd_disclosure_subject": [subject]}


def _verify_candidate(output: Path, identity: Mapping[str, Any]) -> DisclosureEventBuildResult:
    if output.is_symlink() or not output.is_dir():
        raise DisclosureEventBuildError("disclosure candidate must be physical")
    manifest = _object(output / "manifest.json")
    if (output / "READY").read_text(encoding="ascii").strip() != _sha256(output / "manifest.json"):
        raise DisclosureEventBuildError("disclosure READY differs")
    for field, value in identity.items():
        if manifest.get(field) != value:
            raise DisclosureEventBuildError("disclosure candidate identity differs")
    if manifest.get("owned_tables") != list(_OWNED_TABLES):
        raise DisclosureEventBuildError("disclosure candidate table ownership differs")
    declared = {str(item["path"]): item for item in manifest.get("files", [])}
    for relative, item in declared.items():
        path = output / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item.get("size_bytes")
            or _sha256(path) != item.get("sha256")
        ):
            raise DisclosureEventBuildError(f"disclosure candidate file differs: {relative}")
    physical = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if physical != set(declared) | {"manifest.json", "READY"}:
        raise DisclosureEventBuildError("disclosure physical inventory differs")
    return DisclosureEventBuildResult(
        output.resolve(),
        str(manifest["semantic_digest"]),
        {str(key): int(value) for key, value in manifest["row_counts"].items()},
    )


def verify_disclosure_event_candidate(
    output: Path,
    *,
    run_id: str,
    input_plan_digest: str,
    model_sha256: str,
) -> DisclosureEventBuildResult:
    """Deeply verify a same-run #60 predecessor for downstream builders."""

    manifest = _object(output / "manifest.json")
    if (
        manifest.get("run_id") != run_id
        or manifest.get("input_plan_digest") != input_plan_digest
        or manifest.get("model_sha256") != model_sha256
    ):
        raise DisclosureEventBuildError("disclosure predecessor binding differs")
    identity = {
        field: manifest.get(field)
        for field in (
            "run_id",
            "input_plan_digest",
            "model_sha256",
            "source_audit_sha256",
            "vocabulary_sha256",
            "listed_company_semantic_digest",
            "financial_reporting_semantic_digest",
        )
    }
    return _verify_candidate(output, identity)


def _build_disclosure_event_locked(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    financial_reporting_candidate: Path,
    vocabulary_path: Path,
    source_audit_path: Path,
    output: Path,
    run_id: str,
    announcement_batch_size: int,
) -> DisclosureEventBuildResult:
    plan, ledger = _verify_plan_package(input_plan)
    semantic_plan = _mapping(plan.get("plan"), "input plan")
    model = _object(model_path)
    _verify_ods_binding(ods_package, semantic_plan, model)
    plan_digest = _text(plan.get("plan_digest"), "input plan digest")
    model_sha = _sha256(model_path)
    if plan.get("run_id") != run_id or semantic_plan.get("mode") != "base":
        raise DisclosureEventBuildError("disclosure build must match one Base plan")
    through_date = _report_date(semantic_plan.get("through_date"), "disclosure through_date")
    if _mapping(semantic_plan.get("model"), "input plan model").get("sha256") != model_sha:
        raise DisclosureEventBuildError("disclosure build model differs from plan")
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
    audit, audit_sha = _read_audit(source_audit_path)
    if any(
        audit.get(field) != value
        for field, value in {
            "run_id": run_id,
            "input_plan_digest": plan_digest,
            "model_sha256": model_sha,
            "listed_company_semantic_digest": listed.semantic_digest,
            "financial_reporting_semantic_digest": financial.semantic_digest,
        }.items()
    ):
        raise DisclosureEventBuildError("disclosure source audit binding differs")
    vocabulary, vocabulary_sha = _load_vocabulary(vocabulary_path)
    if audit.get("vocabulary_sha256") != vocabulary_sha:
        raise DisclosureEventBuildError("disclosure vocabulary differs from audit")
    records = [
        _mapping(raw, "source ledger record")
        for raw in _sequence(ledger.get("records"), "source ledger records")
    ]
    sources = _select_sources(records, ods_package)
    identity = {
        "run_id": run_id,
        "input_plan_digest": plan_digest,
        "model_sha256": model_sha,
        "source_audit_sha256": audit_sha,
        "vocabulary_sha256": vocabulary_sha,
        "listed_company_semantic_digest": listed.semantic_digest,
        "financial_reporting_semantic_digest": financial.semantic_digest,
    }
    if output.exists():
        return _verify_candidate(output, identity)
    work = output.parent / f".{output.name}.work"
    work.mkdir(parents=True, exist_ok=True)
    checkpoint_path = work / "checkpoint.json"
    checkpoint = (
        _object(checkpoint_path)
        if checkpoint_path.exists()
        else {"schema_version": 1, "identity": identity, "completed_batches": {}, "stages": {}}
    )
    if checkpoint.get("identity") != identity:
        raise DisclosureEventBuildError("disclosure checkpoint identity differs")
    schemas = disclosure_event_schemas()
    resolver = _load_company_resolver(listed_company_candidate)
    report_index = _financial_report_index(financial_reporting_candidate)
    totals = {table: 0 for table in _OWNED_TABLES}
    quality: Counter[str] = Counter()
    connection = sqlite3.connect(f"file:{sources['announcements'].path}?mode=ro", uri=True)
    try:
        cursor = connection.execute(
            "SELECT code,title,publish_time,ann_id,url,category,impact "
            "FROM announcements ORDER BY rowid"
        )
        batch_index = 0
        while True:
            raw_batch = cursor.fetchmany(announcement_batch_size)
            if not raw_batch:
                break
            batch_id = f"ann-{batch_index:06d}"
            prior = checkpoint["completed_batches"].get(batch_id)
            if prior is not None:
                for table, count in prior["row_counts"].items():
                    totals[table] += int(count)
                quality.update(prior.get("quality_exceptions", {}))
                batch_index += 1
                continue
            rows = {"dwd_disclosure": [], "dwd_disclosure_subject": []}
            batch_quality: Counter[str] = Counter()
            for code, title, publish_time, ann_id, url, category, impact in raw_batch:
                del impact
                published = _provider_timestamp(publish_time, "announcement publish_time")
                publication_date = published.astimezone(_SHANGHAI).date()
                if publication_date > through_date:
                    batch_quality["AFTER_THROUGH_DATE"] += 1
                    continue
                company, reason = resolver.resolve_for_report(
                    str(code), period_end=publication_date
                )
                if company is None:
                    batch_quality[str(reason)] += 1
                    continue
                period = _title_period(str(title))
                report_refs = report_index.get((company.company_id, period), ()) if period else ()
                report_ids = tuple(report_id for report_id, _knowledge in report_refs)
                expanded = materialize_announcement_rows(
                    {
                        "ann_id": ann_id,
                        "publish_time": publish_time,
                        "title": title,
                        "url": url,
                        "category": category,
                    },
                    company_id=company.company_id,
                    security_id=company.security_id,
                    source_sha256=sources["announcements"].sha256,
                    source_revision=run_id,
                    financial_report_ids=report_ids,
                    parent_knowledge_from=company.knowledge_from,
                    financial_report_knowledge=dict(report_refs),
                )
                for table in rows:
                    rows[table].extend(expanded[table])
                if reason is not None:
                    batch_quality[str(reason)] += 1
            files: list[str] = []
            counts: dict[str, int] = {}
            for table, values in rows.items():
                counts[table] = len(values)
                files.extend(_write_partitioned_rows(work, table, values, schemas[table], batch_id))
                totals[table] += len(values)
            checkpoint["completed_batches"][batch_id] = {
                "input_rows": len(raw_batch),
                "row_counts": counts,
                "quality_exceptions": dict(sorted(batch_quality.items())),
                "files": files,
            }
            quality.update(batch_quality)
            _write_json_atomic(checkpoint_path, checkpoint)
            batch_index += 1
    finally:
        connection.close()
    if not checkpoint["stages"].get("events"):
        try:
            import pyarrow.parquet as pq
        except ModuleNotFoundError as exc:
            raise DisclosureEventBuildError("pyarrow is required for event build") from exc
        rows = {table: [] for table in _OWNED_TABLES}
        event_quality: Counter[str] = Counter()
        natural_keys: set[tuple[object, ...]] = set()
        for source_id in ("corporate_actions", "dividend_history"):
            source = sources[source_id]
            for batch in pq.ParquetFile(source.path).iter_batches(batch_size=8192):
                for raw in batch.to_pylist():
                    if source_id == "dividend_history":
                        raw["ex_date"] = raw.get("ex_dividend_date")
                    coordinate, announced = _action_coordinate(raw)
                    if coordinate > through_date:
                        event_quality["AFTER_THROUGH_DATE"] += 1
                        continue
                    key = (
                        str(raw.get("stock_code")),
                        coordinate,
                        announced,
                        str(raw.get("cash_per_share", raw.get("dividend_per_share"))),
                        str(raw.get("bonus_shares")),
                        str(raw.get("transfer_shares")),
                    )
                    if key in natural_keys:
                        continue
                    natural_keys.add(key)
                    company, reason = resolver.resolve_for_report(
                        str(raw.get("stock_code")), period_end=coordinate
                    )
                    if company is None:
                        event_quality[str(reason)] += 1
                        continue
                    expanded = materialize_corporate_action_rows(
                        raw,
                        source_id=source_id,
                        source_sha256=source.sha256,
                        source_revision=run_id,
                        company_id=company.company_id,
                        security_id=company.security_id,
                        vocabulary=vocabulary,
                        parent_knowledge_from=company.knowledge_from,
                    )
                    for table, values in expanded.items():
                        rows[table].extend(values)
                    synthetic = _synthetic_action_disclosure(
                        expanded["dwd_corporate_action"][0], source_sha256=source.sha256
                    )
                    rows["dwd_disclosure"].extend(synthetic["dwd_disclosure"])
                    rows["dwd_disclosure_subject"].extend(synthetic["dwd_disclosure_subject"])
        factor_source = sources["adjustment_factors"]
        for batch in pq.ParquetFile(factor_source.path).iter_batches(batch_size=8192):
            for raw in batch.to_pylist():
                coordinate = _report_date(raw.get("effective_date"), "factor date")
                if coordinate > through_date:
                    event_quality["AFTER_THROUGH_DATE"] += 1
                    continue
                company, reason = resolver.resolve_for_report(
                    str(raw.get("stock_code")), period_end=coordinate
                )
                if company is None:
                    event_quality[str(reason)] += 1
                    continue
                expanded = materialize_adjustment_factor_rows(
                    raw,
                    source_sha256=factor_source.sha256,
                    company_security_id=company.security_id,
                    vocabulary=vocabulary,
                    parent_knowledge_from=company.knowledge_from,
                )
                for table, values in expanded.items():
                    rows[table].extend(values)
        close_adjustment_factor_intervals(
            rows["dwd_adjustment_factor"],
            rows["dwd_adjustment_factor_input"],
        )
        event_counts: dict[str, int] = {}
        files: list[str] = []
        for table, values in rows.items():
            if not values:
                continue
            event_counts[table] = len(values)
            files.extend(_write_partitioned_rows(work, table, values, schemas[table], "events"))
            totals[table] += len(values)
        checkpoint["stages"]["events"] = {
            "row_counts": event_counts,
            "quality_exceptions": dict(sorted(event_quality.items())),
            "files": files,
        }
        quality.update(event_quality)
        _write_json_atomic(checkpoint_path, checkpoint)
    else:
        for table, count in checkpoint["stages"]["events"]["row_counts"].items():
            totals[table] += int(count)
        quality.update(checkpoint["stages"]["events"].get("quality_exceptions", {}))
    reconciliation = {
        "schema_version": 1,
        "kind": "dwd_disclosure_event_reconciliation",
        "row_counts": totals,
        "quality_exceptions": dict(sorted(quality.items())),
        "announcement_input_rows": audit["source_rows"]["announcements"]["input_rows"],
    }
    _write_json_atomic(work / "reconciliation/source-row-reconciliation.json", reconciliation)
    shutil.copyfile(source_audit_path, work / "source-audit.json")
    files = sorted(
        path
        for path in work.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "READY"}
    )
    table_files = {
        table: [
            _file_record(work, path)
            for path in files
            if f"/{table}/" in f"/{path.relative_to(work).as_posix()}"
        ]
        for table in _OWNED_TABLES
    }
    semantic_digest = _digest({**identity, "table_files": table_files})
    manifest = {
        "schema_version": 1,
        "kind": "dwd_disclosure_event_candidate",
        **identity,
        "semantic_digest": semantic_digest,
        "owned_tables": list(_OWNED_TABLES),
        "row_counts": totals,
        "quality_exceptions": dict(sorted(quality.items())),
        "files": [_file_record(work, path) for path in files],
    }
    _write_json_atomic(work / "manifest.json", manifest)
    _write_text_atomic(work / "READY", _sha256(work / "manifest.json") + "\n")
    _fsync_directory(work)
    _verify_candidate(work, identity)
    os.replace(work, output)
    _fsync_directory(output.parent)
    return _verify_candidate(output, identity)


def build_disclosure_event(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    financial_reporting_candidate: Path,
    vocabulary_path: Path,
    source_audit_path: Path,
    output: Path,
    run_id: str,
    announcement_batch_size: int = 50000,
) -> DisclosureEventBuildResult:
    """Build a checkpointed Issue #60 candidate without advancing CURRENT."""

    if announcement_batch_size < 1 or output.is_symlink():
        raise DisclosureEventBuildError("invalid disclosure build arguments")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / f".{output.name}.lock"
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o640,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise DisclosureEventBuildError("disclosure build lock is not physical")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _build_disclosure_event_locked(
            ods_package=ods_package,
            input_plan=input_plan,
            model_path=model_path,
            listed_company_candidate=listed_company_candidate,
            financial_reporting_candidate=financial_reporting_candidate,
            vocabulary_path=vocabulary_path,
            source_audit_path=source_audit_path,
            output=output,
            run_id=run_id,
            announcement_batch_size=announcement_batch_size,
        )
    finally:
        os.close(descriptor)
