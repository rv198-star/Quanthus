"""Audit and build the governed ownership and capital domain for Issue #63."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.pipelines.securities_warehouse.disclosure_event import (
    _load_company_resolver,
    _report_date,
    _sha256,
    _source_path,
    _text,
    _write_json_immutable,
)
from quanthus.pipelines.securities_warehouse.disclosure_event import (
    _select_sources as _select_disclosure_sources,
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
class _Source:
    source_id: str
    path: Path
    relative_path: str
    sha256: str
    row_count: int
    member: str | None = None


_F10_CATEGORIES = {"股本结构", "股东研究"}
_F10_PARSER_VERSION = "quanthus-tdx-f10-table-v1"


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


def _audit_shareholder_count(source: _Source, resolver: Any) -> dict[str, Any]:
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


def _audit_holder_changes(source: _Source, resolver: Any) -> dict[str, Any]:
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
                reason = _identity_quality(resolver, code, changed or disclosed)
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
            "shareholder_count": _audit_shareholder_count(sources["shareholder_count"], resolver),
            "holder_changes": _audit_holder_changes(sources["holder_changes"], resolver),
            "f10": _audit_f10_sources(sources["parent_f10"], sources["hotfix_f10"], resolver),
        },
        "build_gate": {
            "requires_same_run_issue60_candidate": True,
            "reason": "optional disclosure foreign keys and declared DAG edge 60_to_63",
        },
    }
    _write_json_immutable(output, report)
    return OwnershipCapitalSourceAudit(report)
