"""Decode provider-native financial-statement envelopes for Issue #61."""

from __future__ import annotations

import ast
import fcntl
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse.listed_company import (
    ListedCompanyBuildError,
    verify_listed_company_candidate,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    TradingCalendarBuildError,
    _verify_ods_binding,
    _verify_plan_package,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    _mapping as _plan_mapping,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    _object as _plan_object,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    _sequence as _plan_sequence,
)


class FinancialReportingBuildError(RuntimeError):
    """Raised when a provider financial-statement envelope is not lossless."""


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_PRECISION = re.compile(r"^f(-?\d+)$")
_TAXONOMY_NAMESPACE = "sina.company_finance"
_UNSPECIFIED_SCOPE = "SINA_PROVIDER_SCOPE_UNSPECIFIED"
_UNSPECIFIED_STANDARD = "SINA_PROVIDER_STANDARD_UNSPECIFIED"
_UNSPECIFIED_UNIT = "SINA_PROVIDER_UNIT_UNSPECIFIED"


@dataclass(frozen=True)
class DecodedFinancialEnvelope:
    """Verified source payload plus its provider report periods."""

    raw_payload: bytes
    report_list: Mapping[str, Any]


@dataclass(frozen=True)
class StatementRowAccounting:
    """Explicit disposition counts for one provider statement envelope."""

    heading_rows: int
    null_placeholders: int
    valid_facts: int
    invalid_rows: int


@dataclass(frozen=True)
class FinancialStatementSource:
    """One checksum-bound statement-envelope source selected from the input plan."""

    label: str
    path: Path
    relative_path: str
    source_sha256: str
    source_revision: str
    precedence: Literal["parent", "hotfix"]


@dataclass(frozen=True)
class FinancialReportingSource:
    """One non-empty modeled financial input bound to its sealed ODS artifact."""

    source_id: str
    source_kind: Literal["statement_envelope", "quarterly_projection", "financial_abstract"]
    ledger_identity: str
    path: Path
    relative_path: str
    source_sha256: str
    source_revision: str
    precedence: Literal["parent", "hotfix", "independent"]
    declared_row_count: int = 0
    member: str | None = None


@dataclass(frozen=True)
class FinancialReportingSourceInventory:
    """Complete non-empty adapter inputs plus explicitly empty modeled evidence."""

    sources: tuple[FinancialReportingSource, ...]
    empty_modeled_sources: tuple[str, ...]


@dataclass(frozen=True)
class FinancialReportingSourceAudit:
    """Deterministic source accounting used to calibrate the #61 candidate build."""

    report: dict[str, Any]


@dataclass(frozen=True)
class FinancialCompanyIdentity:
    """A #57 company identity valid for one stock-code interval."""

    stock_code: str
    security_id: str
    company_id: str
    effective_from: datetime
    effective_to: datetime | None


@dataclass(frozen=True)
class FinancialEnvelopeRows:
    """Typed DWD rows expanded from one verified provider statement envelope."""

    reports: tuple[dict[str, Any], ...]
    concepts: tuple[dict[str, Any], ...]
    facts: tuple[dict[str, Any], ...]
    quality_exceptions: tuple[dict[str, Any], ...] = ()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise FinancialReportingBuildError(f"{field} must be a non-empty string")
    return value


def _unpack_byte_literal(value: object) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise FinancialReportingBuildError("raw_payload_gzip must be bytes")
    try:
        decoded = ast.literal_eval(bytes(value).decode("ascii"))
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        raise FinancialReportingBuildError(
            "raw_payload_gzip is not an ASCII bytes literal"
        ) from exc
    if not isinstance(decoded, bytes):
        raise FinancialReportingBuildError("raw_payload_gzip literal must decode to bytes")
    return decoded


def decode_financial_envelope(row: Mapping[str, Any]) -> DecodedFinancialEnvelope:
    """Losslessly unpack, decompress, hash-check, and parse one ODS envelope."""

    packed = _unpack_byte_literal(row.get("raw_payload_gzip"))
    try:
        raw = gzip.decompress(packed)
    except OSError as exc:
        raise FinancialReportingBuildError("raw_payload_gzip is not a valid gzip stream") from exc
    declared_bytes = row.get("raw_payload_bytes")
    if not isinstance(declared_bytes, int) or isinstance(declared_bytes, bool):
        raise FinancialReportingBuildError("raw_payload_bytes must be an integer")
    if len(raw) != declared_bytes:
        raise FinancialReportingBuildError("raw payload length differs from its envelope")
    declared_sha256 = _text(row.get("raw_payload_sha256"), "raw_payload_sha256")
    actual_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual_sha256 != declared_sha256:
        raise FinancialReportingBuildError("raw payload SHA-256 differs from its envelope")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FinancialReportingBuildError("raw financial payload is not JSON") from exc
    if not isinstance(payload, Mapping):
        raise FinancialReportingBuildError("raw financial payload root must be an object")
    result = payload.get("result")
    data = result.get("data") if isinstance(result, Mapping) else None
    report_list = data.get("report_list") if isinstance(data, Mapping) else None
    if not isinstance(report_list, Mapping):
        raise FinancialReportingBuildError("financial payload has no object report_list")
    return DecodedFinancialEnvelope(raw_payload=raw, report_list=report_list)


def conservative_provider_timestamp(value: object) -> datetime | None:
    """Convert a provider date or local timestamp to a UTC availability timestamp.

    A date-only value represents no more than a calendar-day disclosure, so its
    availability is conservatively set to the end of that Shanghai calendar day.
    """

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=_SHANGHAI)
        return parsed.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(
            value,
            time(23, 59, 59, 999999),
            tzinfo=_SHANGHAI,
        ).astimezone(timezone.utc)
    if not isinstance(value, str):
        raise FinancialReportingBuildError("provider timestamp must be a date or string")
    text = value.strip()
    if not text:
        return None
    try:
        if len(text) == 8 and text.isdigit():
            parsed = datetime.strptime(text, "%Y%m%d").replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=_SHANGHAI
            )
        elif len(text) == 10:
            parsed = datetime.strptime(text, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=_SHANGHAI
            )
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=_SHANGHAI)
    except ValueError as exc:
        raise FinancialReportingBuildError("provider timestamp is invalid") from exc
    return parsed.astimezone(timezone.utc)


def classify_statement_rows(report: Mapping[str, Any]) -> StatementRowAccounting:
    """Classify every provider item without silently dropping malformed rows."""

    items = report.get("data")
    if not isinstance(items, list):
        raise FinancialReportingBuildError("provider report data must be an array")
    headings = 0
    nulls = 0
    facts = 0
    invalid = 0
    for item in items:
        if not isinstance(item, Mapping):
            invalid += 1
            continue
        field = item.get("item_field")
        display_type = item.get("item_display_type")
        if display_type == 1:
            headings += 1
            continue
        if not isinstance(field, str) or not field.strip():
            invalid += 1
            continue
        value = item.get("item_value")
        if value is None or (isinstance(value, str) and not value.strip()):
            nulls += 1
        else:
            facts += 1
    return StatementRowAccounting(headings, nulls, facts, invalid)


def iter_statement_reports(
    envelope: DecodedFinancialEnvelope,
) -> Iterator[tuple[str, Mapping[str, Any], StatementRowAccounting]]:
    """Yield provider reports in deterministic reporting-period order."""

    for period, value in sorted(envelope.report_list.items(), key=lambda item: str(item[0])):
        if not isinstance(period, str) or not period:
            raise FinancialReportingBuildError("financial report period must be a non-empty string")
        if not isinstance(value, Mapping):
            raise FinancialReportingBuildError(f"financial report {period} must be an object")
        yield period, value, classify_statement_rows(value)


_ACCOUNTING_FIELDS = (
    "envelope_rows",
    "report_periods",
    "heading_rows",
    "null_placeholders",
    "valid_facts",
    "invalid_rows",
    "materializable_fact_rows",
    "uncompressed_bytes",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise FinancialReportingBuildError(f"cannot hash source file: {path}") from exc
    return "sha256:" + digest.hexdigest()


def _source_text(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise FinancialReportingBuildError(f"financial source {field} must be a non-empty string")
    return value


def _source_path(ods_package: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or relative.as_posix() == "." or ".." in relative.parts:
        raise FinancialReportingBuildError(f"unsafe financial source path: {relative_path}")
    root = ods_package.resolve()
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise FinancialReportingBuildError(
            f"financial source is not a physical file: {relative_path}"
        )
    return path


def select_financial_statement_sources(
    ledger_records: Sequence[Mapping[str, Any]], ods_package: Path
) -> tuple[FinancialStatementSource, FinancialStatementSource]:
    """Resolve the exact parent-first statement sources from a sealed Base ledger.

    The July 2026 Base retains the complete weekly corpus under the generic
    ``financial_statements`` identity and a 209-security Hotfix under the
    ``fundamentals_financial_statements_parquet`` identity.  The latter is a
    fill-only overlay on the business key ``(stock_code, statement_type)``.
    """

    candidates = [
        record
        for record in ledger_records
        if record.get("record_type") == "file"
        and record.get("disposition") == "modeled"
        and PurePosixPath(str(record.get("path", ""))).name == "financial_statements.parquet"
    ]
    parent = [
        record
        for record in candidates
        if record.get("mapping_identity") == "financial_statements"
        and record.get("role") == "retained_immutable_source_gap_fill"
    ]
    hotfix = [
        record
        for record in candidates
        if record.get("mapping_identity") == "fundamentals_financial_statements_parquet"
    ]
    if len(parent) != 1 or len(hotfix) != 1 or len(candidates) != 2:
        raise FinancialReportingBuildError(
            "Base input plan must contain exactly one full financial-statements source "
            "and one fill-only financial-statements Hotfix"
        )

    def build(record: Mapping[str, Any], *, precedence: Literal["parent", "hotfix"]):
        relative_path = _source_text(record, "path")
        path = _source_path(ods_package, relative_path)
        declared_sha256 = _source_text(record, "sha256")
        if _sha256(path) != declared_sha256:
            raise FinancialReportingBuildError(
                f"financial source checksum differs from input plan: {relative_path}"
            )
        return FinancialStatementSource(
            label=_source_text(record, "mapping_identity"),
            path=path,
            relative_path=relative_path,
            source_sha256=declared_sha256,
            source_revision=str(record.get("source_revision", "")),
            precedence=precedence,
        )

    return build(parent[0], precedence="parent"), build(hotfix[0], precedence="hotfix")


def select_financial_reporting_sources(
    ledger_records: Sequence[Mapping[str, Any]], ods_package: Path
) -> FinancialReportingSourceInventory:
    """Bind every non-empty #61 source without collapsing repeated ledger identities."""

    specifications = (
        (
            "weekly_full_financial_statements",
            "statement_envelope",
            "financial_statements",
            "file",
            "retained_immutable_source_gap_fill",
            "parent",
        ),
        (
            "hotfix_financial_statements",
            "statement_envelope",
            "fundamentals_financial_statements_parquet",
            "file",
            "provider_source_evidence",
            "hotfix",
        ),
        (
            "canonical_financial_quarterly",
            "quarterly_projection",
            "financial_quarterly",
            "file",
            "source_evidence",
            "independent",
        ),
        (
            "weekly_full_financial_quarterly",
            "quarterly_projection",
            "financial_quarterly",
            "file",
            "retained_immutable_source_gap_fill",
            "parent",
        ),
        (
            "hotfix_financial_quarterly",
            "quarterly_projection",
            "fundamentals_financial_quarterly_parquet",
            "file",
            "provider_source_evidence",
            "hotfix",
        ),
        (
            "announcement_archive_fin_abstract",
            "financial_abstract",
            "announcement_archive|sqlite_table|fin_abstract",
            "container_member",
            None,
            "independent",
        ),
    )
    selected_records: set[int] = set()
    sources: list[FinancialReportingSource] = []
    for source_id, source_kind, identity, record_type, role, precedence in specifications:
        matches = [
            record
            for record in ledger_records
            if record.get("record_type") == record_type
            and record.get("disposition") == "modeled"
            and record.get("mapping_identity") == identity
            and (role is None or record.get("role") == role)
        ]
        if len(matches) != 1:
            raise FinancialReportingBuildError(
                f"financial source contract expected one {source_id}, found {len(matches)}"
            )
        record = matches[0]
        selected_records.add(id(record))
        row_count = record.get("row_count")
        if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 1:
            raise FinancialReportingBuildError(
                f"financial adapted source must be non-empty: {source_id}"
            )
        member: str | None = None
        artifact_record = record
        relative_path = _source_text(record, "path")
        if record_type == "container_member":
            member = _source_text(record, "member")
            container_path = _source_text(record, "container_path")
            containers = [
                value
                for value in ledger_records
                if value.get("record_type") == "file"
                and value.get("disposition") == "modeled"
                and value.get("mapping_identity") == record.get("container_source_id")
                and value.get("path") == container_path
            ]
            if len(containers) != 1:
                raise FinancialReportingBuildError(
                    "financial abstract container authority is missing or ambiguous"
                )
            artifact_record = containers[0]
            relative_path = container_path
        path = _source_path(ods_package, relative_path)
        declared_size = artifact_record.get("size_bytes")
        if (
            not isinstance(declared_size, int)
            or isinstance(declared_size, bool)
            or path.stat().st_size != declared_size
        ):
            raise FinancialReportingBuildError(
                f"financial source size differs from input plan: {relative_path}"
            )
        declared_sha256 = _source_text(artifact_record, "sha256")
        if _sha256(path) != declared_sha256:
            raise FinancialReportingBuildError(
                f"financial source checksum differs from input plan: {relative_path}"
            )
        sources.append(
            FinancialReportingSource(
                source_id=source_id,
                source_kind=source_kind,
                ledger_identity=identity,
                path=path,
                relative_path=relative_path,
                source_sha256=declared_sha256,
                source_revision=str(record.get("source_revision") or "ods-base-202607-v1.2"),
                precedence=precedence,
                declared_row_count=row_count,
                member=member,
            )
        )

    empty_modeled_sources: list[str] = []
    financial_targets = {
        "dwd_financial_report",
        "dwd_financial_concept",
        "dwd_financial_fact",
    }
    for record in ledger_records:
        targets = record.get("target_tables")
        if (
            id(record) in selected_records
            or record.get("disposition") != "modeled"
            or not isinstance(targets, list)
            or not financial_targets.intersection(targets)
        ):
            continue
        row_count = record.get("row_count")
        if row_count != 0:
            raise FinancialReportingBuildError(
                "unclassified non-empty financial source: "
                f"{record.get('mapping_identity')} path={record.get('path')}"
            )
        relative_path = _source_text(record, "path")
        if record.get("record_type") == "file":
            path = _source_path(ods_package, relative_path)
            declared_size = record.get("size_bytes")
            if (
                not isinstance(declared_size, int)
                or isinstance(declared_size, bool)
                or path.stat().st_size != declared_size
                or _sha256(path) != _source_text(record, "sha256")
            ):
                raise FinancialReportingBuildError(
                    f"empty financial source differs from input plan: {relative_path}"
                )
        empty_modeled_sources.append(relative_path)

    return FinancialReportingSourceInventory(
        sources=tuple(sources),
        empty_modeled_sources=tuple(sorted(empty_modeled_sources)),
    )


def _empty_accounting() -> Counter[str]:
    return Counter({field: 0 for field in _ACCOUNTING_FIELDS})


def _row_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    stock_code = row.get("stock_code")
    statement_type = row.get("statement_type")
    security_id = row.get("security_id")
    if not isinstance(stock_code, str) or not stock_code:
        raise FinancialReportingBuildError("financial source row has no stock_code")
    if not isinstance(statement_type, str) or not statement_type:
        raise FinancialReportingBuildError("financial source row has no statement_type")
    if not isinstance(security_id, str) or not security_id:
        raise FinancialReportingBuildError("financial source row has no security_id")
    return stock_code, statement_type


def _analyze_envelope(
    row: Mapping[str, Any],
) -> tuple[str, Counter[str], set[tuple[str, str]]]:
    statement_type = _row_identity(row)[1]
    envelope = decode_financial_envelope(row)
    accounting = _empty_accounting()
    concept_coordinates: set[tuple[str, str]] = set()
    accounting["envelope_rows"] += 1
    accounting["uncompressed_bytes"] += len(envelope.raw_payload)
    for _period, report, report_accounting in iter_statement_reports(envelope):
        accounting["report_periods"] += 1
        accounting["heading_rows"] += report_accounting.heading_rows
        accounting["null_placeholders"] += report_accounting.null_placeholders
        accounting["valid_facts"] += report_accounting.valid_facts
        accounting["invalid_rows"] += report_accounting.invalid_rows
        accounting["materializable_fact_rows"] += (
            report_accounting.null_placeholders + report_accounting.valid_facts
        )
        items = report.get("data")
        if not isinstance(items, list):
            raise FinancialReportingBuildError("provider report data must be an array")
        for item in items:
            if not isinstance(item, Mapping):
                continue
            field = item.get("item_field")
            if (
                item.get("item_display_type") == 1
                or not isinstance(field, str)
                or not field.strip()
            ):
                continue
            concept_coordinates.add((statement_type, field.strip()))
    digest = row.get("raw_payload_sha256")
    if not isinstance(digest, str) or not digest:
        raise FinancialReportingBuildError("financial source row has no raw_payload_sha256")
    return digest, accounting, concept_coordinates


def _accounting_dict(value: Counter[str]) -> dict[str, int]:
    return {field: int(value[field]) for field in _ACCOUNTING_FIELDS}


def audit_financial_statement_sources(
    sources: Sequence[FinancialStatementSource], *, batch_size: int = 32
) -> FinancialReportingSourceAudit:
    """Stream the full statement corpus and prove its overlay disposition.

    This intentionally avoids DuckDB and full-table materialization.  Each
    Parquet batch is decoded, hash-checked, accounted, then released before the
    next batch.  The resulting fact upper bound is the maximum number of source
    items the #61 candidate must materialize before explicit quality exceptions.
    """

    if batch_size < 1:
        raise FinancialReportingBuildError("batch_size must be positive")
    if [source.precedence for source in sources] != ["parent", "hotfix"]:
        raise FinancialReportingBuildError("financial statement sources must be parent then Hotfix")
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise FinancialReportingBuildError(
            "pyarrow is required for financial source audit"
        ) from exc

    source_accounting: dict[str, Counter[str]] = {
        source.label: _empty_accounting() for source in sources
    }
    selected_accounting: dict[str, Counter[str]] = {
        source.label: _empty_accounting() for source in sources
    }
    source_key_counts: Counter[str] = Counter()
    selected_key_counts: Counter[str] = Counter()
    parent_payloads: dict[tuple[str, str], str] = {}
    hotfix_keys: set[tuple[str, str]] = set()
    source_concepts: dict[str, set[tuple[str, str]]] = {source.label: set() for source in sources}
    selected_concepts: set[tuple[str, str]] = set()
    overlap_rows = 0
    hotfix_fill_rows = 0

    for source in sources:
        try:
            parquet = pq.ParquetFile(source.path)
        except (OSError, ValueError) as exc:
            raise FinancialReportingBuildError(
                f"cannot open financial statement source: {source.relative_path}"
            ) from exc
        required = {
            "security_id",
            "stock_code",
            "statement_type",
            "raw_payload_gzip",
            "raw_payload_sha256",
            "raw_payload_bytes",
        }
        present = set(parquet.schema_arrow.names)
        missing = sorted(required.difference(present))
        if missing:
            raise FinancialReportingBuildError(
                f"financial statement source lacks required columns: {missing}"
            )
        try:
            batches = parquet.iter_batches(batch_size=batch_size)
            for batch in batches:
                for raw_row in batch.to_pylist():
                    row = dict(raw_row)
                    key = _row_identity(row)
                    digest, row_accounting, row_concepts = _analyze_envelope(row)
                    source_accounting[source.label].update(row_accounting)
                    source_concepts[source.label].update(row_concepts)
                    source_key_counts[source.label] += 1
                    if source.precedence == "parent":
                        if key in parent_payloads:
                            raise FinancialReportingBuildError(
                                f"duplicate parent financial statement key: {key}"
                            )
                        parent_payloads[key] = digest
                        selected_accounting[source.label].update(row_accounting)
                        selected_concepts.update(row_concepts)
                        selected_key_counts[source.label] += 1
                        continue
                    if key in hotfix_keys:
                        raise FinancialReportingBuildError(
                            f"duplicate Hotfix financial statement key: {key}"
                        )
                    hotfix_keys.add(key)
                    parent_digest = parent_payloads.get(key)
                    if parent_digest is not None:
                        overlap_rows += 1
                        if parent_digest != digest:
                            raise FinancialReportingBuildError(
                                "financial statement parent/Hotfix overlap has different "
                                "raw payload "
                                f"for key: {key}"
                            )
                        continue
                    hotfix_fill_rows += 1
                    selected_accounting[source.label].update(row_accounting)
                    selected_concepts.update(row_concepts)
                    selected_key_counts[source.label] += 1
        except FinancialReportingBuildError:
            raise
        except Exception as exc:  # noqa: BLE001 - malformed Parquet must fail the audit
            raise FinancialReportingBuildError(
                f"cannot stream financial statement source: {source.relative_path}"
            ) from exc

    all_source = _empty_accounting()
    selected = _empty_accounting()
    for source in sources:
        all_source.update(source_accounting[source.label])
        selected.update(selected_accounting[source.label])
    source_rows = {
        source.label: {
            "path": source.relative_path,
            "source_sha256": source.source_sha256,
            "source_revision": source.source_revision,
            "precedence": source.precedence,
            "input_accounting": _accounting_dict(source_accounting[source.label]),
            "selected_accounting": _accounting_dict(selected_accounting[source.label]),
            "input_business_key_count": int(source_key_counts[source.label]),
            "selected_business_key_count": int(selected_key_counts[source.label]),
            "unique_provider_concept_count": len(source_concepts[source.label]),
        }
        for source in sources
    }
    report = {
        "schema_version": 1,
        "kind": "dwd_financial_reporting_source_audit",
        "decoder_contract": "byte_literal_gzip_sha256_json_v1",
        "source_order": [source.label for source in sources],
        "business_key": ["stock_code", "statement_type"],
        "source_rows": source_rows,
        "overlay": {
            "policy": "parent_base_wins_on_overlapping_business_keys_hotfix_fills_absent_keys_only",
            "overlap_mismatch_policy": "fail_build",
            "parent_overlap_rows": overlap_rows,
            "hotfix_fill_rows": hotfix_fill_rows,
            "selected_envelope_rows": int(sum(selected_key_counts.values())),
        },
        "all_source_accounting": _accounting_dict(all_source),
        "selected_source_accounting": _accounting_dict(selected),
        "candidate_size_calibration": {
            "dwd_financial_report_rows": int(selected["report_periods"]),
            "dwd_financial_fact_upper_bound": int(selected["materializable_fact_rows"]),
            "provider_native_concept_coordinate_count": len(selected_concepts),
            "selected_uncompressed_bytes": int(selected["uncompressed_bytes"]),
        },
    }
    return FinancialReportingSourceAudit(report=report)


def write_financial_reporting_source_audit(
    output: Path, audit: FinancialReportingSourceAudit
) -> Path:
    """Write an immutable, deterministic source-audit evidence document."""

    if output.is_symlink():
        raise FinancialReportingBuildError(
            "financial source audit output must not be a symbolic link"
        )
    payload = (json.dumps(audit.report, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "ascii"
    )
    if output.exists():
        if not output.is_file() or output.read_bytes() != payload:
            raise FinancialReportingBuildError(
                "financial source audit output already exists with different content"
            )
        return output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise FinancialReportingBuildError(
            "financial source audit parent must be a physical directory"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".partial", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, output)
        except FileExistsError as exc:
            if not output.is_file() or output.read_bytes() != payload:
                raise FinancialReportingBuildError(
                    "financial source audit output was concurrently written with different content"
                ) from exc
        directory_descriptor = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return output.resolve()


def combine_financial_reporting_source_audits(
    *, statement_audit_path: Path, native_audit_path: Path, output: Path
) -> FinancialReportingSourceAudit:
    """Seal statement and native-source evidence into the only buildable audit."""

    statement = _read_json_object(statement_audit_path, "financial statement source audit")
    native = _read_json_object(native_audit_path, "financial native source audit")
    statement_calibration = statement.get("candidate_size_calibration")
    native_calibration = native.get("candidate_size_calibration")
    if (
        statement.get("schema_version") != 1
        or statement.get("kind") != "dwd_financial_reporting_source_audit"
        or not isinstance(statement_calibration, Mapping)
        or native.get("schema_version") != 1
        or native.get("kind") != "dwd_financial_reporting_native_source_audit"
        or not isinstance(native_calibration, Mapping)
    ):
        raise FinancialReportingBuildError("financial source audits cannot be combined")
    statement_reports = statement_calibration.get("dwd_financial_report_rows")
    statement_facts = statement_calibration.get("dwd_financial_fact_upper_bound")
    native_reports = native_calibration.get("dwd_financial_report_rows")
    native_facts = native_calibration.get("dwd_financial_fact_rows")
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (statement_reports, statement_facts, native_reports, native_facts)
    ):
        raise FinancialReportingBuildError("financial source audit counts are invalid")
    required_bindings = (
        "run_id",
        "input_plan_digest",
        "model_sha256",
        "listed_company_manifest_sha256",
        "listed_company_semantic_digest",
        "source_sha256",
    )
    if any(native.get(field) in (None, "", {}) for field in required_bindings):
        raise FinancialReportingBuildError("native financial audit lacks build bindings")
    report = {
        "schema_version": 1,
        "kind": "dwd_financial_reporting_combined_source_audit",
        **{field: native[field] for field in required_bindings},
        "component_audits": {
            "statement": {
                "path": str(statement_audit_path.resolve()),
                "sha256": _sha256(statement_audit_path),
                "audit": statement,
            },
            "native": {
                "path": str(native_audit_path.resolve()),
                "sha256": _sha256(native_audit_path),
                "audit": native,
            },
        },
        "candidate_size_calibration": {
            "dwd_financial_report_rows": statement_reports + native_reports,
            "dwd_financial_fact_rows": statement_facts + native_facts,
            "statement_report_rows": statement_reports,
            "statement_fact_rows": statement_facts,
            "native_report_rows": native_reports,
            "native_fact_rows": native_facts,
        },
        "quality_exceptions": native.get("quality_exceptions", {}),
    }
    audit = FinancialReportingSourceAudit(report=report)
    write_financial_reporting_source_audit(output, audit)
    return audit


def financial_reporting_schemas() -> dict[str, Any]:
    """Return the physical schemas for the three Issue #61-owned DWD tables."""

    try:
        import pyarrow as pa
    except ModuleNotFoundError as exc:
        raise FinancialReportingBuildError("pyarrow is required for financial DWD rows") from exc
    lineage = [
        pa.field("record_source", pa.string(), nullable=False),
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("source_revision", pa.string(), nullable=False),
        pa.field("source_artifact_sha256", pa.string(), nullable=False),
        pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("knowledge_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    reporting_period = [
        pa.field("period_start", pa.date32(), nullable=True),
        pa.field("period_end", pa.date32(), nullable=False),
        pa.field("published_at", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    effective_interval = [
        pa.field("effective_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("effective_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    return {
        "dwd_financial_report": pa.schema(
            lineage
            + reporting_period
            + [
                pa.field("financial_report_id", pa.string(), nullable=False),
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("report_type", pa.string(), nullable=False),
                pa.field("statement_scope", pa.string(), nullable=False),
                pa.field("accounting_standard", pa.string(), nullable=False),
                pa.field("audit_status", pa.string(), nullable=True),
                pa.field("restatement_flag", pa.bool_(), nullable=False),
            ]
        ),
        "dwd_financial_concept": pa.schema(
            lineage
            + effective_interval
            + [
                pa.field("financial_concept_id", pa.string(), nullable=False),
                pa.field("taxonomy_namespace", pa.string(), nullable=False),
                pa.field("taxonomy_version", pa.string(), nullable=False),
                pa.field("concept_code", pa.string(), nullable=False),
                pa.field("concept_name", pa.string(), nullable=False),
                pa.field("statement_type", pa.string(), nullable=False),
                pa.field("data_type", pa.string(), nullable=False),
                pa.field("period_type", pa.string(), nullable=False),
                pa.field("balance_type", pa.string(), nullable=True),
            ]
        ),
        "dwd_financial_fact": pa.schema(
            lineage
            + reporting_period
            + [
                pa.field("financial_fact_id", pa.string(), nullable=False),
                pa.field("financial_report_id", pa.string(), nullable=False),
                pa.field("financial_concept_id", pa.string(), nullable=False),
                pa.field("unit_code", pa.string(), nullable=False),
                pa.field("value_decimal", pa.decimal128(38, 10), nullable=True),
                pa.field("value_text", pa.string(), nullable=True),
                pa.field("dimensions_json", pa.string(), nullable=False),
                pa.field("context_hash", pa.string(), nullable=False),
                pa.field("decimals", pa.int32(), nullable=True),
                pa.field("is_nil", pa.bool_(), nullable=False),
            ]
        ),
    }


def _stable_id(kind: str, *parts: str) -> str:
    encoded = "\x1f".join(("securities-warehouse-v1", kind, *parts)).encode("utf-8")
    return f"{kind}_{hashlib.sha256(encoded).hexdigest()[:32]}"


def _utc_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise FinancialReportingBuildError(f"{field} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FinancialReportingBuildError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FinancialReportingBuildError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _report_date(value: object, field: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise FinancialReportingBuildError(f"{field} must use YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise FinancialReportingBuildError(f"{field} must use a valid YYYYMMDD date") from exc


def _period_coordinate(period_end: date) -> datetime:
    return datetime.combine(period_end, time.min, tzinfo=_SHANGHAI).astimezone(timezone.utc)


def _provider_text(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _context_hash(dimensions_json: str) -> str:
    return "sha256:" + hashlib.sha256(dimensions_json.encode("utf-8")).hexdigest()


def _numeric_value(raw_value: str) -> Decimal | None:
    try:
        value = Decimal(raw_value)
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value.as_tuple().exponent < -10:
        return None
    if value.scaleb(10).copy_abs() >= Decimal(10) ** 38:
        return None
    return value


def _item_decimals(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    matched = _PRECISION.fullmatch(value.strip())
    if matched is None:
        return None
    parsed = int(matched.group(1))
    return parsed if -(2**31) <= parsed < 2**31 else None


class FinancialCompanyResolver:
    """Resolve a statement code through #57's effective stock-code identities."""

    def __init__(self, identities: Sequence[FinancialCompanyIdentity]) -> None:
        by_code: dict[str, list[FinancialCompanyIdentity]] = {}
        by_company: dict[str, list[FinancialCompanyIdentity]] = {}
        for identity in identities:
            if not identity.stock_code or not identity.company_id or not identity.security_id:
                raise FinancialReportingBuildError("financial company identity has an empty key")
            by_code.setdefault(identity.stock_code, []).append(identity)
            by_company.setdefault(identity.company_id, []).append(identity)
        self._by_code = {
            code: tuple(sorted(values, key=lambda item: (item.effective_from, item.security_id)))
            for code, values in by_code.items()
        }
        self._by_company = {
            company_id: tuple(
                sorted(values, key=lambda item: (item.effective_from, item.security_id))
            )
            for company_id, values in by_company.items()
        }

    def resolve_for_report(
        self, stock_code: str, *, period_end: date
    ) -> tuple[FinancialCompanyIdentity | None, str | None]:
        """Resolve at report period, bridging later code changes within one company."""

        coordinate = _period_coordinate(period_end)
        code_identities = self._by_code.get(stock_code, ())
        direct = [
            identity
            for identity in code_identities
            if identity.effective_from <= coordinate
            and (identity.effective_to is None or coordinate < identity.effective_to)
        ]
        direct_companies = {identity.company_id for identity in direct}
        if len(direct_companies) == 1:
            return sorted(direct, key=lambda item: item.security_id)[0], None
        if len(direct_companies) > 1:
            raise FinancialReportingBuildError(
                "financial report resolves to multiple companies at period end: "
                f"stock_code={stock_code} period_end={period_end.isoformat()}"
            )
        candidate_companies = {identity.company_id for identity in code_identities}
        if len(candidate_companies) != 1:
            raise FinancialReportingBuildError(
                "financial report has no unambiguous company bridge: "
                f"stock_code={stock_code} candidate_companies={len(candidate_companies)}"
            )
        company_id = next(iter(candidate_companies))
        company_identities = self._by_company[company_id]
        active_company = [
            identity
            for identity in company_identities
            if identity.effective_from <= coordinate
            and (identity.effective_to is None or coordinate < identity.effective_to)
        ]
        if active_company:
            return sorted(active_company, key=lambda item: item.security_id)[0], None
        if coordinate < min(identity.effective_from for identity in company_identities):
            resolved = sorted(
                company_identities,
                key=lambda item: (item.effective_from, item.security_id),
            )[0]
            return resolved, "BEFORE_FIRST_LISTED_COMPANY_IDENTITY"
        prior = [
            identity for identity in company_identities if identity.effective_from <= coordinate
        ]
        if not prior:
            raise FinancialReportingBuildError(
                "financial company identity interval classification is inconsistent"
            )
        resolved = sorted(
            prior, key=lambda item: (item.effective_from, item.security_id)
        )[-1]
        later = [
            identity for identity in company_identities if identity.effective_from > coordinate
        ]
        reason = (
            "BETWEEN_LISTED_COMPANY_IDENTITY_INTERVALS"
            if later
            else "AFTER_LAST_LISTED_COMPANY_IDENTITY"
        )
        return resolved, reason

    def resolve(
        self, stock_code: str, *, period_end: date, published_at: datetime | None
    ) -> FinancialCompanyIdentity:
        del published_at
        identity, reason = self.resolve_for_report(stock_code, period_end=period_end)
        if identity is None:
            raise FinancialReportingBuildError(
                "financial report is outside the listed-company identity scope: "
                f"stock_code={stock_code} period_end={period_end.isoformat()} reason={reason}"
            )
        return identity


def financial_company_resolver_from_listed_company_rows(
    security_rows: Sequence[Mapping[str, Any]], identifier_rows: Sequence[Mapping[str, Any]]
) -> FinancialCompanyResolver:
    """Build the #61 resolver from a deeply verified same-run #57 candidate."""

    securities: dict[str, Mapping[str, Any]] = {}
    for row in security_rows:
        security_id = row.get("security_id")
        company_id = row.get("company_id")
        if not isinstance(security_id, str) or not security_id:
            raise FinancialReportingBuildError("#57 security row has no security_id")
        if not isinstance(company_id, str) or not company_id:
            raise FinancialReportingBuildError("#57 security row has no company_id")
        prior = securities.get(security_id)
        if prior is not None and prior.get("company_id") != company_id:
            raise FinancialReportingBuildError(
                f"#57 security_id resolves to more than one company: {security_id}"
            )
        securities[security_id] = row
    identities: list[FinancialCompanyIdentity] = []
    for row in identifier_rows:
        namespace = row.get("identifier_namespace")
        stock_code = row.get("identifier_value")
        security_id = row.get("security_id")
        if not isinstance(namespace, str) or not namespace.startswith("CN_STOCK_CODE:"):
            continue
        if not isinstance(stock_code, str) or not stock_code:
            raise FinancialReportingBuildError("#57 stock-code identifier is empty")
        if not isinstance(security_id, str) or security_id not in securities:
            raise FinancialReportingBuildError("#57 stock-code identifier has no security")
        effective_from = row.get("effective_from")
        effective_to = row.get("effective_to")
        if not isinstance(effective_from, datetime):
            raise FinancialReportingBuildError("#57 stock-code identifier lacks effective_from")
        if effective_to is not None and not isinstance(effective_to, datetime):
            raise FinancialReportingBuildError("#57 stock-code identifier has invalid effective_to")
        identities.append(
            FinancialCompanyIdentity(
                stock_code=stock_code,
                security_id=security_id,
                company_id=str(securities[security_id]["company_id"]),
                effective_from=effective_from.astimezone(timezone.utc),
                effective_to=(
                    effective_to.astimezone(timezone.utc)
                    if isinstance(effective_to, datetime)
                    else None
                ),
            )
        )
    if not identities:
        raise FinancialReportingBuildError("#57 candidate has no CN stock-code identities")
    return FinancialCompanyResolver(identities)


def expand_financial_envelope(
    row: Mapping[str, Any],
    *,
    source: FinancialStatementSource,
    company_resolver: FinancialCompanyResolver,
) -> FinancialEnvelopeRows:
    """Expand one losslessly decoded provider envelope into atomic #61 DWD rows."""

    stock_code, statement_type = _row_identity(row)
    source_name = _text(row.get("source"), "source")
    source_revision = _text(row.get("source_revision"), "source_revision")
    knowledge_from = _utc_timestamp(row.get("acquired_at"), "acquired_at")
    envelope = decode_financial_envelope(row)
    reports: list[dict[str, Any]] = []
    concepts: dict[str, dict[str, Any]] = {}
    facts: list[dict[str, Any]] = []
    quality_exceptions: list[dict[str, Any]] = []
    seen_fact_keys: set[tuple[str, str, str, str, date]] = set()
    period_type = "instant" if statement_type == "balance_sheet" else "duration"

    for period, report, _accounting in iter_statement_reports(envelope):
        period_end = _report_date(period, "financial report period")
        published_at = conservative_provider_timestamp(report.get("publish_date"))
        company, resolution_reason = company_resolver.resolve_for_report(
            stock_code, period_end=period_end
        )
        if company is None:
            quality_exceptions.append(
                {
                    "code": resolution_reason,
                    "stock_code": stock_code,
                    "period_end": period_end.isoformat(),
                    "source_record_id": (
                        f"{source.relative_path}#stock_code={stock_code}"
                        f"&statement_type={statement_type}&period_end={period}"
                    ),
                    "source_artifact_sha256": source.source_sha256,
                }
            )
            continue
        if resolution_reason is not None:
            quality_exceptions.append(
                {
                    "code": resolution_reason,
                    "stock_code": stock_code,
                    "period_end": period_end.isoformat(),
                    "source_record_id": (
                        f"{source.relative_path}#stock_code={stock_code}"
                        f"&statement_type={statement_type}&period_end={period}"
                    ),
                    "source_artifact_sha256": source.source_sha256,
                }
            )
        statement_scope = _provider_text(report.get("rType"), _UNSPECIFIED_SCOPE)
        accounting_standard = _provider_text(report.get("data_source"), _UNSPECIFIED_STANDARD)
        audit_status = _provider_text(report.get("is_audit"), "") or None
        report_id = _stable_id(
            "financial_report",
            _TAXONOMY_NAMESPACE,
            company.company_id,
            stock_code,
            statement_type,
            period,
            statement_scope,
        )
        report_record_id = (
            f"{source.relative_path}#stock_code={stock_code}&statement_type={statement_type}"
            f"&period_end={period}"
        )
        reports.append(
            {
                "record_source": source_name,
                "source_record_id": report_record_id,
                "source_revision": source_revision,
                "source_artifact_sha256": source.source_sha256,
                "knowledge_from": knowledge_from,
                "knowledge_to": None,
                "period_start": None,
                "period_end": period_end,
                "published_at": published_at,
                "financial_report_id": report_id,
                "company_id": company.company_id,
                "report_type": statement_type,
                "statement_scope": statement_scope,
                "accounting_standard": accounting_standard,
                "audit_status": audit_status,
                "restatement_flag": False,
            }
        )
        items = report.get("data")
        if not isinstance(items, list):
            raise FinancialReportingBuildError("provider report data must be an array")
        for item_index, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise FinancialReportingBuildError("provider report item is not an object")
            concept_code = item.get("item_field")
            if item.get("item_display_type") == 1:
                continue
            if not isinstance(concept_code, str) or not concept_code.strip():
                raise FinancialReportingBuildError(
                    "non-heading provider report item has no item_field"
                )
            concept_code = concept_code.strip()
            concept_name = _provider_text(item.get("item_title"), concept_code)
            concept_id = _stable_id(
                "financial_concept",
                _TAXONOMY_NAMESPACE,
                source_revision,
                statement_type,
                concept_code,
                concept_name,
            )
            prior_concept = concepts.get(concept_id)
            concept_row = {
                "record_source": source_name,
                "source_record_id": (
                    f"{source.relative_path}#statement_type={statement_type}"
                    f"&concept={concept_code}"
                    f"&concept_name_sha256={hashlib.sha256(concept_name.encode('utf-8')).hexdigest()}"
                ),
                "source_revision": source_revision,
                "source_artifact_sha256": source.source_sha256,
                "knowledge_from": knowledge_from,
                "knowledge_to": None,
                "effective_from": knowledge_from,
                "effective_to": None,
                "financial_concept_id": concept_id,
                "taxonomy_namespace": _TAXONOMY_NAMESPACE,
                "taxonomy_version": source_revision,
                "concept_code": concept_code,
                "concept_name": concept_name,
                "statement_type": statement_type,
                "data_type": "provider_native_scalar",
                "period_type": period_type,
                "balance_type": None,
            }
            if prior_concept is not None and prior_concept["concept_name"] != concept_name:
                raise FinancialReportingBuildError(
                    "provider concept code has conflicting names within one envelope: "
                    f"statement_type={statement_type} concept={concept_code}"
                )
            concepts[concept_id] = concept_row
            raw_value = item.get("item_value")
            is_nil = raw_value is None or (isinstance(raw_value, str) and not raw_value.strip())
            raw_text = None if is_nil else str(raw_value)
            unit_code = _provider_text(report.get("rCurrency"), _UNSPECIFIED_UNIT)
            dimensions_json = _canonical_json(
                {
                    "data_source": report.get("data_source"),
                    "item_display": item.get("item_display"),
                    "item_display_type": item.get("item_display_type"),
                    "item_group_no": item.get("item_group_no"),
                    "provider_item_index": item_index,
                    "item_precision": item.get("item_precision"),
                    "item_source": item.get("item_source"),
                    "item_tongbi": item.get("item_tongbi"),
                    "provider_update_time": report.get("update_time"),
                    "statement_scope": statement_scope,
                }
            )
            context_hash = _context_hash(dimensions_json)
            fact_key = (report_id, concept_id, unit_code, context_hash, period_end)
            if fact_key in seen_fact_keys:
                raise FinancialReportingBuildError(
                    "provider report has duplicate financial fact coordinate: "
                    f"report={report_id} concept={concept_id} context={context_hash}"
                )
            seen_fact_keys.add(fact_key)
            facts.append(
                {
                    "record_source": source_name,
                    "source_record_id": f"{report_record_id}&item_index={item_index}",
                    "source_revision": source_revision,
                    "source_artifact_sha256": source.source_sha256,
                    "knowledge_from": knowledge_from,
                    "knowledge_to": None,
                    "period_start": None,
                    "period_end": period_end,
                    "published_at": published_at,
                    "financial_fact_id": _stable_id(
                        "financial_fact", report_id, concept_id, unit_code, context_hash
                    ),
                    "financial_report_id": report_id,
                    "financial_concept_id": concept_id,
                    "unit_code": unit_code,
                    "value_decimal": _numeric_value(raw_text) if raw_text is not None else None,
                    "value_text": raw_text,
                    "dimensions_json": dimensions_json,
                    "context_hash": context_hash,
                    "decimals": _item_decimals(item.get("item_precision")),
                    "is_nil": is_nil,
                }
            )
    return FinancialEnvelopeRows(
        tuple(reports),
        tuple(concepts.values()),
        tuple(facts),
        tuple(quality_exceptions),
    )


_CANONICAL_QUARTERLY_FIELDS = (
    "roe",
    "revenue",
    "net_profit",
    "revenue_growth_yoy",
    "profit_growth_yoy",
)
_SINA_QUARTERLY_FIELDS = ("revenue", "net_profit", "parent_net_profit")
_FINANCIAL_ABSTRACT_FIELDS = (
    "net_profit_attr",
    "revenue",
    "op_cost",
    "net_profit",
    "net_profit_deducted",
    "equity",
    "goodwill",
    "ocf",
    "eps_basic",
    "bps",
    "cfps",
    "roe",
    "roa",
    "gross_margin",
    "net_margin",
    "expense_ratio",
    "debt_ratio",
    "revenue_yoy",
    "np_attr_yoy",
    "ocf_to_np",
    "current_ratio",
    "quick_ratio",
    "equity_multiplier",
    "inventory_turnover",
    "ar_days",
    "asset_turnover",
    "retained_eps",
    "ocf_ps",
)
_MONETARY_FIELDS = {
    "revenue",
    "net_profit",
    "parent_net_profit",
    "net_profit_attr",
    "op_cost",
    "net_profit_deducted",
    "equity",
    "goodwill",
    "ocf",
}
_PERCENT_FIELDS = {
    "roe",
    "roa",
    "gross_margin",
    "net_margin",
    "expense_ratio",
    "debt_ratio",
    "revenue_growth_yoy",
    "profit_growth_yoy",
    "revenue_yoy",
    "np_attr_yoy",
}
_PER_SHARE_FIELDS = {"eps_basic", "bps", "cfps", "retained_eps", "ocf_ps"}
_RATIO_FIELDS = {
    "ocf_to_np",
    "current_ratio",
    "quick_ratio",
    "equity_multiplier",
    "inventory_turnover",
    "asset_turnover",
}


def _native_report_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip().replace("-", "")
        if re.fullmatch(r"\d{8}", normalized):
            return _report_date(normalized, "native financial report_date")
    raise FinancialReportingBuildError("native financial report_date is invalid")


def _native_unit(field: str, currency: str) -> str:
    if field in _MONETARY_FIELDS:
        return currency
    if field in _PERCENT_FIELDS:
        return "PERCENT"
    if field in _PER_SHARE_FIELDS:
        return f"{currency}_PER_SHARE"
    if field in _RATIO_FIELDS:
        return "RATIO"
    if field == "ar_days":
        return "DAY"
    return _UNSPECIFIED_UNIT


def _native_source_contract(
    source: FinancialReportingSource,
) -> tuple[str, str, tuple[str, ...], str, str]:
    if source.source_id == "canonical_financial_quarterly":
        return (
            "legacy.ods.financial_quarterly",
            "FINANCIAL_QUARTERLY_PROJECTION",
            _CANONICAL_QUARTERLY_FIELDS,
            "PROVIDER_NATIVE_QUARTERLY_PROJECTION",
            "duration",
        )
    if source.source_id in {
        "weekly_full_financial_quarterly",
        "hotfix_financial_quarterly",
    }:
        return (
            "sina.full_income_statement_projection",
            "FINANCIAL_QUARTERLY_PROJECTION",
            _SINA_QUARTERLY_FIELDS,
            "PROVIDER_NATIVE_QUARTERLY_PROJECTION",
            "duration",
        )
    if source.source_id == "announcement_archive_fin_abstract":
        return (
            "announcement_archive.fin_abstract",
            "FINANCIAL_ABSTRACT",
            _FINANCIAL_ABSTRACT_FIELDS,
            "PROVIDER_NATIVE_FINANCIAL_ABSTRACT",
            "provider_native_mixed",
        )
    raise FinancialReportingBuildError(
        f"financial native-row adapter is unsupported: {source.source_id}"
    )


def expand_financial_native_row(
    row: Mapping[str, Any],
    *,
    source: FinancialReportingSource,
    company_resolver: FinancialCompanyResolver,
    knowledge_cutoff: datetime,
) -> FinancialEnvelopeRows:
    """Expand one quarterly projection or abstract row without cross-source coalescing."""

    if source.source_kind not in {"quarterly_projection", "financial_abstract"}:
        raise FinancialReportingBuildError("native-row adapter received a statement envelope")
    if knowledge_cutoff.tzinfo is None or knowledge_cutoff.utcoffset() is None:
        raise FinancialReportingBuildError("financial knowledge cutoff must include a timezone")
    namespace, report_type, fields, statement_scope, period_type = _native_source_contract(
        source
    )
    stock_value = row.get("stock_code", row.get("code"))
    stock_code = _provider_text(stock_value, "")
    if not re.fullmatch(r"\d{6}", stock_code):
        raise FinancialReportingBuildError("native financial row has an invalid stock code")
    period_end = _native_report_date(row.get("report_date"))
    company, resolution_reason = company_resolver.resolve_for_report(
        stock_code, period_end=period_end
    )
    source_record_id = (
        f"{source.relative_path}"
        + (f"#table={source.member}" if source.member is not None else "")
        + f"#stock_code={stock_code}&period_end={period_end.isoformat()}"
    )
    if company is None:
        return FinancialEnvelopeRows(
            (),
            (),
            (),
            (
                {
                    "code": resolution_reason,
                    "stock_code": stock_code,
                    "period_end": period_end.isoformat(),
                    "source_record_id": source_record_id,
                    "source_artifact_sha256": source.source_sha256,
                },
            ),
        )
    quality_exceptions: tuple[dict[str, Any], ...] = ()
    if resolution_reason is not None:
        quality_exceptions = (
            {
                "code": resolution_reason,
                "stock_code": stock_code,
                "period_end": period_end.isoformat(),
                "source_record_id": source_record_id,
                "source_artifact_sha256": source.source_sha256,
            },
        )
    acquired_at = row.get("acquired_at")
    knowledge_from = (
        _utc_timestamp(acquired_at, "native financial acquired_at")
        if acquired_at not in {None, ""}
        else knowledge_cutoff.astimezone(timezone.utc)
    )
    published_at = (
        conservative_provider_timestamp(row.get("publish_date"))
        if "publish_date" in row
        else None
    )
    source_name = _provider_text(row.get("source"), namespace)
    source_revision = _provider_text(row.get("source_revision"), source.source_revision)
    currency = _provider_text(row.get("currency"), "CNY")
    report_id = _stable_id(
        "financial_report",
        namespace,
        company.company_id,
        stock_code,
        report_type,
        period_end.isoformat(),
    )
    report = {
        "record_source": source_name,
        "source_record_id": source_record_id,
        "source_revision": source_revision,
        "source_artifact_sha256": source.source_sha256,
        "knowledge_from": knowledge_from,
        "knowledge_to": None,
        "period_start": None,
        "period_end": period_end,
        "published_at": published_at,
        "financial_report_id": report_id,
        "company_id": company.company_id,
        "report_type": report_type,
        "statement_scope": statement_scope,
        "accounting_standard": "PROVIDER_NATIVE_UNSPECIFIED",
        "audit_status": _provider_text(
            row.get("audit_opinion", row.get("is_audit")), ""
        )
        or None,
        "restatement_flag": False,
    }
    concepts: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for field in fields:
        concept_id = _stable_id(
            "financial_concept", namespace, source_revision, report_type, field
        )
        unit_code = _native_unit(field, currency)
        dimensions_json = _canonical_json(
            {
                "adapter_source_id": source.source_id,
                "provider_security_id": row.get("security_id"),
                "provider_update_time": row.get("update_time"),
                "publish_deadline": (
                    row.get("publish_deadline").isoformat()
                    if isinstance(row.get("publish_deadline"), date)
                    else row.get("publish_deadline")
                ),
                "source_member": source.member,
            }
        )
        context_hash = _context_hash(dimensions_json)
        raw_value = row.get(field)
        is_nil = raw_value is None or (
            isinstance(raw_value, str) and not raw_value.strip()
        )
        raw_text = None if is_nil else str(raw_value)
        concepts.append(
            {
                "record_source": source_name,
                "source_record_id": f"{source_record_id}&concept={field}",
                "source_revision": source_revision,
                "source_artifact_sha256": source.source_sha256,
                "knowledge_from": knowledge_from,
                "knowledge_to": None,
                "effective_from": knowledge_from,
                "effective_to": None,
                "financial_concept_id": concept_id,
                "taxonomy_namespace": namespace,
                "taxonomy_version": source_revision,
                "concept_code": field,
                "concept_name": field,
                "statement_type": report_type,
                "data_type": "provider_native_scalar",
                "period_type": period_type,
                "balance_type": None,
            }
        )
        facts.append(
            {
                "record_source": source_name,
                "source_record_id": f"{source_record_id}&concept={field}",
                "source_revision": source_revision,
                "source_artifact_sha256": source.source_sha256,
                "knowledge_from": knowledge_from,
                "knowledge_to": None,
                "period_start": None,
                "period_end": period_end,
                "published_at": published_at,
                "financial_fact_id": _stable_id(
                    "financial_fact", report_id, concept_id, unit_code, context_hash
                ),
                "financial_report_id": report_id,
                "financial_concept_id": concept_id,
                "unit_code": unit_code,
                "value_decimal": _numeric_value(raw_text) if raw_text is not None else None,
                "value_text": raw_text,
                "dimensions_json": dimensions_json,
                "context_hash": context_hash,
                "decimals": None,
                "is_nil": is_nil,
            }
        )
    return FinancialEnvelopeRows(
        (report,), tuple(concepts), tuple(facts), quality_exceptions
    )


def _iter_financial_native_rows(
    source: FinancialReportingSource, *, batch_size: int
) -> Iterator[dict[str, Any]]:
    if source.source_kind == "quarterly_projection":
        try:
            import pyarrow.parquet as pq
        except ModuleNotFoundError as exc:
            raise FinancialReportingBuildError(
                "pyarrow is required for native financial audit"
            ) from exc
        parquet = pq.ParquetFile(source.path)
        required = {"stock_code", "report_date"}
        _namespace, _report_type, fields, _scope, _period_type = _native_source_contract(
            source
        )
        required.update(fields)
        if source.source_id != "canonical_financial_quarterly":
            required.update(
                {
                    "security_id",
                    "publish_date",
                    "currency",
                    "source",
                    "source_revision",
                    "acquired_at",
                }
            )
        missing = sorted(required.difference(parquet.schema_arrow.names))
        if missing:
            raise FinancialReportingBuildError(
                f"native quarterly source lacks required columns: {source.source_id} {missing}"
            )
        for batch in parquet.iter_batches(batch_size=batch_size):
            for row in batch.to_pylist():
                yield dict(row)
        return
    if source.source_kind != "financial_abstract" or source.member != "fin_abstract":
        raise FinancialReportingBuildError(
            f"native financial source kind is unsupported: {source.source_id}"
        )
    uri = f"file:{source.path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise FinancialReportingBuildError("cannot open financial abstract archive") from exc
    try:
        columns = ["code", "report_date", *_FINANCIAL_ABSTRACT_FIELDS]
        present = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(fin_abstract)")
        }
        missing = sorted(set(columns).difference(present))
        if missing:
            raise FinancialReportingBuildError(
                f"financial abstract lacks required columns: {missing}"
            )
        cursor = connection.execute(f"SELECT {', '.join(columns)} FROM fin_abstract")
        while True:
            values = cursor.fetchmany(batch_size)
            if not values:
                break
            for value in values:
                yield dict(zip(columns, value, strict=True))
    except sqlite3.Error as exc:
        raise FinancialReportingBuildError("cannot stream financial abstract rows") from exc
    finally:
        connection.close()


def audit_financial_native_sources(
    inventory: FinancialReportingSourceInventory,
    *,
    company_resolver: FinancialCompanyResolver,
    knowledge_cutoff: datetime,
    batch_size: int = 4096,
) -> FinancialReportingSourceAudit:
    """Validate all lightweight financial sources and calibrate their DWD output."""

    if batch_size < 1:
        raise FinancialReportingBuildError("native financial audit batch size must be positive")
    native_sources = [
        source for source in inventory.sources if source.source_kind != "statement_envelope"
    ]
    if [source.source_id for source in native_sources] != [
        "canonical_financial_quarterly",
        "weekly_full_financial_quarterly",
        "hotfix_financial_quarterly",
        "announcement_archive_fin_abstract",
    ]:
        raise FinancialReportingBuildError("native financial source inventory order differs")
    source_reports: dict[str, dict[str, Any]] = {}
    parent_digests: dict[tuple[str, date], str] = {}
    hotfix_keys: set[tuple[str, date]] = set()
    concept_ids: set[str] = set()
    total_reports = 0
    total_facts = 0
    total_exceptions: Counter[str] = Counter()
    overlap_rows = 0
    hotfix_fill_rows = 0

    def quarterly_digest(row: Mapping[str, Any]) -> str:
        payload = {
            field: row.get(field)
            for field in (
                "stock_code",
                "report_date",
                "publish_date",
                "update_time",
                *_SINA_QUARTERLY_FIELDS,
                "currency",
                "is_audit",
                "audit_opinion",
                "source",
                "source_revision",
            )
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    for source in native_sources:
        seen_keys: set[tuple[str, date]] = set()
        input_rows = 0
        selected_rows = 0
        output_reports = 0
        output_facts = 0
        exception_counts: Counter[str] = Counter()
        for row in _iter_financial_native_rows(source, batch_size=batch_size):
            input_rows += 1
            stock_value = row.get("stock_code", row.get("code"))
            stock_code = _provider_text(stock_value, "")
            period_end = _native_report_date(row.get("report_date"))
            key = (stock_code, period_end)
            if key in seen_keys:
                raise FinancialReportingBuildError(
                    f"duplicate native financial source key: {source.source_id} {key}"
                )
            seen_keys.add(key)
            selected = True
            if source.source_id == "weekly_full_financial_quarterly":
                parent_digests[key] = quarterly_digest(row)
            elif source.source_id == "hotfix_financial_quarterly":
                if key in hotfix_keys:
                    raise FinancialReportingBuildError(
                        f"duplicate quarterly Hotfix key: {key}"
                    )
                hotfix_keys.add(key)
                if key in parent_digests:
                    overlap_rows += 1
                    if parent_digests[key] != quarterly_digest(row):
                        raise FinancialReportingBuildError(
                            f"quarterly parent/Hotfix overlap differs: {key}"
                        )
                    selected = False
                else:
                    hotfix_fill_rows += 1
            if not selected:
                continue
            selected_rows += 1
            expanded = expand_financial_native_row(
                row,
                source=source,
                company_resolver=company_resolver,
                knowledge_cutoff=knowledge_cutoff,
            )
            output_reports += len(expanded.reports)
            output_facts += len(expanded.facts)
            concept_ids.update(
                str(concept["financial_concept_id"]) for concept in expanded.concepts
            )
            for exception in expanded.quality_exceptions:
                exception_counts[str(exception["code"])] += 1
        if input_rows != source.declared_row_count:
            raise FinancialReportingBuildError(
                "native financial source row count differs from input plan: "
                f"{source.source_id} expected={source.declared_row_count} actual={input_rows}"
            )
        source_reports[source.source_id] = {
            "source_kind": source.source_kind,
            "path": source.relative_path,
            "member": source.member,
            "source_sha256": source.source_sha256,
            "input_rows": input_rows,
            "selected_rows": selected_rows,
            "output_reports": output_reports,
            "output_facts": output_facts,
            "quality_exceptions": dict(sorted(exception_counts.items())),
        }
        total_reports += output_reports
        total_facts += output_facts
        total_exceptions.update(exception_counts)
    report = {
        "schema_version": 1,
        "kind": "dwd_financial_reporting_native_source_audit",
        "source_order": [source.source_id for source in native_sources],
        "source_rows": source_reports,
        "quarterly_overlay": {
            "policy": "weekly_parent_then_hotfix_fill_only",
            "parent_overlap_rows": overlap_rows,
            "hotfix_fill_rows": hotfix_fill_rows,
        },
        "empty_modeled_sources": list(inventory.empty_modeled_sources),
        "candidate_size_calibration": {
            "dwd_financial_report_rows": total_reports,
            "dwd_financial_fact_rows": total_facts,
            "dwd_financial_concept_count": len(concept_ids),
            "quality_exception_rows": int(sum(total_exceptions.values())),
        },
        "quality_exceptions": dict(sorted(total_exceptions.items())),
    }
    return FinancialReportingSourceAudit(report=report)


_OWNED_TABLES = (
    "dwd_financial_report",
    "dwd_financial_concept",
    "dwd_financial_fact",
)
_PARTITIONED_TABLES = ("dwd_financial_report", "dwd_financial_fact")
_FINANCIAL_BUILDER_CONTRACT = "financial-reporting-six-source-v2"
@dataclass(frozen=True)
class FinancialReportingBuildResult:
    """A sealed, non-published financial-reporting candidate."""

    root: Path
    semantic_digest: str
    row_counts: Mapping[str, int]


def _digest_value(value: object) -> str:
    """Hash the small JSON control-plane values used in receipts and manifests."""

    def normalize(item: object) -> object:
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, Mapping):
            return {str(key): normalize(value) for key, value in sorted(item.items())}
        if isinstance(item, list | tuple):
            return [normalize(value) for value in item]
        return item

    payload = json.dumps(
        normalize(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _read_json_object(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinancialReportingBuildError(f"cannot read {field}: {path}") from exc
    if not isinstance(value, dict):
        raise FinancialReportingBuildError(f"{field} must be a JSON object")
    return value


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    path.parent.mkdir(parents=True, exist_ok=True)
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
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_parquet_atomic(path: Path, table: Any) -> None:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise FinancialReportingBuildError("pyarrow is required for financial DWD rows") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    if temporary.exists():
        temporary.unlink()
    try:
        pq.write_table(table, temporary, compression="zstd", write_statistics=True)
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _file_record(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _verify_financial_model(model: Mapping[str, Any]) -> None:
    tables = {
        str(raw.get("name")): raw
        for raw in model.get("tables", [])
        if isinstance(raw, Mapping) and raw.get("name") in _OWNED_TABLES
    }
    if set(tables) != set(_OWNED_TABLES):
        raise FinancialReportingBuildError("model does not define the three financial objects")
    expected = {
        "dwd_financial_report": ("period_end_year", "reporting_period"),
        "dwd_financial_concept": (None, "effective_interval"),
        "dwd_financial_fact": ("period_end_year", "reporting_period"),
    }
    for table, (partition, time_profile) in expected.items():
        value = tables[table]
        if value.get("time_profile") != time_profile:
            raise FinancialReportingBuildError("financial model time profile differs")
        expected_partition = [] if partition is None else [partition]
        if value.get("partition_by") != expected_partition:
            raise FinancialReportingBuildError("financial model partition contract differs")


def _verify_financial_source_audit(path: Path) -> tuple[dict[str, Any], str]:
    audit = _read_json_object(path, "financial source audit")
    calibration = audit.get("candidate_size_calibration")
    if (
        audit.get("schema_version") != 1
        or audit.get("kind") != "dwd_financial_reporting_combined_source_audit"
        or not isinstance(calibration, Mapping)
        or not isinstance(calibration.get("dwd_financial_report_rows"), int)
        or not isinstance(calibration.get("dwd_financial_fact_rows"), int)
    ):
        raise FinancialReportingBuildError("financial source audit has an unsupported contract")
    return audit, _sha256(path)


def _candidate_identity(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    source_audit_path: Path,
    run_id: str,
) -> tuple[
    dict[str, Any],
    FinancialReportingSourceInventory,
    FinancialCompanyResolver,
    dict[str, Any],
    datetime,
]:
    """Verify all governed inputs before any financial candidate write."""

    try:
        plan, ledger = _verify_plan_package(input_plan)
        semantic_plan = _plan_mapping(plan.get("plan"), "input plan")
        model = _plan_object(model_path)
        _verify_ods_binding(ods_package, semantic_plan, model)
    except TradingCalendarBuildError as exc:
        raise FinancialReportingBuildError(
            f"financial input-plan verification failed: {exc}"
        ) from exc
    if plan.get("run_id") != run_id or semantic_plan.get("mode") != "base":
        raise FinancialReportingBuildError("financial build must match one sealed Base run")
    model_sha = _sha256(model_path)
    model_identity = _plan_mapping(semantic_plan.get("model"), "input plan model")
    if model_identity.get("sha256") != model_sha:
        raise FinancialReportingBuildError("financial model differs from sealed input plan")
    _verify_financial_model(model)
    input_plan_digest = plan.get("plan_digest")
    if not isinstance(input_plan_digest, str) or not input_plan_digest:
        raise FinancialReportingBuildError("input plan has no semantic digest")
    try:
        upstream = verify_listed_company_candidate(
            listed_company_candidate,
            run_id=run_id,
            input_plan_digest=input_plan_digest,
            model_sha256=model_sha,
        )
    except ListedCompanyBuildError as exc:
        raise FinancialReportingBuildError(
            f"listed-company predecessor failed verification: {exc}"
        ) from exc
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise FinancialReportingBuildError("pyarrow is required for financial DWD rows") from exc
    security_rows = pq.read_table(
        listed_company_candidate / "payload/listed_company/dwd_security.parquet"
    ).to_pylist()
    identifier_rows = pq.read_table(
        listed_company_candidate / "payload/listed_company/dwd_security_identifier.parquet"
    ).to_pylist()
    resolver = financial_company_resolver_from_listed_company_rows(security_rows, identifier_rows)
    records = [
        _plan_mapping(value, "source ledger record")
        for value in _plan_sequence(ledger.get("records"), "source ledger records")
    ]
    inventory = select_financial_reporting_sources(records, ods_package)
    audit, audit_sha = _verify_financial_source_audit(source_audit_path)
    expected_bindings = {
        "run_id": run_id,
        "input_plan_digest": input_plan_digest,
        "model_sha256": model_sha,
        "listed_company_manifest_sha256": upstream.manifest_sha256,
        "listed_company_semantic_digest": upstream.semantic_digest,
        "source_sha256": {
            source.source_id: source.source_sha256 for source in inventory.sources
        },
    }
    if any(audit.get(field) != value for field, value in expected_bindings.items()):
        raise FinancialReportingBuildError("financial combined audit binding differs")
    identity = {
        "schema_version": 1,
        **expected_bindings,
        "source_audit_sha256": audit_sha,
        "builder_contract": _FINANCIAL_BUILDER_CONTRACT,
    }
    cutoff = _utc_timestamp(
        semantic_plan.get("knowledge_cutoff"), "financial knowledge cutoff"
    )
    return identity, inventory, resolver, audit, cutoff


def audit_financial_native_source_package(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    output: Path,
    run_id: str,
    batch_size: int = 4096,
) -> FinancialReportingSourceAudit:
    """Audit every lightweight #61 source against the sealed plan and #57 identity."""

    try:
        plan, ledger = _verify_plan_package(input_plan)
        semantic_plan = _plan_mapping(plan.get("plan"), "input plan")
        model = _plan_object(model_path)
        _verify_ods_binding(ods_package, semantic_plan, model)
    except TradingCalendarBuildError as exc:
        raise FinancialReportingBuildError(
            f"financial native-audit input verification failed: {exc}"
        ) from exc
    if plan.get("run_id") != run_id or semantic_plan.get("mode") != "base":
        raise FinancialReportingBuildError("financial native audit must match one Base run")
    model_sha = _sha256(model_path)
    if _plan_mapping(semantic_plan.get("model"), "input plan model").get(
        "sha256"
    ) != model_sha:
        raise FinancialReportingBuildError("financial model differs from sealed input plan")
    _verify_financial_model(model)
    input_plan_digest = _text(plan.get("plan_digest"), "input plan digest")
    try:
        upstream = verify_listed_company_candidate(
            listed_company_candidate,
            run_id=run_id,
            input_plan_digest=input_plan_digest,
            model_sha256=model_sha,
        )
    except ListedCompanyBuildError as exc:
        raise FinancialReportingBuildError(
            f"listed-company predecessor failed verification: {exc}"
        ) from exc
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise FinancialReportingBuildError("pyarrow is required for financial audit") from exc
    security_rows = pq.read_table(
        listed_company_candidate / "payload/listed_company/dwd_security.parquet"
    ).to_pylist()
    identifier_rows = pq.read_table(
        listed_company_candidate / "payload/listed_company/dwd_security_identifier.parquet"
    ).to_pylist()
    resolver = financial_company_resolver_from_listed_company_rows(
        security_rows, identifier_rows
    )
    records = [
        _plan_mapping(value, "source ledger record")
        for value in _plan_sequence(ledger.get("records"), "source ledger records")
    ]
    inventory = select_financial_reporting_sources(records, ods_package)
    cutoff = _utc_timestamp(
        semantic_plan.get("knowledge_cutoff"), "financial knowledge cutoff"
    )
    audit = audit_financial_native_sources(
        inventory,
        company_resolver=resolver,
        knowledge_cutoff=cutoff,
        batch_size=batch_size,
    )
    audit.report.update(
        {
            "run_id": run_id,
            "input_plan_digest": input_plan_digest,
            "model_sha256": model_sha,
            "listed_company_manifest_sha256": upstream.manifest_sha256,
            "listed_company_semantic_digest": upstream.semantic_digest,
            "source_sha256": {
                source.source_id: source.source_sha256 for source in inventory.sources
            },
        }
    )
    write_financial_reporting_source_audit(output, audit)
    return audit


def _load_or_initialize_checkpoint(work: Path, identity: Mapping[str, Any]) -> dict[str, Any]:
    checkpoint_path = work / "checkpoint.json"
    if checkpoint_path.exists():
        checkpoint = _read_json_object(checkpoint_path, "financial checkpoint")
        if (
            checkpoint.get("schema_version") != 1
            or checkpoint.get("identity") != dict(identity)
            or not isinstance(checkpoint.get("completed_batches"), Mapping)
        ):
            raise FinancialReportingBuildError("existing financial work has a different identity")
        return checkpoint
    checkpoint = {
        "schema_version": 1,
        "identity": dict(identity),
        "completed_batches": {},
        "concept_finalized": False,
    }
    _write_json_atomic(checkpoint_path, checkpoint)
    return checkpoint


def _batch_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    source: FinancialStatementSource,
    resolver: FinancialCompanyResolver,
) -> tuple[dict[str, dict[int, list[dict[str, Any]]]], list[dict[str, Any]], dict[str, int]]:
    rows_by_year: dict[str, dict[int, list[dict[str, Any]]]] = {
        table: defaultdict(list) for table in _PARTITIONED_TABLES
    }
    concepts_by_id: dict[str, dict[str, Any]] = {}
    row_counts = {table: 0 for table in _OWNED_TABLES}
    for row in rows:
        expanded = expand_financial_envelope(row, source=source, company_resolver=resolver)
        for report in expanded.reports:
            rows_by_year["dwd_financial_report"][report["period_end"].year].append(report)
        for fact in expanded.facts:
            rows_by_year["dwd_financial_fact"][fact["period_end"].year].append(fact)
        for concept in expanded.concepts:
            concepts_by_id.setdefault(str(concept["financial_concept_id"]), concept)
        row_counts["dwd_financial_report"] += len(expanded.reports)
        row_counts["dwd_financial_fact"] += len(expanded.facts)
    concepts = [concepts_by_id[key] for key in sorted(concepts_by_id)]
    row_counts["dwd_financial_concept"] = len(concepts)
    return rows_by_year, concepts, row_counts


def _reporting_batch_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    source: FinancialReportingSource,
    resolver: FinancialCompanyResolver,
    knowledge_cutoff: datetime,
) -> tuple[
    dict[str, dict[int, list[dict[str, Any]]]],
    list[dict[str, Any]],
    dict[str, int],
    dict[str, int],
]:
    """Materialize one checkpoint batch from any of the six governed sources."""

    rows_by_year: dict[str, dict[int, list[dict[str, Any]]]] = {
        table: defaultdict(list) for table in _PARTITIONED_TABLES
    }
    concepts_by_id: dict[str, dict[str, Any]] = {}
    row_counts = {table: 0 for table in _OWNED_TABLES}
    exception_counts: Counter[str] = Counter()
    statement_source = (
        FinancialStatementSource(
            label=source.ledger_identity,
            path=source.path,
            relative_path=source.relative_path,
            source_sha256=source.source_sha256,
            source_revision=source.source_revision,
            precedence=source.precedence,
        )
        if source.source_kind == "statement_envelope"
        else None
    )
    for row in rows:
        if statement_source is not None:
            expanded = expand_financial_envelope(
                row, source=statement_source, company_resolver=resolver
            )
        else:
            expanded = expand_financial_native_row(
                row,
                source=source,
                company_resolver=resolver,
                knowledge_cutoff=knowledge_cutoff,
            )
        for report in expanded.reports:
            rows_by_year["dwd_financial_report"][report["period_end"].year].append(
                report
            )
        for fact in expanded.facts:
            rows_by_year["dwd_financial_fact"][fact["period_end"].year].append(fact)
        for concept in expanded.concepts:
            concepts_by_id.setdefault(str(concept["financial_concept_id"]), concept)
        row_counts["dwd_financial_report"] += len(expanded.reports)
        row_counts["dwd_financial_fact"] += len(expanded.facts)
        exception_counts.update(
            str(exception["code"]) for exception in expanded.quality_exceptions
        )
    concepts = [concepts_by_id[key] for key in sorted(concepts_by_id)]
    row_counts["dwd_financial_concept"] = len(concepts)
    return rows_by_year, concepts, row_counts, dict(sorted(exception_counts.items()))


def _iter_reporting_source_batches(
    source: FinancialReportingSource, *, statement_batch_size: int, native_batch_size: int
) -> Iterator[list[dict[str, Any]]]:
    if source.source_kind == "statement_envelope":
        try:
            import pyarrow.parquet as pq
        except ModuleNotFoundError as exc:
            raise FinancialReportingBuildError(
                "pyarrow is required for financial DWD rows"
            ) from exc
        for batch in pq.ParquetFile(source.path).iter_batches(
            batch_size=statement_batch_size
        ):
            yield [dict(row) for row in batch.to_pylist()]
        return
    batch: list[dict[str, Any]] = []
    for row in _iter_financial_native_rows(source, batch_size=native_batch_size):
        batch.append(row)
        if len(batch) == native_batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _write_batch(
    *,
    work: Path,
    batch_id: str,
    rows_by_year: Mapping[str, Mapping[int, Sequence[Mapping[str, Any]]]],
    concepts: Sequence[Mapping[str, Any]],
    schemas: Mapping[str, Any],
) -> list[dict[str, object]]:
    try:
        import pyarrow as pa
    except ModuleNotFoundError as exc:
        raise FinancialReportingBuildError("pyarrow is required for financial DWD rows") from exc
    files: list[dict[str, object]] = []
    for table in _PARTITIONED_TABLES:
        for year, rows in sorted(rows_by_year[table].items()):
            if not rows:
                continue
            relative = (
                f"payload/financial_reporting/{table}/period_end_year={year}/"
                f"part-{batch_id}.parquet"
            )
            path = work / relative
            if path.exists():
                raise FinancialReportingBuildError("financial batch part already exists")
            _write_parquet_atomic(path, pa.Table.from_pylist(list(rows), schema=schemas[table]))
            files.append(_file_record(work, path))
    if concepts:
        relative = f"staging/concepts/part-{batch_id}.parquet"
        path = work / relative
        if path.exists():
            raise FinancialReportingBuildError("financial concept staging part already exists")
        _write_parquet_atomic(
            path,
            pa.Table.from_pylist(list(concepts), schema=schemas["dwd_financial_concept"]),
        )
        files.append(_file_record(work, path))
    return files


def _finalize_concepts(work: Path, checkpoint: dict[str, Any], schemas: Mapping[str, Any]) -> int:
    final_path = work / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
    if checkpoint.get("concept_finalized"):
        try:
            import pyarrow.parquet as pq
        except ModuleNotFoundError as exc:
            raise FinancialReportingBuildError(
                "pyarrow is required for financial DWD rows"
            ) from exc
        if not final_path.is_file():
            raise FinancialReportingBuildError("financial concept checkpoint has no output")
        return pq.ParquetFile(final_path).metadata.num_rows
    concept_paths = sorted((work / "staging/concepts").glob("*.parquet"))
    if not concept_paths:
        raise FinancialReportingBuildError("financial build has no concept staging rows")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise FinancialReportingBuildError("duckdb is required for financial DWD auditing") from exc
    scratch = work / ".audit"
    scratch.mkdir(exist_ok=True)
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute("SET memory_limit='512MB'")
        escaped_scratch = str(scratch).replace("'", "''")
        connection.execute(f"SET temp_directory='{escaped_scratch}'")
        connection.from_parquet([str(path) for path in concept_paths]).create_view("concept_stage")
        conflict = connection.execute(
            """
            SELECT financial_concept_id
            FROM (
              SELECT DISTINCT financial_concept_id, taxonomy_namespace, taxonomy_version,
                concept_code, concept_name, statement_type, data_type, period_type, balance_type
              FROM concept_stage
            )
            GROUP BY financial_concept_id
            HAVING COUNT(*) <> 1
            LIMIT 1
            """
        ).fetchone()
        if conflict is not None:
            raise FinancialReportingBuildError("financial concept has conflicting source semantics")
        concept_table = connection.execute(
            """
            SELECT * EXCLUDE (ordinality)
            FROM (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY financial_concept_id
                ORDER BY knowledge_from, source_record_id, source_artifact_sha256
              ) AS ordinality
              FROM concept_stage
            )
            WHERE ordinality = 1
            ORDER BY financial_concept_id
            """
        ).to_arrow_table()
    finally:
        connection.close()
    if not concept_table.schema.equals(schemas["dwd_financial_concept"], check_metadata=True):
        concept_table = concept_table.cast(schemas["dwd_financial_concept"])
    _write_parquet_atomic(final_path, concept_table)
    checkpoint["concept_finalized"] = True
    _write_json_atomic(work / "checkpoint.json", checkpoint)
    return concept_table.num_rows


def _audit_financial_candidate(
    *,
    work: Path,
    schemas: Mapping[str, Any],
    expected_reports: int,
    expected_facts: int,
) -> tuple[dict[str, int], dict[str, object]]:
    try:
        import duckdb
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise FinancialReportingBuildError(
            "duckdb and pyarrow are required for financial audit"
        ) from exc
    paths = {
        "dwd_financial_report": sorted(
            (work / "payload/financial_reporting/dwd_financial_report").rglob("*.parquet")
        ),
        "dwd_financial_concept": [
            work / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
        ],
        "dwd_financial_fact": sorted(
            (work / "payload/financial_reporting/dwd_financial_fact").rglob("*.parquet")
        ),
    }
    if not all(paths.values()):
        raise FinancialReportingBuildError("financial candidate has an empty owned table")
    metadata_counts: dict[str, int] = {}
    for table, values in paths.items():
        count = 0
        for path in values:
            parquet = pq.ParquetFile(path)
            if not parquet.schema_arrow.equals(schemas[table], check_metadata=True):
                raise FinancialReportingBuildError(f"financial output schema differs: {table}")
            count += parquet.metadata.num_rows
        metadata_counts[table] = count
    if (
        metadata_counts["dwd_financial_report"] != expected_reports
        or metadata_counts["dwd_financial_fact"] != expected_facts
    ):
        raise FinancialReportingBuildError("financial output count differs from its source audit")
    scratch = work / ".audit"
    scratch.mkdir(exist_ok=True)
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute("SET memory_limit='1536MB'")
        connection.execute("SET threads=1")
        connection.execute("SET preserve_insertion_order=false")
        escaped_scratch = str(scratch).replace("'", "''")
        connection.execute(f"SET temp_directory='{escaped_scratch}'")

        def year_partitions(values: Sequence[Path], table: str) -> dict[int, list[Path]]:
            partitions: dict[int, list[Path]] = defaultdict(list)
            for path in values:
                matched = re.fullmatch(r"period_end_year=(\d{4})", path.parent.name)
                if matched is None:
                    raise FinancialReportingBuildError(
                        f"financial output has an invalid year partition: {table} {path}"
                    )
                partitions[int(matched.group(1))].append(path)
            return dict(sorted(partitions.items()))

        report_partitions = year_partitions(
            paths["dwd_financial_report"], "dwd_financial_report"
        )
        fact_partitions = year_partitions(
            paths["dwd_financial_fact"], "dwd_financial_fact"
        )
        missing_report_years = sorted(set(fact_partitions).difference(report_partitions))
        if missing_report_years:
            raise FinancialReportingBuildError(
                "financial fact partitions have no report partition: "
                + ",".join(str(year) for year in missing_report_years)
            )
        connection.from_parquet(
            [str(path) for path in paths["dwd_financial_concept"]]
        ).create_view("dwd_financial_concept")
        null_counts = {
            "dwd_financial_report": 0,
            "dwd_financial_concept": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM dwd_financial_concept
                    WHERE financial_concept_id IS NULL OR knowledge_from IS NULL
                      OR effective_from IS NULL
                    """
                ).fetchone()[0]
            ),
            "dwd_financial_fact": 0,
        }
        duplicate_reports = 0
        duplicate_concepts = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                  SELECT financial_concept_id, taxonomy_version, effective_from, knowledge_from
                  FROM dwd_financial_concept GROUP BY ALL HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
        duplicate_facts = 0
        orphan_reports = 0
        orphan_concepts = 0
        partition_audit: dict[str, dict[str, int]] = {}
        for year, report_paths in report_partitions.items():
            connection.from_parquet([str(path) for path in report_paths]).create_view(
                "financial_report_partition", replace=True
            )
            report_nulls = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM financial_report_partition
                    WHERE financial_report_id IS NULL OR company_id IS NULL
                      OR period_end IS NULL OR knowledge_from IS NULL
                    """
                ).fetchone()[0]
            )
            report_duplicates = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM (
                      SELECT financial_report_id, period_end, knowledge_from
                      FROM financial_report_partition GROUP BY ALL HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
            )
            null_counts["dwd_financial_report"] += report_nulls
            duplicate_reports += report_duplicates
            partition_audit[str(year)] = {
                "report_rows": sum(
                    pq.ParquetFile(path).metadata.num_rows for path in report_paths
                ),
                "fact_rows": 0,
                "null_report_keys": report_nulls,
                "null_fact_keys": 0,
                "duplicate_report_keys": report_duplicates,
                "duplicate_fact_keys": 0,
                "orphan_report_ids": 0,
                "orphan_concept_ids": 0,
            }
        for year, fact_paths in fact_partitions.items():
            connection.from_parquet([str(path) for path in fact_paths]).create_view(
                "financial_fact_partition", replace=True
            )
            connection.from_parquet(
                [str(path) for path in report_partitions[year]]
            ).create_view("financial_report_partition", replace=True)
            fact_nulls = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM financial_fact_partition
                    WHERE financial_fact_id IS NULL OR financial_report_id IS NULL
                      OR financial_concept_id IS NULL OR period_end IS NULL
                      OR knowledge_from IS NULL
                    """
                ).fetchone()[0]
            )
            fact_duplicates = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM (
                      SELECT financial_report_id, financial_concept_id, unit_code,
                        context_hash, period_end, knowledge_from
                      FROM financial_fact_partition GROUP BY ALL HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
            )
            report_orphans = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM financial_fact_partition AS fact
                    LEFT JOIN financial_report_partition AS report
                      ON fact.financial_report_id = report.financial_report_id
                    WHERE report.financial_report_id IS NULL
                    """
                ).fetchone()[0]
            )
            concept_orphans = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM financial_fact_partition AS fact
                    LEFT JOIN dwd_financial_concept AS concept
                      ON fact.financial_concept_id = concept.financial_concept_id
                    WHERE concept.financial_concept_id IS NULL
                    """
                ).fetchone()[0]
            )
            null_counts["dwd_financial_fact"] += fact_nulls
            duplicate_facts += fact_duplicates
            orphan_reports += report_orphans
            orphan_concepts += concept_orphans
            year_audit = partition_audit[str(year)]
            year_audit.update(
                {
                    "fact_rows": sum(
                        pq.ParquetFile(path).metadata.num_rows for path in fact_paths
                    ),
                    "null_fact_keys": fact_nulls,
                    "duplicate_fact_keys": fact_duplicates,
                    "orphan_report_ids": report_orphans,
                    "orphan_concept_ids": concept_orphans,
                }
            )
    finally:
        connection.close()
    if any(null_counts.values()) or any(
        (duplicate_reports, duplicate_concepts, duplicate_facts, orphan_reports, orphan_concepts)
    ):
        raise FinancialReportingBuildError(
            "financial candidate has key, duplicate, or FK violations"
        )
    audit = {
        "schema_version": 1,
        "kind": "dwd_financial_reporting_reconciliation",
        "expected_reports": expected_reports,
        "expected_facts": expected_facts,
        "row_counts": metadata_counts,
        "null_row_keys": null_counts,
        "duplicate_business_keys": {
            "dwd_financial_report": duplicate_reports,
            "dwd_financial_concept": duplicate_concepts,
            "dwd_financial_fact": duplicate_facts,
        },
        "orphan_foreign_keys": {
            "financial_report_id": orphan_reports,
            "financial_concept_id": orphan_concepts,
        },
        "year_partitions": partition_audit,
        "audit_policy": "duckdb_year_partitioned_external_audit_v2",
    }
    return metadata_counts, audit


def _verify_existing_financial_candidate(
    output: Path, *, identity: Mapping[str, Any]
) -> FinancialReportingBuildResult:
    if output.is_symlink() or not output.is_dir():
        raise FinancialReportingBuildError("financial candidate must be a physical directory")
    manifest_path = output / "manifest.json"
    manifest = _read_json_object(manifest_path, "financial candidate manifest")
    try:
        ready = (output / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise FinancialReportingBuildError("financial candidate READY is unreadable") from exc
    if ready != _sha256(manifest_path):
        raise FinancialReportingBuildError("financial candidate READY does not match manifest")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "dwd_financial_reporting_candidate"
        or manifest.get("run_id") != identity.get("run_id")
        or manifest.get("input_plan_digest") != identity.get("input_plan_digest")
        or manifest.get("model_sha256") != identity.get("model_sha256")
        or manifest.get("source_audit_sha256") != identity.get("source_audit_sha256")
        or manifest.get("builder_contract") != identity.get("builder_contract")
        or manifest.get("listed_company_manifest_sha256")
        != identity.get("listed_company_manifest_sha256")
        or manifest.get("listed_company_semantic_digest")
        != identity.get("listed_company_semantic_digest")
        or manifest.get("source_sha256") != identity.get("source_sha256")
        or manifest.get("owned_tables") != list(_OWNED_TABLES)
        or not isinstance(manifest.get("row_counts"), Mapping)
    ):
        raise FinancialReportingBuildError("financial candidate manifest identity is inconsistent")
    declared: set[str] = set()
    for raw in manifest.get("files", []):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("path"), str):
            raise FinancialReportingBuildError("financial candidate manifest file is invalid")
        relative = str(raw["path"])
        if (
            relative in declared
            or relative.startswith("/")
            or ".." in PurePosixPath(relative).parts
        ):
            raise FinancialReportingBuildError("financial candidate manifest has unsafe files")
        declared.add(relative)
        path = output / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != raw.get("size_bytes")
            or _sha256(path) != raw.get("sha256")
        ):
            raise FinancialReportingBuildError(
                f"financial candidate file verification failed: {relative}"
            )
    physical = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if physical != declared | {"manifest.json", "READY"} or any(
        path.is_symlink() for path in output.rglob("*")
    ):
        raise FinancialReportingBuildError("financial candidate physical inventory is inconsistent")
    return FinancialReportingBuildResult(
        root=output.resolve(),
        semantic_digest=str(manifest.get("semantic_digest")),
        row_counts={str(key): int(value) for key, value in manifest["row_counts"].items()},
    )


def verify_financial_reporting_candidate(
    output: Path, *, run_id: str, input_plan_digest: str, model_sha256: str
) -> FinancialReportingBuildResult:
    """Deeply verify a same-run #61 predecessor for downstream domain builders."""

    manifest = _read_json_object(output / "manifest.json", "financial candidate manifest")
    if (
        manifest.get("run_id") != run_id
        or manifest.get("input_plan_digest") != input_plan_digest
        or manifest.get("model_sha256") != model_sha256
    ):
        raise FinancialReportingBuildError("financial predecessor binding differs")
    identity_fields = (
        "run_id",
        "input_plan_digest",
        "model_sha256",
        "source_audit_sha256",
        "builder_contract",
        "listed_company_manifest_sha256",
        "listed_company_semantic_digest",
        "source_sha256",
    )
    identity = {field: manifest.get(field) for field in identity_fields}
    return _verify_existing_financial_candidate(output, identity=identity)


def _build_financial_reporting_locked(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    source_audit_path: Path,
    output: Path,
    run_id: str,
    source_batch_size: int,
    native_batch_size: int,
) -> FinancialReportingBuildResult:
    if source_batch_size < 1 or native_batch_size < 1:
        raise FinancialReportingBuildError("financial source batch sizes must be positive")
    identity, inventory, resolver, source_audit, knowledge_cutoff = _candidate_identity(
        ods_package=ods_package,
        input_plan=input_plan,
        model_path=model_path,
        listed_company_candidate=listed_company_candidate,
        source_audit_path=source_audit_path,
        run_id=run_id,
    )
    if output.exists():
        return _verify_existing_financial_candidate(output, identity=identity)
    work = output.parent / f".{output.name}.work"
    if work.exists() and (work.is_symlink() or not work.is_dir()):
        raise FinancialReportingBuildError("financial work directory must be physical")
    work.mkdir(parents=True, exist_ok=True)
    checkpoint_identity = {
        **identity,
        "source_batch_size": source_batch_size,
        "native_batch_size": native_batch_size,
    }
    checkpoint = _load_or_initialize_checkpoint(work, checkpoint_identity)
    schemas = financial_reporting_schemas()
    parent_payloads: dict[tuple[str, str], str] = {}
    hotfix_keys: set[tuple[str, str]] = set()
    parent_quarterly: dict[tuple[str, date], str] = {}
    hotfix_quarterly_keys: set[tuple[str, date]] = set()
    totals = {table: 0 for table in _OWNED_TABLES}
    selected_source_rows = 0
    quality_exceptions: Counter[str] = Counter()

    def quarterly_digest(row: Mapping[str, Any]) -> str:
        payload = {
            field: row.get(field)
            for field in (
                "stock_code",
                "report_date",
                "publish_date",
                "update_time",
                *_SINA_QUARTERLY_FIELDS,
                "currency",
                "is_audit",
                "audit_opinion",
                "source",
                "source_revision",
            )
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str).encode(
                "utf-8"
            )
        ).hexdigest()

    for source_index, source in enumerate(inventory.sources):
        source_batches = _iter_reporting_source_batches(
            source,
            statement_batch_size=source_batch_size,
            native_batch_size=native_batch_size,
        )
        for batch_index, raw_rows in enumerate(source_batches):
            selected_rows: list[dict[str, Any]] = []
            for row in raw_rows:
                if source.source_kind == "statement_envelope":
                    key = _row_identity(row)
                    payload_digest = _text(
                        row.get("raw_payload_sha256"), "raw_payload_sha256"
                    )
                    if source.precedence == "parent":
                        if key in parent_payloads:
                            raise FinancialReportingBuildError(
                                "duplicate parent financial statement key"
                            )
                        parent_payloads[key] = payload_digest
                        selected_rows.append(row)
                        continue
                    if key in hotfix_keys:
                        raise FinancialReportingBuildError(
                            "duplicate Hotfix financial statement key"
                        )
                    hotfix_keys.add(key)
                    parent_digest = parent_payloads.get(key)
                    if parent_digest is not None:
                        if parent_digest != payload_digest:
                            raise FinancialReportingBuildError(
                                "financial statement parent/Hotfix overlap has "
                                "different raw payload"
                            )
                        continue
                    selected_rows.append(row)
                    continue
                if source.source_id in {
                    "weekly_full_financial_quarterly",
                    "hotfix_financial_quarterly",
                }:
                    stock_code = _provider_text(row.get("stock_code"), "")
                    key = (stock_code, _native_report_date(row.get("report_date")))
                    digest = quarterly_digest(row)
                    if source.precedence == "parent":
                        if key in parent_quarterly:
                            raise FinancialReportingBuildError(
                                "duplicate parent financial quarterly key"
                            )
                        parent_quarterly[key] = digest
                    else:
                        if key in hotfix_quarterly_keys:
                            raise FinancialReportingBuildError(
                                "duplicate Hotfix financial quarterly key"
                            )
                        hotfix_quarterly_keys.add(key)
                        parent_digest = parent_quarterly.get(key)
                        if parent_digest is not None and parent_digest != digest:
                            raise FinancialReportingBuildError(
                                "financial quarterly parent/Hotfix overlap differs"
                            )
                        if parent_digest is not None:
                            continue
                selected_rows.append(row)
            batch_id = f"{source_index:02d}-{batch_index:06d}"
            completed = checkpoint["completed_batches"].get(batch_id)
            if completed is not None:
                if not isinstance(completed, Mapping):
                    raise FinancialReportingBuildError("financial checkpoint batch is invalid")
                counts = completed.get("row_counts")
                exceptions = completed.get("quality_exceptions", {})
                if not isinstance(counts, Mapping) or not isinstance(exceptions, Mapping):
                    raise FinancialReportingBuildError(
                        "financial checkpoint batch accounting is invalid"
                    )
                for table in _OWNED_TABLES:
                    totals[table] += int(counts.get(table, 0))
                quality_exceptions.update(
                    {str(key): int(value) for key, value in exceptions.items()}
                )
                selected_source_rows += int(completed.get("selected_source_rows", 0))
                continue
            rows_by_year, concepts, counts, exceptions = _reporting_batch_rows(
                selected_rows,
                source=source,
                resolver=resolver,
                knowledge_cutoff=knowledge_cutoff,
            )
            files = _write_batch(
                work=work,
                batch_id=batch_id,
                rows_by_year=rows_by_year,
                concepts=concepts,
                schemas=schemas,
            )
            checkpoint["completed_batches"][batch_id] = {
                "source": source.source_id,
                "selected_source_rows": len(selected_rows),
                "row_counts": counts,
                "quality_exceptions": exceptions,
                "files": files,
            }
            _write_json_atomic(work / "checkpoint.json", checkpoint)
            for table in _OWNED_TABLES:
                totals[table] += counts[table]
            quality_exceptions.update(exceptions)
            selected_source_rows += len(selected_rows)
    calibration = _plan_mapping(
        source_audit.get("candidate_size_calibration"), "financial source audit calibration"
    )
    if (
        totals["dwd_financial_report"] != calibration.get("dwd_financial_report_rows")
        or totals["dwd_financial_fact"] != calibration.get("dwd_financial_fact_rows")
    ):
        raise FinancialReportingBuildError("financial build does not reconcile to source audit")
    concept_count = _finalize_concepts(work, checkpoint, schemas)
    row_counts, reconciliation = _audit_financial_candidate(
        work=work,
        schemas=schemas,
        expected_reports=totals["dwd_financial_report"],
        expected_facts=totals["dwd_financial_fact"],
    )
    if row_counts["dwd_financial_concept"] != concept_count:
        raise FinancialReportingBuildError("financial concept audit count differs from output")
    reconciliation["selected_source_rows"] = selected_source_rows
    reconciliation["source_audit_sha256"] = identity["source_audit_sha256"]
    reconciliation["concept_staging_rows"] = totals["dwd_financial_concept"]
    reconciliation["quality_exceptions"] = dict(sorted(quality_exceptions.items()))
    _write_json_atomic(work / "reconciliation/source-row-reconciliation.json", reconciliation)
    audit_copy = work / "audits/financial-source-audit.json"
    audit_copy.parent.mkdir(parents=True, exist_ok=True)
    if audit_copy.exists() and audit_copy.read_bytes() != source_audit_path.read_bytes():
        raise FinancialReportingBuildError("candidate financial source audit differs")
    if not audit_copy.exists():
        shutil.copyfile(source_audit_path, audit_copy)
        _fsync_directory(audit_copy.parent)
    shutil.rmtree(work / "staging", ignore_errors=True)
    shutil.rmtree(work / ".audit", ignore_errors=True)
    table_files = {
        table: [
            _file_record(work, path)
            for path in sorted((work / "payload/financial_reporting" / table).rglob("*.parquet"))
        ]
        for table in _OWNED_TABLES
    }
    table_digests = {table: _digest_value(files) for table, files in table_files.items()}
    semantic_digest = _digest_value({**identity, "table_digests": table_digests})
    receipt = {
        "schema_version": 1,
        "kind": "dwd_financial_reporting_build_receipt",
        "status": "built",
        **identity,
        "semantic_digest": semantic_digest,
        "row_counts": row_counts,
        "table_digests": table_digests,
        "quality_exceptions": dict(sorted(quality_exceptions.items())),
        "resource_policy": "partitioned_write_and_duckdb_external_audit_v1",
    }
    _write_json_atomic(work / "receipts/financial-reporting-build.json", receipt)
    checkpoint["completed"] = True
    checkpoint["semantic_digest"] = semantic_digest
    _write_json_atomic(work / "checkpoint.json", checkpoint)
    files = sorted(
        path
        for path in work.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name not in {"manifest.json", "READY"}
    )
    manifest = {
        "schema_version": 1,
        "kind": "dwd_financial_reporting_candidate",
        "run_id": run_id,
        "input_plan_digest": identity["input_plan_digest"],
        "model_sha256": identity["model_sha256"],
        "source_audit_sha256": identity["source_audit_sha256"],
        "builder_contract": identity["builder_contract"],
        "listed_company_manifest_sha256": identity["listed_company_manifest_sha256"],
        "listed_company_semantic_digest": identity["listed_company_semantic_digest"],
        "source_sha256": identity["source_sha256"],
        "semantic_digest": semantic_digest,
        "owned_tables": list(_OWNED_TABLES),
        "partition_by": {
            "dwd_financial_report": ["period_end_year"],
            "dwd_financial_concept": [],
            "dwd_financial_fact": ["period_end_year"],
        },
        "row_counts": row_counts,
        "files": [_file_record(work, path) for path in files],
    }
    _write_json_atomic(work / "manifest.json", manifest)
    _write_text_atomic(work / "READY", _sha256(work / "manifest.json") + "\n")
    _fsync_directory(work)
    _verify_existing_financial_candidate(work, identity=identity)
    os.replace(work, output)
    _fsync_directory(output.parent)
    return _verify_existing_financial_candidate(output, identity=identity)


def build_financial_reporting(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    source_audit_path: Path,
    output: Path,
    run_id: str,
    source_batch_size: int = 32,
    native_batch_size: int = 4096,
) -> FinancialReportingBuildResult:
    """Build a checkpointed Issue #61 candidate without changing publication state."""

    if output.is_symlink():
        raise FinancialReportingBuildError("financial candidate output must not be a symbolic link")
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized = output.resolve()
    lock_path = normalized.parent / f".{normalized.name}.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o640)
    except OSError as exc:
        raise FinancialReportingBuildError("cannot open financial build lock") from exc
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise FinancialReportingBuildError("financial build lock must be a physical file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        return _build_financial_reporting_locked(
            ods_package=ods_package,
            input_plan=input_plan,
            model_path=model_path,
            listed_company_candidate=listed_company_candidate,
            source_audit_path=source_audit_path,
            output=normalized,
            run_id=run_id,
            source_batch_size=source_batch_size,
            native_batch_size=native_batch_size,
        )
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
