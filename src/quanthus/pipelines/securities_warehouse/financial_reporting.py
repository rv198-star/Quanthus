"""Decode provider-native financial-statement envelopes for Issue #61."""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zoneinfo import ZoneInfo


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
    if not isinstance(value, str):
        raise FinancialReportingBuildError("provider timestamp must be a string")
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
        if display_type == 1 or not isinstance(field, str) or not field.strip():
            headings += 1
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
    source_concepts: dict[str, set[tuple[str, str]]] = {
        source.label: set() for source in sources
    }
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


def financial_reporting_schemas() -> dict[str, Any]:
    """Return the physical schemas for the three Issue #61-owned DWD tables."""

    try:
        import pyarrow as pa
    except ModuleNotFoundError as exc:
        raise FinancialReportingBuildError(
            "pyarrow is required for financial DWD rows"
        ) from exc
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
        for identity in identities:
            if not identity.stock_code or not identity.company_id or not identity.security_id:
                raise FinancialReportingBuildError("financial company identity has an empty key")
            by_code.setdefault(identity.stock_code, []).append(identity)
        self._by_code = {
            code: tuple(sorted(values, key=lambda item: (item.effective_from, item.security_id)))
            for code, values in by_code.items()
        }

    def resolve(
        self, stock_code: str, *, period_end: date, published_at: datetime | None
    ) -> FinancialCompanyIdentity:
        coordinate = published_at or _period_coordinate(period_end)
        matches = [
            identity
            for identity in self._by_code.get(stock_code, ())
            if identity.effective_from <= coordinate
            and (identity.effective_to is None or coordinate < identity.effective_to)
        ]
        if len(matches) != 1:
            raise FinancialReportingBuildError(
                "financial report cannot resolve one effective company identity: "
                f"stock_code={stock_code} coordinate={coordinate.isoformat()} "
                f"matches={len(matches)}"
            )
        return matches[0]


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
    seen_fact_keys: set[tuple[str, str, str, str, date]] = set()
    period_type = "instant" if statement_type == "balance_sheet" else "duration"

    for period, report, _accounting in iter_statement_reports(envelope):
        period_end = _report_date(period, "financial report period")
        published_at = conservative_provider_timestamp(report.get("publish_date"))
        company = company_resolver.resolve(
            stock_code, period_end=period_end, published_at=published_at
        )
        statement_scope = _provider_text(report.get("rType"), _UNSPECIFIED_SCOPE)
        accounting_standard = _provider_text(report.get("data_source"), _UNSPECIFIED_STANDARD)
        audit_status = _provider_text(report.get("is_audit"), "") or None
        report_id = _stable_id(
            "financial_report", company.company_id, statement_type, period, statement_scope
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
            if item.get("item_display_type") == 1 or not isinstance(concept_code, str):
                continue
            concept_code = concept_code.strip()
            if not concept_code:
                continue
            concept_name = _provider_text(item.get("item_title"), concept_code)
            concept_id = _stable_id(
                "financial_concept",
                _TAXONOMY_NAMESPACE,
                source_revision,
                statement_type,
                concept_code,
            )
            prior_concept = concepts.get(concept_id)
            concept_row = {
                "record_source": source_name,
                "source_record_id": (
                    f"{source.relative_path}#statement_type={statement_type}&concept={concept_code}"
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
    return FinancialEnvelopeRows(tuple(reports), tuple(concepts.values()), tuple(facts))
