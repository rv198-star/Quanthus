"""Audit and build the governed ownership and capital domain for Issue #63."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import sqlite3
import stat
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse.disclosure_event import (
    DisclosureEventBuildError,
    _load_company_resolver,
    _report_date,
    _sha256,
    _source_path,
    _text,
    _write_json_immutable,
    verify_disclosure_event_candidate,
)
from quanthus.pipelines.securities_warehouse.disclosure_event import (
    _select_sources as _select_disclosure_sources,
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
from quanthus.pipelines.securities_warehouse.model_extension import (
    ModelExtensionError,
    verify_additive_model_extension,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    TradingCalendarBuildError,
    _mapping,
    _object,
    _sequence,
    _verify_ods_binding,
    _verify_plan_package,
)


class OwnershipCapitalBuildError(RuntimeError):
    """Raised when Issue #63 inputs cannot be represented losslessly."""


@dataclass(frozen=True)
class OwnershipCapitalSourceAudit:
    """Checksum-bound preflight evidence for the Issue #63 build."""

    report: Mapping[str, Any]


@dataclass(frozen=True)
class OwnershipCapitalBuildResult:
    """A sealed, non-published four-object Issue #63 candidate."""

    root: Path
    semantic_digest: str
    row_counts: Mapping[str, int]


@dataclass(frozen=True)
class _Source:
    source_id: str
    path: Path
    relative_path: str
    sha256: str
    row_count: int
    member: str | None = None


_F10_CATEGORIES = {"股本结构", "股东研究"}
_F10_PARSER_VERSION = "quanthus-tdx-f10-table-v1"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _effective_start(value: date) -> datetime:
    return datetime.combine(value, time.min, _SHANGHAI).astimezone(timezone.utc)


def ownership_capital_schemas() -> dict[str, Any]:
    """Return v1 + additive v1.1 physical schemas for Issue #63."""

    try:
        import pyarrow as pa
    except ModuleNotFoundError as exc:
        raise OwnershipCapitalBuildError("pyarrow is required for ownership DWD rows") from exc
    lineage = [
        pa.field("record_source", pa.string(), nullable=False),
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("source_revision", pa.string(), nullable=False),
        pa.field("source_artifact_sha256", pa.string(), nullable=False),
        pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("knowledge_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    market = [
        pa.field("observation_start", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("observation_end", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    reporting = [
        pa.field("period_start", pa.date32(), nullable=True),
        pa.field("period_end", pa.date32(), nullable=False),
        pa.field("published_at", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    event = [
        pa.field("effective_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("announced_at", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    d3810 = pa.decimal128(38, 10)
    d1812 = pa.decimal128(18, 12)
    return {
        "dwd_share_capital_observation": pa.schema(
            lineage
            + market
            + [
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("capital_type", pa.string(), nullable=False),
                pa.field("share_quantity", d3810, nullable=False),
                pa.field("unit_code", pa.string(), nullable=False),
                pa.field("raw_value", pa.string(), nullable=False),
                pa.field("raw_unit", pa.string(), nullable=False),
                pa.field("parser_version", pa.string(), nullable=False),
            ]
        ),
        "dwd_shareholder_count_observation": pa.schema(
            lineage
            + reporting
            + [
                pa.field("shareholder_count_observation_id", pa.string(), nullable=False),
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("security_id", pa.string(), nullable=True),
                pa.field("observation_scope", pa.string(), nullable=False),
                pa.field("holder_count", pa.int64(), nullable=False),
                pa.field("average_holding", d3810, nullable=True),
                pa.field("raw_holder_count", pa.string(), nullable=False),
                pa.field("raw_average_holding", pa.string(), nullable=True),
                pa.field("raw_unit", pa.string(), nullable=False),
                pa.field("parser_version", pa.string(), nullable=False),
            ]
        ),
        "dwd_shareholding_change": pa.schema(
            lineage
            + event
            + [
                pa.field("shareholding_change_id", pa.string(), nullable=False),
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("security_id", pa.string(), nullable=True),
                pa.field("shareholder_source_name", pa.string(), nullable=False),
                pa.field("shareholder_id", pa.string(), nullable=True),
                pa.field("change_type", pa.string(), nullable=False),
                pa.field("share_change", d3810, nullable=True),
                pa.field("percentage_change", d1812, nullable=True),
                pa.field("post_holding", d3810, nullable=True),
                pa.field("post_percentage", d1812, nullable=True),
                pa.field("disclosure_id", pa.string(), nullable=True),
                pa.field("holder_match_key", pa.string(), nullable=False),
                pa.field("raw_share_change", pa.string(), nullable=True),
                pa.field("share_change_unit", pa.string(), nullable=True),
                pa.field("raw_percentage_change", pa.string(), nullable=True),
                pa.field("percentage_change_unit", pa.string(), nullable=True),
                pa.field("raw_post_holding", pa.string(), nullable=True),
                pa.field("post_holding_unit", pa.string(), nullable=True),
                pa.field("raw_post_percentage", pa.string(), nullable=True),
                pa.field("post_percentage_unit", pa.string(), nullable=True),
                pa.field("parser_version", pa.string(), nullable=False),
            ]
        ),
        "dwd_shareholding_observation": pa.schema(
            lineage
            + market
            + [
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("shareholder_source_name", pa.string(), nullable=False),
                pa.field("shareholder_id", pa.string(), nullable=True),
                pa.field("holding_quantity", d3810, nullable=True),
                pa.field("holding_percentage", d1812, nullable=True),
                pa.field("holding_type", pa.string(), nullable=False),
                pa.field("holder_match_key", pa.string(), nullable=False),
                pa.field("table_kind", pa.string(), nullable=False),
                pa.field("holder_rank", pa.int32(), nullable=False),
                pa.field("holder_category", pa.string(), nullable=True),
                pa.field("share_nature", pa.string(), nullable=True),
                pa.field("raw_holding_quantity", pa.string(), nullable=True),
                pa.field("holding_quantity_unit", pa.string(), nullable=True),
                pa.field("raw_holding_percentage", pa.string(), nullable=True),
                pa.field("holding_percentage_unit", pa.string(), nullable=True),
                pa.field("parser_version", pa.string(), nullable=False),
            ]
        ),
    }


def _table_cells(line: str) -> list[str]:
    if "｜" not in line:
        return []
    return [cell.strip() for cell in line.strip().strip("｜").split("｜")]


def _normalized_number(raw: str, *, percentage: bool = False) -> Decimal | None:
    text = raw.strip().replace(",", "")
    if not text or text == "-":
        return None
    matched = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if matched is None:
        return None
    try:
        value = Decimal(matched.group(0))
    except InvalidOperation as exc:
        raise OwnershipCapitalBuildError(f"F10 number is invalid: {raw}") from exc
    if not percentage:
        if "亿" in text:
            value *= Decimal("100000000")
        elif "万" in text:
            value *= Decimal("10000")
    return value


def parse_f10_capital_structure(content: str) -> list[dict[str, Any]]:
    """Expand the governed F10 capital matrix into dated atomic observations."""

    in_section = False
    dates: list[date | None] = []
    rows: list[dict[str, Any]] = []
    for line in content.splitlines():
        if "【1.股本结构】" in line:
            in_section = True
            continue
        if in_section and re.match(r"【[2-9]", line.strip()):
            break
        if not in_section:
            continue
        cells = _table_cells(line)
        if not cells:
            continue
        if cells[0].startswith("单位"):
            dates = []
            for value in cells[1:]:
                if value in {"", "-"}:
                    dates.append(None)
                    continue
                try:
                    dates.append(date.fromisoformat(value))
                except ValueError as exc:
                    raise OwnershipCapitalBuildError(
                        f"F10 capital header date is invalid: {value}"
                    ) from exc
            continue
        if not dates or len(cells) != len(dates) + 1:
            continue
        capital_type = re.sub(r"\s+", "", cells[0])
        if not capital_type:
            continue
        for observed, raw_value in zip(dates, cells[1:], strict=True):
            if observed is None:
                continue
            normalized = _normalized_number(raw_value)
            if normalized is None:
                continue
            unit = "亿股" if "亿" in raw_value else "万股" if "万" in raw_value else "股"
            rows.append(
                {
                    "observation_date": observed,
                    "capital_type": capital_type,
                    "share_quantity": normalized,
                    "unit_code": "SHARE",
                    "raw_value": raw_value,
                    "raw_unit": unit,
                    "parser_version": _F10_PARSER_VERSION,
                }
            )
    return rows


def parse_f10_shareholding_tables(content: str) -> list[dict[str, Any]]:
    """Parse dated top-holder tables, including holder-name continuation rows."""

    header = re.compile(r"截至日期：(\d{4}-\d{2}-\d{2})\s+十大(流通)?股东情况")
    current_date: date | None = None
    table_kind: str | None = None
    in_table = False
    pending: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending
        for rank, row in enumerate(pending, start=1):
            row["holder_rank"] = rank
            rows.append(row)
        pending = []

    for line in content.splitlines():
        matched = header.search(line)
        if matched:
            flush()
            current_date = date.fromisoformat(matched.group(1))
            table_kind = "TOP_FLOATING_SHAREHOLDERS" if matched.group(2) else "TOP_SHAREHOLDERS"
            in_table = False
            continue
        cells = _table_cells(line)
        if current_date is None or table_kind is None:
            continue
        if cells and cells[0] == "股东名称":
            in_table = True
            continue
        if in_table and line.strip().startswith("└"):
            flush()
            in_table = False
            current_date = None
            table_kind = None
            continue
        if not in_table or not cells:
            continue
        if len(cells) == 1 and cells[0] and pending:
            pending[-1]["shareholder_source_name"] += cells[0]
            pending[-1]["holder_match_key"] = _stable_holder_key(
                pending[-1]["shareholder_source_name"]
            )
            continue
        if len(cells) < 4:
            continue
        holder_name, raw_quantity, raw_percentage, classification = cells[:4]
        if holder_name and not raw_quantity and pending:
            pending[-1]["shareholder_source_name"] += holder_name
            pending[-1]["holder_match_key"] = _stable_holder_key(
                pending[-1]["shareholder_source_name"]
            )
            continue
        quantity = _normalized_number(raw_quantity)
        percentage = _normalized_number(raw_percentage, percentage=True)
        if not holder_name or quantity is None:
            continue
        pending.append(
            {
                "observation_date": current_date,
                "table_kind": table_kind,
                "shareholder_source_name": holder_name,
                "holder_match_key": _stable_holder_key(holder_name),
                "holder_category": (
                    classification or None if table_kind == "TOP_FLOATING_SHAREHOLDERS" else None
                ),
                "share_nature": (
                    classification or None if table_kind == "TOP_SHAREHOLDERS" else None
                ),
                "holding_quantity": quantity,
                "holding_percentage": percentage,
                "holding_type": table_kind,
                "raw_holding_quantity": raw_quantity,
                "holding_quantity_unit": (
                    "亿股" if "亿" in raw_quantity else "万股" if "万" in raw_quantity else "股"
                ),
                "raw_holding_percentage": raw_percentage,
                "holding_percentage_unit": "PERCENT" if percentage is not None else None,
                "parser_version": _F10_PARSER_VERSION,
            }
        )
    flush()
    return rows


def _stable_holder_key(name: str) -> str:
    normalized = re.sub(r"\s+", "", name).casefold()
    return "holder_match_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _stable_id(kind: str, *parts: str) -> str:
    payload = "\x1f".join(("securities-warehouse-v1", kind, *parts)).encode("utf-8")
    return f"{kind}_{hashlib.sha256(payload).hexdigest()[:32]}"


def _utc_timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parsed = datetime.combine(
                date.fromisoformat(value), time(23, 59, 59, 999999), _SHANGHAI
            )
            return parsed.astimezone(timezone.utc)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OwnershipCapitalBuildError(f"{field} is not a timestamp") from exc
    else:
        raise OwnershipCapitalBuildError(f"{field} is empty")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=_SHANGHAI)
    return parsed.astimezone(timezone.utc)


def _raw_number(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        return format(value, ".17g")
    return str(value)


def _bounded_decimal(raw: str | None, scale: int) -> Decimal | None:
    if raw is None:
        return None
    return Decimal(raw).quantize(Decimal(1).scaleb(-scale))


def materialize_shareholder_count_row(
    row: Mapping[str, Any],
    *,
    company_id: str,
    security_id: str,
    source_sha256: str,
    parent_knowledge_from: datetime | None = None,
) -> dict[str, Any]:
    """Convert one canonical holder-count row with raw numeric evidence."""

    stock_code = _text(row.get("code"), "shareholder_count code")
    period_end = _report_date(row.get("end_date"), "shareholder_count end_date")
    raw_count = _raw_number(row.get("shareholder_count"))
    if raw_count is None:
        raise OwnershipCapitalBuildError("shareholder_count is empty")
    holder_count = Decimal(raw_count)
    if holder_count != holder_count.to_integral_value():
        raise OwnershipCapitalBuildError("shareholder_count is not integral")
    raw_average = _raw_number(row.get("avg_hold_num"))
    published_at = _utc_timestamp(
        row.get("knowledge_cutoff") or row.get("hold_notice_date"),
        "shareholder_count knowledge_cutoff",
    )
    knowledge_from = max(
        value for value in (published_at, parent_knowledge_from) if value is not None
    )
    source_record_id = f"stock_code={stock_code}&period_end={period_end.isoformat()}"
    return {
        "record_source": str(row.get("source") or "shareholder_count"),
        "source_record_id": source_record_id,
        "source_revision": str(row.get("fetched_at") or "ods-base-v1.2"),
        "source_artifact_sha256": source_sha256,
        "knowledge_from": knowledge_from,
        "knowledge_to": None,
        "period_start": None,
        "period_end": period_end,
        "published_at": published_at,
        "shareholder_count_observation_id": _stable_id(
            "shareholder_count", company_id, stock_code, period_end.isoformat()
        ),
        "company_id": company_id,
        "security_id": security_id,
        "observation_scope": "COMPANY_SECURITY_HOLDER_ACCOUNTS",
        "holder_count": int(holder_count),
        "average_holding": _bounded_decimal(raw_average, 10),
        "raw_holder_count": raw_count,
        "raw_average_holding": raw_average,
        "raw_unit": "HOLDER_ACCOUNT;SHARE_PER_HOLDER",
        "parser_version": "quanthus-structured-shareholder-count-v1",
    }


def materialize_holder_change_row(
    row: Mapping[str, Any],
    *,
    company_id: str,
    security_id: str,
    source_sha256: str,
    source_revision: str,
    parent_knowledge_from: datetime | None = None,
) -> dict[str, Any]:
    """Convert one SQLite holder-change event with explicit unit semantics."""

    row_key = _text(row.get("row_key"), "holder_change row_key")
    holder = _text(row.get("person") or row.get("actor"), "holder_change shareholder")
    changed = _report_date(
        row.get("change_date") or row.get("disclosure_date"), "holder_change date"
    )
    disclosed = (
        _report_date(row["disclosure_date"], "holder_change disclosure_date")
        if row.get("disclosure_date")
        else None
    )
    raw_change = _raw_number(row.get("change_shares"))
    raw_ratio = _raw_number(row.get("change_ratio"))
    raw_after = _raw_number(row.get("shares_after"))
    source_knowledge = _effective_start(disclosed or changed)
    knowledge = max(
        value for value in (source_knowledge, parent_knowledge_from) if value is not None
    )
    return {
        "record_source": "announcement_archive.holder_changes",
        "source_record_id": row_key,
        "source_revision": source_revision,
        "source_artifact_sha256": source_sha256,
        "knowledge_from": knowledge,
        "knowledge_to": None,
        "effective_at": _effective_start(changed),
        "announced_at": _effective_start(disclosed) if disclosed else None,
        "shareholding_change_id": _stable_id("shareholding_change", row_key),
        "company_id": company_id,
        "security_id": security_id,
        "shareholder_source_name": holder,
        "shareholder_id": None,
        "change_type": str(row.get("reason") or "UNSPECIFIED_CHANGE"),
        "share_change": _bounded_decimal(raw_change, 10),
        "percentage_change": _bounded_decimal(raw_ratio, 12),
        "post_holding": _bounded_decimal(raw_after, 10),
        "post_percentage": None,
        "disclosure_id": None,
        "holder_match_key": _stable_holder_key(holder),
        "raw_share_change": raw_change,
        "share_change_unit": "SHARE" if raw_change is not None else None,
        "raw_percentage_change": raw_ratio,
        "percentage_change_unit": "PERCENT" if raw_ratio is not None else None,
        "raw_post_holding": raw_after,
        "post_holding_unit": "SHARE" if raw_after is not None else None,
        "raw_post_percentage": None,
        "post_percentage_unit": None,
        "parser_version": "quanthus-sqlite-holder-change-v1",
    }


def materialize_f10_rows(
    row: Mapping[str, Any],
    *,
    company_id: str,
    security_id: str,
    source_sha256: str,
    parent_knowledge_from: datetime | None = None,
    identity_by_date: Mapping[date, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Attach governed lineage and identities to parsed F10 ownership rows."""

    category = _text(row.get("category"), "F10 category")
    content = _text(row.get("content"), "F10 content")
    stock_code = _text(row.get("stock_code"), "F10 stock_code")
    source_record_id = (
        f"stock_code={stock_code}&category={category}&content_sha256="
        f"{_text(row.get('content_sha256'), 'F10 content_sha256')}"
    )
    source_knowledge = _utc_timestamp(row.get("acquired_at"), "F10 acquired_at")
    common_knowledge_from = max(
        value
        for value in (
            source_knowledge,
            None if identity_by_date is not None else parent_knowledge_from,
        )
        if value is not None
    )
    common = {
        "record_source": str(row.get("source") or "tdx_f10"),
        "source_record_id": source_record_id,
        "source_revision": str(row.get("source_revision") or "provider-native"),
        "source_artifact_sha256": source_sha256,
        "knowledge_from": common_knowledge_from,
        "knowledge_to": None,
    }
    result = {
        "dwd_share_capital_observation": [],
        "dwd_shareholding_observation": [],
    }
    if category == "股本结构":
        for parsed in parse_f10_capital_structure(content):
            identity = (
                identity_by_date.get(parsed["observation_date"])
                if identity_by_date is not None
                else None
            )
            if identity_by_date is not None and identity is None:
                continue
            result["dwd_share_capital_observation"].append(
                {
                    **common,
                    "knowledge_from": max(
                        common["knowledge_from"],
                        identity.knowledge_from
                        if identity is not None
                        else common["knowledge_from"],
                    ),
                    "observation_start": _effective_start(parsed["observation_date"]),
                    "observation_end": None,
                    "security_id": identity.security_id if identity is not None else security_id,
                    **{key: value for key, value in parsed.items() if key != "observation_date"},
                }
            )
    elif category == "股东研究":
        for parsed in parse_f10_shareholding_tables(content):
            identity = (
                identity_by_date.get(parsed["observation_date"])
                if identity_by_date is not None
                else None
            )
            if identity_by_date is not None and identity is None:
                continue
            result["dwd_shareholding_observation"].append(
                {
                    **common,
                    "knowledge_from": max(
                        common["knowledge_from"],
                        identity.knowledge_from
                        if identity is not None
                        else common["knowledge_from"],
                    ),
                    "observation_start": _effective_start(parsed["observation_date"]),
                    "observation_end": None,
                    "company_id": identity.company_id if identity is not None else company_id,
                    "security_id": identity.security_id if identity is not None else security_id,
                    "shareholder_id": None,
                    **{key: value for key, value in parsed.items() if key != "observation_date"},
                }
            )
    else:
        raise OwnershipCapitalBuildError(f"unsupported ownership F10 category: {category}")
    return result


def _exclude_out_of_range_holdings(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    accepted: list[dict[str, Any]] = []
    excluded = 0
    for row in rows:
        percentage = row.get("holding_percentage")
        if percentage is not None and percentage > 100:
            excluded += 1
        else:
            accepted.append(row)
    return accepted, excluded


def _single_record(
    records: Sequence[Mapping[str, Any]],
    *,
    identity: str,
    record_type: str,
    disposition: str,
    role: str | None = None,
) -> Mapping[str, Any]:
    matches = [
        record
        for record in records
        if record.get("mapping_identity") == identity
        and record.get("record_type") == record_type
        and record.get("disposition") == disposition
        and (role is None or record.get("role") == role)
    ]
    if len(matches) != 1:
        raise OwnershipCapitalBuildError(
            f"expected one governed {identity} source, found {len(matches)}"
        )
    return matches[0]


def _file_source(
    records: Sequence[Mapping[str, Any]],
    ods_package: Path,
    *,
    source_id: str,
    identity: str,
    role: str,
) -> _Source:
    record = _single_record(
        records,
        identity=identity,
        record_type="file",
        disposition="modeled",
        role=role,
    )
    relative = _text(record.get("path"), f"{source_id} path")
    path = _source_path(ods_package, relative)
    sha256 = _text(record.get("sha256"), f"{source_id} sha256")
    rows = record.get("row_count")
    if (
        not isinstance(rows, int)
        or isinstance(rows, bool)
        or rows < 0
        or record.get("size_bytes") != path.stat().st_size
        or _sha256(path) != sha256
    ):
        raise OwnershipCapitalBuildError(f"{source_id} differs from sealed input plan")
    return _Source(source_id, path, relative, sha256, rows)


def _select_sources(records: Sequence[Mapping[str, Any]], ods_package: Path) -> dict[str, _Source]:
    sources = {
        "shareholder_count": _file_source(
            records,
            ods_package,
            source_id="shareholder_count",
            identity="shareholder_count",
            role="source_evidence",
        ),
        "parent_f10": _file_source(
            records,
            ods_package,
            source_id="parent_f10",
            identity="raw_f10",
            role="retained_immutable_source_gap_fill",
        ),
        "hotfix_f10": _file_source(
            records,
            ods_package,
            source_id="hotfix_f10",
            identity="fundamentals_raw_f10_parquet",
            role="provider_source_evidence",
        ),
    }
    disclosure_sources = _select_disclosure_sources(records, ods_package)
    member = _single_record(
        records,
        identity="announcement_archive|sqlite_table|holder_changes",
        record_type="container_member",
        disposition="modeled",
    )
    rows = member.get("row_count")
    if not isinstance(rows, int) or isinstance(rows, bool) or rows < 0:
        raise OwnershipCapitalBuildError("holder_changes row count is invalid")
    archive = disclosure_sources["announcement_archive"]
    sources["holder_changes"] = _Source(
        "holder_changes",
        archive.path,
        archive.relative_path,
        archive.sha256,
        rows,
        "holder_changes",
    )
    return sources


def _identity_quality(resolver: Any, code: object, coordinate: object) -> str | None:
    stock_code = _text(code, "ownership stock code")
    identity, reason = resolver.resolve_for_report(
        stock_code, period_end=_report_date(coordinate, "ownership coordinate")
    )
    return str(reason) if identity is None or reason is not None else None


def _audit_shareholder_count(
    source: _Source, resolver: Any, *, through_date: date
) -> dict[str, Any]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise OwnershipCapitalBuildError("pyarrow is required for ownership audit") from exc
    required = {
        "code",
        "end_date",
        "shareholder_count",
        "avg_hold_num",
        "hold_notice_date",
        "source",
        "knowledge_cutoff",
        "fetched_at",
    }
    parquet = pq.ParquetFile(source.path)
    missing = sorted(required.difference(parquet.schema_arrow.names))
    if missing or parquet.metadata.num_rows != source.row_count:
        raise OwnershipCapitalBuildError(
            f"shareholder_count schema/count differs: missing={missing}"
        )
    keys: set[tuple[str, str]] = set()
    quality: Counter[str] = Counter()
    null_counts: Counter[str] = Counter()
    for batch in parquet.iter_batches(batch_size=8192):
        for row in batch.to_pylist():
            code = _text(row.get("code"), "shareholder_count code")
            end_date = _report_date(row.get("end_date"), "shareholder_count end_date")
            key = (code, end_date.isoformat())
            if key in keys:
                raise OwnershipCapitalBuildError("shareholder_count has duplicate keys")
            keys.add(key)
            if end_date > through_date:
                quality["AFTER_THROUGH_DATE"] += 1
                continue
            if row.get("shareholder_count") is None:
                null_counts["shareholder_count"] += 1
            reason = _identity_quality(resolver, code, end_date)
            if reason is not None:
                quality[reason] += 1
    return {
        "input_rows": source.row_count,
        "unique_business_keys": len(keys),
        "null_values": dict(sorted(null_counts.items())),
        "identity_quality_exceptions": dict(sorted(quality.items())),
    }


def _audit_holder_changes(source: _Source, resolver: Any, *, through_date: date) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{source.path}?mode=ro", uri=True)
    required = {
        "exchange",
        "code",
        "person",
        "position",
        "shares_before",
        "change_shares",
        "shares_after",
        "change_ratio",
        "reason",
        "change_date",
        "disclosure_date",
        "row_key",
    }
    quality: Counter[str] = Counter()
    rows = 0
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(holder_changes)")}
        if not required.issubset(columns):
            raise OwnershipCapitalBuildError("holder_changes schema is incomplete")
        duplicate = connection.execute(
            "SELECT row_key FROM holder_changes GROUP BY row_key HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate is not None:
            raise OwnershipCapitalBuildError("holder_changes has duplicate row_key")
        cursor = connection.execute(
            "SELECT code,change_date,disclosure_date,row_key FROM holder_changes"
        )
        while True:
            batch = cursor.fetchmany(8192)
            if not batch:
                break
            for code, changed, disclosed, row_key in batch:
                rows += 1
                _text(row_key, "holder_changes row_key")
                coordinate = _report_date(changed or disclosed, "holder change date")
                if coordinate > through_date:
                    quality["AFTER_THROUGH_DATE"] += 1
                    continue
                reason = _identity_quality(resolver, code, coordinate)
                if reason is not None:
                    quality[reason] += 1
    except sqlite3.Error as exc:
        raise OwnershipCapitalBuildError("cannot audit holder_changes") from exc
    finally:
        connection.close()
    if rows != source.row_count:
        raise OwnershipCapitalBuildError("holder_changes count differs from input plan")
    return {
        "input_rows": rows,
        "unique_business_keys": rows,
        "identity_quality_exceptions": dict(sorted(quality.items())),
    }


def _audit_f10_sources(parent: _Source, hotfix: _Source, resolver: Any) -> dict[str, Any]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise OwnershipCapitalBuildError("pyarrow is required for F10 audit") from exc
    required = {
        "stock_code",
        "category",
        "content",
        "content_sha256",
        "provider_updated_date",
        "market_date",
        "source",
        "source_revision",
        "acquired_at",
    }
    selected: dict[tuple[str, str], tuple[str, str]] = {}
    source_reports: dict[str, Any] = {}
    quality: Counter[str] = Counter()
    for source, precedence in ((parent, "parent"), (hotfix, "hotfix_fill_only")):
        parquet = pq.ParquetFile(source.path)
        missing = sorted(required.difference(parquet.schema_arrow.names))
        if missing or parquet.metadata.num_rows != source.row_count:
            raise OwnershipCapitalBuildError(
                f"{source.source_id} schema/count differs: missing={missing}"
            )
        categories: Counter[str] = Counter()
        selected_rows = 0
        overlap_rows = 0
        for batch in parquet.iter_batches(batch_size=1024):
            for row in batch.to_pylist():
                category = _text(row.get("category"), "F10 category")
                categories[category] += 1
                if category not in _F10_CATEGORIES:
                    continue
                code = _text(row.get("stock_code"), "F10 stock_code")
                key = (code, category)
                digest = _text(row.get("content_sha256"), "F10 content_sha256")
                prior = selected.get(key)
                if prior is not None:
                    if precedence == "parent" or prior[1] != digest:
                        raise OwnershipCapitalBuildError(
                            f"F10 parent/Hotfix overlap differs: {key}"
                        )
                    overlap_rows += 1
                    continue
                selected[key] = (source.source_id, digest)
                selected_rows += 1
                reason = _identity_quality(
                    resolver,
                    code,
                    row.get("market_date") or row.get("provider_updated_date"),
                )
                if reason is not None:
                    quality[reason] += 1
        source_reports[source.source_id] = {
            "input_rows": source.row_count,
            "selected_ownership_rows": selected_rows,
            "overlap_rows": overlap_rows,
            "precedence": precedence,
            "categories": dict(sorted(categories.items())),
        }
    return {
        "sources": source_reports,
        "selected_business_keys": len(selected),
        "selected_categories": sorted(_F10_CATEGORIES),
        "identity_quality_exceptions": dict(sorted(quality.items())),
        "overlay_policy": "parent_wins_hotfix_fills_absent_stock_code_category_keys",
    }


def audit_ownership_capital_source_package(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    model_extension_path: Path,
    listed_company_candidate: Path,
    output: Path,
    run_id: str,
) -> OwnershipCapitalSourceAudit:
    """Verify all authoritative #63 sources before candidate payload is written."""

    try:
        plan, ledger = _verify_plan_package(input_plan)
        semantic_plan = _mapping(plan.get("plan"), "input plan")
        model = _object(model_path)
        _verify_ods_binding(ods_package, semantic_plan, model)
        extension, extension_sha = verify_additive_model_extension(model_path, model_extension_path)
    except (TradingCalendarBuildError, ModelExtensionError) as exc:
        raise OwnershipCapitalBuildError("ownership input verification failed") from exc
    if plan.get("run_id") != run_id or semantic_plan.get("mode") != "base":
        raise OwnershipCapitalBuildError("ownership audit must match one Base run")
    model_sha = _sha256(model_path)
    plan_digest = _text(plan.get("plan_digest"), "input plan digest")
    try:
        listed = verify_listed_company_candidate(
            listed_company_candidate,
            run_id=run_id,
            input_plan_digest=plan_digest,
            model_sha256=model_sha,
        )
    except ListedCompanyBuildError as exc:
        raise OwnershipCapitalBuildError("ownership predecessor verification failed") from exc
    records = [
        _mapping(raw, "source ledger record")
        for raw in _sequence(ledger.get("records"), "source ledger records")
    ]
    sources = _select_sources(records, ods_package)
    resolver = _load_company_resolver(listed_company_candidate)
    through_date = _report_date(semantic_plan.get("through_date"), "ownership through_date")
    report = {
        "schema_version": 1,
        "kind": "dwd_ownership_capital_source_audit",
        "run_id": run_id,
        "input_plan_digest": plan_digest,
        "model_sha256": model_sha,
        "model_extension_sha256": extension_sha,
        "model_extension_contract": extension["contract_id"],
        "listed_company_semantic_digest": listed.semantic_digest,
        "source_sha256": {key: value.sha256 for key, value in sorted(sources.items())},
        "source_rows": {
            "shareholder_count": _audit_shareholder_count(
                sources["shareholder_count"], resolver, through_date=through_date
            ),
            "holder_changes": _audit_holder_changes(
                sources["holder_changes"], resolver, through_date=through_date
            ),
            "f10": _audit_f10_sources(sources["parent_f10"], sources["hotfix_f10"], resolver),
        },
        "build_gate": {
            "requires_same_run_issue60_candidate": True,
            "reason": "optional disclosure foreign keys and declared DAG edge 60_to_63",
        },
    }
    _write_json_immutable(output, report)
    return OwnershipCapitalSourceAudit(report)


_OWNED_TABLES = (
    "dwd_share_capital_observation",
    "dwd_shareholder_count_observation",
    "dwd_shareholding_change",
    "dwd_shareholding_observation",
)


def _partition_for_row(table: str, row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    if table == "dwd_shareholder_count_observation":
        return (("period_end_year", f"{row['period_end'].year:04d}"),)
    if table == "dwd_shareholding_change":
        effective_at = row["effective_at"]
        return (
            ("effective_at_year", f"{effective_at.year:04d}"),
            ("effective_at_month", f"{effective_at.month:02d}"),
        )
    return (("observation_start_year", f"{row['observation_start'].year:04d}"),)


def _write_ownership_rows(
    work: Path,
    table: str,
    rows: Sequence[Mapping[str, Any]],
    schema: Any,
    batch_id: str,
) -> list[str]:
    try:
        import pyarrow as pa
    except ModuleNotFoundError as exc:
        raise OwnershipCapitalBuildError("pyarrow is required for ownership writes") from exc
    grouped: dict[tuple[tuple[str, str], ...], list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_partition_for_row(table, row), []).append(row)
    files: list[str] = []
    for partition, values in sorted(grouped.items()):
        directory = work / "payload/ownership_capital" / table
        for key, value in partition:
            directory /= f"{key}={value}"
        path = directory / f"part-{batch_id}.parquet"
        _write_parquet_atomic(path, pa.Table.from_pylist(values, schema=schema))
        files.append(path.relative_to(work).as_posix())
    return files


def _verify_ownership_candidate(
    output: Path, identity: Mapping[str, Any]
) -> OwnershipCapitalBuildResult:
    if output.is_symlink() or not output.is_dir():
        raise OwnershipCapitalBuildError("ownership candidate must be physical")
    manifest = _object(output / "manifest.json")
    if (output / "READY").read_text(encoding="ascii").strip() != _sha256(output / "manifest.json"):
        raise OwnershipCapitalBuildError("ownership READY differs")
    if any(manifest.get(field) != value for field, value in identity.items()):
        raise OwnershipCapitalBuildError("ownership candidate identity differs")
    if manifest.get("owned_tables") != list(_OWNED_TABLES):
        raise OwnershipCapitalBuildError("ownership candidate table scope differs")
    declared = {str(item["path"]): item for item in manifest.get("files", [])}
    for relative, item in declared.items():
        path = output / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item.get("size_bytes")
            or _sha256(path) != item.get("sha256")
        ):
            raise OwnershipCapitalBuildError(f"ownership file differs: {relative}")
    physical = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if physical != set(declared) | {"manifest.json", "READY"}:
        raise OwnershipCapitalBuildError("ownership inventory differs")
    return OwnershipCapitalBuildResult(
        output.resolve(),
        str(manifest["semantic_digest"]),
        {str(key): int(value) for key, value in manifest["row_counts"].items()},
    )


def _build_ownership_capital_locked(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    model_extension_path: Path,
    listed_company_candidate: Path,
    disclosure_event_candidate: Path,
    source_audit_path: Path,
    output: Path,
    run_id: str,
    batch_size: int,
) -> OwnershipCapitalBuildResult:
    try:
        plan, ledger = _verify_plan_package(input_plan)
        semantic_plan = _mapping(plan.get("plan"), "input plan")
        model = _object(model_path)
        _verify_ods_binding(ods_package, semantic_plan, model)
        _, extension_sha = verify_additive_model_extension(model_path, model_extension_path)
    except (TradingCalendarBuildError, ModelExtensionError) as exc:
        raise OwnershipCapitalBuildError("ownership build input verification failed") from exc
    plan_digest = _text(plan.get("plan_digest"), "input plan digest")
    model_sha = _sha256(model_path)
    if plan.get("run_id") != run_id:
        raise OwnershipCapitalBuildError("ownership run differs from plan")
    through_date = _report_date(semantic_plan.get("through_date"), "ownership through_date")
    listed = verify_listed_company_candidate(
        listed_company_candidate,
        run_id=run_id,
        input_plan_digest=plan_digest,
        model_sha256=model_sha,
    )
    try:
        disclosure = verify_disclosure_event_candidate(
            disclosure_event_candidate,
            run_id=run_id,
            input_plan_digest=plan_digest,
            model_sha256=model_sha,
        )
    except DisclosureEventBuildError as exc:
        raise OwnershipCapitalBuildError("ownership #60 predecessor differs") from exc
    audit = _object(source_audit_path)
    audit_sha = _sha256(source_audit_path)
    expected_audit = {
        "run_id": run_id,
        "input_plan_digest": plan_digest,
        "model_sha256": model_sha,
        "model_extension_sha256": extension_sha,
        "listed_company_semantic_digest": listed.semantic_digest,
    }
    if audit.get("kind") != "dwd_ownership_capital_source_audit" or any(
        audit.get(field) != value for field, value in expected_audit.items()
    ):
        raise OwnershipCapitalBuildError("ownership source audit differs")
    records = [
        _mapping(raw, "source ledger record")
        for raw in _sequence(ledger.get("records"), "source ledger records")
    ]
    sources = _select_sources(records, ods_package)
    identity = {
        "run_id": run_id,
        "input_plan_digest": plan_digest,
        "model_sha256": model_sha,
        "model_extension_sha256": extension_sha,
        "source_audit_sha256": audit_sha,
        "listed_company_semantic_digest": listed.semantic_digest,
        "disclosure_event_semantic_digest": disclosure.semantic_digest,
    }
    if output.exists():
        return _verify_ownership_candidate(output, identity)
    work = output.parent / f".{output.name}.work"
    work.mkdir(parents=True, exist_ok=True)
    checkpoint_path = work / "checkpoint.json"
    checkpoint = (
        _object(checkpoint_path)
        if checkpoint_path.exists()
        else {"schema_version": 1, "identity": identity, "batches": {}}
    )
    if checkpoint.get("identity") != identity:
        raise OwnershipCapitalBuildError("ownership checkpoint differs")
    schemas = ownership_capital_schemas()
    resolver = _load_company_resolver(listed_company_candidate)
    quality: Counter[str] = Counter()

    def commit_batch(
        batch_id: str, rows: Mapping[str, Sequence[Mapping[str, Any]]], notes: Counter[str]
    ) -> None:
        if batch_id in checkpoint["batches"]:
            return
        files: list[str] = []
        counts: dict[str, int] = {}
        for table, values in rows.items():
            counts[table] = len(values)
            files.extend(_write_ownership_rows(work, table, values, schemas[table], batch_id))
        checkpoint["batches"][batch_id] = {
            "row_counts": counts,
            "quality_exceptions": dict(sorted(notes.items())),
            "files": files,
        }
        _write_json_atomic(checkpoint_path, checkpoint)

    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise OwnershipCapitalBuildError("pyarrow is required for ownership build") from exc
    for index, batch in enumerate(
        pq.ParquetFile(sources["shareholder_count"].path).iter_batches(batch_size=batch_size)
    ):
        batch_id = f"count-{index:06d}"
        if batch_id in checkpoint["batches"]:
            continue
        values: list[dict[str, Any]] = []
        notes: Counter[str] = Counter()
        for raw in batch.to_pylist():
            coordinate = _report_date(raw.get("end_date"), "holder count date")
            if coordinate > through_date:
                notes["AFTER_THROUGH_DATE"] += 1
                continue
            company, reason = resolver.resolve_for_report(
                str(raw.get("code")), period_end=coordinate
            )
            if company is None:
                notes[str(reason)] += 1
                continue
            values.append(
                materialize_shareholder_count_row(
                    raw,
                    company_id=company.company_id,
                    security_id=company.security_id,
                    source_sha256=sources["shareholder_count"].sha256,
                    parent_knowledge_from=company.knowledge_from,
                )
            )
            if reason is not None:
                notes[str(reason)] += 1
        commit_batch(batch_id, {"dwd_shareholder_count_observation": values}, notes)
    connection = sqlite3.connect(f"file:{sources['holder_changes'].path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute("SELECT * FROM holder_changes ORDER BY rowid")
        index = 0
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                break
            batch_id = f"change-{index:06d}"
            if batch_id not in checkpoint["batches"]:
                values = []
                notes = Counter()
                for item in batch:
                    raw = dict(item)
                    coordinate = _report_date(
                        raw.get("change_date") or raw.get("disclosure_date"),
                        "holder change date",
                    )
                    if coordinate > through_date:
                        notes["AFTER_THROUGH_DATE"] += 1
                        continue
                    company, reason = resolver.resolve_for_report(
                        str(raw.get("code")), period_end=coordinate
                    )
                    if company is None:
                        notes[str(reason)] += 1
                        continue
                    values.append(
                        materialize_holder_change_row(
                            raw,
                            company_id=company.company_id,
                            security_id=company.security_id,
                            source_sha256=sources["holder_changes"].sha256,
                            source_revision=run_id,
                            parent_knowledge_from=company.knowledge_from,
                        )
                    )
                    if reason is not None:
                        notes[str(reason)] += 1
                commit_batch(batch_id, {"dwd_shareholding_change": values}, notes)
            index += 1
    finally:
        connection.close()
    selected: set[tuple[str, str]] = set()
    for source_index, source_id in enumerate(("parent_f10", "hotfix_f10")):
        source = sources[source_id]
        for index, batch in enumerate(pq.ParquetFile(source.path).iter_batches(batch_size=256)):
            batch_id = f"f10-{source_index}-{index:06d}"
            rows = {
                "dwd_share_capital_observation": [],
                "dwd_shareholding_observation": [],
            }
            notes = Counter()
            for raw in batch.to_pylist():
                category = str(raw.get("category"))
                if category not in _F10_CATEGORIES:
                    continue
                key = (str(raw.get("stock_code")), category)
                if key in selected:
                    continue
                selected.add(key)
                if batch_id in checkpoint["batches"]:
                    continue
                content = _text(raw.get("content"), "F10 content")
                parsed_rows = (
                    parse_f10_capital_structure(content)
                    if category == "股本结构"
                    else parse_f10_shareholding_tables(content)
                )
                identity_by_date: dict[date, Any] = {}
                for coordinate in sorted({parsed["observation_date"] for parsed in parsed_rows}):
                    if coordinate > through_date:
                        notes["AFTER_THROUGH_DATE"] += 1
                        continue
                    company, reason = resolver.resolve_for_report(key[0], period_end=coordinate)
                    if company is None:
                        notes[str(reason)] += 1
                        continue
                    identity_by_date[coordinate] = company
                fallback = next(iter(identity_by_date.values()), None)
                expanded = materialize_f10_rows(
                    raw,
                    company_id=fallback.company_id if fallback is not None else "UNRESOLVED",
                    security_id=fallback.security_id if fallback is not None else "UNRESOLVED",
                    source_sha256=source.sha256,
                    parent_knowledge_from=(
                        fallback.knowledge_from if fallback is not None else None
                    ),
                    identity_by_date=identity_by_date,
                )
                accepted_holdings, excluded_holdings = _exclude_out_of_range_holdings(
                    expanded["dwd_shareholding_observation"]
                )
                notes["OUT_OF_RANGE_HOLDING_PERCENTAGE"] += excluded_holdings
                expanded["dwd_shareholding_observation"] = accepted_holdings
                for table, values in expanded.items():
                    rows[table].extend(values)
                if not any(expanded.values()):
                    notes[f"EMPTY_F10_{category}"] += 1
            if batch_id not in checkpoint["batches"]:
                commit_batch(batch_id, rows, notes)
    totals = {table: 0 for table in _OWNED_TABLES}
    for batch in checkpoint["batches"].values():
        for table, count in batch["row_counts"].items():
            totals[table] += int(count)
        quality.update(batch.get("quality_exceptions", {}))
    reconciliation = {
        "schema_version": 1,
        "kind": "dwd_ownership_capital_reconciliation",
        "row_counts": totals,
        "quality_exceptions": dict(sorted(quality.items())),
    }
    _write_json_atomic(work / "reconciliation/source-row-reconciliation.json", reconciliation)
    shutil.copyfile(source_audit_path, work / "source-audit.json")
    files = sorted(
        path
        for path in work.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "READY"}
    )
    semantic_digest = _digest({**identity, "files": [_file_record(work, path) for path in files]})
    manifest = {
        "schema_version": 1,
        "kind": "dwd_ownership_capital_candidate",
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
    _verify_ownership_candidate(work, identity)
    os.replace(work, output)
    _fsync_directory(output.parent)
    return _verify_ownership_candidate(output, identity)


def build_ownership_capital(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    model_extension_path: Path,
    listed_company_candidate: Path,
    disclosure_event_candidate: Path,
    source_audit_path: Path,
    output: Path,
    run_id: str,
    batch_size: int = 8192,
) -> OwnershipCapitalBuildResult:
    """Build a checkpointed Issue #63 candidate without advancing CURRENT."""

    if batch_size < 1 or output.is_symlink():
        raise OwnershipCapitalBuildError("invalid ownership build arguments")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / f".{output.name}.lock"
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o640,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OwnershipCapitalBuildError("ownership lock is not physical")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _build_ownership_capital_locked(
            ods_package=ods_package,
            input_plan=input_plan,
            model_path=model_path,
            model_extension_path=model_extension_path,
            listed_company_candidate=listed_company_candidate,
            disclosure_event_candidate=disclosure_event_candidate,
            source_audit_path=source_audit_path,
            output=output,
            run_id=run_id,
            batch_size=batch_size,
        )
    finally:
        os.close(descriptor)
